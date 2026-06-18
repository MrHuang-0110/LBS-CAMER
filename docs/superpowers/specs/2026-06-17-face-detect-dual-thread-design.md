# face_detect 双线程架构修复 Design

> 日期：2026-06-17
> 主题：face_detect 从"单线程（show_image + lv.task_handler + NPU 同循环）"改为"双线程（AI 线程 + LVGL 主线程）"，对齐官方 ai_lvgl.py，根治 Display.show_image(OSD1) 偶发阻塞卡死。功能全部保留。

## 1. 背景

### 1.1 现象与根因（systematic-debugging 结论）

face_detect 连跑卡死。经多轮定位确认根因：

**`Display.show_image(img, LAYER_OSD1)` 偶发永久阻塞。** 证据：探针显示 `snapshot done` 打印、`show_image done` 不打印，中间只有 `Display.show_image` 一行代码。卡死概率性（有时第 1 帧、有时 1440 帧）。

对照官方 `examples/21-AI-With-Others/ai_lvgl.py`（AI+LVGL 共存标杆）：它用**完全相同**的 `snapshot(chn=0) + show_image(OSD1)` + 每帧 `gc.collect()` + 每帧 `lv.task_handler()`，且稳定。唯一本质差异：**官方用双线程**——AI 线程跑 snapshot+NPU+show_image+gc，LVGL 主线程只跑 `lv.task_handler()`（[ai_lvgl.py:180-191, 358-359](examples/21-AI-With-Others/ai_lvgl.py)）。注释明说"each detection task runs in its own thread for improved responsiveness"。

我们当前单线程：主循环 `lv.task_handler() → runner.tick() → on_frame(snapshot+show_image+NPU+gc)` 全在一个循环。`show_image` 的 OSD1 DMA 提交与 NPU/AI2D 的 DMA、LVGL flush 的 DMA 同线程争用，偶发拿不到资源永久阻塞。

旧颜色识别代码（camer-2026-05-14-back）同样用双线程（业务线程+LVGL 主线程）稳定运行，与官方一致。

### 1.2 已排除的变量

- chn2 格式常量：`PIXEL_FORMAT_RGB_888_PLANAR` 与 `Sensor.RGBP888` 同义，改常量不改变卡死（板端验证）。
- chn0 分辨率：已是屏幕原生 640×480，无下采样。
- gc 位置：gc 放帧头/帧尾均卡，且 gc 本身能返回（pre/post-gc 探针确认）——gc 非元凶。
- bind_layer(VIDEO1)：官方 AI+LVGL 标杆不用 bind_layer，用 show_image 软件推帧 + 双线程即稳定。方案 A 前提证伪。

### 1.3 chn2 PLANAR 修复（已完成，保留）

前置提交（9ccc22e5/6cba9e24/ec3ff718/RGBP888）已把 AI 帧数据路径从"chn0 软件转置 CHW"改为"chn2 RGBP888 硬件直出 CHW"，对齐官方 PipeLine.get_frame()。本设计在此基础上做双线程。

## 2. 目标

- 根治 show_image 偶发阻塞卡死：AI 帧循环移到独立线程，与 LVGL flush 分离。
- 对齐官方 ai_lvgl.py 双线程模型。
- **功能全部保留**：绿色十字架、4 色人脸框、ID 标签、顶/底栏、K2 注册、保存/清除弹窗、toast、UART 上送、4 脸持久化。
- 流畅度优先。

## 3. 非目标（本次不做）

- ROI 单脸过滤（稳定性计划 Task 2.2，延后）。
- AIScriptBase 继承重构（Task 2.3，延后）。
- camera app 双线程化（本次只改 face_detect；camera 当前稳定，待后续 AI APP 扩展时统一抽象）。
- ScriptRunner 架构改动（APP 自管线程，Runner 不动）。

## 4. 架构

对齐官方 ai_lvgl.py 双线程模型：

```
┌─────────────── 主线程（main 循环，不变）─────────────────┐
│  while True:                                              │
│    lv.task_handler()    # LVGL flush（OSD2 顶/底栏/弹窗/toast）│
│    runner.tick()        # K2轮询(设注册标志)+握手+退出检测  │
│    sleep_ms(5)                                            │
└───────────────────────────────────────────────────────────┘
        │  runner.tick 内：on_frame（仅 toast tick，无重活）
        │  on_key('K2')：设 _register_pending=True
        │
┌─────────────── AI 线程（on_enter 启动）──────────────────┐
│  while _ai_running:                                       │
│    img = sensor.snapshot(chn=0)          # 预览帧          │
│    _draw_overlay(img)   # 画十字架+人脸框+ID 到 img        │
│    Display.show_image(img, OSD1)         # 显示（含叠加层）│
│    ai_img = sensor.snapshot(chn=2)       # AI 帧           │
│    frame = ai_img.to_numpy_ref()         # planar CHW      │
│    det_boxes, landms = face_det.run(frame)                │
│    (有人脸) face_reg.run + _search_face → 识别结果         │
│    if _register_pending: 注册当前帧最大脸 → 清标志         │
│    _send_recognition_data()              # UART 上送       │
│    gc.collect()                          # 坑#16 每帧 gc   │
└───────────────────────────────────────────────────────────┘
```

### 4.1 线程职责

**主线程（已有 main 循环 + runner.tick）**：
- `lv.task_handler()`：LVGL flush，渲染 OSD2 层（顶/底栏、弹窗、toast）。
- `runner.tick()` 内的 K2 轮询：检测到 K2 按下 → 设 `self._register_pending = True`（不直接跑 NPU）。
- `runner.tick()` 内的握手轮询、退出检测。
- toast 超时检测、弹窗 dismiss（LVGL 操作，必须在主线程）。
- `_on_save`/`_on_clear`/list 弹窗按钮回调（LVGL 事件，天然在主线程）。

**AI 线程（新增 `_ai_loop`）**：
- 每帧：chn0 snapshot → 画叠加层 → show_image(OSD1) → chn2 snapshot + NPU 推理 → 注册检查 → UART 发送 → gc。
- 所有 NPU 推理（face_det/face_reg）集中在此线程。
- 不碰任何 LVGL 对象（线程安全）。

### 4.2 线程间通信

仅通过简单标志（MicroPython `_thread` 无锁原语需求，标志读写原子性足够）：

| 标志 | 写 | 读 | 含义 |
|------|----|----|------|
| `_ai_running` | on_enter 设 True / on_exit 设 False | AI 线程循环条件 | 线程运行/退出 |
| `_register_pending` | 主线程 on_key('K2') 设 True | AI 线程每帧检查，执行后清 False | 请求注册当前帧 |
| `_exit_requested` | ctx.request_exit / 返回按钮 | on_exit 触发 | 退出 APP |

识别结果、UART 数据：AI 线程内部使用，不跨线程。LVGL UI（toast/弹窗/status 文字）：仅主线程碰。

### 4.3 显示叠加

相机帧 img（chn0 VGA 640×480）在 show_image 前，用 image 模块画图函数叠加：
- **十字架**：`img.draw_line` 画 4 条绿色线（中心 (320, 240)，复用 CROSSHAIR_ARM/GAP 常量）。
- **人脸框**：`img.draw_rectangle` 画矩形（复用 BOX_COLORS 4 色 + BOX_UNKNOWN）。
- **ID 标签**：`img.draw_string` 画"ID1"等（image 模块内置字体，纯英文数字）。

坐标映射：AI 检测框在 chn2 坐标系（1024×768），需映射到 img 坐标系（640×480）。原 `_update_face_boxes` 的映射逻辑（`x * 640 // ai_w` 等）搬到画图函数，目标从"屏幕预览区 640×PREVIEW_H"改为"img 640×480"。

## 5. 详细改动

### 5.1 新增 `_ai_loop()`（AI 线程入口）

移入原 `on_frame` 的 AI/snapshot/show_image/gc/UART 逻辑，加 `_register_pending` 检查：

```python
def _ai_loop(self):
    """AI 线程：帧循环——snapshot+画叠加+show_image+NPU+注册+UART+gc。

    对齐官方 ai_lvgl.py 双线程：本线程独占 NPU 与 show_image(OSD1)，
    与主线程 lv.task_handler 分离，避免 show_image DMA 与 LVGL flush
    DMA 同线程争用偶发阻塞。不碰任何 LVGL 对象（线程安全）。
    """
    import os as _os
    import time as _time
    import gc as _gc
    sensor = self.ctx.lcd.get_sensor()
    frame_count = 0
    while self._ai_running:
        _os.exitpoint()
        try:
            frame_count += 1
            # 1. 预览帧（chn0）
            img = sensor.snapshot()
            if img is None:
                continue
            # 2. AI 推理（chn2）——先算结果再画框
            recognition_results = []
            if self._face_det is not None:
                ai_img = sensor.snapshot(chn=CAM_CHN_ID_2)
                if ai_img is not None:
                    frame = ai_img.to_numpy_ref()
                    self._current_frame_data = frame
                    det_boxes, landms = self._face_det.run(frame)
                    self._current_boxes = det_boxes if det_boxes else []
                    self._current_landmarks = landms if landms else []
                    # 识别（与 DB 比对，最多4脸）
                    if self._current_boxes and self._current_landmarks:
                        for i, landm in enumerate(self._current_landmarks):
                            if i >= 4:
                                break
                            try:
                                self._face_reg.config_preprocess(landm)
                                feature = self._face_reg.run(frame)
                                matched_id, score = self._search_face(feature)
                                recognition_results.append(
                                    (self._current_boxes[i], matched_id, score))
                            except Exception:
                                recognition_results.append(
                                    (self._current_boxes[i], None, 0.0))
            self._recognition_results = recognition_results
            # 3. K2 注册检查（NPU 集中本线程）
            if self._register_pending:
                self._register_pending = False
                self._do_register()  # 用当前帧 face_reg 注册
            # 4. 画叠加层到 img（十字架+人脸框+ID）
            self._draw_overlay(img, recognition_results)
            # 5. 显示
            Display.show_image(img, 0, 0, Display.LAYER_OSD1)
            self._prev_img = img  # DMA 安全区持有1帧
            # 6. UART 上送（10ms 节流）
            if self.ctx.host is not None:
                now = _time.ticks_ms()
                if _time.ticks_diff(now, self._last_send_ticks) >= SEND_INTERVAL_MS:
                    self._last_send_ticks = now
                    self._send_recognition_data()
            # 7. 每帧 gc（坑#16）
            _gc.collect()
        except Exception as e:
            import sys as _sys
            print("[FaceDetect] AI loop error: %s" % e)
            _sys.print_exception(e)
    print("[FaceDetect] AI loop exited")
```

### 5.2 新增 `_draw_overlay(img, recognition_results)`

替代原 `_update_face_boxes` + `_build_crosshair`，画到 image.Image：

```python
def _draw_overlay(self, img, recognition_results):
    """在相机帧 img 上画十字架 + 人脸框 + ID 标签（image 模块画图）。

    替代原 LVGL 对象池方案——双线程下 AI 线程不碰 LVGL 对象，
    改画到 img 随 show_image(OSD1) 一起显示。对齐官方 ai_lvgl.py
    draw_result 模式。
    """
    # 十字架（绿色，中心 320,240）
    cx, cy = 320, 240
    arm, gap = CROSSHAIR_ARM, CROSSHAIR_GAP
    c = (0x44, 0xCC, 0x44)  # 绿色 RGB
    img.draw_line(cx, cy - arm, cx, cy - gap, color=c, thickness=2)
    img.draw_line(cx, cy + gap, cx, cy + arm, color=c, thickness=2)
    img.draw_line(cx - arm, cy, cx - gap, cy, color=c, thickness=2)
    img.draw_line(cx + gap, cy, cx + arm, cy, color=c, thickness=2)

    # 人脸框 + ID（chn2 坐标 1024×768 → img 坐标 640×480）
    ai_w = self._face_det.rgb888p_size[0] if self._face_det else 1024
    ai_h = self._face_det.rgb888p_size[1] if self._face_det else 768
    for i, (box, matched_id, _) in enumerate(recognition_results):
        if i >= 4:
            break
        x, y, w, h = [int(v) for v in box[:4]]
        sx = x * 640 // ai_w
        sy = y * 480 // ai_h
        sw = max(w * 640 // ai_w, 4)
        sh = max(h * 480 // ai_h, 4)
        color_int = BOX_COLORS.get(matched_id, BOX_UNKNOWN)
        col = ((color_int >> 16) & 0xFF, (color_int >> 8) & 0xFF, color_int & 0xFF)
        img.draw_rectangle(sx, sy, sw, sh, color=col, thickness=2)
        if matched_id is not None:
            img.draw_string(sx + 2, sy + 2, "ID%d" % matched_id,
                            color=col, scale=1)
```

> 注：image 模块 draw_line/draw_rectangle/draw_string 的确切参数签名（color 元组格式、thickness、scale）需对齐 K230 image API，实施时以板端 demo（如 ai_lvgl.py 的 draw_result）为准校正。

### 5.3 删除 LVGL 叠加方案

- 删 `_build_crosshair`（LVGL 十字架对象）。
- 删 `_build_face_box_pool`（LVGL 人脸框对象池）。
- 删 `_update_face_boxes`（LVGL 框更新）。
- `_build_ui` 移除对上述两者的调用，只保留顶/底栏。
- 删 `self._crosshair_lines`、`self._box_pool` 实例属性。

> 坑#10 对策（对象池）不再需要：双线程后 AI 线程不 create/delete LVGL 对象，主线程 LVGL UI 静态，无 pending 终结器。每帧 gc 安全（官方 ai_lvgl.py 验证）。

### 5.4 on_enter 启动 AI 线程

`on_enter` 末尾，建完 UI 后：

```python
import _thread
self._ai_running = True
self._register_pending = False
_thread.start_new_thread(self._ai_loop, ())
print("[FaceDetect] AI thread started")
```

### 5.5 on_exit 停 AI 线程

`on_exit` 开头，先停 AI 线程再 deinit（否则线程还在跑 NPU 时 deinit 崩溃）：

```python
def on_exit(self):
    print("[FaceDetect] on_exit: begin")
    # 1. 停 AI 线程，等其退出（MicroPython _thread 无 join，轮询标志）
    self._ai_running = False
    import time as _time
    for _ in range(100):  # 最多等 ~1s
        if not self._ai_thread_alive:
            break
        _time.sleep_ms(10)
    print("[FaceDetect] AI thread stopped")
    # 2. 刷盘 DB（原逻辑）
    # 3. deinit AI 模型（原逻辑）
    # 4. 销毁 UI（原逻辑，删 _crosshair_lines/_box_pool 相关）
    ...
```

> `_ai_thread_alive` 由 AI 线程退出时设 False（`_ai_loop` 末尾 `self._ai_thread_alive = False`），on_enter 启动时设 True。

### 5.6 on_frame 精简

`on_frame` 不再做 AI 重活（已移到 `_ai_loop`）。runner.tick 仍调 on_frame，但只留 toast 超时检测（LVGL 操作，主线程安全）：

```python
def on_frame(self):
    import os
    os.exitpoint()
    self._tick_toast()
```

### 5.7 on_key('K2') 改设标志

```python
def on_key(self, key):
    if key == 'K2':
        self._register_pending = True  # AI 线程检查并执行注册
```

`_register_current_face` 重命名为 `_do_register`，逻辑移到 AI 线程调用（用当前帧 `_current_frame_data` + `_current_landmarks`）。

## 6. 数据流（修复后）

```
AI 线程每帧:
  chn0 snapshot → img
  chn2 snapshot → ai_img → to_numpy_ref → frame(planar CHW)
  face_det.run(frame) → det_boxes, landms
  (有人脸) face_reg.run(frame) ×N → recognition_results
  if _register_pending: _do_register() → 清标志
  _draw_overlay(img, recognition_results)  # 十字架+框+ID 画到 img
  show_image(img, OSD1)                     # 含叠加层一起显示
  UART send (10ms 节流)
  gc.collect()

主线程每帧:
  lv.task_handler()  # OSD2 顶/底栏/弹窗/toast
  runner.tick()      # K2(设标志)+握手+退出检测+on_frame(toast tick)
```

## 7. 错误处理

- AI 线程单帧异常：`try/except` 包整个帧体，记日志继续下一帧（不崩线程）。
- AI 线程意外退出：`_ai_thread_alive` 设 False，on_exit 检测到不等满。运行期若需感知，可加心跳（本次先不做）。
- on_exit 等 AI 线程超时（~1s 未退）：强制继续 deinit（打警告），板端验证是否需更长。
- chn0/chn2 snapshot 返回 None：跳过本帧对应步骤。
- NPU 推理异常：单帧 except，记日志继续。

## 8. 测试

### 8.1 主机端（Windows，纯 AST/逻辑）

- **删**：`test_face_detect_has_prebuilt_face_box_pool`、`test_face_detect_update_boxes_does_not_churn_lvgl_objects`（对象池已删，契约不再适用）。
- **新增**：
  - `test_face_detect_has_ai_loop`：FaceDetectApp 必须有 `_ai_loop` 方法。
  - `test_face_detect_ai_loop_does_not_touch_lvgl`：`_ai_loop` + `_draw_overlay` 不得调用 `lv.` 任何属性（线程安全契约）。
  - `test_face_detect_on_enter_starts_ai_thread`：on_enter 必须调 `_thread.start_new_thread` 启 `_ai_loop`。
  - `test_face_detect_on_exit_stops_ai_thread`：on_exit 必须设 `_ai_running = False`。
  - `test_face_detect_draw_overlay_uses_image_drawing`：`_draw_overlay` 必须用 `draw_rectangle`/`draw_line`（image 模块），不得用 lv.obj。
- **保留**：`test_face_detect_on_frame_collects_gc_each_frame` 改为守 `_ai_loop` 内有 gc.collect（on_frame 不再有 gc）；其余结构/chn2/host/face_db 测试保留。

### 8.2 板端

- **验收 1（核心）**：连跑 5 分钟不卡死。AI 线程帧率稳定（可加临时心跳），mem_free 稳定。
- **验收 2**：前 20 帧 AI 线程正常推进（show_image 不阻塞）。
- **验收 3（功能回归）**：十字架显示、人脸框贴脸+ID 标签、K2 注册 4 脸、保存/清除弹窗、toast、退出重进读到已注册、UART 上送。
- **验收 4（线程退出）**：返回按钮退出 → AI 线程干净退出 → on_exit deinit → 回主菜单，不崩溃。

## 9. 风险与板端验证项

| 风险 | 验证 | 回退 |
|------|------|------|
| `_thread` 栈不足（NPU+ulab 重负载）崩溃 | 验收 2 前 20 帧 | `_thread.stack_size(较大值)` 或启动时设栈 |
| AI 线程退出同步（无 join） | 验收 4 退出不崩 | 增大 on_exit 等待超时 / 加 sleep |
| image draw_string 字体/外观 | 验收 3 ID 标签可读 | 调 scale/换 draw_string_advanced |
| image 画图函数签名差异 | 实施时对齐 ai_lvgl.py draw_result | — |
| AI 线程与主线程 LVGL 误共享 | 验收 3 不崩 | AST 测试守门（_ai_loop 不碰 lv） |
| 坐标映射（chn2 1024×768 → img 640×480）框错位 | 验收 3 框贴脸 | 校正 _draw_overlay 映射 |

## 10. K230 硬约束遵守

- 坑#2（FATFS/DMA）：AI 线程无文件 I/O（DB 读写仅在 on_enter/on_exit 主线程安全窗口）。✓
- 坑#10（gc 触发 LVGL 终结器）：AI 线程不碰 LVGL 对象，无 pending 终结器；主线程 LVGL UI 静态。每帧 gc 安全。✓
- 坑#14（VB 不归还）：chn2 PLANAR `to_numpy_ref()` 独立 buffer；`_prev_img` 持 chn0 帧 1 帧。✓
- 坑#15（多通道启动期声明）：不改 lcd.py 通道配置。✓
- 坑#16（AI 循环每帧 gc）：AI 线程每帧 `gc.collect()`，对齐官方 ai_lvgl.py。✓

## 11. 后续扩展（本次不做，记录）

双线程验证稳定后，可抽象到 `AIScriptBase`：`_ai_loop` 模板 + 子类实现 `_run_ai`/`_draw_overlay` 钩子。后续 hand_detect/object_detect/color_detect 继承即享双线程安全。camera app 当前稳定（无 NPU），暂不动；未来若加 AI 功能再统一。
