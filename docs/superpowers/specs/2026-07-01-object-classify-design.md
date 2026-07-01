# 物体分类脚本(object_classify)设计

- 日期: 2026-07-01
- 状态: 已确认,待写实施计划
- 范围: 新增检测脚本 `scripts/object_classify/`,UI/持久化/架构复刻 body_detect,核心业务为"学习任意物体→设ID→分辨不同物体",并支持点击锁定跟踪单一物体

## 1. 背景与目标

现有脚本中 `body_detect`(人体:检测+特征提取+余弦匹配+KEY注册+4槽)是最接近"学习+识别"模式的模板,`object_detect`(YOLOv8n COCO80 检测+按类别注册槽位)提供了"检测任意物体"的能力。本脚本为两者合体:

- 用 `yolov8n_320.kmodel` 检测画面中的任意物体(COCO80)
- 用 `recognition.kmodel` 提取每个检测框的特征向量
- 通过余弦相似度把物体分辨为已注册的 ID(1~4)
- 用户可点击画面中任意物体**锁定跟踪**该物体(锁定期间只显示该物体)
- 按下 KEY2 将当前锁定物体学习进空槽,成为新 ID

## 2. 交互模型(已与用户确认)

1. **点击物体 = 锁定跟踪该物体**:点击命中某个检测框 → 把该框特征存为 `locked_feature` → 进入锁定模式,此后逐帧只跟踪/显示这一个物体,忽略其它所有物体(包括已注册 ID 的物体也不显示)。
2. **点击空白 = 解除锁定**:点预览区空白处清空 `locked_feature`,回到未锁定显示模式。
3. **锁定丢失自动解锁**:锁定期间若最高余弦相似度 < 0.75(目标离开画面/被遮挡),自动清空 `locked_feature` 回到未锁定模式,不卡死。
4. **KEY2 = 学习当前锁定物体进空槽**:仅当 `locked_feature` 非空时,KEY2 触发注册到空槽(空槽优先,满槽轮转覆盖)。注册后**保持锁定**,不清空。
5. **锁定期间显示规则**:只显示被锁定的物体(高亮黄框 + 居中十字 + 锁定标识),其余检测到的物体一律不画框。

## 3. 整体架构

- **目录**:
  - `scripts/object_classify/app.py` — 主脚本(复刻 body_detect 单线程模板)
  - `core/object_classify_db.py` — DB 模块(复刻 body_db)
- **双 kmodel**:
  - 检测器: `yolov8n_320.kmodel`,320×320 输入
  - 特征器: `recognition.kmodel`,224×224 输入,512 维特征(同 body_detect)
- **通道/格式**: `CAM_CHN_ID_2`,1024×768 RGB888P(同 body_detect / object_detect)
- **CATEGORY_TYPE**: `object_classify = 0x0A`
- **协议号**: `TYPE_OBJECT_CLASSIFY = 0x0A`,上报格式同 body_detect(4 槽 × 10 字节: id+x+y+w+h+conf,大端)
- **槽位**: 4 槽,空槽优先、满槽轮转覆盖(1→2→3→4→1,同 body_detect)
- **匹配阈值**: 余弦映射 `score = dot/2 + 0.5`,默认 0.75(同 body_detect 已板端验证值)

## 4. 每帧流程(on_frame)

单线程模板,异常隔离(on_frame 内 try/catch,AI 异常不退出主循环):

1. **检测**: ai2d 把 chn2 的 1024×768 RGB888P 缩放到 320×320 → 跑 `yolov8n_320` → 纯 Python NMS(conf 0.5 / iou 0.2,复用 object_detect 的 nms)→ 检测框列表 `boxes`,按置信度降序,截断到前 N=5 个。
2. **特征提取**: 对每个 box,crop → ai2d resize 到 224×224 → 跑 `recognition.kmodel` → 512 维特征。组成 `detected = [{box, feature, conf}, ...]`(最多 5 项)。ai2d 复用同一 output tensor 减少分配。
3. **分支(是否锁定)**:
   - **已锁定**(`locked_feature` 非空): 在 `detected` 里逐个算余弦相似度,取 score 最高者。若 ≥0.75 → 作为锁定目标,**只画这一个**(高亮黄框 + 十字 + 锁定标识);若 <0.75 → 锁定丢失,清空 `locked_feature`,回未锁定模式。
   - **未锁定**: 遍历 `detected`,对每个特征跑 `database_search` 查 DB:
     - 命中已注册槽(≥0.75)→ 画该槽颜色框(1=绿/2=蓝/3=橙/4=紫)+ ID 号
     - 未命中 → 画白框(可点击提示)
4. **触摸点击处理**: 点击命中某检测框 → 该框特征存为 `locked_feature` → 进入锁定模式。点空白 → 解除锁定。
5. **KEY2 注册**: `IdRegistry.poll_k2()` 触发 → 若 `locked_feature` 非空 → `_id_registry.try_register(locked_feature, object_classify_db.register)` → 写入空槽 → flush 持久化。注册后保持锁定。
6. **上报**: `send_id_data` 发 4 槽结果。锁定目标命中已注册槽 → 该槽填坐标+conf,id=slot_id;锁定目标未命中 → 占第 1 槽位填坐标+conf,id=0(表示未注册),其余槽空。未锁定时按各槽实际命中情况上报。

## 5. DB 模块与持久化(core/object_classify_db.py)

逐字复刻 `body_db.py`:

- **内存结构**: `features = {slot_id: feature_vector}`,slot_id ∈ {1,2,3,4},feature_vector 为 512 维 float 列表(整数化存储,同 body_db)。
- **`register(feature)`**: 空槽优先,无空槽则轮转覆盖(1→2→3→4→1,记 `next_slot`),存特征,返回 slot_id。
- **`search(feature)`**: 遍历已注册特征算余弦 `score = dot/2+0.5`,返回最高分且 ≥0.75 的 (slot_id, score),否则 (None, 0)。纯 Python cosine,host 端可单测。
- **持久化**: `flush_to_disk()` / `load_from_disk()`,路径 `/sdcard/CamerAi/data/object_classify_db.json`(os.stat 预检 + db_store 安全读写)。
- **`locked_feature` 不持久化**: 运行期临时态,重启清空。
- **`clear()`**: 清空 4 槽 + flush。
- **`count()`**: 已注册槽位数(底栏计数显示)。

复用理由:余弦匹配、4 槽轮转、整数化特征、cosine 可单测均已在 body_detect 板端验证(body_db 默认阈值 0.5→0.75 修正、cos≥0 致误命中 ID1 的坑已闭合)。换名不换逻辑,降低板端风险。

## 6. UI 布局

完全复刻统一模板(顶栏 + 预览区 + 底栏),与前几个脚本一模一样:

- **顶栏(52px, 0x1A1A1A)**: 左返回按钮 → 退出回主菜单;中间标题(i18n key `category.object_classify`)。
- **预览区(全宽 ×376px, y=52 起)**: 显示 chn2 画面(1024×768 缩放到显示尺寸)。检测框坐标按 AI 分辨率→显示分辨率等比缩放(同 object_detect / body_detect)。
- **底栏(52px, 0x1A1A1A)**: 左列表按钮 → 弹浮层(清除/保存);中间计数标签 → 显示已注册槽位数 `N/4`。
- **触摸交互(预览区)**: 点击命中检测框 → 锁定该物体;点空白 → 解锁。这是相对 body_detect 的唯一 UI 增量,其余布局零改动。

**画框规则**(对应 §4 分支):
- 未锁定 + 命中已注册槽:槽位色框(1=绿/2=蓝/3=橙/4=紫)+ ID 号。
- 未锁定 + 未命中:白框(可点击提示)。
- 已锁定:高亮黄框 + 居中十字 + 锁定标识(角标或 "LOCK" 文字),只画这一个。
- 字体:复用模板已验证字号(K230 fonts 无 small)。

**i18n 新增**: `resource/i18n/zh_CN.json` 加 `category.object_classify` = "物体分类"(英文 "Object Classify")及锁定相关提示文案。

## 7. 模型选型理由与性能

**模型**:
- 检测器 `yolov8n_320.kmodel` 而非 `person_detect_yolov5n`:唯一能在 K230 上"检测任意物体"的现成 kmodel(COCO80)。person_detect 只识人,不满足"任意物体"。
- 特征器 `recognition.kmodel`(224×224,512 维):与 body_detect 同款,已板端验证;crop 无对齐直接提特征(已验证可行)。

**性能控制**(方案 A 主要风险——每帧多框提特征):
- 检测框截断 N=5:每帧最多 5 次 recognition 推理 + 1 次 yolov8n 推理。
- ai2d 一次 crop+resize 复用同一 output tensor,减少分配。
- **降级预案**(不在初版实现,仅记入): 若板端帧率不达标,锁定状态下改为"只对置信度最高框提特征"(N=1)。

## 8. K230 坑防范(对照已记录坑)

- **坑#16 NPU 每帧 gc**: 每帧推理后手动 `gc.collect()`(face_detect/body_detect 已验证模式)。
- **坑#20 gc.collect 后 sleep_ms 长时长卡死**: 主循环内不在 gc 后紧跟 sleep_ms,沿用模板帧节流方式(模板板端验收 6/6 已闭合)。
- **fonts 无 small**: 画框标签用模板已验证字号,不调 small。
- **get_pixel RGB888 返回 None**: 本脚本画框用 LVGL label/画线,不读像素,规避。
- **rfind 传 int 崩 MP**: 协议帧解析若涉及 rfind,传 bytes 不传 int(沿用 host 协议已修模式)。
- **滚动分配致 gc 死(主菜单坑)**: 本脚本预览区静态、不滚屏,规避。

## 9. 需改动的现有文件(注册新脚本)

1. `comm/host_api.py` — 加 `object_classify = 0x0A` 到 CATEGORY_TYPE,加 `TYPE_OBJECT_CLASSIFY = 0x0A`。
2. `core/icon_cache.py` — 加 `preload_object_classify_icons()`。
3. `resource/i18n/zh_CN.json`(及英文) — 加 `category.object_classify` 及锁定文案。
4. `resource/icons/object_classify_icon/` — 新增图标资源。
5. `config/categories.json`(若存在) — 加脚本配置(模板路径)。
6. 主菜单注册入口 — 按现有脚本注册方式新增 object_classify 入口。

## 10. 测试策略(TDD)

host 端纯 Python 可单测的部分先写测试再实现:
- `object_classify_db`: register 空槽优先/满槽轮转、search 命中/未命中/阈值边界(0.75)、cosine score 映射、clear、count、持久化 round-trip。
- 余弦锁定逻辑(host 模拟): 给定 locked_feature + detected 列表,验证取最高分、≥0.75 才锁定、<0.75 丢失解锁。
- NMS: 复用 object_detect 的 nms,若有独立模块直接测;否则视为已验证不重复测。
- 协议契约: TYPE_OBJECT_CLASSIFY=0x0A、上报帧 4×10 字节大端格式(参考 body_detect 现有 AST 契约测试)。

板端验收项(人工): 检测白框、点击锁定黄框、锁定丢失自动解锁、KEY2 注册成 ID 后命中色框、计数 N/4、持久化重启仍在。

## 11. 非目标(YAGNI)

- 不做物体类别名显示(object_detect 的"person"等英文类名)——本脚本只按 ID 分辨,不报 COCO 类名。
- 不做锁定目标的轨迹预测/Kalman——特征余弦逐帧匹配足够。
- 不做多目标同时锁定——只支持单物体锁定。
- 降级预案(N=1)不在初版实现。
