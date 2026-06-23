# settings APP 改造（BaseScript → run(runtime) 范式）实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 settings APP 从旧 BaseScript 架构（无 run() 入口、进不去）改造为 reset 框架的 run(runtime) 范式，与通用脚本模板框架一致，能从主菜单点进、左右分栏 UI 正常、语言切换/关于可用、触摸返回稳定退出。

**Architecture:** 原地改造 `scripts/settings/app.py`：`SettingsApp(BaseScript)` 类 → 模块级 `run(runtime)` 函数式（对齐模板风格）。走 init_app 统一路径，`ctx.X`→`runtime.X`，加顶栏返回钮 + exit_flag 退出，322 行左右分栏/语言/关于 UI 复用、业务零改动。page 型单线程纯 UI，无取帧无竞争。

**Tech Stack:** MicroPython / K230 CanMV / LVGL / reset 框架。Host 测试用 AST 解析（board 模块 Windows 不可导入，沿用 tests/ 现有 AST 风格）。

**参考设计:** `docs/superpowers/specs/2026-06-23-settings-refactor-design.md`
**参考模板:** `scripts/_template/app.py`（顶栏返回钮、run 骨架、exit_flag 模式）

**测试运行方式（所有 host 测试统一）:** `python tests/<file>.py`，期望输出 `ALL PASS`。

---

## 文件结构

| 文件 | 责任 | 操作 |
|------|------|------|
| `scripts/settings/app.py` | 设置页：run(runtime) 入口 + 顶栏 + 左右分栏 + 语言/关于业务 | 修改（类→函数式） |
| `tests/test_settings.py` | settings 改造的 AST 测试 | 新建 |

> 不改 app_runtime.py / main.py / categories.json / manifest.json（模板阶段已就绪，cleanup 对 settings 生效）。

---

## Task 1: 新建 test_settings.py + run 入口/退出机制测试

**Files:**
- Create: `tests/test_settings.py`
- Modify: `scripts/settings/app.py`（加 run 入口 + exit_flag 循环，保留旧类暂不动）

> 本 Task 先建测试文件并加最小 run 骨架（不改旧类），让 run 入口测试通过。旧类代码在 Task 4 整体替换。

- [ ] **Step 1: 新建 tests/test_settings.py**

```python
# tests/test_settings.py — host-side AST tests for settings run(runtime) refactor.
# Run with:
#   python tests/test_settings.py
import ast
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP_PATH = os.path.join(ROOT, "scripts", "settings", "app.py")


def _parse(path):
    with open(path, "r", encoding="utf-8") as f:
        return ast.parse(f.read(), filename=path)


def _function_node(tree, name):
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError("Function %s missing" % name)


def test_settings_has_run_entry():
    """settings 必须有 run(runtime) 入口（reset 框架要求）。"""
    tree = _parse(APP_PATH)
    run_fn = _function_node(tree, "run")
    arg_names = [a.arg for a in run_fn.args.args]
    assert "runtime" in arg_names, "run(runtime) entry is required by reset framework"


def test_settings_run_uses_exit_flag_loop():
    """run() 主循环必须用 exit_flag 检测退出 + task_handler（page 型纯 UI）。"""
    src = open(APP_PATH, encoding="utf-8").read()
    assert "exit_flag" in src, "run must use exit_flag for exit detection"
    assert "while" in src, "run must have main loop"
    assert "task_handler" in src, "run must call lv.task_handler()"


def test_runner():
    failures = 0
    tests = [(name, fn) for name, fn in sorted(globals().items())
             if name.startswith("test_") and callable(fn) and name != "test_runner"]
    for name, fn in tests:
        try:
            fn()
            print("PASS %s" % name)
        except Exception as e:
            failures += 1
            print("FAIL %s: %s" % (name, e))
    print("\n%s" % ("ALL PASS" if failures == 0 else "%d FAILED" % failures))
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    test_runner()
```

- [ ] **Step 2: 运行确认失败**

Run: `python tests/test_settings.py`
Expected: FAIL `test_settings_has_run_entry`（run 函数不存在）、`test_settings_run_uses_exit_flag_loop`

- [ ] **Step 3: 在 settings/app.py 顶部加 run 骨架（旧类暂保留）**

在 `scripts/settings/app.py` 的 import 块之后、`def _png_zoom` 之前，插入以下模块级全局 + run 函数（旧 `SettingsApp` 类暂不动，Task 4 整体替换）：

```python
import os
import time
from media.display import Display

# ── 模块级 UI 引用（替代旧类的 self._xxx）──
_screen = None
_top_bar = None
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
```

> 注：`_build_ui` / `_destroy_ui` 此时还是旧类的方法，run 调用会在运行期失败——但本 Task 的 AST 测试只验"run 函数存在 + 含 exit_flag/while/task_handler 字样"，不执行 run，所以测试能过。运行期正确性在 Task 4 整体替换后保证。

- [ ] **Step 4: 运行确认通过**

Run: `python tests/test_settings.py`
Expected: ALL PASS

- [ ] **Step 5: 确认旧测试不破坏**

Run: `python tests/test_template.py && python tests/test_framework.py`
Expected: 均 ALL PASS

- [ ] **Step 6: 提交**

```bash
git add scripts/settings/app.py tests/test_settings.py
git commit -m "feat(settings): add run(runtime) entry + exit_flag loop skeleton"
```

---

## Task 2: 顶栏返回钮 + 去旧架构 + ctx→runtime 测试

**Files:**
- Test: `tests/test_settings.py`

> 本 Task 只加测试，实现由 Task 4 整体替换保证。测试先 Red，Task 4 让它们 Green。

- [ ] **Step 1: 在 tests/test_settings.py 的 test_runner 之前追加 4 个测试**

```python
def test_settings_no_basescript():
    """改造后不得残留旧 BaseScript 架构。"""
    src = open(APP_PATH, encoding="utf-8").read()
    for token in ("BaseScript", "on_enter", "on_exit", "SCRIPT_ID",
                  "SELF_MANAGED_TOP_BAR", "class SettingsApp"):
        assert token not in src, "old architecture token must be removed: %s" % token


def test_settings_uses_runtime_not_ctx():
    """ctx.X 必须改为 runtime.X（不留 ctx 引用）。"""
    src = open(APP_PATH, encoding="utf-8").read()
    assert "runtime.lang" in src, "must use runtime.lang"
    assert "runtime.config" in src, "must use runtime.config"
    # 不应再有 self.ctx 或裸 ctx.lang/ctx.config（注释里的 ctx 不算：用行内检查）
    for line in src.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        assert "self.ctx" not in line, "self.ctx must be removed: %s" % line
        assert "ctx.lang" not in line and "ctx.config" not in line, \
            "ctx.lang/ctx.config must be runtime.* : %s" % line


def test_settings_has_top_bar_back_button():
    """必须有顶栏返回钮（CLICKED 设 exit_flag）。"""
    src = open(APP_PATH, encoding="utf-8").read()
    assert "_build_top_bar" in src, "must have _build_top_bar"
    assert "EVENT.CLICKED" in src, "back button must bind CLICKED"
    assert "exit_flag[0] = True" in src, "back callback must set exit_flag"


def test_settings_title_from_lang():
    """顶栏标题必须取 lang（非硬编码）。"""
    src = open(APP_PATH, encoding="utf-8").read()
    assert "category.settings" in src, "title must come from lang.t('category.settings')"


def test_settings_does_not_self_init_media():
    """走 init_app，不自 init media/sensor。"""
    src = open(APP_PATH, encoding="utf-8").read()
    assert "MediaManager.init" not in src, "must not self-init MediaManager"
    assert "sensor.reset" not in src, "must not self-reset sensor"


def test_settings_keeps_language_and_about_business():
    """语言切换 + 关于业务必须保留。"""
    src = open(APP_PATH, encoding="utf-8").read()
    assert "_set_lang" in src, "language switch function must remain"
    assert ".switch(" in src, "lang.switch must remain"
    assert ".save()" in src, "config.save must remain"
    assert "event_bus.emit" in src, "event_bus.emit must remain"
    assert "_render_about" in src, "about render must remain"
```

- [ ] **Step 2: 运行确认失败**

Run: `python tests/test_settings.py`
Expected: 新增 6 个测试 FAIL（旧类还在：BaseScript/ctx/无 _build_top_bar 等）

- [ ] **Step 3: 提交（测试先行，Red 状态）**

```bash
git add tests/test_settings.py
git commit -m "test(settings): add refactor verification tests (red)"
```

---

## Task 3: 准备完整目标文件内容（核对清单）

**Files:** 无（本 Task 是核对，为 Task 4 的整体替换做准备）

> settings 改造是大段搬移现有代码 + 改骨架。Task 4 将整体替换 app.py。本 Task 列出改造核对清单，确保 Task 4 不漏。

- [ ] **Step 1: 核对现有 settings/app.py 的业务函数清单**

现有 `SettingsApp` 类的方法（改造后全部变为模块级函数，`self.` 去掉，`self.ctx`→`runtime`）：
- `_build_ui(self)` → `_build_ui(runtime, exit_flag)`（加顶栏调用 + ctx→runtime）
- `_build_left_row(self, index, item_id, name_key)` → `_build_left_row(index, item_id, name_key)`（`self.ctx.lang`→`runtime.lang`）
- `_select_item(self, item_id)` → `_select_item(item_id)`
- `_render_right(self, item_id)` → `_render_right(item_id)`（`self.ctx`→`runtime`）
- `_render_language(self)` → `_render_language()`（`self.ctx`→`runtime`）
- `_set_lang(self, code)` → `_set_lang(code)`（`ctx.lang`/`ctx.config`→`runtime.lang`/`runtime.config`）
- `_render_about(self)` → `_render_about()`（`self.ctx`→`runtime`）
- `_refresh_texts(self)` → `_refresh_texts()`
- `_destroy_ui(self)` → `_destroy_ui()`（加删 `_top_bar`）

- [ ] **Step 2: 确认无需改动的部分**

- 布局常量（BAR_H/PANEL_TOP_GAP/LEFT_W/RIGHT_W/ROW_H 等，第 20-35 行）全部保留
- `_png_zoom` 模块级函数（第 38-47 行）保留不动
- 业务逻辑（语言切换的 switch/save/emit/refresh、关于的 6 行）原样保留，只改引用

- [ ] **Step 3: 确认本 Task 无代码改动，直接进入 Task 4**

（本 Task 是核对，不提交。）

---

## Task 4: 整体替换 settings/app.py 为 run(runtime) 函数式

**Files:**
- Modify: `scripts/settings/app.py`（整体替换）

- [ ] **Step 1: 用以下完整内容替换 `scripts/settings/app.py` 全文**

```python
# scripts/settings/app.py — 设置页（左右分栏布局）run(runtime) 范式
#
# 左栏 35%：功能列表（图标 + 名称），默认选中第一项
# 右栏 65%：内容区（语言切换 / 关于信息）
# ui_mode = "page"，LVGL 全程管理，无相机参与。
#
# 走 runtime.init_app 统一路径（与通用脚本模板一致）。page 型单线程纯 UI，
# 主循环只有 lv.task_handler()，无取帧无竞争。
#
# ⚠️ K230 约束：on_enter() 内不得做文件 I/O 的约束已不适用（改为 run() 入口）。
# config.save() 在触摸回调执行，单线程串行无并发 flush，安全。
# 图标数据由 core/icon_cache 在启动阶段预读到内存，此处只读缓存。

import os
import time
import struct
import lvgl as lv
from core.event_bus import event_bus
from core.icon_cache import icon_cache
from ui.theme import Colors, make_back_bar_text_style
from core.font_manager import fonts


# ── 布局参数 ──────────────────────────────────────
BAR_H = 52          # 返回栏高度（与顶栏对齐）
PANEL_TOP_GAP = 6   # 面板与顶栏间距
PANEL_BOTTOM_GAP = 10  # 面板与屏幕底部间距
DIVIDER_GAP = 10    # 分界线上下留白
LEFT_W = 224        # 左栏宽度 (640 * 35%)
RIGHT_W = 412       # 右栏宽度 (640 - LEFT_W - DIVIDER_W)
ROW_H = 56          # 左栏行高
ICON_TARGET = 36    # 左栏图标显示目标尺寸
ICON_PAD_LEFT = 8   # 图标左边距
ICON_TEXT_GAP = 8   # 图标与文字水平间距
CONTENT_PAD = 12    # 右栏内边距
LANG_ROW_H = 48     # 语言行高
ABOUT_ROW_H = 44    # 关于行高
DIVIDER_W = 4       # 左右栏分界线宽度
DIVIDER_COLOR = 0x555555  # 分界线颜色（比两侧面板亮）

# 顶栏按钮
TOP_BTN_SIZE = 48

# ── 模块级 UI 引用（替代旧类 self._xxx）──
_screen = None
_top_bar = None
_left_panel = None
_right_panel = None
_divider = None
_rows = []        # [(btn_obj, item_id), ...]
_active_item = "language"


def _png_zoom(png_data, target):
    """从 PNG 头解析真实尺寸，计算缩放因子（对齐 main_menu._auto_scale_icon）"""
    if not png_data or len(png_data) < 24:
        return 256
    w = struct.unpack('>I', png_data[16:20])[0]
    h = struct.unpack('>I', png_data[20:24])[0]
    if w <= 0 or h <= 0:
        return 256
    zoom = int(min(target / w, target / h) * 256)
    return max(8, min(zoom, 256))


def run(runtime):
    """settings 主入口（reset 框架调 mod.run(runtime)）。page 型，无取帧。

    单线程主循环：仅 lv.task_handler()（处理触摸 + LVGL 重绘）。
    触摸返回钮设 exit_flag → 循环退出 → _destroy_ui → main.py cleanup+reset 回菜单。
    """
    global _active_item
    _active_item = "language"
    exit_flag = [False]
    _build_ui(runtime, exit_flag)
    while not exit_flag[0]:
        os.exitpoint()
        time.sleep_ms(lv.task_handler())
    _destroy_ui()


def _build_top_bar(runtime, exit_flag):
    """顶栏：返回钮(左) + 标题"设置"(中)。对齐通用脚本模板顶栏。"""
    global _top_bar
    lang = runtime.lang

    bar = lv.obj(lv.scr_act())
    bar.set_size(lv.pct(100), BAR_H)
    bar.set_pos(0, 0)
    bar.set_style_bg_color(lv.color_hex(0x1A1A1A), 0)
    bar.set_style_bg_opa(255, 0)
    bar.set_style_border_width(0, 0)
    bar.set_style_pad_all(0, 0)
    bar.set_style_radius(0, 0)
    bar.clear_flag(lv.obj.FLAG.SCROLLABLE)
    _top_bar = bar

    # 返回钮（48×48 透明点击区 + back 图标）
    btn = lv.obj(bar)
    btn.set_size(TOP_BTN_SIZE, TOP_BTN_SIZE)
    btn.align(lv.ALIGN.LEFT_MID, 2, 0)
    btn.set_style_bg_opa(0, 0)
    btn.set_style_border_width(0, 0)
    btn.set_style_shadow_width(0, 0)
    btn.set_style_outline_width(0, 0)
    btn.set_style_outline_opa(0, 0)
    btn.set_style_pad_all(0, 0)
    btn.clear_flag(lv.obj.FLAG.SCROLLABLE)
    btn.add_flag(lv.obj.FLAG.CLICKABLE)

    icon_data, icon_dsc = icon_cache.get_back_icon()
    if icon_dsc is not None and icon_data is not None:
        w = h = 64
        if len(icon_data) >= 24:
            w = struct.unpack('>I', icon_data[16:20])[0]
            h = struct.unpack('>I', icon_data[20:24])[0]
        target = int(TOP_BTN_SIZE * 0.85)
        zoom = int(min(target / w, target / h) * 256) if w > 0 and h > 0 else 256
        zoom = max(8, min(zoom, 256))
        icon_img = lv.img(btn)
        icon_img.set_src(icon_dsc)
        icon_img.set_zoom(zoom)
        icon_img.center()
    else:
        lbl = lv.label(btn)
        lbl.set_text("<")
        lbl.center()

    def _on_back(e):
        if e.get_code() == lv.EVENT.CLICKED:
            exit_flag[0] = True
    btn.add_event(_on_back, lv.EVENT.CLICKED, None)

    # 标题居中
    title = lv.label(bar)
    title.set_text(lang.t("category.settings"))
    title.align(lv.ALIGN.CENTER, 0, 0)
    title.add_style(make_back_bar_text_style(fonts.body), 0)


def _build_ui(runtime, exit_flag):
    """顶栏(返回钮+标题) + 左右分栏。"""
    global _screen, _left_panel, _right_panel, _divider
    lang = runtime.lang

    screen = lv.scr_act()
    screen.set_style_bg_color(lv.color_hex(Colors.BG), 0)
    screen.set_style_bg_opa(255, 0)
    _screen = screen

    _build_top_bar(runtime, exit_flag)

    scr_w = screen.get_width()
    scr_h = screen.get_height()
    content_h = scr_h - BAR_H - PANEL_TOP_GAP - PANEL_BOTTOM_GAP
    panel_y = BAR_H + PANEL_TOP_GAP

    # ── 左栏（功能列表）──
    _left_panel = lv.obj(screen)
    _left_panel.set_size(LEFT_W, content_h)
    _left_panel.set_pos(0, panel_y)
    _left_panel.set_style_bg_color(lv.color_hex(0x1A1A1A), 0)
    _left_panel.set_style_bg_opa(255, 0)
    _left_panel.set_style_border_width(0, 0)
    _left_panel.set_style_pad_all(0, 0)
    _left_panel.set_style_radius(14, 0)
    _left_panel.set_style_clip_corner(True, 0)
    _left_panel.clear_flag(lv.obj.FLAG.SCROLLABLE)

    # ── 分界线（上下各留 DIVIDER_GAP）──
    _divider = lv.obj(screen)
    _divider.set_size(DIVIDER_W, content_h - DIVIDER_GAP * 2)
    _divider.set_pos(LEFT_W, panel_y + DIVIDER_GAP)
    _divider.set_style_bg_color(lv.color_hex(DIVIDER_COLOR), 0)
    _divider.set_style_bg_opa(255, 0)
    _divider.set_style_border_width(0, 0)
    _divider.set_style_pad_all(0, 0)
    _divider.set_style_radius(0, 0)
    _divider.clear_flag(lv.obj.FLAG.SCROLLABLE)
    _divider.clear_flag(lv.obj.FLAG.CLICKABLE)

    # ── 右栏（内容区，高度与左栏持平）──
    _right_panel = lv.obj(screen)
    _right_panel.set_size(RIGHT_W, content_h)
    _right_panel.set_pos(LEFT_W + DIVIDER_W, panel_y)
    _right_panel.set_style_bg_color(lv.color_hex(0x222222), 0)
    _right_panel.set_style_bg_opa(255, 0)
    _right_panel.set_style_border_width(0, 0)
    _right_panel.set_style_pad_all(CONTENT_PAD, 0)
    _right_panel.set_style_radius(14, 0)
    _right_panel.set_style_clip_corner(True, 0)
    _right_panel.clear_flag(lv.obj.FLAG.SCROLLABLE)

    # ── 构建左栏行 ──
    items = [
        ("language", "settings.tab_language"),
        ("about",    "settings.tab_about"),
    ]
    for i, (item_id, name_key) in enumerate(items):
        _build_left_row(runtime, i, item_id, name_key)

    # ── 渲染右栏默认内容 ──
    _render_right(runtime, _active_item)


def _build_left_row(runtime, index, item_id, name_key):
    """构建左栏单行：图标(最左) + 名称(图标右侧)，无箭头"""
    lang = runtime.lang
    y = index * ROW_H
    active = (item_id == _active_item)

    row = lv.btn(_left_panel)
    row.set_size(LEFT_W, ROW_H)
    row.set_pos(0, y)
    row.set_style_bg_color(
        lv.color_hex(0x2A2A2A if active else 0x1A1A1A), 0)
    row.set_style_bg_opa(255, 0)
    row.set_style_border_width(0, 0)
    row.set_style_shadow_width(0, 0)
    row.set_style_outline_width(0, 0)
    row.set_style_outline_opa(0, 0)
    row.set_style_radius(0, 0)
    row.set_style_pad_all(0, 0)

    # 图标（最左对齐 — 须补偿 set_zoom 后的居中偏移）
    icon_data, icon_dsc = icon_cache.get_settings_icon(item_id)
    if icon_dsc is not None and icon_data is not None:
        icon_img = lv.img(row)
        icon_img.set_src(icon_dsc)
        zoom = _png_zoom(icon_data, ICON_TARGET)
        icon_img.set_zoom(zoom)
        src_w = struct.unpack('>I', icon_data[16:20])[0]
        rendered_w = src_w * zoom // 256
        icon_x = ICON_PAD_LEFT - (src_w - rendered_w) // 2
        icon_img.align(lv.ALIGN.LEFT_MID, icon_x, 0)

    # 功能名（图标右侧）
    name_label = lv.label(row)
    name_label.set_text(lang.t(name_key))
    text_x = ICON_PAD_LEFT + ICON_TARGET + ICON_TEXT_GAP
    name_label.align(lv.ALIGN.LEFT_MID, text_x, 0)
    name_label.add_style(make_back_bar_text_style(fonts.body), 0)

    def _on_row(e, iid=item_id):
        if e.get_code() == lv.EVENT.CLICKED:
            _select_item(iid)
    row.add_event(_on_row, lv.EVENT.CLICKED, None)

    _rows.append((row, item_id))


def _select_item(item_id):
    """切换左栏选中项 + 刷新右栏"""
    global _active_item
    if item_id == _active_item:
        return
    _active_item = item_id

    for row, iid in _rows:
        active = (iid == item_id)
        row.set_style_bg_color(
            lv.color_hex(0x2A2A2A if active else 0x1A1A1A), 0)

    _right_panel.clean()
    _render_right(_ctx_runtime(), item_id)


def _render_right(runtime, item_id):
    if item_id == "language":
        _render_language(runtime)
    elif item_id == "about":
        _render_about(runtime)


def _render_language(runtime):
    lang = runtime.lang
    config = runtime.config
    container = _right_panel

    langs = [
        ("zh_CN", "settings.lang_zh"),
        ("en_US", "settings.lang_en"),
    ]
    for i, (code, key) in enumerate(langs):
        active = (lang.lang == code)
        row = lv.btn(container)
        row.set_size(lv.pct(100), LANG_ROW_H)
        row.align(lv.ALIGN.TOP_MID, 0, i * (LANG_ROW_H + 6))
        row.set_style_bg_color(
            lv.color_hex(0x2A5298 if active else 0x2A2A2A), 0)
        row.set_style_bg_opa(255, 0)
        row.set_style_radius(10, 0)
        row.set_style_border_width(0, 0)
        row.set_style_shadow_width(0, 0)

        lbl = lv.label(row)
        lbl.set_text(lang.t(key))
        lbl.align(lv.ALIGN.LEFT_MID, 12, 0)
        lbl.add_style(make_back_bar_text_style(fonts.body), 0)

        if active:
            cur_lbl = lv.label(row)
            cur_lbl.set_text(lang.t("settings.lang_current"))
            cur_lbl.align(lv.ALIGN.RIGHT_MID, -12, 0)
            cur_lbl.add_style(make_back_bar_text_style(fonts.body), 0)
            cur_lbl.set_style_text_color(
                lv.color_hex(Colors.ACCENT), 0)

        def _on_lang(e, c=code):
            if e.get_code() == lv.EVENT.CLICKED:
                _set_lang(c)
        row.add_event(_on_lang, lv.EVENT.CLICKED, None)


def _set_lang(code):
    """切换语言：switch + 持久化 + 通知 + 刷新"""
    runtime = _ctx_runtime()
    if runtime.lang.lang == code:
        return
    runtime.lang.switch(code)
    runtime.config.set('lang', code)
    runtime.config.save()
    event_bus.emit('lang_changed')
    _refresh_texts(runtime)
    _right_panel.clean()
    _render_right(runtime, _active_item)


def _render_about(runtime):
    lang = runtime.lang
    container = _right_panel

    rows = [
        ("settings.about_app_name", lang.t("settings.about_app_name_val")),
        ("settings.about_version",  lang.t("settings.about_version_val")),
        ("settings.about_platform", lang.t("settings.about_platform_val")),
        ("settings.about_display",  lang.t("settings.about_display_val")),
        ("settings.about_sdk",      lang.t("settings.about_sdk_val")),
        ("settings.about_author",   lang.t("settings.about_author_val")),
    ]
    for i, (key, val) in enumerate(rows):
        row = lv.obj(container)
        row.set_size(lv.pct(100), ABOUT_ROW_H)
        row.align(lv.ALIGN.TOP_MID, 0, i * (ABOUT_ROW_H + 4))
        row.set_style_bg_color(lv.color_hex(0x1A1A1A), 0)
        row.set_style_bg_opa(255, 0)
        row.set_style_border_width(0, 0)
        row.set_style_pad_hor(12, 0)
        row.set_style_pad_ver(0, 0)
        row.clear_flag(lv.obj.FLAG.SCROLLABLE)

        lbl_key = lv.label(row)
        lbl_key.set_text(lang.t(key))
        lbl_key.align(lv.ALIGN.LEFT_MID, 0, 0)
        lbl_key.add_style(make_back_bar_text_style(fonts.body), 0)
        lbl_key.set_style_text_color(lv.color_hex(Colors.TEXT_DIM), 0)

        lbl_val = lv.label(row)
        lbl_val.set_text(val)
        lbl_val.align(lv.ALIGN.RIGHT_MID, 0, 0)
        lbl_val.add_style(make_back_bar_text_style(fonts.body), 0)


def _refresh_texts(runtime):
    """语言切换后刷新左栏文字"""
    global _rows
    _left_panel.clean()
    _rows = []
    items = [
        ("language", "settings.tab_language"),
        ("about",    "settings.tab_about"),
    ]
    for i, (item_id, name_key) in enumerate(items):
        _build_left_row(runtime, i, item_id, name_key)


def _destroy_ui():
    """删顶栏/左右栏/分界线。不碰 runtime 硬件（main.py cleanup 负责）。"""
    global _screen, _top_bar, _left_panel, _right_panel, _divider, _rows
    for obj in (_top_bar, _left_panel, _right_panel, _divider):
        if obj is not None:
            try:
                obj.delete()
            except Exception:
                pass
    _top_bar = None
    _left_panel = None
    _right_panel = None
    _divider = None
    _rows = []
    _screen = None


def _ctx_runtime():
    """返回当前 run() 的 runtime（run 入口缓存到模块级）。

    _select_item/_set_lang 在 LVGL 回调中触发，拿不到 run() 的 runtime 参数，
    通过本函数取模块级缓存的 runtime。
    """
    return _RUNTIME


# run() 入口缓存的 runtime，供 LVGL 回调中的 _select_item/_set_lang 取用
_RUNTIME = None
```

- [ ] **Step 2: 修正 run() —— 缓存 runtime 供回调使用**

上面的 `_ctx_runtime()` 依赖 `_RUNTIME` 模块级缓存。在 `run()` 函数体开头加一行缓存。定位 `run` 函数：

```python
def run(runtime):
    """settings 主入口..."""
    global _active_item
    _active_item = "language"
    exit_flag = [False]
    _build_ui(runtime, exit_flag)
```

改为（加 `global _RUNTIME` + `_RUNTIME = runtime`）：

```python
def run(runtime):
    """settings 主入口..."""
    global _active_item, _RUNTIME
    _active_item = "language"
    _RUNTIME = runtime
    exit_flag = [False]
    _build_ui(runtime, exit_flag)
```

> 注：`_RUNTIME = None` 定义在文件末尾。Python 模块级全局在函数内赋值需 `global` 声明，已加。运行时 `run()` 先执行（设 _RUNTIME），之后 LVGL 回调才可能触发 `_select_item`/`_set_lang` 调 `_ctx_runtime()`，时序安全。

- [ ] **Step 3: 运行 settings 测试确认通过**

Run: `python tests/test_settings.py`
Expected: ALL PASS（8 个测试全过）

- [ ] **Step 4: 确认全部 host 测试不破坏**

Run: `python tests/test_template.py && python tests/test_framework.py && python tests/test_face_detect.py`
Expected: 三个文件均 ALL PASS

- [ ] **Step 5: 提交**

```bash
git add scripts/settings/app.py
git commit -m "refactor(settings): rewrite to run(runtime) functional style (drop BaseScript)"
```

---

## Task 5: 板端部署与验收（非 host 任务）

**Files:** 无（部署 + 板端验收）

> 此 Task 是板端验证，host 测试无法覆盖。需用户在 K230 板上执行。

- [ ] **Step 1: 部署文件到 /sdcard/CamerAi/**

同步到板子（覆盖）：
- `scripts/settings/app.py`

（其他文件 app_runtime.py/main.py/categories.json 模板阶段已部署，未变。）

- [ ] **Step 2: 硬断电重启**

拔电源等 5 秒以上，重新上电。

- [ ] **Step 3: 验收 1 — 能进**

从主菜单点"设置"卡片，确认稳定进入（顶栏"设置"+左栏语言/关于列表+右栏内容区）。
✅ / ❌

- [ ] **Step 4: 验收 2 — 左右分栏交互**

左栏点"语言"→右栏显示中文/English 两行；点"关于"→右栏显示 6 行信息（app名/版本/平台/显示/SDK/作者）。切换正常。
✅ / ❌

- [ ] **Step 5: 验收 3 — 语言切换持久化**

在语言页点 English → 退出回主菜单 → 确认菜单文字已变英文（`config.save()` 持久化生效，主菜单新进程重读 config）。
再进设置切回中文，确认能切回。
✅ / ❌

- [ ] **Step 6: 验收 4 — 触摸返回稳定**

点顶栏返回钮，稳定退出回主菜单。连续 5 次进出循环不卡。
✅ / ❌

- [ ] **Step 7: 验收 5 — 退出干净**

退出后能再次从菜单点进（不 wedged，不需硬断电）。
✅ / ❌

- [ ] **Step 8: 记录验收结果**

在 `项目记录.md` 追加 settings 改造板端验收结果。提交。

```bash
git add 项目记录.md
git commit -m "docs: record settings refactor board acceptance"
```

---

## Self-Review

**1. Spec 覆盖检查:**
- §4.1 类→函数式 → Task 4 ✓
- §4.1 ctx→runtime → Task 4（runtime.lang/runtime.config）✓
- §4.1 顶栏返回钮 + run 主循环 → Task 1（骨架）+ Task 4（完整）✓
- §4.1 语言切换/关于业务保留 → Task 4（_set_lang/_render_about 原样）✓
- §4.1 删 BaseScript/on_enter/on_exit/SCRIPT_ID → Task 4 ✓（Task 2 测试 test_settings_no_basescript 验证）
- §4.2 不改 init_app/categories/manifest/框架 → 计划全程未触碰 ✓
- §6.1 模块级结构 → Task 4 ✓
- §6.2 改造要点（删/加/改/业务零改动）→ Task 4 ✓
- §6.3 顶栏返回钮复用模板 → Task 4 _build_top_bar ✓
- §6.4 左右分栏布局不变 → Task 4（常量 + 布局照搬）✓
- §6.5 _active_item 重置 → Task 1/4（run 入口重置）✓
- §7.1 host AST 测试 8 维度 → Task 1（2个）+ Task 2（6个）= 8 ✓
- §7.2 板端验收 5 项 → Task 5 Step 3-7 ✓
- §7.3 回归 → Task 1/4 Step 5 ✓

**2. 占位符扫描:** Task 3 是核对清单（无代码改动，明确说明），非占位。Task 4 给了完整 app.py 全文（无 .../TODO）。无占位符。

**3. 类型/命名一致性:**
- `run(runtime)` — Task 1 定义，Task 4 一致 ✓
- `exit_flag` / `exit_flag[0]` — Task 1/4 一致 ✓
- `_build_ui(runtime, exit_flag)` / `_build_top_bar(runtime, exit_flag)` / `_destroy_ui()` — Task 4 一致 ✓
- `_RUNTIME` / `_ctx_runtime()` — Task 4 定义且 run() 缓存，回调取用一致 ✓
- `runtime.lang` / `runtime.config` — Task 4 全文一致 ✓
- `_active_item` 模块级 + run 入口重置 — Task 1/4 一致 ✓
- 8 个 AST 测试名 — Task 1（2个）+ Task 2（6个）覆盖 spec §7.1 全部 8 维度 ✓

**注（设计偏离修正）**：spec §6.1 伪代码未提 `_RUNTIME`/`_ctx_runtime()`——这是实施时发现的细节：`_select_item`/`_set_lang` 在 LVGL 回调中触发，拿不到 run() 的 runtime 参数，需模块级缓存。计划 Task 4 已纳入此机制。这是 spec 伪代码省略的实现细节，非设计冲突。

无问题。
