# 物体识别脚本(object_detect)设计

> 2026-06-26。基于已通过的 tag_detect 实现经验,新增 object_detect 脚本(YOLOv8n COCO80)。
> 用户确认要点:UI/串口/ID 设置与 tag_detect 一致;底栏仅左侧 list 图标(清除/保存);加居中绿十字;
> 注册语义=按类别注册(KEY2 注册当前最大框的类别到槽1-4);注册框显示 `ID%d english_class_name`(槽号+英文类名,类名不双语);未注册白框无文字。

## 目标

在 K230 上提供基于 YOLOv8n(`yolov8n_320.kmodel`,COCO 80 类)的通用物体识别脚本,复用 reset 框架单线程主循环 + LVGL 底栏 + 串口协议 + KEY2 类别注册(最多4类)+ 居中绿十字,UI 风格与 face_detect/tag_detect 一致。

## 现状(已具备,无需新建)

- `config/categories.json` 已注册 `object_detect`(order 5, ui_mode=stream)。
- `resource/icons/menu_icon/object_detect.png` 已存在(主菜单卡片图标)。
- `comm/host_api.py` 已定义 `TYPE_OBJECT_DETECT = 0x05` 常量(仅缺 CATEGORY_TYPE 映射)。
- `resource/i18n/{zh_CN,en_US}.json` 已有 `category.object_detect` / `category.object_detect_desc`。
- `resource/icons/object_detect_icon/` 目录存在(空,需放 back.png+list.png)。
- `core/id_registry.py` 已支持 `try_register(feature, buzzer, registrar=)`(tag_detect 引入)。
- `main.py` 按 `scripts/<name>/app.py` 的 `run(runtime)` 路由,无需 manifest。

## 参考

- 检测后处理:`demo/AI类实验例程/实验15 物体检测实验/main.py`(ObjectDetectionApp:YOLOv8 输出 [N,84] → argmax → conf 阈值 → 纯 Python NMS)。
- AI 封装范式:`core/face_ai.py` FaceDetectionApp(AIBase 子类,AI2D resize,run/postprocess/draw_result/deinit)。
- DB 范式:`core/tag_db.py` TagDB(纯 Python 内存-only + flush_to_disk no-op 预留,round-robin register,精确 match)。
- 主循环/UI 范式:`scripts/face_detect/app.py`(底栏 list 浮层 + 居中十字)、`scripts/tag_detect/app.py`(双功能卡,本脚本去掉)。

## 架构

### 主循环(单线程,复用 _template)

```
run(runtime):
  init_object_ai()         # 加载 yolov8n kmodel + config_preprocess
  _init_registry(fpioa)    # IdRegistry(pin=0)
  _build_ui(runtime, exit_flag)  # 顶栏 back+标题 / 透明预览 / 底栏 list+计数
  loop:
    os.exitpoint()
    img = sensor.snapshot(chn=CAM_CHN_ID_0)   # VGA RGB888 显示帧
    on_frame(img)                              # 检测+注册+画框+十字+host_tick
    _id_registry.poll_k2()
    _process_overlay_close()
    Display.show_image(img, OSD1)
    time.sleep_ms(lv.task_handler())
  finally:
    _deinit_ai(); _destroy_ui(); db.flush_to_disk(); _RUNTIME=None
```

### 检测通道

`app_runtime._channels_for` 增加:
```python
elif category_id == "object_detect":
    chs.append((CAM_CHN_ID_2, Sensor.XGA, Sensor.RGBP888))   # 与 face_detect 同通道
```
- chn0:VGA RGB888(显示 + 画框/十字)。
- chn2:XGA(1024×768) RGBP888(AI 推理输入,对齐 face_ai.RGB888P_SIZE)。
- 检测框坐标 rgb888p→display 整数缩放:`x*display_size[0]//rgb888p_size[0]`(同 face_ai.draw_result)。

### core/object_ai.py —— YOLOv8 封装

`ObjectDetectionApp(AIBase)`,镜像 face_ai.FaceDetectionApp 风格:

- 构造:`kmodel_path="/sdcard/examples/kmodel/yolov8n_320.kmodel"`,`model_input_size=[320,320]`,`confidence_threshold=0.2`,`nms_threshold=0.2`,`max_boxes_num=50`,`rgb888p_size=[1024,768]`,`display_size=[640,480]`,`labels=COCO_LABELS`。
- AI2D:set_ai2d_dtype NCHW/NCHW uint8/uint8。`config_preprocess`:仅 resize(tf_bilinear, half_pixel),**不做 letterbox**(同 demo 实验15)。build [1,3,rgb888p_h,rgb888p_w]→[1,3,320,320]。
- `postprocess(results)`(移植 demo):
  - results[0] reshape→transpose → boxes_ori[N,4](xywh 中心格式),scores_ori[N,80]。
  - argmax 取每行最大类 conf+class_id;conf>0.2 留存;xywh→l,t,r,b(rgb888p 坐标,×x_factor/y_factor)。
  - 纯 Python NMS(thresh=0.2,移植 demo.nms)。
  - 截断 max_boxes_num。返回 `[[l,t,r,b,score,class_id], ...]`(float,rgb888p 坐标)。
- `COCO_LABELS`:80 类英文标签(从 demo 拷贝,person/bicycle/car/.../toothbrush)。
- **不内置 draw_result**(画框在 app on_frame 按注册槽上色)。
- `deinit()`:del kpu/ai2d/tensors + gc.collect + sleep_ms(50)(同 face_ai)。
- `run(img_np)`:AIBase.run → postprocess → 返回 dets。

### core/object_db.py —— 类别→槽 DB

`ObjectDB` 纯 Python,镜像 tag_db.TagDB:

- `_features: dict {slot: class_id}`(slot 1-4),`_next_slot`(round-robin 1→4→1),`_dirty`。
- `register(class_id)→slot(1-4)`:round-robin 覆盖,**若该 class_id 已在某槽则返回原槽**(同类不重复占槽)。
- `match(class_id)→(slot, 1.0)` / `(None, 0.0)`:精确匹配(class_id 是 int,无相似度,score=1.0)。
- `clear()`,`count` 属性,`flush_to_disk()` no-op(持久化路径待定,同 face_db/tag_db)。
- **纯 Python,可 Windows 真单测**(镜像 test_tag_db.py)。

### on_frame 流程

```
on_frame(img):
  img_ai = sensor.snapshot(chn=CAM_CHN_ID_2)
  img_np = img_ai.to_numpy_ref()
  dets = _object_det.run(img_np)        # [[l,t,r,b,score,class_id], ...] rgb888p
  slots = [None]*4
  # 每类别取最大实例(面积最大)用于画框 + 填槽
  per_class_max = {}   # class_id -> (det, area)
  for det in dets:
    l,t,r,b,score,cid = det
    area = (r-l)*(b-t)
    if cid not in per_class_max or area > per_class_max[cid][1]:
      per_class_max[cid] = (det, area)
  for cid, (det, area) in per_class_max.items():
    l,t,r,b,score,_ = det
    slot, _ = db.match(cid)
    # 坐标 rgb888p->display
    x = int(l)*display_w//rgb888p_w; y = int(t)*display_h//rgb888p_h
    w = int(r-l)*display_w//rgb888p_w; h = int(b-t)*display_h//rgb888p_h
    conf = int(score*100)
    if slot is not None:                    # 注册类别:彩色框 + ID号+英文类名 + 填槽
      color = _draw_color(BOX_COLORS[slot])
      img.draw_rectangle(x,y,w,h, color, thickness=4)
      img.draw_string_advanced(x, y-24, 24, "ID%d %s" % (slot, COCO_LABELS[cid]), color)
      slots[slot-1] = (slot, x, y, w, h, conf)
    else:                                   # 未注册:白框无文字
      img.draw_rectangle(x,y,w,h, _draw_color(BOX_UNKNOWN), thickness=2)
  # 居中绿十字
  img.draw_cross(320, 240, color=(0xFF,0x00,0xFF,0x00), size=20, thickness=2)
  # KEY2 注册:取当前帧最大框的类别
  if _id_registry.has_pending() and dets:
    max_det = max(dets, key=面积)
    cid = int(max_det[5])
    slot = _id_registry.try_register(cid, _RUNTIME.buzzer, registrar=db.register)
    if slot is not None: _refresh_count()
  _RUNTIME.host_tick(slots)                 # 类型 0x05
  gc.collect()
```

### UI(_build_ui)

镜像 face_detect/tag_detect,顶栏(back + 标题 `lang.t("category.object_detect")`)+ 透明预览 + 底栏:
- 左侧 list 图标(绑 `_on_list_clicked` → 清除/保存浮层,CLICKABLE)。
- **无双功能卡**(object_detect 单一功能,区别于 tag_detect)。
- 居中/右侧计数标签 `lang.t("object_detect.registered", db.count)`。
- 浮层 clear/save(`_on_clear_clicked` 清 db + 蜂鸣 + 刷新;`_on_save_clicked` no-op),`_process_overlay_close` deferred 关闭。整套与 face_detect 一致。

### 串口协议

- `host_api.CATEGORY_TYPE` 加 `"object_detect": TYPE_OBJECT_DETECT`(0x05)。
- on_frame 构建4槽位,注册类别填 `(slot,x,y,w,h,conf)`(大端打包由 send_id_data 处理),未注册类别不填。`host_tick(slots)`。

### 图标 / i18n / icon_cache

- 拷贝 `resource/icons/face_detect_icon/{back,list}.png` → `resource/icons/object_detect_icon/`。
- `icon_cache.py`:加 `_object_icons={}`、`preload_object_icons()`(读 back/list.png)、`get_object_icon(name)`。`app_runtime.init_app` 加 `elif category_id=="object_detect": icon_cache.preload_object_icons()`。
- i18n `object_detect` 段(双语):
  - `registered`:`已注册 %d/4` / `Registered %d/4`
  - `clear`:`清除` / `Clear`
  - `save`:`保存` / `Save`

## 测试策略

- `tests/test_object_db.py`:真单测(register round-robin、同类不重复占槽、match、clear、count、flush no-op)。
- `tests/test_object_ai.py`:AST 契约(板端不可导入 AIBase/Ai2d)— 验证 COCO_LABELS 80 项、postprocess 含 NMS、kmodel 路径、confidence_threshold、run 返回结构。
- `tests/test_object_detect_app.py`:AST 契约 — run/on_frame 存在、on_frame 调 host_tick+slots+draw_cross(320,240)+try_register(registrar=)、白框+彩色框+_draw_color、list 浮层 handler 齐、无硬编码中文、用 CAM_CHN_ID_2。
- `tests/test_host_api.py`:加 object_detect→0x05。
- `tests/test_icon_cache.py`:加 preload_object_icons/get_object_icon 契约。
- `tests/test_app_runtime_object.py`:加 _channels_for object_detect→CAM_CHN_ID_2/XLA/RGBP888 + init_app preload。
- `tests/test_i18n_object.py`:object_detect 段 required keys(registered/clear/save)双语存在。

## 风险与降级

1. **YOLOv8 后处理纯 Python NMS**(无 aidemo C 加速),帧率可能低于 face_detect。降级:调小 max_boxes_num(50→20)、每帧 gc.collect、必要时降 rgb888p 分辨率。板端验证帧率,卡顿则记录坑并降级。
2. **多实例同类**:一帧多个"person"全画彩色框,但只填一个槽(最大实例)。合理——协议每槽一坐标。
3. **同类已注册再按 KEY2**:db.register 同类返回原槽(不重复占槽),与 tag_db 一致。
4. **NPU 累积风险**(坑#16):每帧 gc.collect,异常隔离(try/except 包 run + 画框,失败不崩主循环)。

## 不做(YAGNI)

- 不做类名双语(用户定:英文类名)。
- 不做物体分割/姿态(那是 object_classify/yolov8n_seg 的事)。
- 不做持久化(flush_to_disk no-op,路径待定,同 face_db/tag_db)。
- 不做双功能卡(单一物体检测功能)。
