# 图像分类(image_classify)预览脚手架 设计

> 状态:已确认,待 writing-plans 产出实施计划。
> 日期:2026-07-01

## 目标

新增 `image_classify` 脚本,本轮**只输出单路摄像源预览**(chn0 VGA RGB888),
其余 AI/识别功能暂定(留空/占位)。UI 布局与持久化约定与前述脚本一致,
但本轮**不做持久化**(无 DB、无 JSON 读写)。

## 背景与现状(已就位)

项目已为 image_classify 预置了下列基建,本设计只补齐脚本与映射:

- `config/categories.json` — `image_classify` 已注册(order 11, script=image_classify, ui_mode=stream)。
- `resource/i18n/zh_CN.json` + `en_US.json` — `category.image_classify` / `image_classify_desc` 已存在。
- `comm/host_api.py` — `TYPE_IMAGE_CLASSIFY = 0x13` 常量已存在。
- `resource/icons/image_classify_icon/` — 目录已存在但**为空**(无 back.png/list.png)。

## 待补齐项

1. `scripts/image_classify/` 模块(`__init__.py` + `app.py`)— 不存在。
2. `comm/host_api.py` `CATEGORY_TYPE` — **缺少 `"image_classify": TYPE_IMAGE_CLASSIFY`**
   (否则 `host_tick` 回退到 `TYPE_MAIN_MENU` 0x01,协议发错)。
3. `core/app_runtime.py` `_channels_for` — 无 image_classify 分支
   (默认单 chn0 VGA RGB888,正好是预览所需;补一个显式 `pass` 分支以表意)。

## 架构

`scripts/image_classify/app.py` 以 `_template/app.py` 为骨架,
借鉴 `road_detect/app.py` 的 `_DETECTION_ENABLED` 模式:

- 单线程主循环:`snapshot(chn=CAM_CHN_ID_0)` → `on_frame(img)`(try/except 隔离)
  → `Display.show_image(img, LAYER_OSD1)` → `runtime.host_tick(None)`
  → `time.sleep_ms(lv.task_handler())`。
- `_DETECTION_ENABLED = False` 标志位。`on_frame` 在标志为 False 时只调
  `runtime.host_tick(None)` 后 return,不跑任何 AI。后续加 AI 时翻 True 并
  在 `app_runtime._channels_for` 补 chn2、在 `on_frame` 补检测分支。
- 顶栏:64×64 返回钮(用 `icon_cache.get_back_icon()`)+ i18n 标题
  `runtime.lang.t("category.image_classify")`。
- 透明预览区:透出 OSD1 摄像头画面。
- 空底栏:占位,无按钮。

### 修复模板的潜在 bug

`_template/app.py` 的 `_on_back` 回调引用了未定义的 `_RUNTIME`(模板里没设)。
image_classify 的 `run()` 里设 `global _RUNTIME; _RUNTIME = runtime`,与
road_detect 一致,避免回退到模板的隐患。

## 组件与文件

1. **`scripts/image_classify/__init__.py`** — 空包标记(同 road_detect)。

2. **`scripts/image_classify/app.py`** — 脚本主体:
   - `run(runtime)` — 设 `_RUNTIME`,`_build_ui`,单线程循环,
     `finally: _destroy_ui()`。
   - `_build_ui(runtime, exit_flag)` — 顶栏(返回钮 + i18n 标题)
     + 透明预览 + 空底栏。布局常量 `BAR_H=52`、`PREVIEW_H=376`(同模板)。
   - `on_frame(img)` — `if not _DETECTION_ENABLED: _RUNTIME.host_tick(None); return`
     + 受保护的检测分支占位(注释 stub,同 road_detect)。
   - `_DETECTION_ENABLED = False` 顶部标志 + 注释。

3. **`comm/host_api.py`** — `CATEGORY_TYPE` 增
   `"image_classify": TYPE_IMAGE_CLASSIFY`。

4. **`core/app_runtime.py`** — `_channels_for` 增
   `elif category_id == "image_classify": pass`(显式单 chn0,镜像 road_detect)。
   无需 icon 预读分支(通用 back 图标已在 `init_app` 无条件预读)。

**无新图标资产** — `resource/icons/image_classify_icon/` 保持空;返回钮走共享的
`settings_icon/back.png`(`get_back_icon()`)。

**无持久化** — 无 DB、无 JSON load/flush(按用户选择)。

## 数据流

K230 → `runtime.host_tick(None)` → `HostAPI.tick("image_classify", None)`
→ `send_id_data(0x13, None)` → 47B 帧(40B 全零 payload)
→ 主机 `port_data_parsing` case `0x13` → `set_sensor_parameter`
→ JSON `{"port":4,"camer":{"mode":19,...}}`(0x13=19)。

## 错误处理

`on_frame` 异常在 `run()` 循环内 try/except 接住(打印 + `sys.print_exception`),
不杀预览循环 — 同 road_detect/template 隔离策略。

## 测试(TDD,host 侧 AST 契约)

新增 `tests/test_image_classify_ast.py`,镜像 `test_gesture_detect_ast.py` 结构:

- `test_image_classify_in_category_type_map` — `CATEGORY_TYPE` 含
  `"image_classify": TYPE_IMAGE_CLASSIFY`(守护 0x01 回退 bug)。
- `test_app_imports` / `test_has_run` — app.py 存在且有 `run(runtime)`。
- `test_has_host_tick` — on_frame 调 `host_tick`(协议 0x13)。
- `test_detection_disabled_default` — 存在 `_DETECTION_ENABLED = False` 字面量。
- `test_runner` — 自跑器。

如 `tests/test_host_api.py` 已有 CATEGORY_TYPE 完整性断言,顺带核对/补。

## 非目标(留待后续 AI 阶段)

AI 模型加载、检测、特征提取、ID 槽位、持久化 DB、左表、滑块、触摸取点锁定 —
全部延后。
