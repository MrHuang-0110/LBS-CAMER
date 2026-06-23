# camera APP 框架迁移实施计划 (BaseScript → run(runtime))

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 camera APP 从 `CameraApp(BaseScript)` 类迁移到 reset 框架的模块级 `run(runtime)` 函数式,使其能从主菜单启动/退出;业务(拍照/录像/图库)原样保留,删缩略图死代码。

**Architecture:** 模块级 `run(runtime)` 单线程主循环(snapshot→show_image→task_handler 串行,一个写者)+ 模块级状态替代 `self._xxx` + `_RUNTIME` 缓存供 LVGL 回调取用 + `_destroy_ui()` 只删 LVGL 对象(硬件由 main.py `runtime.cleanup()` 释放)。传感器从旧架构的共享常驻 `ctx.lcd.sensor` 改为每进程独立 `runtime.sensor`(init_app 已配 chn0 预览 + chn1 拍照)。对齐 settings/_template 已确立的范式。

**Tech Stack:** MicroPython / K230 CanMV / lvgl / media (Display/Sensor/MediaManager) / host 端 ast.parse AST 测试

**设计文档:** [docs/superpowers/specs/2026-06-23-camera-refactor-design.md](../specs/2026-06-23-camera-refactor-design.md)

---

## 文件结构

| 文件 | 责任 | 操作 |
|------|------|------|
| `scripts/camera/app.py` | camera 全部逻辑:run 入口+主循环+顶底栏+拍照/录像状态机+图库 | 整体重写(CameraApp 类 → 模块级函数式) |
| `tests/test_camera.py` | 迁移契约测试(对齐 test_settings) | 新增 |
| `tests/test_camera_gallery.py` | 图库回归测试 | 重写(删缩略图测试,类断言改模块级) |

**不改动**:`core/app_runtime.py`(init_app 已支持 camera chn 配置,见 `_channels_for`)、`config/categories.json`(camera category 已存在,ui_mode=stream)、`main.py`(run_script 通用路径已支持)。

**测试运行约定**:host 端 Windows 无法导入 K230 板端模块(lvgl/media/image),全部用 `ast.parse` + 字符串/AST 节点断言。运行命令统一用 `python tests/test_xxx.py`(项目无 pytest 依赖,测试文件自带 `test_runner` + `__main__`)。

**关键参考文件**(实现时对照):
- [scripts/settings/app.py](../../scripts/settings/app.py) — run(runtime) 范式模板(模块级状态、`_ctx_runtime()`、`_destroy_ui`)
- [scripts/_template/app.py](../../scripts/_template/app.py) — 单线程主循环 + 顶栏返回钮 + 透明预览
- [scripts/camera/app.py](../../scripts/camera/app.py) 当前版本 — 业务逻辑来源(拍照/录像/图库),迁移时逐函数平移

---

### Task 1: 新建 test_camera.py 骨架(run 入口 + exit_flag 主循环契约)

**Files:**
- Create: `tests/test_camera.py`

- [ ] **Step 1: 写失败的测试文件**

创建 `tests/test_camera.py`,内容:

```python
# tests/test_camera.py — host-side AST tests for camera run(runtime) refactor.
# Run with:
#   python tests/test_camera.py
import ast
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP_PATH = os.path.join(ROOT, "scripts", "camera", "app.py")


def _parse(path):
    with open(path, "r", encoding="utf-8") as f:
        return ast.parse(f.read(), filename=path)


def _function_node(tree, name):
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError("Function %s missing" % name)


def test_camera_has_run_entry():
    """camera 必须有 run(runtime) 入口(reset 框架要求)。"""
    tree = _parse(APP_PATH)
    run_fn = _function_node(tree, "run")
    arg_names = [a.arg for a in run_fn.args.args]
    assert "runtime" in arg_names, "run(runtime) entry is required by reset framework"


def test_camera_run_uses_exit_flag_loop():
    """run() 主循环必须用 exit_flag 检测退出 + task_handler。"""
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

Run: `python tests/test_camera.py`
Expected: FAIL — `run` function missing(camera 当前是 CameraApp 类,无模块级 run)。两个测试都因找不到 `run` 函数失败。

- [ ] **Step 3: 实现最小代码让测试通过**

在 `scripts/camera/app.py` **顶部**(import 之后、CameraApp 类之前)新增最小 run 骨架(此时不改 CameraApp 类,仅加入口让 main.py 能调到):

```python
# ── reset 框架入口（迁移中：CameraApp 类业务逐步平移到下方函数）──
_RUNTIME = None


def run(runtime):
    """camera 主入口(reset 框架调 mod.run(runtime))。

    单线程主循环:chn0 预览帧 → OSD1 + 状态业务(录像计时/白闪) + task_handler。
    触摸返回钮设 exit_flag → 循环退出 → _destroy_ui → main.py cleanup+reset 回菜单。
    """
    global _RUNTIME
    _RUNTIME = runtime
    exit_flag = [False]
    _build_ui(runtime, exit_flag)
    while not exit_flag[0]:
        os.exitpoint()
        time.sleep_ms(lv.task_handler())
    _destroy_ui()


def _build_ui(runtime, exit_flag):
    pass


def _destroy_ui():
    pass
```

同时在文件顶部 import 区补 `import time`(若尚无)。

> 注意:此时 `run` 只是空壳骨架,真正的 UI/主循环业务在后续 Task 平移。本步只让 run 入口存在 + exit_flag 循环结构就位,使 Task 1 的两个契约测试通过。

- [ ] **Step 4: 运行确认通过**

Run: `python tests/test_camera.py`
Expected: PASS(2 tests)

- [ ] **Step 5: 提交**

```bash
git add tests/test_camera.py scripts/camera/app.py
git commit -m "feat(camera): add run(runtime) entry skeleton + contract tests

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 2: 去旧架构 + ctx→runtime + 用 runtime.sensor 契约测试(Red)

**Files:**
- Test: `tests/test_camera.py`

- [ ] **Step 1: 在 test_camera.py 追加失败测试**

在 `test_camera_run_uses_exit_flag_loop` 之后、`test_runner` 之前追加:

```python
def test_camera_no_basescript():
    """改造后不得残留旧 BaseScript 架构。"""
    src = open(APP_PATH, encoding="utf-8").read()
    for token in ("BaseScript", "on_enter", "on_exit", "SCRIPT_ID",
                  "SELF_MANAGED_TOP_BAR", "class CameraApp"):
        assert token not in src, "old architecture token must be removed: %s" % token


def test_camera_no_ctx_lcd():
    """不得依赖旧架构的 ctx.lcd 共享 sensor。"""
    src = open(APP_PATH, encoding="utf-8").read()
    for token in ("ctx.lcd", "get_sensor", "ensure_sensor_running",
                  "clear_framebuffers", "capture_chn"):
        assert token not in src, "old ctx.lcd shared-sensor token must be removed: %s" % token


def test_camera_uses_runtime_sensor():
    """预览必须用 runtime.sensor.snapshot(chn=CAM_CHN_ID_0) 推 OSD1。"""
    src = open(APP_PATH, encoding="utf-8").read()
    assert "runtime.sensor.snapshot" in src, "must use runtime.sensor.snapshot for preview"
    assert "CAM_CHN_ID_0" in src, "preview must use chn0"
    assert "LAYER_OSD1" in src, "preview must push to OSD1"


def test_camera_no_self_ctx():
    """不得残留 self.ctx(self._xxx 改为模块级状态)。"""
    src = open(APP_PATH, encoding="utf-8").read()
    assert "self.ctx" not in src, "self.ctx must be removed (module-level state)"
    assert "self.ctx" not in src


def test_camera_uses_runtime_lang():
    """语言取自 runtime.lang(非 ctx.lang)。"""
    src = open(APP_PATH, encoding="utf-8").read()
    assert "runtime.lang" in src, "must use runtime.lang"
```

- [ ] **Step 2: 运行确认失败**

Run: `python tests/test_camera.py`
Expected: FAIL — Task 1 的 2 个测试 PASS,新增 5 个 FAIL(CameraApp 类仍在、ctx.lcd 残留、无 runtime.sensor.snapshot 等)。

- [ ] **Step 3: 此 Task 不实现,留 Red 给 Task 3 实现**

本 Task 只写测试(Red)。实现由 Task 3 整体重写 app.py 完成。不提交(Red 状态),直接进 Task 3。

> 说明:camera 迁移是"整体重写"型改造(1136 行类 → 模块级函数式),逐行 patch 不现实。Task 3 一次性把 CameraApp 类业务平移到模块级函数并删旧架构,使 Task 2 全部测试转 Green。因此 Task 2 与 Task 3 合并提交。

---

### Task 3: 整体重写 app.py 为 run(runtime) 函数式(Green)

**Files:**
- Modify: `scripts/camera/app.py`(整体重写)

- [ ] **Step 1: 用模块级函数式整体重写 app.py**

将 `scripts/camera/app.py` **整体替换**为以下内容(逐函数从旧 CameraApp 平移:self._xxx → 模块级变量;ctx.* → runtime/*;删 _init_camera/_stop_camera/_load_thumbnail/_fit_thumb_size/_bmp_dimensions 及缩略图相关):

```python
# scripts/camera/app.py — 相机 APP(拍照/录像 + 图库)run(runtime) 范式
#
# 架构(reset 框架,对齐 settings/_template):
#   OSD1 层:相机帧(runtime.sensor.snapshot(chn0) → Display.show_image(LAYER_OSD1))
#   OSD2 层:LVGL UI(flush 回调显式 show_image(LAYER_OSD2))
#   LVGL 预览区 bg_opa=0 透明 → 透出下层 OSD1 相机画面
#
# 单线程主循环(snapshot→状态业务→task_handler 串行,一个写者),从结构上
# 消除双线程双写者 display DMA 竞争。
#
# 传感器:每进程独立 runtime.sensor(init_app 已配 chn0 VGA/RGB888 预览 +
# chn1 SXGAM/RGB565 拍照,并由 init_app 启动取流)。退出由 main.py runtime.cleanup()
# 统一 stop + deinit,不再用旧架构的共享常驻 lcd sensor。
#
# 状态机:PHOTO ←→ VIDEO → RECORDING,任意待机态 → GALLERY

import os
import time
import struct
import lvgl as lv
from media.display import Display
from media.sensor import CAM_CHN_ID_0, CAM_CHN_ID_1
from core.icon_cache import icon_cache
from core.font_manager import fonts
from ui.theme import Colors, make_back_bar_text_style


# ── 布局常量 ──────────────────────────────────────
BAR_H = 52              # 顶栏/底栏高度
PREVIEW_Y = BAR_H       # 预览区起始 Y
PREVIEW_H = 376         # 480 - BAR_H * 2
BTN_SIZE = 48           # 栏上按钮点击区
ICON_TARGET = 40        # 栏上图标目标尺寸
SHUTTER_OUTER = 44      # 快门外径(含圆环)
BAR_BG = 0x1A1A1A       # 栏背景色

# ── 图库常量 ──
GAL_ROW_H = 100          # 照片行高度
GAL_ROW_BG = 0x1A1A1A    # 照片行卡片背景
GAL_DATE_H = 28          # 日期分组标题高度
GAL_DATE_BG = 0x111111   # 日期分组标题背景
GAL_DELETE_SIZE = 36     # 删除按钮尺寸
GAL_ROW_GAP = 6          # 行间距
GAL_ROW_RADIUS = 8       # 照片行圆角

# 颜色
RED = 0xCC4444
GREEN = 0x44CC44
WHITE = 0xFFFFFF

# 状态
STATE_PHOTO = 0
STATE_VIDEO = 1
STATE_RECORDING = 2
STATE_GALLERY = 3

# ── 模块级状态(替代旧类 self._xxx)──
_RUNTIME = None
_state = STATE_PHOTO
_screen = None
_top_bar = None
_bottom_bar = None
_preview_bg = None
_timer_label = None
_shutter_btn = None
_mode_green_dot = None
_title_label = None

# 录像相关
_record_start_ticks = 0
_timer_blink = True
_record_path = ""

# 拍照白闪反馈(主循环轮询删除,K230 无 lv.timer)
_flash_obj = None
_flash_start = 0

# 图库相关
_gallery_list = None
_gallery_objects = []
_gallery_groups = []


def _ctx_runtime():
    """返回当前 run() 的 runtime(入口缓存到模块级)。

    LVGL 回调(_on_shutter/_on_mode_toggle 等)拿不到 run() 的 runtime 参数,
    通过本函数取模块级缓存的 _RUNTIME。
    """
    return _RUNTIME


def _png_zoom(png_data, target):
    """从 PNG 头解析真实尺寸,计算缩放因子。"""
    if not png_data or len(png_data) < 24:
        return 256
    w = struct.unpack('>I', png_data[16:20])[0]
    h = struct.unpack('>I', png_data[20:24])[0]
    if w <= 0 or h <= 0:
        return 256
    zoom = int(min(target / w, target / h) * 256)
    return max(8, min(zoom, 256))


def _make_icon(parent, icon_data, icon_dsc, target_size, x):
    """在 parent 上创建图标,返回 img_obj。"""
    if icon_dsc is None or icon_data is None:
        return None

    img = lv.img(parent)
    img.set_src(icon_dsc)
    zoom = _png_zoom(icon_data, target_size)
    img.set_zoom(zoom)

    src_w = struct.unpack('>I', icon_data[16:20])[0]
    rendered_w = src_w * zoom // 256
    actual_x = x - (src_w - rendered_w) // 2
    img.align(lv.ALIGN.LEFT_MID, actual_x, 0)
    return img


# ── 主入口 ──────────────────────────────────────

def run(runtime):
    """camera 主入口(reset 框架调 mod.run(runtime))。

    单线程主循环:chn0 预览帧 → OSD1 + 状态业务(录像计时/白闪) + task_handler。
    触摸返回钮设 exit_flag → 循环退出 → _destroy_ui → main.py cleanup+reset 回菜单。
    """
    global _RUNTIME, _state
    _RUNTIME = runtime
    _state = STATE_PHOTO
    exit_flag = [False]
    _build_ui(runtime, exit_flag)
    while not exit_flag[0]:
        os.exitpoint()
        # 推送相机帧到 OSD1 层(图库态不推)
        if _state != STATE_GALLERY:
            try:
                img = runtime.sensor.snapshot(chn=CAM_CHN_ID_0)
                Display.show_image(img, 0, 0, Display.LAYER_OSD1)
            except Exception:
                pass  # 偶发 snapshot 失败不刷屏
        # 录像计时器更新
        if _state == STATE_RECORDING:
            _update_timer()
        # 拍照白闪清理(120ms 后删)
        _update_flash()
        time.sleep_ms(lv.task_handler())
    _destroy_ui()


# ── UI 构建 ──────────────────────────────────

def _build_ui(runtime, exit_flag):
    """构建顶栏(返回钮+标题) + 透明预览区 + 底栏(图库/快门/模式)。"""
    global _screen
    screen = lv.scr_act()
    # 屏幕背景透明:OSD2 透明处透出下层 OSD1 相机画面
    screen.set_style_bg_opa(0, 0)
    _screen = screen

    _build_top_bar(runtime, exit_flag)
    _build_preview_area()
    _build_bottom_bar(runtime)


def _build_top_bar(runtime, exit_flag):
    """顶栏:返回钮(左) + 标题(居中)。"""
    global _top_bar, _title_label
    lang = runtime.lang

    bar = lv.obj(_screen)
    bar.set_size(lv.pct(100), BAR_H)
    bar.set_pos(0, 0)
    bar.set_style_bg_color(lv.color_hex(BAR_BG), 0)
    bar.set_style_bg_opa(255, 0)
    bar.set_style_border_width(0, 0)
    bar.set_style_pad_all(0, 0)
    bar.set_style_radius(0, 0)
    bar.clear_flag(lv.obj.FLAG.SCROLLABLE)
    _top_bar = bar

    # 返回钮(48×48 透明点击区 + back 图标)
    btn = lv.obj(bar)
    btn.set_size(BTN_SIZE, BTN_SIZE)
    btn.align(lv.ALIGN.LEFT_MID, 2, 0)
    btn.set_style_bg_opa(0, 0)
    btn.set_style_border_width(0, 0)
    btn.set_style_shadow_width(0, 0)
    btn.set_style_outline_width(0, 0)
    btn.set_style_outline_opa(0, 0)
    btn.set_style_pad_all(0, 0)
    btn.clear_flag(lv.obj.FLAG.SCROLLABLE)
    btn.add_flag(lv.obj.FLAG.CLICKABLE)

    icon_data, icon_dsc = icon_cache.get_camera_icon("back")
    if icon_dsc is not None and icon_data is not None:
        _make_icon(btn, icon_data, icon_dsc, ICON_TARGET, 4)
    else:
        lbl = lv.label(btn)
        lbl.set_text("<")
        lbl.center()

    def _on_back(e):
        if e.get_code() == lv.EVENT.CLICKED:
            if _state == STATE_GALLERY:
                _leave_gallery()
            else:
                exit_flag[0] = True
    btn.add_event(_on_back, lv.EVENT.CLICKED, None)

    # 标题居中
    title = lv.label(bar)
    title.set_text(lang.t("category.camera"))
    title.align(lv.ALIGN.CENTER, 0, 0)
    title.add_style(make_back_bar_text_style(fonts.body), 0)
    _title_label = title


def _build_preview_area():
    """预览区:全透明 LVGL 对象,让底层 OSD1 相机画面透出。"""
    global _preview_bg
    preview = lv.obj(_screen)
    preview.set_size(lv.pct(100), PREVIEW_H)
    preview.set_pos(0, PREVIEW_Y)
    preview.set_style_bg_opa(0, 0)  # 透明!透出下层 OSD1
    preview.set_style_border_width(0, 0)
    preview.set_style_pad_all(0, 0)
    preview.set_style_radius(0, 0)
    preview.clear_flag(lv.obj.FLAG.SCROLLABLE)
    preview.clear_flag(lv.obj.FLAG.CLICKABLE)
    _preview_bg = preview


def _build_bottom_bar(runtime):
    """底栏:图库(左) + 快门(中) + 模式(右)。"""
    global _bottom_bar, _shutter_btn, _timer_label, _mode_green_dot

    bar = lv.obj(_screen)
    bar.set_size(lv.pct(100), BAR_H)
    bar.set_pos(0, PREVIEW_Y + PREVIEW_H)
    bar.set_style_bg_color(lv.color_hex(BAR_BG), 0)
    bar.set_style_bg_opa(255, 0)
    bar.set_style_border_width(0, 0)
    bar.set_style_pad_all(0, 0)
    bar.set_style_radius(0, 0)
    bar.clear_flag(lv.obj.FLAG.SCROLLABLE)
    _bottom_bar = bar

    # ── 图库按钮(左)──
    gallery_btn = lv.obj(bar)
    gallery_btn.set_size(BTN_SIZE, BTN_SIZE)
    gallery_btn.align(lv.ALIGN.LEFT_MID, 24, 0)
    gallery_btn.set_style_bg_opa(0, 0)
    gallery_btn.set_style_border_width(0, 0)
    gallery_btn.set_style_shadow_width(0, 0)
    gallery_btn.set_style_outline_width(0, 0)
    gallery_btn.set_style_outline_opa(0, 0)
    gallery_btn.set_style_pad_all(0, 0)
    gallery_btn.clear_flag(lv.obj.FLAG.SCROLLABLE)
    gallery_btn.add_flag(lv.obj.FLAG.CLICKABLE)

    icon_data, icon_dsc = icon_cache.get_camera_icon("gallery")
    if icon_dsc is not None and icon_data is not None:
        _make_icon(gallery_btn, icon_data, icon_dsc, ICON_TARGET, 4)

    gallery_btn.add_event(_on_gallery, lv.EVENT.CLICKED, None)

    # ── 快门按钮(中)──
    shutter_btn = lv.obj(bar)
    shutter_btn.set_size(SHUTTER_OUTER, SHUTTER_OUTER)
    shutter_btn.align(lv.ALIGN.CENTER, 0, 0)
    shutter_btn.set_style_bg_opa(0, 0)
    shutter_btn.set_style_border_width(3, 0)
    shutter_btn.set_style_border_color(lv.color_hex(WHITE), 0)
    shutter_btn.set_style_border_opa(255, 0)
    shutter_btn.set_style_radius(lv.pct(50), 0)
    shutter_btn.set_style_pad_all(0, 0)
    shutter_btn.clear_flag(lv.obj.FLAG.SCROLLABLE)
    shutter_btn.add_flag(lv.obj.FLAG.CLICKABLE)
    shutter_btn.add_event(_on_shutter, lv.EVENT.CLICKED, None)
    _shutter_btn = shutter_btn

    # ── 录制计时器(快门右侧,初始隐藏)──
    timer = lv.label(bar)
    timer.set_text("")
    timer.align_to(shutter_btn, lv.ALIGN.OUT_RIGHT_MID, 16, 0)
    timer.add_style(make_back_bar_text_style(fonts.body), 0)
    timer.set_style_text_color(lv.color_hex(RED), 0)
    timer.set_style_text_opa(255, 0)
    timer.set_style_bg_opa(0, 0)
    timer.set_style_border_width(0, 0)
    timer.set_style_pad_all(0, 0)
    try:
        timer.set_style_shadow_width(0, 0)
        timer.set_style_shadow_opa(0, 0)
    except Exception:
        pass
    try:
        timer.set_style_text_outline_width(0, 0)
        timer.set_style_text_outline_opa(0, 0)
    except Exception:
        pass
    timer.add_flag(lv.obj.FLAG.HIDDEN)
    _timer_label = timer

    # ── 模式按钮(右)──
    mode_btn = lv.obj(bar)
    mode_btn.set_size(BTN_SIZE, BTN_SIZE)
    mode_btn.align(lv.ALIGN.RIGHT_MID, -24, 0)
    mode_btn.set_style_bg_opa(0, 0)
    mode_btn.set_style_border_width(0, 0)
    mode_btn.set_style_shadow_width(0, 0)
    mode_btn.set_style_outline_width(0, 0)
    mode_btn.set_style_outline_opa(0, 0)
    mode_btn.set_style_pad_all(0, 0)
    mode_btn.clear_flag(lv.obj.FLAG.SCROLLABLE)
    mode_btn.add_flag(lv.obj.FLAG.CLICKABLE)

    icon_data, icon_dsc = icon_cache.get_camera_icon("mode")
    if icon_dsc is not None and icon_dsc is not None:
        _make_icon(mode_btn, icon_data, icon_dsc, ICON_TARGET, 4)

    # 模式指示绿点(录像模式,K230 LVGL v8 img_recolor 替代方案)
    dot = lv.obj(bar)
    dot.set_size(8, 8)
    dot.align(lv.ALIGN.RIGHT_MID, -18, 16)
    dot.set_style_bg_color(lv.color_hex(GREEN), 0)
    dot.set_style_bg_opa(0, 0)  # 初始隐藏
    dot.set_style_border_width(0, 0)
    dot.set_style_radius(lv.pct(50), 0)
    dot.clear_flag(lv.obj.FLAG.SCROLLABLE)
    dot.clear_flag(lv.obj.FLAG.CLICKABLE)
    _mode_green_dot = dot

    mode_btn.add_event(_on_mode_toggle, lv.EVENT.CLICKED, None)

    _refresh_shutter()
    _refresh_mode_icon()


# ── 模式切换 ──────────────────────────────────

def _on_mode_toggle(e):
    """切换拍照 ↔ 录像(仅待机状态)。"""
    global _state
    if e.get_code() != lv.EVENT.CLICKED:
        return
    if _state == STATE_PHOTO:
        _state = STATE_VIDEO
    elif _state == STATE_VIDEO:
        _state = STATE_PHOTO
    else:
        return  # 录像中或图库中不响应
    _refresh_shutter()
    _refresh_mode_icon()


def _refresh_shutter():
    """根据当前状态更新快门外观。"""
    if _shutter_btn is None:
        return
    btn = _shutter_btn
    if _state == STATE_PHOTO:
        btn.set_style_bg_opa(0, 0)
        btn.set_style_border_color(lv.color_hex(WHITE), 0)
        btn.set_style_border_width(3, 0)
        btn.set_style_radius(lv.pct(50), 0)
    elif _state == STATE_VIDEO:
        btn.set_style_bg_color(lv.color_hex(RED), 0)
        btn.set_style_bg_opa(255, 0)
        btn.set_style_border_color(lv.color_hex(WHITE), 0)
        btn.set_style_border_width(3, 0)
        btn.set_style_radius(lv.pct(50), 0)
    elif _state == STATE_RECORDING:
        btn.set_style_bg_color(lv.color_hex(RED), 0)
        btn.set_style_bg_opa(255, 0)
        btn.set_style_border_width(0, 0)
        btn.set_style_radius(4, 0)


def _refresh_mode_icon():
    """根据状态更新模式图标指示(绿点)。"""
    if _mode_green_dot is None:
        return
    is_video = (_state in (STATE_VIDEO, STATE_RECORDING))
    _mode_green_dot.set_style_bg_opa(255 if is_video else 0, 0)


# ── 快门 ──────────────────────────────────────

def _on_shutter(e):
    """快门按钮:拍照 / 开始录像 / 停止录像。"""
    if e.get_code() != lv.EVENT.CLICKED:
        return
    runtime = _ctx_runtime()
    if _state == STATE_PHOTO:
        _capture_photo()
        runtime.buzzer.beep(ms=30)
    elif _state == STATE_VIDEO:
        _start_recording()
        runtime.buzzer.beep(ms=50)
    elif _state == STATE_RECORDING:
        _stop_recording()
        runtime.buzzer.beep(ms=80)


# ── 拍照 ──────────────────────────────────────

def _capture_photo():
    """拍照并保存到 /data/photo/(JPG,chn1 通道)。"""
    global _flash_obj, _flash_start
    runtime = _ctx_runtime()

    photo_dir = "/data/photo/"
    try:
        os.mkdir(photo_dir)
    except Exception:
        pass

    t = time.localtime()
    fname = "IMG_%04d%02d%02d_%02d%02d%02d.jpg" % (
        t[0], t[1], t[2], t[3], t[4], t[5])
    path = photo_dir + fname

    try:
        # chn1 = SXGAM/RGB565(支持 jpg save)。首帧偶发未就绪,短重试几次。
        img = None
        last_err = None
        for _attempt in range(5):
            try:
                img = runtime.sensor.snapshot(chn=CAM_CHN_ID_1)
                break
            except Exception as se:
                last_err = se
                time.sleep_ms(30)
        if img is None:
            raise last_err if last_err else Exception("snapshot returned None")
        img.save(path)
        print("[Camera] photo saved: %s" % path)
        _flash_feedback()
    except Exception as e:
        print("[Camera] capture failed: %s" % e)


def _flash_feedback():
    """拍照白闪反馈 — 创建半透明白层,由主循环 _update_flash 在 ~120ms 后删除。"""
    global _flash_obj, _flash_start
    if _preview_bg is None:
        return
    flash = lv.obj(_preview_bg)
    flash.set_size(lv.pct(100), lv.pct(100))
    flash.set_pos(0, 0)
    flash.set_style_bg_color(lv.color_hex(WHITE), 0)
    flash.set_style_bg_opa(160, 0)
    flash.set_style_border_width(0, 0)
    flash.set_style_radius(0, 0)
    flash.clear_flag(lv.obj.FLAG.SCROLLABLE)
    flash.clear_flag(lv.obj.FLAG.CLICKABLE)
    _flash_obj = flash
    _flash_start = time.ticks_ms()


def _update_flash():
    """主循环每帧调:白闪 120ms 后删除。"""
    global _flash_obj
    if _flash_obj is None:
        return
    if time.ticks_diff(time.ticks_ms(), _flash_start) >= 120:
        try:
            _flash_obj.delete()
        except Exception:
            pass
        _flash_obj = None


# ── 录像(空壳:状态 + 计时器,无实际编码)──

def _start_recording():
    """开始录像(空壳:仅状态 + 计时器)。"""
    global _state, _record_start_ticks, _record_path
    photo_dir = "/data/photo/"
    try:
        os.mkdir(photo_dir)
    except Exception:
        pass

    t = time.localtime()
    fname = "VID_%04d%02d%02d_%02d%02d%02d.avi" % (
        t[0], t[1], t[2], t[3], t[4], t[5])
    _record_path = photo_dir + fname
    _record_start_ticks = time.ticks_ms()

    _state = STATE_RECORDING
    _refresh_shutter()
    _show_timer(True)
    print("[Camera] recording started: %s" % _record_path)


def _stop_recording():
    """停止录像(空壳)。"""
    global _state
    _state = STATE_VIDEO
    _refresh_shutter()
    _show_timer(False)
    print("[Camera] recording stopped: %s" % _record_path)


def _show_timer(visible):
    """显示/隐藏录制计时器。"""
    if _timer_label is None:
        return
    if visible:
        _timer_label.clear_flag(lv.obj.FLAG.HIDDEN)
        _timer_label.set_text("● 00:00:00")
    else:
        _timer_label.add_flag(lv.obj.FLAG.HIDDEN)
        _timer_label.set_text("")


def _update_timer():
    """每帧调用:更新录制时间 + 红点闪烁。"""
    global _timer_blink
    if _timer_label is None or _state != STATE_RECORDING:
        return

    elapsed = time.ticks_diff(time.ticks_ms(), _record_start_ticks) // 1000
    h = elapsed // 3600
    m = (elapsed % 3600) // 60
    s = elapsed % 60

    _timer_blink = (elapsed % 2 == 0)
    dot = "●" if _timer_blink else "○"
    _timer_label.set_text("%s %02d:%02d:%02d" % (dot, h, m, s))


# ── 图库 ──────────────────────────────────────

def _on_gallery(e):
    """图库按钮(仅待机状态可用)。"""
    if e.get_code() != lv.EVENT.CLICKED:
        return
    if _state in (STATE_RECORDING, STATE_GALLERY):
        return
    _enter_gallery()


def _group_photos_by_date(photo_dir):
    """扫描照片目录,按日期分组。无缩略图(只显示文件名+日期+删除)。

    Returns: list[dict] 每组 {date_key, label, photos: [{path,fname,mtime}]}
    """
    files = []
    try:
        for f in os.listdir(photo_dir):
            low = f.lower()
            if low.endswith('.thumb.bmp'):
                continue
            if low.endswith('.avi'):
                continue  # 录像为空壳,不展示
            if low.endswith('.jpg') or low.endswith('.bmp'):
                full_path = photo_dir + f
                try:
                    st = os.stat(full_path)
                    files.append((f, full_path, st[8]))  # (name, path, mtime)
                except Exception:
                    files.append((f, full_path, 0))
    except Exception as e:
        print("[Gallery] listdir failed: %s" % e)
        return []

    if not files:
        return []

    # 按 mtime 倒序 → 按日期分组
    files.sort(key=lambda x: x[2], reverse=True)

    groups_dict = {}
    for fname, fpath, mtime in files:
        if mtime > 0:
            t = time.localtime(mtime)
            date_key = "%04d-%02d-%02d" % (t[0], t[1], t[2])
            date_label = "%d年%d月%d日" % (t[0], t[1], t[2])
        else:
            date_key = "unknown"
            date_label = "未知日期"

        if date_key not in groups_dict:
            groups_dict[date_key] = {
                'date_key': date_key,
                'label': date_label,
                'photos': [],
            }
        groups_dict[date_key]['photos'].append({
            'fname': fname,
            'path': fpath,
            'mtime': mtime,
        })

    groups = list(groups_dict.values())
    groups.sort(key=lambda g: g['date_key'], reverse=True)
    return groups


def _enter_gallery():
    """进入图库页面 — 扫描 + 分组 + 构建 UI(无缩略图)。"""
    global _state, _gallery_objects, _gallery_groups

    _state = STATE_GALLERY

    # 隐藏相机 UI
    if _bottom_bar is not None:
        _bottom_bar.add_flag(lv.obj.FLAG.HIDDEN)
    if _preview_bg is not None:
        _preview_bg.add_flag(lv.obj.FLAG.HIDDEN)
    if _timer_label is not None:
        _timer_label.add_flag(lv.obj.FLAG.HIDDEN)

    # 图库页需要不透明屏幕背景(相机预览时 bg_opa=0 透出 OSD1)
    if _screen is not None:
        _screen.set_style_bg_color(lv.color_hex(Colors.BG), 0)
        _screen.set_style_bg_opa(255, 0)

    # 更新标题
    if _title_label is not None:
        _title_label.set_text(_ctx_runtime().lang.t("camera.gallery"))

    # 扫描 + 分组(无缩略图 I/O)
    _gallery_objects = []
    _gallery_groups = []

    photo_dir = "/data/photo/"
    try:
        os.mkdir(photo_dir)
    except Exception:
        pass

    groups = _group_photos_by_date(photo_dir)
    _gallery_groups = groups

    _build_gallery_ui(groups)
    print("[Gallery] enter done: %d groups" % len(groups))


def _make_date_header(parent, y, text):
    """创建日期分组标题 bar — 深色背景 + 居中灰色文字。"""
    bar = lv.obj(parent)
    bar.set_size(lv.pct(100), GAL_DATE_H)
    bar.set_pos(0, y)
    bar.set_style_bg_color(lv.color_hex(GAL_DATE_BG), 0)
    bar.set_style_bg_opa(255, 0)
    bar.set_style_border_width(0, 0)
    bar.set_style_pad_all(0, 0)
    bar.set_style_radius(0, 0)
    bar.clear_flag(lv.obj.FLAG.SCROLLABLE)
    bar.clear_flag(lv.obj.FLAG.CLICKABLE)
    _gallery_objects.append(bar)

    label = lv.label(bar)
    label.set_text(text)
    label.align(lv.ALIGN.CENTER, 0, 0)
    label.add_style(make_back_bar_text_style(fonts.body), 0)
    label.set_style_text_color(lv.color_hex(Colors.TEXT_DIM), 0)
    _gallery_objects.append(label)
    return bar


def _make_photo_row(parent, y, photo):
    """创建照片行 — 文件名/日期 + 删除按钮(不显示缩略图)。"""
    row = lv.obj(parent)
    row.set_size(lv.pct(100), GAL_ROW_H)
    row.set_pos(0, y)
    row.set_style_bg_color(lv.color_hex(GAL_ROW_BG), 0)
    row.set_style_bg_opa(255, 0)
    row.set_style_border_width(0, 0)
    row.set_style_radius(GAL_ROW_RADIUS, 0)
    row.set_style_pad_all(4, 0)
    row.clear_flag(lv.obj.FLAG.SCROLLABLE)
    _gallery_objects.append(row)

    # ── 文件名(左侧)──
    name_lbl = lv.label(row)
    fname = photo['fname']
    if len(fname) > 30:
        fname = fname[:28] + ".."
    name_lbl.set_text(fname)
    name_x = 16
    name_lbl.align(lv.ALIGN.LEFT_MID, name_x, -12)
    name_lbl.set_style_text_color(lv.color_hex(WHITE), 0)
    name_lbl.add_style(make_back_bar_text_style(fonts.body), 0)
    _gallery_objects.append(name_lbl)

    # ── 日期时间(文件名下方)──
    date_lbl = lv.label(row)
    mtime = photo['mtime']
    if mtime > 0:
        t = time.localtime(mtime)
        date_str = "%04d-%02d-%02d %02d:%02d" % (t[0], t[1], t[2], t[3], t[4])
    else:
        date_str = "?"
    date_lbl.set_text(date_str)
    date_lbl.align(lv.ALIGN.LEFT_MID, name_x, 12)
    date_lbl.set_style_text_color(lv.color_hex(Colors.TEXT_DIM), 0)
    date_lbl.add_style(make_back_bar_text_style(fonts.body), 0)
    _gallery_objects.append(date_lbl)

    # ── 删除按钮(右侧)──
    del_btn = lv.obj(row)
    del_btn.set_size(GAL_DELETE_SIZE, GAL_DELETE_SIZE)
    del_btn.align(lv.ALIGN.RIGHT_MID, -12, 0)
    del_btn.set_style_bg_opa(0, 0)
    del_btn.set_style_border_width(0, 0)
    del_btn.set_style_shadow_width(0, 0)
    del_btn.set_style_outline_width(0, 0)
    del_btn.set_style_outline_opa(0, 0)
    del_btn.set_style_pad_all(0, 0)
    del_btn.clear_flag(lv.obj.FLAG.SCROLLABLE)
    del_btn.add_flag(lv.obj.FLAG.CLICKABLE)

    x_lbl = lv.label(del_btn)
    x_lbl.set_text("×")
    x_lbl.center()
    x_lbl.set_style_text_color(lv.color_hex(0xCC4444), 0)
    x_lbl.add_style(make_back_bar_text_style(fonts.body), 0)
    _gallery_objects.extend([del_btn, x_lbl])

    # 闭包捕获 photo 和 row
    del_btn.add_event(
        lambda e, p=photo, r=row: (
            _on_delete_photo(p, r) if e.get_code() == lv.EVENT.CLICKED else None
        ),
        lv.EVENT.CLICKED, None)

    return row


def _build_gallery_ui(groups):
    """构建图库纵向滚动列表 — 日期分组标题 + 照片行。"""
    global _gallery_list
    screen = _screen
    list_h = screen.get_height() - BAR_H

    lst = lv.obj(screen)
    lst.set_size(lv.pct(100), list_h)
    lst.set_pos(0, BAR_H)
    lst.set_style_bg_color(lv.color_hex(Colors.BG), 0)
    lst.set_style_bg_opa(255, 0)
    lst.set_style_border_width(0, 0)
    lst.set_style_pad_all(8, 0)
    lst.set_style_radius(0, 0)
    lst.set_scroll_dir(lv.DIR.VER)
    _gallery_list = lst

    if not groups:
        lang = _ctx_runtime().lang
        empty = lv.label(lst)
        empty.set_text(lang.t("camera.no_photos"))
        empty.align(lv.ALIGN.CENTER, 0, 0)
        empty.add_style(make_back_bar_text_style(fonts.body), 0)
        empty.set_style_text_color(lv.color_hex(Colors.TEXT_DIM), 0)
        _gallery_objects.append(empty)
        return

    y = 4
    for group in groups:
        _make_date_header(lst, y, group['label'])
        y += GAL_DATE_H + 4
        for photo in group['photos']:
            _make_photo_row(lst, y, photo)
            y += GAL_ROW_H + GAL_ROW_GAP
        y += 8  # 组间额外间距

    content_h = y + 4
    if content_h < list_h:
        content_h = list_h
    lst.set_content_height(content_h)


def _remove_photo_from_groups(groups, photo):
    """从分组数据移除照片,空组删除。"""
    for group in list(groups):
        photos = group.get('photos', [])
        if photo in photos:
            photos.remove(photo)
            if not photos:
                groups.remove(group)
            return True
    return False


def _rebuild_gallery_ui():
    """删除旧列表对象,按当前 _gallery_groups 重建。"""
    global _gallery_objects
    old_objects = _gallery_objects
    _gallery_objects = []

    for obj in old_objects:
        try:
            obj.delete()
        except Exception:
            pass

    if _gallery_list is not None:
        try:
            _gallery_list.delete()
        except Exception:
            pass
        _gallery_list = None

    _build_gallery_ui(_gallery_groups)


def _on_delete_photo(photo, row_obj):
    """删除照片文件 + 移除 UI 行。"""
    path = photo['path']
    print("[Gallery] delete: %s" % path)

    # 1. 删除文件
    try:
        os.remove(path)
    except Exception as e:
        print("[Gallery] remove failed: %s" % e)
        return  # 删除失败,保留 UI

    # 2. 从分组数据移除,重建列表让下方照片上移
    _remove_photo_from_groups(_gallery_groups, photo)
    _rebuild_gallery_ui()

    # 3. 蜂鸣反馈
    _ctx_runtime().buzzer.beep(ms=20)


def _leave_gallery():
    """离开图库,清理 LVGL 对象 + 恢复相机 UI。"""
    global _state

    for obj in _gallery_objects:
        try:
            obj.delete()
        except Exception:
            pass
    _gallery_objects[:] = []

    if _gallery_list is not None:
        try:
            _gallery_list.delete()
        except Exception:
            pass
        _gallery_list = None

    _gallery_groups = []

    # 恢复相机 UI:屏幕透明(相机预览需 bg_opa=0 透出 OSD1)
    if _screen is not None:
        _screen.set_style_bg_opa(0, 0)
    if _bottom_bar is not None:
        _bottom_bar.clear_flag(lv.obj.FLAG.HIDDEN)
    if _preview_bg is not None:
        _preview_bg.clear_flag(lv.obj.FLAG.HIDDEN)

    # 恢复标题 + 状态
    if _title_label is not None:
        _title_label.set_text(_ctx_runtime().lang.t("category.camera"))
    _state = STATE_PHOTO
    _refresh_shutter()
    _refresh_mode_icon()
    print("[Gallery] leave done")


# ── 销毁 ──────────────────────────────────────

def _destroy_ui():
    """删全部 LVGL 对象 + 恢复屏幕不透明。
    不碰 runtime 硬件(由 main.py runtime.cleanup() 统一 deinit)。
    """
    global _screen, _top_bar, _bottom_bar, _preview_bg, _timer_label
    global _gallery_list, _gallery_objects, _gallery_groups
    global _shutter_btn, _mode_green_dot, _title_label, _flash_obj

    # 释放图库对象
    _gallery_objects[:] = []
    _gallery_groups = []

    for obj in (_top_bar, _bottom_bar, _preview_bg, _timer_label, _gallery_list):
        if obj is not None:
            try:
                obj.delete()
            except Exception:
                pass
    _top_bar = None
    _bottom_bar = None
    _preview_bg = None
    _timer_label = None
    _gallery_list = None
    _shutter_btn = None
    _mode_green_dot = None
    _title_label = None

    if _flash_obj is not None:
        try:
            _flash_obj.delete()
        except Exception:
            pass
        _flash_obj = None

    # 恢复屏幕背景不透明(相机页设过透明,主菜单需不透明背景)
    try:
        scr = lv.scr_act()
        scr.set_style_bg_color(lv.color_hex(Colors.BG), 0)
        scr.set_style_bg_opa(255, 0)
    except Exception:
        pass
    _screen = None
```

> **平移要点核对**(对照旧 CameraApp):
> - 删 `import image as _image_lib`(缩略图死代码用,不再需要)
> - 删 `_init_camera`/`_stop_camera`(传感器简化,runtime.sensor 开箱即用)
> - 删 `_load_thumbnail`/`_fit_thumb_size`/`_bmp_dimensions` + GAL_THUMB_* 常量 + `_gallery_thumbs`(从未调用的缩略图死代码)
> - 删 `import struct`? **不删** —— `_png_zoom`/`_make_icon` 仍用 struct 解析 PNG 头
> - `self.ctx.lang` → `runtime.lang`(或 `_ctx_runtime().lang`)
> - `self.ctx.buzzer.beep` → `_ctx_runtime().buzzer.beep`
> - `self.ctx.lcd.capture_chn` → 常量 `CAM_CHN_ID_1`
> - `self.ctx.request_exit()` → `exit_flag[0] = True`
> - `self._xxx` → 模块级 `_xxx`,需 `global` 声明的赋值处都补上
> - 录像为空壳(无实际编码),保持与旧代码一致
> - `camera.gallery`/`camera.no_photos` lang key 沿用旧代码

- [ ] **Step 2: 运行 test_camera.py 确认 Task 2 全部转 Green**

Run: `python tests/test_camera.py`
Expected: ALL PASS(7 tests: Task1 的 2 个 + Task2 的 5 个)

- [ ] **Step 3: 运行 test_camera_gallery.py 看哪些过哪些挂(基线)**

Run: `python tests/test_camera_gallery.py`
Expected: 部分测试 FAIL —— 该测试文件仍针对旧 CameraApp 类(已不存在)。具体:
- `test_thumbnail_loader_decodes_jpg_via_image_module` FAIL(死代码已删,`_load_thumbnail` 不存在)—— 本就应删
- `test_camera_app_defines_delete_reflow_helper` FAIL(找 `CameraApp` 类失败)
- `test_delete_handler_uses_reflow_helper_and_rebuilds_ui` FAIL(同上)
- `test_photo_capture_saves_as_jpg` FAIL(同上)
- `test_camera_app_imports_image_module` FAIL(`CameraApp` 类失败 / image 不再 import)

这些 FAIL 是预期的(测试文件待 Task 4 重写)。记录此状态,进 Task 4。

- [ ] **Step 4: 提交(Task 2 测试 + Task 3 实现合并提交)**

```bash
git add tests/test_camera.py scripts/camera/app.py
git commit -m "refactor(camera): migrate CameraApp class to module-level run(runtime)

- 删 BaseScript/on_enter/on_frame/on_exit 生命周期,改 run(runtime) 单线程主循环
- self._xxx → 模块级状态;ctx.* → runtime.*;ctx.lcd 共享 sensor → runtime.sensor
- 删 _init_camera/_stop_camera(每进程独立 runtime.sensor,cleanup 统一释放)
- 删缩略图死代码 _load_thumbnail/_fit_thumb_size/_bmp_dimensions + GAL_THUMB_*
- 业务原样保留:拍照(JPG/chn1)/录像空壳/图库(文件名+日期+删除)
- _destroy_ui 恢复屏幕不透明(相机运行期 bg_opa=0)

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 4: 重写 test_camera_gallery.py(模块级 + 删缩略图测试)

**Files:**
- Modify: `tests/test_camera_gallery.py`(整体重写)

- [ ] **Step 1: 整体重写 test_camera_gallery.py**

将 `tests/test_camera_gallery.py` **整体替换**为:

```python
# tests/test_camera_gallery.py — host-side regression tests for camera gallery.
# 迁移后 camera 是模块级 run(runtime) 函数式(无 CameraApp 类)。
# Run with:
#   python tests/test_camera_gallery.py
import ast
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP_PATH = os.path.join(ROOT, "scripts", "camera", "app.py")


def _parse(path):
    with open(path, "r", encoding="utf-8") as f:
        return ast.parse(f.read(), filename=path)


def _function_node(tree, name):
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError("Function %s missing" % name)


def _module_functions(tree):
    return {
        node.name for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


def test_gallery_has_delete_reflow_helpers():
    """删除照片必须更新分组数据 + 重建列表 UI。"""
    tree = _parse(APP_PATH)
    funcs = _module_functions(tree)
    assert "_remove_photo_from_groups" in funcs, "delete must remove photo from data model"
    assert "_rebuild_gallery_ui" in funcs, "delete must rebuild gallery UI so lower rows move up"


def test_delete_handler_uses_reflow_helpers():
    """_on_delete_photo 必须调 _remove_photo_from_groups + _rebuild_gallery_ui。"""
    tree = _parse(APP_PATH)
    delete_fn = _function_node(tree, "_on_delete_photo")

    called = set()
    for node in ast.walk(delete_fn):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            called.add(node.func.id)
    assert "_remove_photo_from_groups" in called, "delete must update grouped photo data"
    assert "_rebuild_gallery_ui" in called, "delete must rebuild list positions after data changes"


def test_photo_capture_saves_as_jpg():
    """_capture_photo 必须存 .jpg(用户要求 JPEG 格式)。"""
    tree = _parse(APP_PATH)
    capture_fn = _function_node(tree, "_capture_photo")

    found_jpg = False
    found_bmp = False
    for node in ast.walk(capture_fn):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            val = node.value
            if val.endswith('.jpg') or val.endswith('.jpeg'):
                found_jpg = True
            if val.endswith('.bmp'):
                found_bmp = True

    assert found_jpg, "_capture_photo must save as .jpg — user wants JPEG format"
    assert not found_bmp, "_capture_photo must NOT save as .bmp — user wants JPEG format"


def test_gallery_does_not_decode_thumbnails():
    """图库不显示缩略图(K230 image.Image 不能读 JPG,本设计只显示文件名+日期+删除)。
    缩略图死代码(_load_thumbnail/_fit_thumb_size/_bmp_dimensions)必须已删除。"""
    src = open(APP_PATH, encoding="utf-8").read()
    for token in ("_load_thumbnail", "_fit_thumb_size", "_bmp_dimensions",
                  "_gallery_thumbs"):
        assert token not in src, "dead thumbnail code must be removed: %s" % token


def test_camera_does_not_import_image():
    """删缩略图死代码后,camera 顶层不再 import image。"""
    tree = _parse(APP_PATH)
    for node in tree.body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert alias.name != "image", "must not import image (thumbnail dead code removed)"
        elif isinstance(node, ast.ImportFrom):
            assert node.module != "image" and (
                node.module is None or not node.module.startswith("image")), \
                "must not import from image"


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

> **改动说明**:
> - 删 `test_thumbnail_loader_decodes_jpg_via_image_module`(死代码已删,该测试针对不存在路径)
> - 所有 `_camera_app_class(tree)` 找 `CameraApp` 类 → 改为 `_function_node(tree, name)` 找模块级函数
> - `test_camera_app_imports_image_module`(断言必须 import image)→ 翻转为 `test_camera_does_not_import_image`(断言不 import image)
> - 新增 `test_gallery_does_not_decode_thumbnails` 守护死代码已删
> - `test_delete_handler_uses_reflow_helpers`:`self._xxx(...)` 调用 → 模块级函数调用(`ast.Name` 而非 `ast.Attribute` on self)

- [ ] **Step 2: 运行确认通过**

Run: `python tests/test_camera_gallery.py`
Expected: ALL PASS(5 tests)

- [ ] **Step 3: 运行 test_camera.py 确认仍全绿**

Run: `python tests/test_camera.py`
Expected: ALL PASS(7 tests)

- [ ] **Step 4: 提交**

```bash
git add tests/test_camera_gallery.py
git commit -m "test(camera): rewrite gallery tests for module-level run(runtime)

- 删 test_thumbnail_loader (死代码已删)
- 类断言改模块级函数断言(CameraApp 类不再存在)
- 翻转 image import 断言(删死代码后不再 import image)
- 新增死代码已删守护

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 5: 补全 test_camera.py 剩余契约测试 + 全量回归

**Files:**
- Test: `tests/test_camera.py`

- [ ] **Step 1: 在 test_camera.py 追加剩余契约测试**

在 `test_camera_uses_runtime_lang` 之后、`test_runner` 之前追加:

```python
def test_camera_top_bar_back_button():
    """顶栏有返回钮 + CLICKED 设 exit_flag(GALLERY 态调 _leave_gallery)。"""
    src = open(APP_PATH, encoding="utf-8").read()
    assert "_build_top_bar" in src, "must have _build_top_bar"
    assert "EVENT.CLICKED" in src, "back button must bind CLICKED"
    assert "exit_flag[0] = True" in src, "back callback must set exit_flag"
    assert "_leave_gallery" in src, "back from gallery must call _leave_gallery"


def test_camera_state_machine():
    """状态机四状态常量 + 快门/模式回调存在。"""
    src = open(APP_PATH, encoding="utf-8").read()
    for token in ("STATE_PHOTO", "STATE_VIDEO", "STATE_RECORDING", "STATE_GALLERY"):
        assert token in src, "state constant missing: %s" % token
    tree = _parse(APP_PATH)
    funcs = {n.name for n in tree.body
             if isinstance(n, ast.FunctionDef)}
    for fn in ("_on_shutter", "_on_mode_toggle", "_refresh_shutter", "_refresh_mode_icon"):
        assert fn in funcs, "state machine function missing: %s" % fn


def test_camera_capture_uses_chn1():
    """拍照用 chn1(SXGAM/RGB565 支持 jpg save)。"""
    src = open(APP_PATH, encoding="utf-8").read()
    assert "CAM_CHN_ID_1" in src, "capture must use chn1 (SXGAM/RGB565 for jpg save)"


def test_camera_destroy_ui_restores_opacity():
    """_destroy_ui 必须恢复屏幕不透明(相机运行期 bg_opa=0)。"""
    tree = _parse(APP_PATH)
    destroy_fn = _function_node(tree, "_destroy_ui")
    src_segment = ast.unparse(destroy_fn)
    assert "set_style_bg_opa(255" in src_segment or "bg_opa(255" in src_segment, \
        "_destroy_ui must restore screen opacity to 255 for main menu"


def test_camera_no_self_init_media():
    """走 init_app,不自 init media/sensor(由 runtime 管理)。"""
    src = open(APP_PATH, encoding="utf-8").read()
    assert "MediaManager.init" not in src, "must not self-init MediaManager"
    assert "sensor.reset" not in src, "must not self-reset sensor"
    assert "sensor.run" not in src, "must not self-run sensor (init_app does it)"


def test_camera_business_preserved():
    """核心业务函数保留(拍照/录像/图库)。"""
    tree = _parse(APP_PATH)
    funcs = {n.name for n in tree.body
             if isinstance(n, ast.FunctionDef)}
    for fn in ("_capture_photo", "_start_recording", "_stop_recording",
               "_update_timer", "_enter_gallery", "_leave_gallery",
               "_on_delete_photo", "_group_photos_by_date", "_build_gallery_ui"):
        assert fn in funcs, "business function must remain: %s" % fn


def test_camera_no_on_frame_hook():
    """camera 是叶子 APP,不挂 on_frame 钩子(非 AI 基座)。"""
    src = open(APP_PATH, encoding="utf-8").read()
    # on_frame 是 _template 的 AI 钩子,camera 不应有顶层 def on_frame
    assert "def on_frame" not in src, "camera must NOT have on_frame hook (leaf app, not AI base)"
```

- [ ] **Step 2: 运行确认通过**

Run: `python tests/test_camera.py`
Expected: ALL PASS(14 tests: 7 + 7 新增)

- [ ] **Step 3: 全量回归(所有 camera 相关测试 + 框架测试)**

Run:
```bash
python tests/test_camera.py
python tests/test_camera_gallery.py
python tests/test_framework.py
```
Expected:
- test_camera.py: ALL PASS(14)
- test_camera_gallery.py: ALL PASS(5)
- test_framework.py: ALL PASS(含 back icon 测试等,确认未受影响)

- [ ] **Step 4: 提交**

```bash
git add tests/test_camera.py
git commit -m "test(camera): add remaining migration contract tests

顶栏返回钮/状态机/chn1拍照/destroy恢复不透明/不自init media/业务保留/无on_frame钩子

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 6: 板端部署 + 验收(用户执行)

**Files:**
- Deploy: `scripts/camera/app.py` → 板子 `/sdcard/CamerAi/scripts/camera/app.py`

- [ ] **Step 1: 部署改动文件到板子**

将 `scripts/camera/app.py` 覆盖到板子 `/sdcard/CamerAi/scripts/camera/app.py`。

> 本次 camera 改造只动了 `scripts/camera/app.py`(框架/配置/main.py 都未改),所以**只需部署这一个文件**。core/app_runtime.py、main.py、config/categories.json 在之前 settings/template 工作中已部署且本次未改,无需重传。

- [ ] **Step 2: 硬断电后板端验收**

硬断电重启,从主菜单进 camera,逐项验收:

1. **启动 + UI**:进入 camera,顶栏(返回图标 + "相机"标题)+ 预览(摄像头画面流畅)+ 底栏(图库图标 / 快门 / 模式图标)正常显示,画面不卡
2. **拍照**:点快门(白闪 + 蜂鸣),`/data/photo/` 出现新 IMG_*.jpg
3. **模式切换**:点模式钮,PHOTO↔VIDEO 切换(模式绿点亮/灭 + 快门外观变化)
4. **录像(空壳)**:VIDEO 态点快门开始录像(计时器显示 + 红点闪烁),再点停止(计时器隐藏)
5. **图库**:点图库图标进图库页(不透明背景 + 按日期分组的照片列表:文件名 + 日期 + 删除按钮)
6. **删除**:点某张照片的删除按钮(文件删除 + 下方照片上移重排 + 蜂鸣)
7. **图库返回**:点返回(回相机态,非退出 APP),预览恢复
8. **退出**:点返回(回主菜单)
9. **无污染**:再进 settings / 模板,验证未受 camera 影响(顶栏返回图标正常、能进能出)

- [ ] **Step 3: 验收通过后更新项目记录**

验收 1-9 全过后,在 `项目记录.md` 追加 camera 改造记录(迁移完成、删死代码、单文件、板端验收通过),并提交:

```bash
git add 项目记录.md
git commit -m "docs: camera 框架迁移板端验收通过

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## Self-Review 核对

**1. Spec 覆盖**:
- §3 架构与入口(run/主循环/模块级状态/不挂 on_frame)→ Task 1(run 骨架)+ Task 3(主循环 + 模块级状态)+ Task 5(test_camera_no_on_frame_hook)
- §4 传感器简化(删 _init_camera/_stop_camera、用 runtime.sensor)→ Task 3(实现)+ Task 2(test_camera_no_ctx_lcd / test_camera_uses_runtime_sensor)+ Task 5(test_camera_no_self_init_media)
- §5 业务保留(状态机/拍照/录像空壳/图库/删死代码/ctx→runtime/标题)→ Task 3(实现)+ Task 5(test_camera_state_machine / test_camera_business_preserved / test_camera_capture_uses_chn1)+ Task 4(test_gallery_does_not_decode_thumbnails)
- §6 退出清理/_destroy_ui 恢复不透明 → Task 3(实现)+ Task 5(test_camera_destroy_ui_restores_opacity)
- §7 测试策略(test_camera.py 契约 + 重写 test_camera_gallery.py)→ Task 1/2/4/5
- §8 文件清单(app.py 重写 + test_camera.py 新增 + test_camera_gallery.py 重写)→ Task 1/3/4
- 无遗漏。

**2. 占位符扫描**:无 TBD/TODO/"implement later"/"add error handling"/"similar to Task N"。每个 code step 含完整代码。

**3. 类型一致性**:
- 模块级状态变量名(`_state`/`_RUNTIME`/`_gallery_objects`/`_gallery_groups`/`_flash_obj` 等)在 Task 3 实现与 Task 5 测试断言中一致
- 函数名(`_on_shutter`/`_on_mode_toggle`/`_capture_photo`/`_enter_gallery`/`_leave_gallery`/`_on_delete_photo`/`_remove_photo_from_groups`/`_rebuild_gallery_ui`/`_destroy_ui`/`_ctx_runtime`)在 Task 3 实现与 Task 4/5 测试断言中一致
- `_make_icon` 签名:旧版返回 `(img_obj, actual_x)`,新版 Task 3 简化为只返回 `img_obj`(调用处 gallery/mode 都只用 img 不用 x,且旧 back 调用处也忽略第二返回值)—— 一致
- 状态常量 STATE_PHOTO/VIDEO/RECORDING/GALLERY 值 0/1/2/3 与旧版一致
- lang key `category.camera`/`camera.gallery`/`camera.no_photos` 与旧版一致

**4. 已知风险(实现时注意)**:
- `_make_icon` 新版返回单值,Task 3 中所有调用处(`_build_top_bar` back / `_build_bottom_bar` gallery / mode)都未接收返回值或只接收单值 —— 已核对一致
- `_gallery_objects[:] = []`(就地清空)在 `_leave_gallery` 用,`_gallery_objects = []`(重新绑定)在 `_destroy_ui`/`_enter_gallery` 用 —— 都需 `global _gallery_objects` 声明,Task 3 已在对应函数补 global
- Task 3 是整体重写,执行者须**完整替换** app.py,不能 patch

## 执行选择

计划已保存到 [docs/superpowers/plans/2026-06-23-camera-refactor.md](2026-06-23-camera-refactor.md)。两种执行方式:

**1. Subagent-Driven(推荐)** — 每个 Task 派新 subagent 执行,Task 间 review,快速迭代

**2. Inline Execution** — 在当前会话内执行,batch + checkpoint review

哪种?
