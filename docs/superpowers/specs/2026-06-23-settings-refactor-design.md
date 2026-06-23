# settings APP 改造设计（BaseScript → run(runtime) 范式）

- **日期**: 2026-06-23
- **状态**: 设计已确认，待写实施计划
- **作者**: brainstorming 产出
- **关联**: `docs/superpowers/specs/2026-06-23-script-template-design.md`（通用脚本模板）

## 1. 背景与动机

通用脚本模板 `_template` 已板端验收 6/6 通过，证明 reset 框架的 `run(runtime)` + 单线程结构稳定（fc 持续涨不卡、AI 异常隔离）。

但 `scripts/settings/app.py` 是旧 `BaseScript` 架构孤儿：
- `SettingsApp(BaseScript)` 子类，依赖 `on_enter/on_frame/on_exit` 生命周期 + `ctx.lcd`/`ctx.lang`/`ctx.config`。
- **没有 `run()` 入口** → main.py 调 `mod.run(runtime)` 失败（"script has no run()"）→ 从主菜单点设置进不去。
- reset 框架从不实例化 `ScriptRunner`/`ScriptContext`，旧生命周期从未被调用。

settings 是 page 型（无摄像头），左右分栏 UI（左栏语言/关于列表，右栏内容区），触摸点击交互。现有 322 行 UI 代码（左右分栏/语言切换/关于）已实现且可用，只是骨架是旧架构。

## 2. 目标

把 settings 改造为 reset 框架的 `run(runtime)` 范式，与通用脚本模板**一致、同步、复用**同一套 init/退出框架。改造后 settings 能从主菜单点进、左右分栏 UI 正常、语言切换/关于业务可用、触摸返回稳定退出。

## 3. 核心原则

- **框架一致**：settings 走 `runtime.init_app` 统一路径（和模板、未来 AI 脚本同一条 init 路径），不为 page 型单独分支。sensor 照 init（框架一致），settings `run()` 不取帧。
- **复用现有 UI**：322 行左右分栏/语言/关于 UI 代码大部分复用，只换骨架（类→函数 + ctx→runtime + 加顶栏）。
- **风格统一**：模块级 `run(runtime)` + 模块级 UI 函数 + 模块级全局 UI 引用，对齐模板风格。

## 4. 范围

### 4.1 做

- 原地改造 `scripts/settings/app.py`：`SettingsApp(BaseScript)` 类 → 模块级 `run(runtime)` 函数 + 模块级 UI 函数。
- `ctx.X` → `runtime.X`（`ctx.lang`→`runtime.lang`、`ctx.config`→`runtime.config`）。
- 加顶栏返回钮（标题 `lang.t("category.settings")`）+ `run()` 主循环（`while not exit_flag: task_handler()`）。
- 语言切换 + 关于两块业务原样保留（`config.save()` 在触摸回调，单线程安全；`event_bus.emit('lang_changed')` 保留）。
- 删 `BaseScript` 继承、`on_enter/on_frame/on_exit`、`SCRIPT_ID`/`SELF_MANAGED_TOP_BAR` 等旧架构属性。

### 4.2 不做

- 不改 `init_app`（settings 走 init_app 统一路径，sensor 照 init 但 settings 不取帧——框架一致，不为 page 型分支）。
- 不改 `categories.json`/`manifest.json`（settings 是正式 category，已注册）。
- 不碰 face_detect / camera / _template。
- 不改框架代码（app_runtime.py / main.py 不动——模板阶段已改好，cleanup 对 settings 生效）。
- 不重构左右分栏 UI 布局（复用现有）。

## 5. 架构与调用链

```
main.py main()
  └─ _read_next_script() → "settings"
  └─ run_script("settings")
       ├─ runtime = AppRuntime()
       ├─ runtime.init_app("settings", fpioa)     ← 统一路径(ui_mode=page → FULL render mode)
       │    ├─ Display.init(osd_num=2)
       │    ├─ _config_sensor([(chn0, VGA, RGB888)])  ← 照 init(settings 不取帧,框架一致)
       │    ├─ MediaManager.init()
       │    ├─ _init_backlight
       │    ├─ lv.init() + _lvgl_init(FULL)        ← page → FULL
       │    ├─ _init_touch()                        ← 触摸
       │    ├─ fonts.load_all()
       │    ├─ _init_services(lang/config/buzzer)
       │    └─ sensor.run()                         ← 照 run(框架一致,settings 不取帧)
       ├─ _load_script("settings") → scripts.settings.app
       ├─ mod.run(runtime)        ← settings 主循环(纯 UI,无取帧)
       ├─ runtime.cleanup()       ← 非face_detect,统一 deinit
       ├─ _clear_next_script()
       └─ machine.reset()         ← 回主菜单
```

### 5.1 关键架构点

1. **走 init_app 统一路径**：settings 和模板、未来 AI 脚本同一条 init 路径。`init_app` 对 settings 的 `ui_mode="page"` 自动选 FULL render mode（模板阶段已实现）。不为 page 型单独分支。
2. **run() 主循环极简**：page 型无 sensor 取帧、无 on_frame、无 show_image。主循环只有 `lv.task_handler()`。单线程，display 写者只有 task_handler，无竞争。
3. **退出路径与模板一致**：顶栏返回钮 CLICKED → 设 `exit_flag[0]=True` → `run()` 主循环退出 → `_destroy_ui()` → main.py `runtime.cleanup()` → reset 回菜单。
4. **runtime 属性映射**：`ctx.lang`→`runtime.lang`、`ctx.config`→`runtime.config`（init_app 的 `_init_services` 创建 LangManager/ConfigManager 并 load）。settings **不依赖** `ctx.lcd`/`ctx.buzzer`（grep 确认只用 lang/config）。
5. **模块级风格**：`run(runtime)` + `_build_ui(runtime, exit_flag)` + `_destroy_ui()` + 业务函数都是模块级，UI 对象引用用模块级全局。`self._xxx` → 模块级 `_xxx`。

### 5.2 单写者分析（为什么不卡）

- settings 单线程，主循环只有 `task_handler()`。LVGL FULL 模式，但 page 型 UI 静态（只在点击切换时重绘小区域），无 OSD1 推帧、无 AI 线程。display 只有一个写者（task_handler），无并发竞争。
- 比模板更简单（模板有 OSD1 推帧，settings 没有），更不可能卡。

## 6. app.py 改造结构

### 6.1 模块级结构

```python
# scripts/settings/app.py — 设置页（左右分栏布局）run(runtime) 范式

import struct
import lvgl as lv
from core.event_bus import event_bus
from core.icon_cache import icon_cache
from ui.theme import Colors, make_back_bar_text_style
from core.font_manager import fonts

# ── 布局常量（保留现有，BAR_H=52 已预留顶栏）──
BAR_H = 52; PANEL_TOP_GAP = 6; ...（现有常量照搬）

# ── 模块级 UI 引用（替代 self._xxx）──
_screen = None
_top_bar = None          # 新增：顶栏返回钮
_left_panel = None
_right_panel = None
_divider = None
_rows = []
_active_item = "language"


def run(runtime):
    """settings 主入口（reset 框架调 mod.run(runtime)）。page 型，无取帧。"""
    global _active_item
    _active_item = "language"
    exit_flag = [False]
    _build_ui(runtime, exit_flag)
    while not exit_flag[0]:
        os.exitpoint()
        time.sleep_ms(lv.task_handler())
    _destroy_ui()


def _build_ui(runtime, exit_flag):
    """顶栏(返回钮+标题) + 左右分栏。ctx→runtime。"""
    global _screen, _top_bar, _left_panel, _right_panel, _divider
    lang = runtime.lang
    screen = lv.scr_act()
    screen.set_style_bg_color(lv.color_hex(Colors.BG), 0)
    screen.set_style_bg_opa(255, 0)
    _screen = screen
    _build_top_bar(runtime, exit_flag)     # 新增顶栏
    # 左栏/分界线/右栏（现有 _build_ui 逻辑搬来，self→模块级，ctx→runtime）
    ...
    _render_right(_active_item)


def _build_top_bar(runtime, exit_flag):
    """顶栏：返回钮(左) + 标题"设置"(中)。对齐模板顶栏。"""
    global _top_bar
    # 复用模板/BackBar 的返回钮+图标逻辑，标题 lang.t("category.settings")
    # 返回钮 CLICKED → exit_flag[0] = True


def _destroy_ui():
    """删顶栏/左右栏/分界线 + 恢复屏幕。不碰 runtime 硬件(main.py cleanup)。"""
    # 删 _top_bar/_left_panel/_right_panel/_divider（各 try/except）
    # _rows = []


# ── 业务函数（原样保留，self→模块级，ctx→runtime）──
def _build_left_row(index, item_id, name_key): ...   # runtime.lang
def _select_item(item_id): ...
def _render_right(item_id): ...
def _render_language(): ...      # runtime.lang, runtime.config
def _set_lang(code): ...         # runtime.lang.switch + runtime.config.set/save + event_bus.emit
def _render_about(): ...
def _refresh_texts(): ...
```

### 6.2 改造要点

1. **删**：`class SettingsApp(BaseScript)`、`SCRIPT_ID`、`SELF_MANAGED_TOP_BAR`、`__init__`、`on_enter`、`on_exit`、`super()` 调用、所有 `self.`
2. **加**：模块级 `run(runtime)`、`_build_top_bar`、模块级 UI 全局、`exit_flag` 闭包
3. **改**：`self._xxx` → 模块级 `_xxx`、`self.ctx.lang` → `runtime.lang`、`self.ctx.config` → `runtime.config`、`ctx.lang` → `runtime.lang`
4. **业务逻辑零改动**：语言切换（`_set_lang` 的 switch/save/emit/refresh）、关于（`_render_about` 的 6 行）原样保留，只改引用

### 6.3 顶栏返回钮复用

模板 `_build_ui` 里的返回钮逻辑（48×48 透明点击区 + `icon_cache.get_back_icon()` + zoom + CLICKED 设 `exit_flag`）直接搬到 settings 的 `_build_top_bar`。标题用 `runtime.lang.t("category.settings")`。

### 6.4 左右分栏布局不变

现有 `_build_ui` 的左栏（224px）/分界线（4px）/右栏（412px）布局、`_build_left_row`、`_render_language`、`_render_about` 全部保留。只是从"类方法"变"模块级函数"，内部 `self.ctx` → `runtime`。

### 6.5 _active_item 重置

`run()` 入口把 `_active_item` 重置为 `"language"`（模块级全局，跨次进入需重置，否则上次选的"关于"会残留）。

## 7. 测试策略

新建 `tests/test_settings.py`，沿用模板 AST 风格（board 模块 Windows 不可导入）。

### 7.1 host AST 测试维度

1. `test_settings_has_run_entry` — `run` 函数存在、参数含 `runtime`
2. `test_settings_run_uses_exit_flag_loop` — `exit_flag` + `while` + `task_handler`
3. `test_settings_no_basescript` — 源码不含 `BaseScript`/`on_enter`/`on_exit`/`SCRIPT_ID`
4. `test_settings_uses_runtime_not_ctx` — 含 `runtime.lang`/`runtime.config`，不含 `ctx.`（除注释外）
5. `test_settings_has_top_bar_back_button` — 含顶栏 + 返回钮 CLICKED + `exit_flag[0] = True`
6. `test_settings_keeps_language_and_about` — 含 `lang.switch`/`config.save`/`event_bus.emit`/`render_about`
7. `test_settings_title_from_lang` — 含 `category.settings`（标题取 lang，非硬编码）
8. `test_settings_does_not_self_init_media` — 不含 `MediaManager.init`/`sensor.reset`（走 init_app）

### 7.2 板端验收（host 无法覆盖）

1. 从主菜单点"设置"卡片，稳定进入（顶栏"设置"+左右分栏）
2. 左栏点"语言"→右栏中文/English；点"关于"→右栏 6 行；切换正常
3. 语言切换后退出回菜单，菜单文字已是新语言（`config.save()` 持久化生效）
4. 点顶栏返回钮，稳定退出回主菜单，连续 5 次进出不卡
5. 退出后能再次点进（不 wedged）

### 7.3 回归

改完跑 `test_template.py`/`test_framework.py`/`test_face_detect.py` 确认不破坏。

## 8. 不确定性与风险

| 风险 | 影响 | 对策 |
|------|------|------|
| 旧 UI 代码搬移时漏改 `self.`/`ctx.` | 运行期 NameError | AST 测试 `test_settings_no_basescript`/`test_settings_uses_runtime_not_ctx` 兜底；板端验收暴露 |
| page 型也 init sensor 浪费资源 | 内存/DMA 占用 | 框架一致原则优先；settings 单线程不取帧不卡；资源占用可接受（模板已验证 init_app 全套可跑） |
| `_active_item` 模块级全局跨次残留 | 二次进入默认选中错误 | `run()` 入口重置为 `"language"` |
| 语言切换 `config.save()` 文件 I/O | 坑#2 死锁 | 单线程串行，save 在 task_handler 回调同步执行无并发 flush，安全 |

## 9. 后续

settings 改造完成后，camera 是最后一个旧孤儿（stream 型，带拍照/录像/图库业务，改造量大），另行设计。
