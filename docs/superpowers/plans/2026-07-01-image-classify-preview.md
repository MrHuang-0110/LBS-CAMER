# 图像分类(image_classify)预览脚手架 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 新增 `image_classify` 脚本,本轮只输出单路 chn0 摄像源预览并通过协议 0x13 向主机发心跳(40B 全零),AI/识别/持久化全部留空占位。

**Architecture:** 以 `scripts/_template/app.py` 为骨架(顶栏返回钮+空底栏+透明预览+单线程主循环),借鉴 `scripts/road_detect/app.py` 的 `_DETECTION_ENABLED = False` 模式:`on_frame` 在标志为 False 时只调 `runtime.host_tick(None)` 后 return。补 `comm/host_api.py` 的 `CATEGORY_TYPE["image_classify"]`(否则回退 0x01 主菜单,协议发错)与 `core/app_runtime.py` 的 `_channels_for` 显式 pass 分支。

**Tech Stack:** MicroPython + LVGL v8 + K230 media(sensor/display)。Host 侧测试用 Python `ast` 静态契约(板端模块不可导入),与 `tests/test_gesture_detect_ast.py` 同款;用简易 `test_runner` 自跑(项目无 pytest,见 项目记录.md)。

**Spec:** `docs/superpowers/specs/2026-07-01-image-classify-preview-design.md`

---

## File Structure

- **Create** `scripts/image_classify/__init__.py` — 空包标记(同 `scripts/road_detect/__init__.py`)。
- **Create** `scripts/image_classify/app.py` — 脚本主体:顶栏(返回钮+i18n标题)+ 透明预览 + 空底栏 + 单线程主循环 + `on_frame`(预览模式只 host_tick)。
- **Modify** `comm/host_api.py:42-55` — `CATEGORY_TYPE` dict 增 `"image_classify": TYPE_IMAGE_CLASSIFY`。
- **Modify** `core/app_runtime.py:277-282` — `_channels_for` 增 `elif category_id == "image_classify": pass` 分支。
- **Create** `tests/test_image_classify_ast.py` — host 侧 AST 契约测试(镜像 `test_gesture_detect_ast.py` 结构 + 自跑 runner)。

**职责边界:** `app.py` 只管 UI 与帧循环;协议类型码归属 `host_api.py`;通道配置归属 `app_runtime.py`;契约守护在 `tests/`。三处源码改动各自有独立测试守护,互不耦合。

---

## Task 1: 创建 `__init__.py` 包标记

**Files:**
- Create: `scripts/image_classify/__init__.py`

- [ ] **Step 1: 创建空包标记文件**

写入 `scripts/image_classify/__init__.py`(内容与 `scripts/road_detect/__init__.py` 一致,仅注释行):

```python
# scripts/image_classify/__init__.py
```

- [ ] **Step 2: 验证文件存在且可被 import 视作包**

Run: `python -c "import ast,os; p=os.path.join('scripts','image_classify','__init__.py'); assert os.path.exists(p); print('OK')"`
Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add scripts/image_classify/__init__.py
git commit -m "feat(image_classify): add package marker"
```

---

## Task 2: 写失败测试 — CATEGORY_TYPE 含 image_classify 映射

**Files:**
- Create: `tests/test_image_classify_ast.py`

- [ ] **Step 1: 写失败的 AST 契约测试(只含本任务这一个测试)**

写入 `tests/test_image_classify_ast.py`:

```python
# tests/test_image_classify_ast.py -- host-side AST 契约测试(image_classify)
import ast, os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP_RUNTIME_PATH = os.path.join(ROOT, "core", "app_runtime.py")
HOST_API_PATH = os.path.join(ROOT, "comm", "host_api.py")
APP_PATH = os.path.join(ROOT, "scripts", "image_classify", "app.py")


def _read(path):
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def test_image_classify_in_category_type_map():
    """CATEGORY_TYPE 必须包含 'image_classify': TYPE_IMAGE_CLASSIFY。

    缺它则 HostAPI.tick 回退 TYPE_MAIN_MENU(0x01),协议发错。
    """
    src = _read(HOST_API_PATH)
    assert '"image_classify":' in src
    after = src.split('"image_classify":')[1][:80]
    assert "TYPE_IMAGE_CLASSIFY" in after


def test_runner():
    import sys
    mod = sys.modules[__name__]
    fails = 0
    for n in sorted(dir(mod)):
        if n.startswith("test_") and callable(getattr(mod, n)) and n != "test_runner":
            try:
                getattr(mod, n)()
                print("  PASS %s" % n)
            except Exception as e:
                print("  FAIL %s: %s" % (n, e))
                fails += 1
    assert fails == 0, "%d tests failed" % fails


if __name__ == "__main__":
    test_runner()
```

- [ ] **Step 2: 运行测试,验证它失败(RED)**

Run: `python tests/test_image_classify_ast.py`
Expected: FAIL — `test_image_classify_in_category_type_map` 失败,因为 `comm/host_api.py` 的 `CATEGORY_TYPE` 尚无 `"image_classify":` 键(`'"image_classify":' in src` 为 False)。

- [ ] **Step 3: Commit(RED 状态留证)**

```bash
git add tests/test_image_classify_ast.py
git commit -m "test(image_classify): RED — assert CATEGORY_TYPE has image_classify"
```

---

## Task 3: 实现 — host_api CATEGORY_TYPE 增 image_classify 映射(GREEN)

**Files:**
- Modify: `comm/host_api.py:42-55`

- [ ] **Step 1: 在 CATEGORY_TYPE dict 增 image_classify 键**

在 `comm/host_api.py` 的 `CATEGORY_TYPE` dict 中,`"object_classify": TYPE_OBJECT_CLASSIFY,   # 0x0A` 行之后、`"_template": ...` 行之前,插入 image_classify 行。

用 Edit 工具,`old_string`:

```python
        "object_classify": TYPE_OBJECT_CLASSIFY,   # 0x0A
        "_template":  TYPE_MAIN_MENU,     # 0x01（默认）
```

`new_string`:

```python
        "object_classify": TYPE_OBJECT_CLASSIFY,   # 0x0A
        "image_classify":  TYPE_IMAGE_CLASSIFY,    # 0x13
        "_template":  TYPE_MAIN_MENU,     # 0x01（默认）
```

- [ ] **Step 2: 运行测试,验证通过(GREEN)**

Run: `python tests/test_image_classify_ast.py`
Expected: PASS — `test_image_classify_in_category_type_map` 通过。

- [ ] **Step 3: 回归 host_api 既有测试,确认未破坏**

Run: `python tests/test_host_api.py`
Expected: PASS(全部既有测试通过;`test_category_type_mapping_covers_all_categories` 只检查一个子集,新增键不破坏它)。

- [ ] **Step 4: Commit**

```bash
git add comm/host_api.py
git commit -m "fix(host_api): map image_classify to TYPE_IMAGE_CLASSIFY (0x13)"
```

---

## Task 4: 写失败测试 — app_runtime _channels_for 有 image_classify 分支

**Files:**
- Modify: `tests/test_image_classify_ast.py`(追加测试)

- [ ] **Step 1: 追加失败的 _channels_for 契约测试**

在 `tests/test_image_classify_ast.py` 的 `test_image_classify_in_category_type_map` 函数之后、`test_runner` 之前,追加:

```python
def test_channels_for_image_classify_is_single_chn0():
    """_channels_for 的 image_classify 分支应为单 chn0(预览模式,无 AI 通道)。

    显式 pass 分支镜像 road_detect,表意:本轮不附加 chn2 AI 通道。
    """
    src = _read(APP_RUNTIME_PATH)
    start = src.find("def _channels_for(")
    assert start != -1, "must define _channels_for"
    body = src[start:start + 2200]
    assert "image_classify" in body, "_channels_for must handle image_classify"
    # image_classify 分支不应 append 任何 AI 通道(预览模式)
    after = body.split('"image_classify"')[1][:200]
    assert "append" not in after, "image_classify must NOT append an AI channel (preview-only)"
```

- [ ] **Step 2: 运行测试,验证新测试失败(RED)**

Run: `python tests/test_image_classify_ast.py`
Expected: FAIL — `test_channels_for_image_classify_is_single_chn0` 失败,因为 `core/app_runtime.py` 的 `_channels_for` 无 `image_classify` 分支(`"image_classify" in body` 为 False)。`test_image_classify_in_category_type_map` 仍 PASS。

- [ ] **Step 3: Commit(RED 状态留证)**

```bash
git add tests/test_image_classify_ast.py
git commit -m "test(image_classify): RED — assert _channels_for has image_classify branch"
```

---

## Task 5: 实现 — app_runtime _channels_for 增 image_classify 分支(GREEN)

**Files:**
- Modify: `core/app_runtime.py:277-282`

- [ ] **Step 1: 在 _channels_for 增 image_classify pass 分支**

在 `core/app_runtime.py` 的 `_channels_for` 中,`elif category_id == "object_classify":` 块之后、`elif category_id == "_template":` 之前,插入 image_classify 分支。

用 Edit 工具,`old_string`:

```python
        elif category_id == "object_classify":
            # chn2 XGA RGBP888 做 AI 推理(同 body_detect:YOLOv8n 检测 + recognition 特征)
            chs.append((CAM_CHN_ID_2, Sensor.XGA, Sensor.RGBP888))
        elif category_id == "_template":
```

`new_string`:

```python
        elif category_id == "object_classify":
            # chn2 XGA RGBP888 做 AI 推理(同 body_detect:YOLOv8n 检测 + recognition 特征)
            chs.append((CAM_CHN_ID_2, Sensor.XGA, Sensor.RGBP888))
        elif category_id == "image_classify":
            # 暂时单通道 chn0 VGA RGB888 预览(不跑AI)。
            # 后续完善时改 app.py 的 _DETECTION_ENABLED=True 并在此 append chn2 AI 通道。
            pass
        elif category_id == "_template":
```

- [ ] **Step 2: 运行测试,验证通过(GREEN)**

Run: `python tests/test_image_classify_ast.py`
Expected: PASS — 两个测试均通过。

- [ ] **Step 3: 回归 app_runtime 既有测试**

Run: `python tests/test_app_runtime_object.py`
Expected: PASS(确认未破坏 object_detect/app_runtime 既有契约)。

- [ ] **Step 4: Commit**

```bash
git add core/app_runtime.py
git commit -m "feat(app_runtime): add image_classify single-chn0 preview branch"
```

---

## Task 6: 写失败测试 — app.py 存在且有 run / host_tick / _DETECTION_ENABLED=False

**Files:**
- Modify: `tests/test_image_classify_ast.py`(追加测试)

- [ ] **Step 1: 追加失败的 app.py 契约测试**

在 `tests/test_image_classify_ast.py` 的 `test_channels_for_image_classify_is_single_chn0` 之后、`test_runner` 之前,追加:

```python
def _app_src():
    return _read(APP_PATH)


def test_app_has_run_entry():
    """app.py 必须有 run(runtime) 入口(reset 框架调 mod.run(runtime))。"""
    src = _app_src()
    assert "def run(runtime):" in src, "app must define run(runtime)"


def test_app_has_host_tick_for_protocol_0x13():
    """app.py on_frame 必须调 host_tick(协议 0x13 心跳)。

    预览模式(_DETECTION_ENABLED=False)下每帧 host_tick(None) 推 40B 全零。
    """
    src = _app_src()
    assert "host_tick" in src, "app must call host_tick for protocol 0x13"


def test_app_detection_disabled_by_default():
    """app.py 顶部 _DETECTION_ENABLED 必须默认 False(预览模式)。"""
    src = _app_src()
    assert "_DETECTION_ENABLED = False" in src, \
        "app must default _DETECTION_ENABLED to False (preview-only)"


def test_app_uses_back_icon_and_i18n_title():
    """顶栏返回钮用通用 get_back_icon(),标题用 i18n category.image_classify。"""
    src = _app_src()
    assert "get_back_icon" in src, "back button must use shared get_back_icon()"
    assert "category.image_classify" in src, "title must use i18n category.image_classify"


def test_app_sets_runtime_global():
    """run() 必须设 _RUNTIME 全局(供 _on_back 蜂鸣 + on_frame host_tick 用)。

    _template/app.py 的 _on_back 引用了未定义的 _RUNTIME;image_classify 必须修正。
    """
    src = _app_src()
    assert "global _RUNTIME" in src, "run must set global _RUNTIME"
    assert "_RUNTIME = runtime" in src, "run must assign _RUNTIME = runtime"
```

- [ ] **Step 2: 运行测试,验证新测试失败(RED)**

Run: `python tests/test_image_classify_ast.py`
Expected: FAIL — 5 个新 app.py 测试全部失败(`scripts/image_classify/app.py` 不存在,`_read(APP_PATH)` 抛 FileNotFoundError)。前 2 个测试仍 PASS。

- [ ] **Step 3: Commit(RED 状态留证)**

```bash
git add tests/test_image_classify_ast.py
git commit -m "test(image_classify): RED — assert app.py run/host_tick/disabled/runtime"
```

---

## Task 7: 实现 — 创建 app.py(GREEN)

**Files:**
- Create: `scripts/image_classify/app.py`

- [ ] **Step 1: 写 app.py 完整内容**

写入 `scripts/image_classify/app.py`:

```python
# scripts/image_classify/app.py — 图像分类(预览脚手架)。
#
# 复用 _template 单线程主循环 + road_detect 的 _DETECTION_ENABLED 预览模式。
# chn0 VGA RGB888 显示。本轮不跑 AI、不持久化:只顶栏(返回钮+i18n标题)
# + 透明预览 + 空底栏,每帧 host_tick(None) 推协议 0x13 心跳(40B 全零)。
#
# 后续完善时:改 _DETECTION_ENABLED=True,在 app_runtime._channels_for 的
# image_classify 分支 append chn2 AI 通道,在 on_frame 检测分支填 slots。

import os
import sys
import time
import lvgl as lv
from media.display import Display
from media.sensor import CAM_CHN_ID_0
from core.icon_cache import icon_cache
from core.font_manager import fonts

BAR_H = 52
PREVIEW_Y = BAR_H
PREVIEW_H = 376
BAR_BG = 0x1A1A1A

# 图像分类暂为单摄像源预览(不跑AI、空底栏)。
# 后续完善时改 True:恢复 chn2 AI 通道(_channels_for)+ on_frame 检测分支。
_DETECTION_ENABLED = False

_RUNTIME = None
_screen = None
_top_bar = None
_bottom_bar = None
_preview = None


def _build_ui(runtime, exit_flag):
    """顶栏(返回钮 + i18n 标题) + 透明预览 + 空底栏。

    返回钮 CLICKED 回调设 exit_flag[0]=True(只设标志,不做重操作)。
    返回钮用通用 get_back_icon()(init_app 已无条件预读),无需 image_classify 专属图标。
    """
    global _screen, _top_bar, _bottom_bar, _preview
    screen = lv.scr_act()
    # 屏幕透明:让 OSD1 摄像头画面透出;顶底栏自带不透明背景
    screen.set_style_bg_opa(0, 0)
    screen.add_flag(lv.obj.FLAG.CLICKABLE)
    _screen = screen

    # ── 顶栏:返回钮(左) + 标题(中) ──
    _top_bar = lv.obj(screen)
    _top_bar.set_size(lv.pct(100), BAR_H)
    _top_bar.set_pos(0, 0)
    _top_bar.set_style_bg_color(lv.color_hex(BAR_BG), 0)
    _top_bar.set_style_bg_opa(255, 0)
    _top_bar.set_style_border_width(0, 0)
    _top_bar.set_style_pad_all(0, 0)
    _top_bar.set_style_radius(0, 0)
    _top_bar.clear_flag(lv.obj.FLAG.SCROLLABLE)

    btn = lv.obj(_top_bar)
    btn.set_size(64, 64)
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
        import struct
        w = h = 64
        if len(icon_data) >= 24:
            w = struct.unpack('>I', icon_data[16:20])[0]
            h = struct.unpack('>I', icon_data[20:24])[0]
        target = int(64 * 0.85)
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
            if _RUNTIME is not None and _RUNTIME.buzzer is not None:
                _RUNTIME.buzzer.beep(ms=50)
            exit_flag[0] = True
    btn.add_event(_on_back, lv.EVENT.CLICKED, None)

    title = lv.label(_top_bar)
    title.set_text(runtime.lang.t("category.image_classify"))
    title.align(lv.ALIGN.CENTER, 0, 0)
    from ui.theme import make_back_bar_text_style
    title.add_style(make_back_bar_text_style(fonts.body), 0)

    # ── 预览区:透明,透出 OSD1 摄像头画面 ──
    _preview = lv.obj(screen)
    _preview.set_size(lv.pct(100), PREVIEW_H)
    _preview.set_pos(0, PREVIEW_Y)
    _preview.set_style_bg_opa(0, 0)
    _preview.set_style_border_width(0, 0)
    _preview.set_style_pad_all(0, 0)
    _preview.set_style_radius(0, 0)
    _preview.clear_flag(lv.obj.FLAG.SCROLLABLE)
    _preview.clear_flag(lv.obj.FLAG.CLICKABLE)

    # ── 底栏:纯空栏(占位,后续 AI 加按钮时填) ──
    _bottom_bar = lv.obj(screen)
    _bottom_bar.set_size(lv.pct(100), BAR_H)
    _bottom_bar.set_pos(0, PREVIEW_Y + PREVIEW_H)
    _bottom_bar.set_style_bg_color(lv.color_hex(BAR_BG), 0)
    _bottom_bar.set_style_bg_opa(255, 0)
    _bottom_bar.set_style_border_width(0, 0)
    _bottom_bar.set_style_pad_all(0, 0)
    _bottom_bar.set_style_radius(0, 0)
    _bottom_bar.clear_flag(lv.obj.FLAG.SCROLLABLE)


def on_frame(img):
    """单摄像源预览(暂不跑AI)。后续完善时改 _DETECTION_ENABLED=True 启用检测分支。"""
    if _RUNTIME is None:
        return
    if not _DETECTION_ENABLED:
        # 预览模式:仅显示 chn0(主循环 show_image),不检测。协议心跳用空 slots。
        if _RUNTIME.host is not None:
            _RUNTIME.host_tick(None)
        return
    # --- 以下为检测分支(_DETECTION_ENABLED=True 时启用)---
    # 后续:img_det = _RUNTIME.sensor.snapshot(chn=CAM_CHN_ID_2) 跑 AI,
    # 填 slots = [(id,x,y,w,h,conf), ...],再 _RUNTIME.host_tick(slots)。
    if _RUNTIME.host is not None:
        _RUNTIME.host_tick(None)


def _destroy_ui():
    """删顶栏/底栏/预览区 LVGL 对象 + 恢复屏幕不透明。"""
    global _screen, _top_bar, _bottom_bar, _preview
    for obj in (_top_bar, _bottom_bar, _preview):
        if obj is not None:
            try:
                obj.delete()
            except Exception:
                pass
    _top_bar = None
    _bottom_bar = None
    _preview = None
    try:
        from ui.theme import Colors
        scr = lv.scr_act()
        scr.set_style_bg_color(lv.color_hex(Colors.BG), 0)
        scr.set_style_bg_opa(255, 0)
    except Exception:
        pass
    _screen = None


def run(runtime):
    """主入口(reset 框架调 mod.run(runtime))。

    单线程主循环:snapshot → on_frame(try/except) → show_image(OSD1) → task_handler。
    触摸返回钮设 exit_flag → 循环退出 → _destroy_ui → main.py cleanup+reset 回菜单。
    """
    global _RUNTIME
    _RUNTIME = runtime
    exit_flag = [False]
    _build_ui(runtime, exit_flag)
    fc = 0
    try:
        while not exit_flag[0]:
            os.exitpoint()
            img = runtime.sensor.snapshot(chn=CAM_CHN_ID_0)
            try:
                on_frame(img)
            except Exception as e:
                print("[image_classify] on_frame error: %s" % e)
                try:
                    sys.print_exception(e)
                except Exception:
                    pass
            Display.show_image(img, 0, 0, Display.LAYER_OSD1)
            time.sleep_ms(lv.task_handler())
            fc += 1
            if fc % 30 == 0:
                print("[image_classify] fc=%d" % fc)
    finally:
        _destroy_ui()
        _RUNTIME = None
```

- [ ] **Step 2: 运行测试,验证全部通过(GREEN)**

Run: `python tests/test_image_classify_ast.py`
Expected: PASS — 全部 7 个测试通过(2 个 host/app_runtime 契约 + 5 个 app.py 契约)。

- [ ] **Step 3: 语法编译检查 app.py(板端 MicroPython 语法兼容)**

Run: `python -m py_compile scripts/image_classify/app.py`
Expected: 无输出(编译通过)。

- [ ] **Step 4: Commit**

```bash
git add scripts/image_classify/app.py
git commit -m "feat(image_classify): preview-only scaffold (chn0 + host_tick 0x13)"
```

---

## Task 8: 全量回归 + 部署清单

**Files:** 无新改动,只验证。

- [ ] **Step 1: 跑 image_classify 全部契约测试**

Run: `python tests/test_image_classify_ast.py`
Expected: 全部 PASS。

- [ ] **Step 2: 回归 host_api 与 app_runtime 既有测试**

Run: `python tests/test_host_api.py && python tests/test_app_runtime_object.py`
Expected: 全部 PASS。

- [ ] **Step 3: 编译检查所有改动文件**

Run: `python -m py_compile comm/host_api.py core/app_runtime.py scripts/image_classify/app.py scripts/image_classify/__init__.py tests/test_image_classify_ast.py`
Expected: 无输出(全部编译通过)。

- [ ] **Step 4: 更新 项目记录.md(追加 image_classify 实施完成条目)**

在 `项目记录.md` 顶部 `## 2026-07-01 图像分类(image_classify)预览脚手架` 条目下,追加实施完成小结(部署文件清单 + 验收状态):

- 部署文件:`scripts/image_classify/__init__.py`、`scripts/image_classify/app.py`、`comm/host_api.py`(CATEGORY_TYPE 增 image_classify→0x13)、`core/app_runtime.py`(_channels_for 增 image_classify pass 分支)。
- 测试:`tests/test_image_classify_ast.py` 7 项全绿(host 侧 AST 契约)。
- 待板端验收:进 image_classify 菜单 → 单路预览 → 主机收到 `{"port":4,"camer":{"mode":19,...}}`(0x13=19)。

- [ ] **Step 5: Commit**

```bash
git add 项目记录.md
git commit -m "docs(项目记录): image_classify preview scaffold implemented"
```

---

## 验收标准(板端)

1. 主菜单出现"图像分类"入口(已有 categories.json order 11 + i18n)。
2. 进入后:顶栏返回钮 + i18n 标题"图像分类" + chn0 实时预览 + 空底栏。
3. 主机串口收到 `{"port":4,"camer":{"mode":19,...}}`(0x13=19),无识别时 id1~id4 为 0。
4. 点返回钮蜂鸣 + 回主菜单,无残留 UI、无卡死。
5. 翻 `_DETECTION_ENABLED=True` 后(后续 AI 阶段)只动 on_frame 检测分支 + `_channels_for` append chn2,脚手架结构不变。
