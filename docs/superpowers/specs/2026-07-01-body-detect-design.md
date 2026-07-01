# 人体识别(body_detect)设计

> 日期:2026-07-01
> 复刻 face_detect 的单线程模板 + 双 kmodel + KEY 注册 + 4 槽协议。
> 关键差异:face 的检测用 `aidemo.face_det_post_process` + 4200 anchors(从 .bin 读) +
> umeyama 5 点对齐提特征;人体检测用 YOLOv5n 的 `aicube.anchorbasedet_post_process` +
> 9 个硬编码 anchors + 直接 crop 检测框提特征(人体无关键点,不做仿射对齐)。
>
> **可行性核心**:设备 kmodel 清单确认无 person reid 模型,但实验20 `recognition.kmodel`
> 是**通用特征提取器**——输出特征向量,余弦相似度匹配(`getSimilarity`=dot/(norm×norm)),
> 与 `face_recognition.kmodel` 同构,demo 用在水果上但类别无关。故人体 reid 用
> `person_detect_yolov5n.kmodel`(检测) + `recognition.kmodel`(特征)实现,
> BodyDB 余弦匹配,4 槽=4 个不同人。业务代码与 face_detect 几乎一致。

## 1. 现状(已存在,本项目为补全)

- `config/categories.json`:`body_detect` 条目已存在且 enabled(order 9)。
- `comm/host_api.py`:`TYPE_BODY_DETECT = 0x09` 已存在;但 `CATEGORY_TYPE` 字典尚未映射 body_detect(待补)。
- `resource/i18n/zh_CN.json`+`en_US.json`:已有 `category.body_detect` / `body_detect_desc`;`body_detect` 功能文案块待补。
- `resource/icons/menu_icon/body_detect.png`:主菜单图标已存在。
- `resource/icons/body_detect_icon/`:目录已存在但空,待填 back.png + list.png。
- K230 demo 参考:
  - `demo/AI类实验例程/实验5 人体检测实验/main.py`(检测,`person_detect_yolov5n.kmodel`,1 类,9 anchors,strides [8,16,32])。
  - `demo/AI类实验例程/实验20 自分类学习实验/main.py`(`recognition.kmodel`,通用特征提取,crop+resize 预处理,余弦相似度)。
- `scripts/body_detect/app.py` + `core/body_ai.py` + `core/body_db.py`:均不存在,本次新建。
- `core/icon_cache.py` / `core/app_runtime.py` / `comm/host_api.py`:body_detect 分支待补(同 gesture_detect 套路)。

## 2. 架构与数据流

复刻 face_detect 的单线程模板 + 双 kmodel + KEY 注册 + 4 槽协议:

```
chn0 VGA RGB888 (显示)  ──► on_frame 画框/标签/十字 ──► OSD1 show_image
chn2 XGA RGBP888 (AI)   ──► person_detect_yolov5n.kmodel(YOLOv5n 检测,640×640,9 anchors)
                         ──► recognition.kmodel(特征提取,224×224,crop 检测框)
                              ↓ 特征向量(维度未知,泛化存储)
                         ──► database_search(余弦匹配,阈值 0.5) → 匹配 ID
K2 按键 ──► IdRegistry.poll_k2 → pending → 取最大人体框 → crop+reg 提特征 → register(feature) → slot 1-4 轮转
协议 0x09:4 槽 slots[i]=(id, x, y, w, h, conf×100)  (x/y/w/h 已缩放到 VGA;conf=匹配度 0-100)
```

数据流与 face_detect 一致(逐框检测→逐框提特征→余弦匹配→填槽),区别仅在检测后处理与特征预处理。

## 3. 组件设计

### 3.1 `core/body_db.py`(新建)—— 镜像 `core/face_db.py`

存特征向量(ulab ndarray),余弦匹配。**完全复刻 face_db 的内存-only + flush_to_disk + database_search**,
只改路径常量与日志前缀。

- `BODY_DB_PATH = "/sdcard/CamerAi/data/body_db.json"`
- `_BodyDB`:
  - `_features: {slot_id: np_array}`(运行时)
  - `_next_slot`(1-4 轮转),`_dirty`,`_clear_dirty`
  - `register(feature)` → 空槽优先,否则轮转覆盖,返回 slot_id(1-4)。**无"同类不重复占槽"**(人体是连续特征向量,不像 gesture/object 的离散标签;每次注册都进新槽或覆盖)。纯内存,设 `_dirty`。
  - **无 `match` 方法**(与 face_db 一致)——匹配走模块级 `database_search` 函数,app 直接调用,便于单测。
  - `clear()` / `init_features(path)` / `load_from_disk(path)` / `flush_to_disk(path)` / `count` —— 与 face_db 同实现。
- 模块级 `database_search(feature, db_features, threshold=0.5)` —— **直接复用 face_db 的 cosine 实现**(归一化+点积,`score = dot/2+0.5` 映射到 0-1)。app.py 调 `database_search(features[i], _db_features)`,与 face_detect 同。
- 全局单例 `body_db = _BodyDB()`;`BodyDB = _BodyDB` 别名(供测试 import,同 gesture_db 套路)。

### 3.2 `core/body_ai.py`(新建)—— 移植实验5 + 实验20

复刻 `core/face_ai.py` 的 AIBase/AI2D 结构,但检测用 YOLOv5n 后处理、特征提取用 crop(无对齐)。

模块级常量:
- `RGB888P_SIZE = [1024, 768]` / `DISPLAY_SIZE = [640, 480]`(同 face_ai)
- `PERSON_DET_KMPATH = "/sdcard/examples/kmodel/person_detect_yolov5n.kmodel"`
- `PERSON_RECO_KMPATH = "/sdcard/examples/kmodel/recognition.kmodel"`
- `PERSON_ANCHORS = [10,13, 16,30, 33,23, 30,61, 62,45, 59,119, 116,90, 156,198, 373,326]`(9 anchors,实验5 原值)
- `PERSON_LABELS = ["person"]`
- `BOX_COLORS` / `BOX_UNKNOWN` / `ALIGN_UP` / `_draw_color` —— 与 face_ai 同(可直接 import 复用 face_ai 的,或复制;为独立模块自包含,复制)。

`PersonDetectionApp(AIBase)`(镜像 gesture_ai 的 `HandDetectionApp`,因为同为 aicube.anchorbasedet):
- `__init__(kmodel_path, model_input_size, anchors, confidence_threshold=0.2, nms_threshold=0.6, rgb888p_size, display_size, debug_mode)`
- `config_preprocess`:pad+resize AI2D(同 demo 实验5 的 get_padding_param,letterbox)。
- `postprocess`:`aicube.anchorbasedet_post_process(results[0], results[1], results[2], model_input_size, rgb888p_size, [8,16,32], len(labels), conf_thresh, nms_thresh, anchors, nms_option)` → 返回 dets 列表。
- `draw_result(osd_img, dets, recognition_results)`:镜像 face_ai 的画框(ID 匹配色框+`IDn`,未匹配白框+`person`)。
- `deinit`:同 face_ai。

`PersonRecognitionApp(AIBase)`(镜像实验20 `SelfLearningApp` 的预处理,但无 crop 中心框,改为传入检测框 crop):
- `__init__(kmodel_path, model_input_size, rgb888p_size, debug_mode)`(model_input_size=[224,224])
- `config_preprocess(box, input_image_size)`:按检测框 crop(略扩 1.26×,同 gesture 的 HandRecognitionApp)+ resize 到 224×224。
- `postprocess(results)`:返回 `results[0][0]`(特征向量,与实验20 `SelfLearningApp.postprocess` 一致)。
- `deinit`:同 face_ai。

`PersonRecognition` 组合类(镜像 gesture_ai 的 `HandRecognition`,避 zip 错位):
- `__init__(det_kmodel, reco_kmodel, ...)`:加载两个 App。
- `run(img_np)` → 返回 `(det_boxes, features)`:**等长**,只含通过边界过滤的框(过滤规则同实验5:`h<0.1*display`、窄框贴边剔除)。每个保留框对应一个 feature 向量。
- `deinit()`:deinit 两个子 App。
- ⚠️ 加载顺序(坑#19):`PersonRecognitionApp` kmodel 必须在 `PersonDetectionApp.config_preprocess()` 之前加载。

### 3.3 `scripts/body_detect/app.py`(新建)—— 复刻 face_detect/app.py

~440 行,与 face_detect 几乎逐行对应:

- 模块级全局、`_init_ai()` / `_init_registry()` / `_deinit_ai()` / `on_frame()` / `_refresh_count()` /
  `_on_list_clicked` / `_on_overlay_clicked` / `_on_screen_clicked` / `_on_clear_clicked` / `_on_save_clicked` /
  `_process_overlay_close` / `_build_ui` / `_destroy_ui` / `run` —— 结构与 face_detect 一一对应。
- `_init_ai`:`PersonRecognition(det, reco)` 双 kmodel 加载(⚠️ reco 在 det.config_preprocess 前),`body_db.init_features()`。
- `on_frame`:
  1. chn2 snapshot → `img_np = img_ai.to_numpy_ref()`
  2. `det_boxes, features = _person_rec.run(img_np)`
  3. 逐框:`database_search(features[i], _db_features)` → 匹配填 slots,色框+`IDn`;未匹配白框+`person`。
  4. K2 pending:取最大人体框→`_person_rec` 提特征→`try_register(feature, buzzer, registrar=body_db.register)`→flush→刷新计数。
  5. 屏幕居中绿色十字:`img.draw_cross(320, 240, color=(0xFF,0x00,0xFF,0x00), size=20, thickness=2)`(face_detect 对齐)。
  6. `host_tick(slots)`(协议 0x09)→ gc。
- `_build_ui`:顶栏(back 图标 + 标题"人体识别")+ 预览区 + 底栏(list 图标→清除/保存浮层 + "已学习 N/4" 计数)。
- `run`:标准模板循环(snapshot→on_frame→poll_k2→overlay_close→show_image OSD1→task_handler),finally deinit+destroy+flush。

### 3.4 基础设施改动(同 gesture_detect 套路)

- `comm/host_api.py`:`CATEGORY_TYPE["body_detect"] = TYPE_BODY_DETECT`(0x09 已存在)。
- `core/app_runtime.py`:
  - `_channels_for`:`elif category_id == "body_detect": chs.append((CAM_CHN_ID_2, Sensor.XGA, Sensor.RGBP888))`
  - `init_app`:`elif category_id == "body_detect": icon_cache.preload_body_icons()`
- `core/icon_cache.py`:`_body_icons={}` + `preload_body_icons()`(读 `body_detect_icon/back.png`+`list.png`)+ `get_body_icon(name)`。
- `resource/icons/body_detect_icon/`:从 color/gesture 复制 back.png + list.png。
- `resource/i18n/zh_CN.json`+`en_US.json`:补 `body_detect` 文案块(keys:save/clear/save_success/registered/press_k2/back_fb/list_fb,文案同 gesture 块的中文/英文,"按 K2 注册人体")。

## 4. 测试策略(TDD)

镜像 gesture_detect 的 27 测试结构,全部 host 端纯 Python(无 MicroPython 依赖):

- `tests/test_body_db.py`(内存,~9 项):
  - register 返回 slot_id、按序填 1-4、轮转覆盖、`database_search` 命中返回 (slot, score)、未命中返回 (None, 0.0)、空 DB 返回 (None, 0.0)、clear 重置、count 属性、余弦匹配维度无关(用不同维度向量验证不崩)。
- `tests/test_body_db_persist.py`(持久化,4 项):
  - flush+load 往返、clear 后 flush 写空(镜像 face_db 的 _clear_dirty 行为,待实现确认:face_db 的 clear 只设标志、flush 写空 dict)。
  - 实际复刻 face_db:flush_to_disk 始终写盘(序列化当前 _features),clear 后 _features 为空 → 写 `{"next_slot":1,"slots":{}}`。
  - load 缺失文件返回 None、空 DB flush 写有效 JSON。
- `tests/test_body_ai_ast.py`(AST 契约,6 项):
  - PersonDetectionApp/PersonRecognitionApp/PersonRecognition 类存在、PERSON_LABELS(1 标签)、PERSON_ANCHORS(18 值=9 anchors)、kmodel 路径常量在文件、PersonRecognitionApp.postprocess 有 return、PersonRecognition 有 deinit+run。
- `tests/test_body_detect_ast.py`(AST 契约,8 项):
  - CATEGORY_TYPE 含 body_detect、_channels_for body_detect→chn2 XGA RGBP888 append、init_app preload_body_icons、icon_cache 有 preload_body_icons/get_body_icon、app imports body_ai+PersonRecognition、on_frame 用 registrar、host_tick 存在、draw_cross 存在。

## 5. 风险与降级

1. **recognition.kmodel 维度未知**——泛化存储,余弦匹配与维度无关;板端首帧 print 特征 shape 确认。
2. **recognition.kmodel 水果训练→人体准确率未验证**——板端调阈值(0.5 起),若误匹配高则降级:仅对最大人体框提特征(不逐框)、或每 N 帧分类一次。
3. **双 kmodel 加载顺序(坑#19)**——reco 必须在 det.config_preprocess 前,同 face_detect。
4. **比 face_detect 更重**——人体框比脸大,crop+reg 计算量略增;板端观帧率,必要时降级(上条)。
5. **BodyDB 与 FaceDB 的 clear 持久化行为**——face_db 的 clear 设 `_clear_dirty` 但 flush 始终写当前 _features(已空),故 clear 后 flush 写空 dict。body_db 直接复刻此行为(不门控),persist 测试按此断言。

## 6. 部署清单(板端)

新建 3 文件 + 修改 3 文件 + 1 图标目录 + 2 i18n:
1. `core/body_ai.py`(AI 封装)
2. `core/body_db.py`(人体特征库)
3. `scripts/body_detect/app.py`(主脚本)
4. `comm/host_api.py` / `core/app_runtime.py` / `core/icon_cache.py`(基础设施)
5. `resource/icons/body_detect_icon/`(back.png + list.png)
6. `resource/i18n/zh_CN.json` + `en_US.json`(文案)

设备端 kmodel(已在 sdcard,无需部署):`/sdcard/examples/kmodel/person_detect_yolov5n.kmodel` + `recognition.kmodel`。
