# Step 5: face_reg.run 识别（每帧最大人脸）

## 背景

face_detect 最小基线 Step 1-4 已板端验证通过：绿十字 + 白检测框 + face_db(0 face) + face_reg kmodel（mobile 2.65MB，512 维）已加载（未 run）。Step 5 接入 face_reg.run 做人脸识别，把匹配 ID 通过已有的 `draw_result(recognition_results)` 接口画成彩色框 + ID 标签。

## 目标

每帧对**最大人脸**跑 face_reg 提取 512 维特征，与 `db_features` 余弦比对，匹配则画对应 ID 彩色框 + 标签；不匹配/无人脸则白框。当前 0 face → 全白框（识别逻辑就绪，等 Step 7 注册后生效）。

## 关键决策（已确认）

- **识别范围**：每帧只识别**最大人脸**（按 w*h 面积）。性能优先，帧率稳定。其他脸保持白框。
- **识别阈值**：cosine 相似度 **0.75**（官方 main2.py 默认）。注册后板端调优。
- **线程归属**：识别全在 AI 线程 `face_det_thread`（NPU 推理同线程，face_det/face_reg 共用 NPU）。`db_features` 启动期主线程读入的全局只读字典，AI 线程只读不写 → 无锁安全。

## 数据流

```
img_2 (chn2 RGBP888) → face_det.run(img_np) → (det_boxes, landms)
                                                    ↓
                              选最大人脸 max_i（按 det_boxes[i][2]*det_boxes[i][3]）
                                                    ↓
                              face_reg.config_preprocess(landms[max_i])  # umeyama+affine 对齐
                              face_reg.run(img_np) → feature (512 维)
                                                    ↓
                              database_search(feature, db_features, 0.75) → matched_id 或 None
                                                    ↓
                              recognition_results = [(det_boxes[max_i], matched_id, None)]
                                                    ↓
                              draw_result(img_0, det_boxes, recognition_results)
                              # 最大脸匹配→彩色框+ID；其他脸白框
```

## 组件改动

### a. `FaceDetectionApp.postprocess` — 恢复返回 landmarks

当前只返回 `post_ret[0]`（丢 landmarks）。对齐官方 main2.py:75-81 返回两值：

```python
def postprocess(self, results):
    with ScopedTiming("postprocess", self.debug_mode > 0):
        post_ret = aidemo.face_det_post_process(
            self.confidence_threshold, self.nms_threshold,
            self.model_input_size[1], self.anchors, self.rgb888p_size, results)
        if len(post_ret) == 0:
            return [], []
        return post_ret[0], post_ret[1]   # boxes, landms
```

注：官方传 `self.model_input_size[0]`，当前基线传 `self.model_input_size[1]`——保持基线现状（已验证检测正常），只改返回值。

### b. `FaceRegistrationApp.config_preprocess` — 移植 umeyama + affine

从 backup（app_full_debug_backup.py:138-200）移植：`config_preprocess(landm)` → `_get_affine_matrix(sparse_points)` → `_image_umeyama_112(src)` + `_svd22(...)`。纯数学，无外部依赖。设 ai2d.affine + build。

关键常量 `umeyama_args_112`（5 个标准关键点）从 backup 照搬。

### c. `database_search(feature, db_features, threshold)` — 新增模块级函数

对齐官方 main2.py:305 余弦比对。512 维特征（非官方 128 维，公式同）：

```python
def database_search(feature, db_features, threshold=0.75):
    if not db_features:
        return None  # 0 face → 全 unknown
    feature = feature / np.linalg.norm(feature)
    best_id, best_score = None, 0.0
    for slot_id, db_feat in db_features.items():
        norm = np.linalg.norm(db_feat)
        if norm == 0:
            continue
        db_n = db_feat / norm
        score = np.dot(feature, db_n) / 2 + 0.5
        if score > best_score:
            best_score, best_id = score, slot_id
    if best_score < threshold:
        return None
    return best_id
```

### d. `face_det_thread` — 识别逻辑

```python
det_boxes, landms = face_det.run(img_np)
recognition_results = []
if det_boxes and landms and face_reg is not None:
    try:
        max_i = max(range(len(det_boxes)),
                    key=lambda i: det_boxes[i][2] * det_boxes[i][3])
        face_reg.config_preprocess(landms[max_i])
        feature = face_reg.run(img_np)
        matched_id = database_search(feature, db_features, 0.75)
        recognition_results.append((det_boxes[max_i], matched_id, None))
    except Exception as e:
        print("[baseline-face] recog error: %s" % e)
# 十字架 + face_det.draw_result(img_0, det_boxes, recognition_results)
```

draw_result 已有逻辑（Step 2 实现）：遍历 det_boxes，按 recognition_results[i][1] 决定彩色/白色 + 是否画 ID 标签。**注意**：recognition_results 只有最大脸一条，但 draw_result 用 `i < len(recognition_results)` 索引对齐——需确认 draw_result 的索引逻辑在"只识别最大脸"场景下正确。

### draw_result 索引问题（需修复）

当前 draw_result 用 `recognition_results[i]` 按 det 索引取匹配 ID。但"只识别最大脸"时 recognition_results 只有 1 条且对应 max_i，不能按 det 顺序索引。需改为：**recognition_results 改用 dict {det_index: matched_id}**，或每条带 det_index，draw_result 按索引查。

**设计选择**：recognition_results 为 `[(det_index, matched_id), ...]`，draw_result 查表：

```python
def draw_result(self, osd_img, dets, recognition_results=None):
    rec_map = {}
    if recognition_results:
        for det_idx, mid in recognition_results:
            rec_map[det_idx] = mid
    if dets:
        for i, det in enumerate(dets):
            ...
            matched_id = rec_map.get(i)   # 仅最大脸有值
            color_hex = BOX_COLORS.get(matched_id, BOX_UNKNOWN) if matched_id else BOX_UNKNOWN
            ...
```

## 错误处理

- `face_reg` 加载失败（None）→ 跳过识别，纯检测（全白框）
- `db_features` 空 → database_search 返回 None，全白框
- `det_boxes` 空 → 无识别、无框
- face_reg.run/config_preprocess 异常 → try/except 打印，该帧跳过识别（不崩 AI 线程）
- `np.linalg.norm` 为 0（坏特征）→ database_search 跳过该 db 项

## 线程/坑约束

- 坑#16：AI 线程每帧末尾 `gc.collect()`（已有，保留）
- 坑#18：face_reg kmodel 加载在主线程（Step 4 已做）；识别推理在 AI 线程（NPU，无文件 I/O）
- 坑#19：mobile kmodel（Step 4 已定）
- `db_features` 全局只读，AI 线程不写 → 无锁

## 性能预期

每帧多 1 次 face_reg.run（仅最大脸）。官方单线程每帧多人 face_reg 都能跑，单脸帧率影响应小。板端实测确认（关注 fc 增长是否稳定、total took 是否仍 18-20ms 量级）。

## 测试（host AST + import stub）

- `test_face_det_postprocess_returns_boxes_and_landms`：postprocess 返回两值（AST 检查 return 两个值 / `post_ret[0], post_ret[1]`）
- `test_face_reg_config_preprocess_implemented`：config_preprocess 不再 NotImplementedError，含 umeyama/affine
- `test_database_search_exists_and_zero_face_returns_none`：import stub 后调 database_search({}, ...) 返回 None
- `test_face_det_thread_recognizes_largest_face`：face_det_thread 含 face_reg.run + database_search + recognition_results
- `test_draw_result_uses_det_index_map`：draw_result 用 det_index 查表（rec_map）

## 不在 Step 5 范围

- K2 注册（Step 7）
- UART 上送（Step 6）
- LVGL UI 栏（Step 8）
- 保存/清除（Step 9）
- 阈值实测调优（Step 7 注册后）
