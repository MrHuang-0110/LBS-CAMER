# 手势识别(gesture_detect)设计

> 日期:2026-06-30
> 复刻 face_detect 的单线程模板 + 双 kmodel + KEY 注册 + 4 槽协议。
> 关键差异:face 的"特征"是 512 维向量(余弦匹配);手势的"特征"是 label_idx(0-3),
> 匹配=标签相等,score=检测 softmax 置信度。模型是固定 4 类分类器(gun/other/yeah/five),
> 不做特征提取——这是上一轮已确认的约束。

## 1. 现状(已存在,本项目为补全)

- `config/categories.json`:gesture_detect 条目已存在且 enabled(order 8)。
- `comm/host_api.py`:`TYPE_GESTURE_DETECT = 0x08` 已存在;但 `CATEGORY_TYPE` 字典尚未映射 gesture_detect(待补)。
- `resource/i18n/zh_CN.json`+`en_US.json`:已有 `category.gesture_detect` / `gesture_detect_desc`(待补 `gesture_detect` 功能文案块)。
- `resource/icons/menu_icon/gesture_detect.png`:主菜单图标已存在。
- K230 demo 参考:`demo/AI类实验例程/实验9 手势识别实验/main.py`(双 kmodel:hand_det.kmodel + hand_reco.kmodel,4 类)。
- `scripts/gesture_detect/app.py` + `core/gesture_ai.py` + `core/gesture_db.py` + `resource/icons/gesture_detect_icon/`:均不存在,本次新建。

## 2. 架构与数据流

复刻 face_detect 的单线程模板 + 双 kmodel + KEY 注册 + 4 槽协议:

```
chn0 VGA RGB888 (显示)  ──► on_frame 画框/标签 ──► OSD1 show_image
chn2 XGA RGBP888 (AI)   ──► hand_det.kmodel(手掌检测,512×512,9 anchors)
                         ──► hand_reco.kmodel(手势分类,224×224,4类)
                              ↓ (label_idx, score)
                         ──► gesture_db.find_slot(label_idx) → 匹配 ID
K2 按键 ──► IdRegistry.poll_k2 → pending → 取最大手 → register(label_idx) → slot 1-4 轮转
协议 0x08:4 槽 slots[i]=(id, x, y, w, h, conf)  (x/y/w/h 已缩放到 VGA)
```

**与 face_detect 的关键差异**:face 的"特征"是 512 维向量(余弦匹配);手势的"特征"是
label_idx(0-3),匹配=标签相等,score=检测 softmax 置信度。模型是固定 4 类分类器
(gun/other/yeah/five),不做特征提取——这是上一轮已确认的约束。

## 3. core/gesture_ai.py(AI 模块)

移植 demo 实验9,镜像 core/object_ai.py / core/face_ai.py 风格:

- `HAND_ANCHORS = [26,27, 53,52, 75,71, 80,99, 106,82, 99,134, 140,113, 161,172, 245,276]`
  (9 个,hardcode,同 demo,不从 .bin 读)
- `HAND_LABELS = ["gun", "other", "yeah", "five"]`
- `RGB888P_SIZE = [1024, 768]`、`DISPLAY_SIZE = [640, 480]`(对齐 face_detect 的 chn2 XGA,
  而非 demo 的 1280×960——统一项目 AI 通道约定)
- `ALIGN_UP(x, align=16)` 辅助
- `HandDetectionApp(AIBase)`:
  - kmodel=`/sdcard/examples/kmodel/hand_det.kmodel`,model_input_size=[512,512]
  - anchors=HAND_ANCHORS,strides=[8,16,32],confidence_threshold=0.2,nms_threshold=0.5
  - `config_preprocess`:pad + resize(同 demo `get_padding_param`)
  - `postprocess`:`aicube.anchorbasedet_post_process(...)` 返回 det_boxes
- `HandRecognitionApp(AIBase)`:
  - kmodel=`/sdcard/examples/kmodel/hand_reco.kmodel`,model_input_size=[224,224]
  - `config_preprocess(det)`:crop + resize(同 demo `get_crop_param`)
  - **`postprocess` 改造**:返回 `(label_idx, score)` 而非 demo 的文本字符串。
    `result=results[0].reshape(...)`;`softmax`;`idx=np.argmax(x_softmax)`;`score=x_softmax[idx]`;
    `return (int(idx), float(score))`
- `HandRecognition` 组合类:
  - `__init__` 构造 HandDetectionApp + HandRecognitionApp,先 config_preprocess hand_det
  - `run(img_np) → (det_boxes, rec_results)`,rec_results = `[(label_idx, score), ...]`
  - 同 demo 的边界过滤(h<0.1*rgb888p_h 剔除;边缘窄掌剔除)
  - 无 `draw_result`(画框在 app.on_frame,同 object_ai)
- `deinit()` 供 app 退出调

## 4. core/gesture_db.py(持久化)

复刻 face_db 的内存-only + 退出刷盘 + 坑#2 安全策略,但存 label_idx 而非向量:

- `_slots: {slot_id: label_idx}`(1-4)
- `_next_slot`(1-4 轮转指针)、`_dirty`、`_clear_dirty`(同 face_db)
- `init_features() → _slots`:加载(从 GESTURE_DB_PATH,db_store ENOENT 安全)
- `register(label_idx) → slot_id`:**registrar 签名(供 IdRegistry 复用)**。空槽优先,
  否则 `_next_slot` 轮转 1→2→3→4→1;`_dirty=True`;返回 slot_id。
- `find_slot(label_idx) → slot_id or None`:返回绑定该标签的最低 slot(标签相等匹配)
- `get_features() → _slots`(引用,APP 读)
- `load_from_disk(path)` / `flush_to_disk(path=GESTURE_DB_PATH)` / `clear()`:同 face_db,
  JSON `{"next_slot":int,"slots":{"1":0,"2":2,...}}`
- `GESTURE_DB_PATH = "/sdcard/CamerAi/data/gesture_db.json"`
- 全局单例 `gesture_db = _GestureDB()`

## 5. scripts/gesture_detect/app.py(UI + 主循环)

复刻 face_detect/app.py 几乎逐行:

- `_init_ai()`:
  - 先加载 hand_reco kmodel,再 hand_det `config_preprocess`(双 kmodel 顺序,坑#19,同 face)
  - anchors 用 gesture_ai.HAND_ANCHORS(hardcode,不 np.fromfile)
  - `_db_slots = gesture_db.init_features()`
- `on_frame(img)`:
  - chn2 快照 → `img_np = img_ai.to_numpy_ref()`
  - `det_boxes, rec_results = _hand_rec.run(img_np)`
  - 对每只手 `(label_idx, conf)`:det rect ×缩放到 VGA;`mid = gesture_db.find_slot(label_idx)`;
    `mid is not None` → `slots[mid-1] = (mid, x, y, w, h, int(conf*100))`
  - K2 pending 时:取最大手(area)的 label_idx →
    `slot = _id_registry.try_register(label_idx, _RUNTIME.buzzer, registrar=gesture_db.register)`
    → `gesture_db.flush_to_disk()` + `_db_slots[slot]=label_idx` + `_refresh_count()`
  - **不画十字**(对齐 road_detect 用户反馈"不需要十字架",手势场景无对准意义)
  - 画框:每只手矩形 + 标签文本(手势名 + 匹配 ID,如 "yeah #1");未匹配只显手势名
  - `_RUNTIME.host_tick(slots)`;`gc.collect()`
- `_init_registry(fpioa)`:`IdRegistry(fpioa, pin=0)`(复用,传 registrar)
- `_build_ui(runtime, exit_flag)`:顶栏(返回钮 + 标题"手势识别")+ 预览区 + 底栏
  (list 图标按钮 + "已学习 N/4" 计数)。list → 清除/保存浮层(同 face)
  - 顶栏/底栏图标走 `icon_cache.get_gesture_icon("back"/"list")`
- list 浮层 `_on_clear_clicked` / `_on_save_clicked`:同 face(save 当前 no-op,
  退出兜底 flush;clear 调 `gesture_db.clear()` + `_db_slots.clear()` + 刷新计数 + 蜂鸣)
- `run(runtime)`:init_ai → init_registry → build_ui → 主循环
  (snapshot chn0 → on_frame → poll_k2 → _process_overlay_close → show_image OSD1 → task_handler),
  finally `_deinit_ai()` + `_destroy_ui()` + `gesture_db.flush_to_disk()`
- `_deinit_ai()`:`_hand_rec.deinit()`(内部 deinit hand_det + hand_rec,最佳努力 try/except)

## 6. 基础设施改动

- `core/app_runtime.py`:
  - `_channels_for`:加 `elif category_id == "gesture_detect":` 分支 →
    `chs.append((CAM_CHN_ID_2, Sensor.XGA, Sensor.RGBP888))`(同 face_detect AI 通道)
  - `init_app`:加 `elif category_id == "gesture_detect": icon_cache.preload_gesture_icons()`
- `core/icon_cache.py`:加 `self._gesture_icons = {}`、`preload_gesture_icons()`
  (读 `/sdcard/CamerAi/resource/icons/gesture_detect_icon/`)、`get_gesture_icon(name)`
- `resource/icons/gesture_detect_icon/back.png` + `list.png`:复制自 color_detect_icon(同 road_detect 做法)
- `comm/host_api.py`:`CATEGORY_TYPE` 字典加 `"gesture_detect": TYPE_GESTURE_DETECT,`(0x08)
- `resource/i18n/zh_CN.json` + `en_US.json`:加 `gesture_detect` 功能文案块
  (镜像 face_detect 的 save/clear/save_success/registered/press_k2/back_fb/list_fb),
  `press_k2` 文案改"按K2注册手势"/"Press K2 to register gesture"
- `config/categories.json`:gesture_detect 已存在且 enabled,**无需改**

## 7. 测试(TDD,host 端)

app.py / gesture_ai.py 导入 K230-only 模块(image/lvgl/media/nncase_runtime/ulab/aicube),
host 不可 import——AST 测试只 `ast.parse` 读文本(同 road/color/object)。

- `tests/test_gesture_db.py`:内存逻辑
  - register 空槽优先(1→2→3→4)
  - register 满后轮转覆盖(_next_slot 1→2→3→4→1)
  - find_slot 返回绑定该 label 的最低 slot;未绑定返回 None
  - clear 清空 + _next_slot 复位 1
  - registrar 签名:能被 IdRegistry.try_register(registrar=gesture_db.register) 调用
- `tests/test_gesture_db_persist.py`:JSON 往返 / clear 写空 / 缺失文件返回空库 / 无变化跳过
- `tests/test_gesture_ai_ast.py`:契约
  - `HandDetectionApp` / `HandRecognitionApp` 类存在
  - `HAND_ANCHORS` / `HAND_LABELS` 模块级
  - `HandRecognitionApp.postprocess` 的 return 是 tuple `(idx, score)`
  - kmodel 路径常量 `/sdcard/examples/kmodel/hand_det.kmodel` / `hand_reco.kmodel`
- `tests/test_gesture_detect_ast.py`:契约
  - `_channels_for` gesture_detect 分支含 `append` 且用 `CAM_CHN_ID_2`
  - `init_app` 调 `preload_gesture_icons`
  - `comm/host_api.py` `CATEGORY_TYPE` 含 `"gesture_detect":`
  - `_GESTURE_DB_PATH` 模块级
  - on_frame 含 `try_register(` 且 `registrar=gesture_db.register`

## 8. 风险与降级

- **性能**:双 kmodel(检测+分类)比 face_detect 更重;face_detect 历史上有 fc 卡死问题。
  每帧 `gc.collect()`;若板端卡顿,降级方案:每隔 N 帧跑分类,或只对最大手分类(不全部)。
  先全量实现,板端验收再决定。
- **"other" 类**:`other` 是兜底类,注册为 ID 意义不大但允许(保持"最大4");本次不过滤,
  用户若想过滤可后加。
- **重复标签注册**:同一手势注册到多 slot → 运行时报最低 slot(简单 faithful,不做去重)。
- **板端待验收**:双 kmodel 加载顺序、K2 注册手势→报 ID、list 清除、协议 0x08 上报、帧率稳定性。
