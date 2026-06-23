# Face Detect Template Phase 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Migrate `face_detect` to the `_template` single-thread `run(runtime)+on_frame(img)` pattern and restore stable white-box face detection only.

**Architecture:** Extract reusable detection code into `core/face_ai.py`, then replace `scripts/face_detect/app.py` with a template-style app that initializes the detector before the first `lv.task_handler()` and runs detection inline in `on_frame`. Remove the `face_detect` naked-run special case from `main.py` so it uses `runtime.init_app()` and `runtime.cleanup()` like settings/camera.

**Tech Stack:** K230 CanMV MicroPython, LVGL, `media.sensor`, `media.display`, `nncase_runtime`, `ulab.numpy`, `libs.AIBase`, host-side AST contract tests run with `python tests/*.py`.

---

## File Structure

- Create: `core/face_ai.py`
  - Owns reusable face detection AI helpers: `ALIGN_UP`, `_draw_color`, `FaceDetectionApp`.
  - Phase 1 does not move/use `FaceRegistrationApp`.
- Replace: `scripts/face_detect/app.py`
  - Owns UI, `_init_ai()`, `on_frame(img)`, single-thread `run(runtime)`, `_deinit_ai()`, `_destroy_ui()`.
  - No `_thread`, no `media_init`, no `lvgl_init`, no `disp_drv_flush_cb`, no `face_db`, no `id_registry`.
- Modify: `main.py`
  - Remove `face_detect` skip path; all scripts use `runtime.init_app(category_id, fpioa)` and `runtime.cleanup()`.
- Modify: `core/app_runtime.py`
  - Keep face_detect chn0 + chn2, remove chn1 from face_detect if present.
- Create: `tests/test_face_ai.py`
  - Locks extracted AI helper API and ABGR color conversion.
- Create: `tests/test_face_detect_template.py`
  - Locks the template migration and Phase 1 boundaries.
- Modify: `tests/test_framework.py`
  - Locks framework behavior for face_detect normal init/cleanup and chn2-only AI channel.
- Modify: `项目记录.md`
  - Append implementation completion record after board/host verification.

---

### Task 1: Extract reusable detection code into `core/face_ai.py`

**Files:**
- Create: `tests/test_face_ai.py`
- Create: `core/face_ai.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_face_ai.py`:

```python
# tests/test_face_ai.py — host-side AST tests for reusable face AI helpers.
# Run with:
#   python tests/test_face_ai.py
import ast
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FACE_AI_PATH = os.path.join(ROOT, "core", "face_ai.py")


def _parse(path):
    with open(path, "r", encoding="utf-8") as f:
        return ast.parse(f.read(), filename=path)


def _function_node(tree, name):
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError("Function %s missing" % name)


def _class_node(tree, name):
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == name:
            return node
    raise AssertionError("Class %s missing" % name)


def test_face_ai_defines_detection_helpers():
    tree = _parse(FACE_AI_PATH)
    _function_node(tree, "ALIGN_UP")
    _function_node(tree, "_draw_color")
    _class_node(tree, "FaceDetectionApp")


def test_draw_color_uses_k230_abgr_tuple_order():
    """K230 draw_line/draw_rectangle on Sensor.RGB888 expects (A,B,G,R)."""
    tree = _parse(FACE_AI_PATH)
    fn = _function_node(tree, "_draw_color")
    src = ast.get_source_segment(open(FACE_AI_PATH, encoding="utf-8").read(), fn) or ""
    assert "return (0xFF, b, g, r)" in src or "return (255, b, g, r)" in src, \
        "_draw_color must return (A,B,G,R), not RGBA"


def test_face_detection_draw_result_keeps_recognition_signature():
    tree = _parse(FACE_AI_PATH)
    cls = _class_node(tree, "FaceDetectionApp")
    for node in cls.body:
        if isinstance(node, ast.FunctionDef) and node.name == "draw_result":
            arg_names = [a.arg for a in node.args.args]
            assert "recognition_results" in arg_names, \
                "draw_result must keep recognition_results=None for Phase 3 compatibility"
            defaults = node.args.defaults
            assert defaults and isinstance(defaults[-1], ast.Constant) and defaults[-1].value is None, \
                "recognition_results default must be None"
            return
    raise AssertionError("FaceDetectionApp.draw_result missing")


def test_face_ai_phase1_does_not_define_registration_app():
    src = open(FACE_AI_PATH, encoding="utf-8").read()
    assert "FaceRegistrationApp" not in src, \
        "Phase 1 core/face_ai.py should only extract detection; registration belongs to Phase 3"


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

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
python tests/test_face_ai.py
```

Expected: FAIL because `core/face_ai.py` does not exist or lacks the required symbols.

- [ ] **Step 3: Create `core/face_ai.py`**

Create `core/face_ai.py` by extracting the Phase 1 detection-only pieces from the current `scripts/face_detect/app.py`:

```python
# core/face_ai.py — reusable face AI helpers for CamerAi scripts.
#
# Phase 1 exposes detection only. Registration/feature matching are added in
# later phases after the template-based detection path is board-validated.

import gc
import time

import nncase_runtime as nn
import ulab.numpy as np
import aidemo
from libs.AIBase import AIBase
from libs.AI2D import Ai2d
from libs.Utils import ScopedTiming, letterbox_pad_param

RGB888P_SIZE = [1024, 768]
DISPLAY_SIZE = [640, 480]

BOX_COLORS = {
    1: 0x44CC44,
    2: 0x4488FF,
    3: 0xFF8844,
    4: 0xCC44FF,
}
BOX_UNKNOWN = 0xFFFFFF


def ALIGN_UP(x, align=16):
    return (x + align - 1) // align * align


def _draw_color(hex_color):
    """hex 0xRRGGBB → K230 draw_line/draw_rectangle color tuple.

    On this board, drawing on Sensor.RGB888 images expects tuple order
    (A, B, G, R), not RGBA.
    """
    r = (hex_color >> 16) & 0xFF
    g = (hex_color >> 8) & 0xFF
    b = hex_color & 0xFF
    return (0xFF, b, g, r)


class FaceDetectionApp(AIBase):
    def __init__(self, kmodel_path, model_input_size, anchors,
                 confidence_threshold=0.5, nms_threshold=0.2,
                 rgb888p_size=None, display_size=None, debug_mode=0):
        if rgb888p_size is None:
            rgb888p_size = RGB888P_SIZE
        if display_size is None:
            display_size = DISPLAY_SIZE
        super().__init__(kmodel_path, model_input_size, rgb888p_size, debug_mode)
        self.kmodel_path = kmodel_path
        self.model_input_size = model_input_size
        self.confidence_threshold = confidence_threshold
        self.nms_threshold = nms_threshold
        self.anchors = anchors
        self.rgb888p_size = [ALIGN_UP(rgb888p_size[0], 16), rgb888p_size[1]]
        self.display_size = [ALIGN_UP(display_size[0], 16), display_size[1]]
        self.debug_mode = debug_mode
        self.ai2d = Ai2d(debug_mode)
        self.ai2d.set_ai2d_dtype(nn.ai2d_format.NCHW_FMT, nn.ai2d_format.NCHW_FMT,
                                 np.uint8, np.uint8)

    def config_preprocess(self, input_image_size=None):
        with ScopedTiming("set preprocess config", self.debug_mode > 0):
            ai2d_input_size = input_image_size if input_image_size else self.rgb888p_size
            top, bottom, left, right, _ = letterbox_pad_param(
                self.rgb888p_size, self.model_input_size)
            self.ai2d.pad([0, 0, 0, 0, top, bottom, left, right], 0, [104, 117, 123])
            self.ai2d.resize(nn.interp_method.tf_bilinear, nn.interp_mode.half_pixel)
            self.ai2d.build([1, 3, ai2d_input_size[1], ai2d_input_size[0]],
                            [1, 3, self.model_input_size[1], self.model_input_size[0]])

    def postprocess(self, results):
        with ScopedTiming("postprocess", self.debug_mode > 0):
            post_ret = aidemo.face_det_post_process(
                self.confidence_threshold, self.nms_threshold,
                self.model_input_size[1], self.anchors, self.rgb888p_size, results)
            if len(post_ret) == 0:
                return [], []
            return post_ret[0], post_ret[1]

    def draw_result(self, osd_img, dets, recognition_results=None):
        with ScopedTiming("display_draw", self.debug_mode > 0):
            rec_map = {}
            if recognition_results:
                for det_idx, mid in recognition_results:
                    rec_map[det_idx] = mid
            if dets:
                for i, det in enumerate(dets):
                    x, y, w, h = map(lambda v: int(round(v, 0)), det[:4])
                    x = x * self.display_size[0] // self.rgb888p_size[0]
                    y = y * self.display_size[1] // self.rgb888p_size[1]
                    w = w * self.display_size[0] // self.rgb888p_size[0]
                    h = h * self.display_size[1] // self.rgb888p_size[1]
                    matched_id = rec_map.get(i)
                    color_hex = BOX_COLORS.get(matched_id, BOX_UNKNOWN) if matched_id else BOX_UNKNOWN
                    color = _draw_color(color_hex)
                    osd_img.draw_rectangle(x, y, w, h, color=color, thickness=2)
                    if matched_id is not None:
                        osd_img.draw_string_advanced(x + 2, y + 2, 16,
                                                     "ID%d" % matched_id, color=color)

    def deinit(self):
        try:
            del self.kpu
        except Exception:
            pass
        try:
            del self.ai2d
        except Exception:
            pass
        try:
            self.tensors.clear()
            del self.tensors
        except Exception:
            pass
        gc.collect()
        time.sleep_ms(50)
```

- [ ] **Step 4: Run test to verify it passes**

Run:

```bash
python tests/test_face_ai.py
```

Expected: ALL PASS.

- [ ] **Step 5: Commit**

```bash
git add core/face_ai.py tests/test_face_ai.py
git commit -m "feat(face): extract detection AI helper

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 2: Add Phase 1 template contract tests

**Files:**
- Create: `tests/test_face_detect_template.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_face_detect_template.py`:

```python
# tests/test_face_detect_template.py — host-side contracts for face_detect Phase 1.
# Run with:
#   python tests/test_face_detect_template.py
import ast
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP_PATH = os.path.join(ROOT, "scripts", "face_detect", "app.py")


def _src():
    with open(APP_PATH, "r", encoding="utf-8") as f:
        return f.read()


def _parse(path=APP_PATH):
    return ast.parse(_src(), filename=path)


def _function_node(tree, name):
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError("Function %s missing" % name)


def _module_function_names(tree):
    return {node.name for node in tree.body if isinstance(node, ast.FunctionDef)}


def _called_names(fn_node):
    names = set()
    for node in ast.walk(fn_node):
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name):
                names.add(node.func.id)
            elif isinstance(node.func, ast.Attribute):
                names.add(node.func.attr)
    return names


def test_face_detect_has_template_entry_and_on_frame():
    tree = _parse()
    run_fn = _function_node(tree, "run")
    on_frame_fn = _function_node(tree, "on_frame")
    assert "runtime" in [a.arg for a in run_fn.args.args], "run(runtime) required"
    assert "img" in [a.arg for a in on_frame_fn.args.args], "on_frame(img) required"


def test_face_detect_imports_face_detection_app_from_core():
    src = _src()
    assert "from core.face_ai import" in src, "must import from core.face_ai"
    assert "FaceDetectionApp" in src, "must use FaceDetectionApp"


def test_face_detect_has_no_threads_or_self_media_init():
    src = _src()
    forbidden = [
        "import _thread", "start_new_thread", "face_det_thread",
        "def media_init", "def lvgl_init", "def disp_drv_flush_cb",
        "Display.init", "MediaManager.init", "sensor.run",
    ]
    found = [token for token in forbidden if token in src]
    assert not found, "Phase 1 template app must not self-init media or start threads: %s" % found


def test_face_detect_phase1_excludes_registration_and_db():
    src = _src()
    forbidden = ["face_db", "id_registry", "database_search", "FaceRegistrationApp"]
    found = [token for token in forbidden if token in src]
    assert not found, "Phase 1 must not include registration/DB/recognition: %s" % found


def test_run_uses_chn0_preview_and_task_handler():
    tree = _parse()
    run_fn = _function_node(tree, "run")
    src = ast.get_source_segment(_src(), run_fn) or ""
    assert "snapshot(chn=CAM_CHN_ID_0)" in src, "run() must snapshot chn0 preview"
    assert "on_frame(img)" in src, "run() must call on_frame(img) before show_image"
    assert "Display.show_image" in src, "run() must show chn0 preview on OSD1"
    assert "lv.task_handler()" in src, "run() must service LVGL"
    assert src.find("on_frame(img)") < src.find("Display.show_image"), \
        "run loop order must be on_frame before Display.show_image"


def test_on_frame_uses_chn2_and_does_not_show_image():
    tree = _parse()
    fn = _function_node(tree, "on_frame")
    src = ast.get_source_segment(_src(), fn) or ""
    assert "snapshot(chn=CAM_CHN_ID_2)" in src, "on_frame must snapshot chn2 NPU input"
    assert ".to_numpy_ref()" in src, "on_frame must convert chn2 frame to numpy ref"
    assert ".run(img_np)" in src or ".run(" in src, "on_frame must run detector"
    assert ".draw_result(img" in src, "on_frame must draw boxes onto chn0 preview img"
    assert "Display.show_image" not in src, "on_frame must not write display directly"


def test_ai_init_and_deinit_helpers_exist_and_used():
    tree = _parse()
    funcs = _module_function_names(tree)
    assert "_init_ai" in funcs, "must initialize anchors/kmodel before main loop"
    assert "_deinit_ai" in funcs, "must clean AI after loop"
    run_fn = _function_node(tree, "run")
    called = _called_names(run_fn)
    assert "_init_ai" in called, "run() must call _init_ai"
    assert "_deinit_ai" in called, "run() must call _deinit_ai"


def test_file_io_paths_are_in_init_not_on_frame():
    tree = _parse()
    init_fn = _function_node(tree, "_init_ai")
    on_frame_fn = _function_node(tree, "on_frame")
    init_src = ast.get_source_segment(_src(), init_fn) or ""
    on_frame_src = ast.get_source_segment(_src(), on_frame_fn) or ""
    assert "prior_data_320.bin" in init_src, "_init_ai must load anchors before loop"
    assert "face_detection_320.kmodel" in init_src, "_init_ai must load kmodel before loop"
    assert "prior_data_320.bin" not in on_frame_src, "on_frame must not load anchors"
    assert "face_detection_320.kmodel" not in on_frame_src, "on_frame must not load kmodel"
    assert "open(" not in on_frame_src and "fromfile" not in on_frame_src, \
        "on_frame must not perform file I/O"


def test_template_ui_helpers_exist():
    tree = _parse()
    funcs = _module_function_names(tree)
    for name in ("_build_ui", "_destroy_ui"):
        assert name in funcs, "template UI helper missing: %s" % name
    src = _src()
    assert "人脸检测" in src, "title should be 人脸检测"
    assert "set_style_bg_opa(0" in src, "screen/preview must be transparent for OSD1"


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

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
python tests/test_face_detect_template.py
```

Expected: FAIL. The current app is still dual-thread, self-inits media, references face_db/registration, and does not follow the template contract.

- [ ] **Step 3: Commit the failing test**

```bash
git add tests/test_face_detect_template.py
git commit -m "test(face_detect): add phase1 template contracts

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 3: Rewrite `scripts/face_detect/app.py` to the single-thread template

**Files:**
- Modify: `scripts/face_detect/app.py`
- Test: `tests/test_face_detect_template.py`

- [ ] **Step 1: Replace the app with the Phase 1 template implementation**

Replace `scripts/face_detect/app.py` with:

```python
# scripts/face_detect/app.py — Phase 1 face detection on the single-thread template.
#
# Core loop: snapshot chn0 → on_frame(chn2 AI detect + draw boxes) → show_image →
# lv.task_handler. No AI thread, no self media init, no registration/DB in Phase 1.

import os
import sys
import time
import lvgl as lv
import ulab.numpy as np
from media.display import Display
from media.sensor import CAM_CHN_ID_0, CAM_CHN_ID_2
from core.icon_cache import icon_cache
from core.font_manager import fonts
from core.face_ai import FaceDetectionApp, RGB888P_SIZE, DISPLAY_SIZE

BAR_H = 52
PREVIEW_Y = BAR_H
PREVIEW_H = 376
BAR_BG = 0x1A1A1A
TITLE_TEXT = "人脸检测"

_RUNTIME = None
_screen = None
_top_bar = None
_bottom_bar = None
_preview = None
_face_det = None


def _init_ai():
    """Load anchors and detection kmodel before the first lv.task_handler()."""
    global _face_det
    anchors_path = "/sdcard/examples/utils/prior_data_320.bin"
    kmodel_path = "/sdcard/examples/kmodel/face_detection_320.kmodel"
    print("[face_detect] loading anchors...")
    anchors = np.fromfile(anchors_path, dtype=np.float)
    anchors = anchors.reshape((4200, 4))
    print("[face_detect] loading kmodel...")
    _face_det = FaceDetectionApp(kmodel_path, model_input_size=[320, 320], anchors=anchors,
                                 confidence_threshold=0.5, nms_threshold=0.2,
                                 rgb888p_size=RGB888P_SIZE, display_size=DISPLAY_SIZE,
                                 debug_mode=0)
    _face_det.config_preprocess()
    print("[face_detect] AI ready")


def _deinit_ai():
    """Best-effort AI cleanup after the main loop exits."""
    global _face_det
    if _face_det is not None:
        try:
            _face_det.deinit()
        except Exception as e:
            print("[face_detect] AI deinit warning: %s" % e)
        _face_det = None


def on_frame(img):
    """Run face detection on chn2 and draw white boxes onto the chn0 preview img."""
    if _RUNTIME is None or _face_det is None:
        return
    img_ai = _RUNTIME.sensor.snapshot(chn=CAM_CHN_ID_2)
    img_np = img_ai.to_numpy_ref()
    det_boxes, landms = _face_det.run(img_np)
    _face_det.draw_result(img, det_boxes)


def _build_ui(runtime, exit_flag):
    """Build top bar, transparent preview area, and empty bottom bar."""
    global _screen, _top_bar, _bottom_bar, _preview
    screen = lv.scr_act()
    screen.set_style_bg_opa(0, 0)
    _screen = screen

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
    btn.set_size(48, 48)
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
        target = int(48 * 0.85)
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

    title = lv.label(_top_bar)
    title.set_text(TITLE_TEXT)
    title.align(lv.ALIGN.CENTER, 0, 0)
    from ui.theme import make_back_bar_text_style
    title.add_style(make_back_bar_text_style(fonts.body), 0)

    _preview = lv.obj(screen)
    _preview.set_size(lv.pct(100), PREVIEW_H)
    _preview.set_pos(0, PREVIEW_Y)
    _preview.set_style_bg_opa(0, 0)
    _preview.set_style_border_width(0, 0)
    _preview.set_style_pad_all(0, 0)
    _preview.set_style_radius(0, 0)
    _preview.clear_flag(lv.obj.FLAG.SCROLLABLE)
    _preview.clear_flag(lv.obj.FLAG.CLICKABLE)

    _bottom_bar = lv.obj(screen)
    _bottom_bar.set_size(lv.pct(100), BAR_H)
    _bottom_bar.set_pos(0, PREVIEW_Y + PREVIEW_H)
    _bottom_bar.set_style_bg_color(lv.color_hex(BAR_BG), 0)
    _bottom_bar.set_style_bg_opa(255, 0)
    _bottom_bar.set_style_border_width(0, 0)
    _bottom_bar.set_style_pad_all(0, 0)
    _bottom_bar.set_style_radius(0, 0)
    _bottom_bar.clear_flag(lv.obj.FLAG.SCROLLABLE)


def _destroy_ui():
    """Delete LVGL objects and restore screen opacity for the menu."""
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
    """Entry point called by reset-framework main.py."""
    global _RUNTIME
    _RUNTIME = runtime
    exit_flag = [False]
    _init_ai()
    _build_ui(runtime, exit_flag)
    fc = 0
    try:
        while not exit_flag[0]:
            os.exitpoint()
            img = runtime.sensor.snapshot(chn=CAM_CHN_ID_0)
            try:
                on_frame(img)
            except Exception as e:
                print("[face_detect] on_frame error: %s" % e)
                try:
                    sys.print_exception(e)
                except Exception:
                    pass
            Display.show_image(img, 0, 0, Display.LAYER_OSD1)
            time.sleep_ms(lv.task_handler())
            fc += 1
            if fc % 30 == 0:
                print("[face_detect] fc=%d" % fc)
    finally:
        _deinit_ai()
        _destroy_ui()
        _RUNTIME = None
```

- [ ] **Step 2: Run Phase 1 template tests**

Run:

```bash
python tests/test_face_detect_template.py
```

Expected: ALL PASS.

- [ ] **Step 3: Run extracted AI tests**

Run:

```bash
python tests/test_face_ai.py
```

Expected: ALL PASS.

- [ ] **Step 4: Commit**

```bash
git add scripts/face_detect/app.py
git commit -m "feat(face_detect): migrate phase1 to template loop

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 4: Normalize framework path for `face_detect`

**Files:**
- Modify: `tests/test_framework.py`
- Modify: `main.py`
- Modify: `core/app_runtime.py`

- [ ] **Step 1: Add failing framework tests**

Append these tests before the `if __name__ == "__main__":` block in `tests/test_framework.py`:

```python

def test_main_does_not_special_case_face_detect_init():
    """face_detect Phase 1 uses normal runtime.init_app path, not naked self-init."""
    src = open(MAIN_PATH, encoding="utf-8").read()
    assert 'category_id == "face_detect"' not in src and "category_id == 'face_detect'" not in src, \
        "main.py must not skip init_app for face_detect"
    assert "runtime.init_app(category_id, fpioa)" in src, \
        "run_script must initialize scripts through runtime.init_app(category_id, fpioa)"


def test_main_cleanup_does_not_skip_face_detect():
    src = open(MAIN_PATH, encoding="utf-8").read()
    assert 'category_id != "face_detect"' not in src and "category_id != 'face_detect'" not in src, \
        "runtime.cleanup() must not skip face_detect after template migration"
    assert "runtime.cleanup()" in src, "run_script must call runtime.cleanup()"


def test_app_runtime_face_detect_uses_only_chn0_and_chn2():
    src = open(RUNTIME_PATH, encoding="utf-8").read()
    start = src.find("def _channels_for")
    assert start != -1, "_channels_for missing"
    body = src[start:]
    face_pos = body.find('category_id == "face_detect"')
    assert face_pos != -1, "_channels_for must special-case face_detect channel setup"
    face_block = body[face_pos:body.find("elif", face_pos) if body.find("elif", face_pos) != -1 else len(body)]
    assert "CAM_CHN_ID_2" in face_block, "face_detect must declare chn2 before MediaManager.init"
    assert "CAM_CHN_ID_1" not in face_block, "Phase 1 face_detect must not allocate unused chn1"
```

- [ ] **Step 2: Run framework tests to verify failure**

Run:

```bash
python tests/test_framework.py
```

Expected: FAIL because `main.py` still has a face_detect special case and cleanup skip.

- [ ] **Step 3: Modify `main.py`**

In `run_script(category_id)`, replace the face_detect branch:

```python
    # face_detect 走裸跑基线：run() 自带 media_init/lvgl_init（已板端验证稳定），
    # 不能再调 runtime.init_app（否则 Display/Sensor/MediaManager 双重 init →
    # OSError: sensor(2) is already inited）。但仍需 runtime.fpioa 供 IdRegistry
    # 配置 K2(GPIO0)；buzzer=None，id_registry 静默守卫。buzzer 接回留作单独实验。
    # 其他脚本（迁移后）走完整 runtime.init_app。
    if category_id == "face_detect":
        runtime.fpioa = fpioa
    else:
        runtime.init_app(category_id, fpioa)
```

with:

```python
    runtime.init_app(category_id, fpioa)
```

Then replace the cleanup skip:

```python
    # 统一 deinit：非 face_detect 脚本由 runtime.cleanup() 释放硬件
    # （face_detect 搁置，自管 media，不调 cleanup 避免冲突）
    if category_id != "face_detect":
        try:
            runtime.cleanup()
        except Exception as e:
            print("[CamerAi] cleanup error: %s" % e)
```

with:

```python
    try:
        runtime.cleanup()
    except Exception as e:
        print("[CamerAi] cleanup error: %s" % e)
```

- [ ] **Step 4: Modify `core/app_runtime.py` channel setup if needed**

Ensure `_channels_for` reads exactly as below for the face_detect branch:

```python
    def _channels_for(self, category_id):
        """按 category 决定 sensor 通道配置。"""
        chs = [(CAM_CHN_ID_0, Sensor.VGA, Sensor.RGB888)]
        if category_id == "face_detect":
            chs.append((CAM_CHN_ID_2, Sensor.XGA, Sensor.RGBP888))
        elif category_id == "camera":
            chs.append((CAM_CHN_ID_1, Sensor.SXGAM, Sensor.RGB565))
        elif category_id == "_template":
            pass  # 模板纯显示，单通道 chn0（复用默认）
        return chs
```

- [ ] **Step 5: Run framework tests**

Run:

```bash
python tests/test_framework.py
```

Expected: ALL PASS.

- [ ] **Step 6: Run face_detect template tests**

Run:

```bash
python tests/test_face_detect_template.py
```

Expected: ALL PASS.

- [ ] **Step 7: Commit**

```bash
git add main.py core/app_runtime.py tests/test_framework.py
git commit -m "fix(face_detect): use normal runtime init path

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 5: Retire or update obsolete old face_detect tests

**Files:**
- Modify: `tests/test_face_detect.py`

- [ ] **Step 1: Run old tests to identify contradictions**

Run:

```bash
python tests/test_face_detect.py
```

Expected: FAIL. The old file asserts the old dual-thread baseline (`face_det_thread`, `_thread.start_new_thread`, face_db, face_reg), which is now intentionally obsolete.

- [ ] **Step 2: Replace old test file with compatibility wrapper**

Replace `tests/test_face_detect.py` with:

```python
# tests/test_face_detect.py — compatibility runner for the current face_detect phase.
#
# Historical tests for the old dual-thread face_detect baseline were retired when
# Phase 1 migrated to the single-thread template. The active contracts live in:
#   - tests/test_face_detect_template.py
#   - tests/test_face_ai.py
#
# Run with:
#   python tests/test_face_detect.py

import os
import runpy

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


if __name__ == "__main__":
    runpy.run_path(os.path.join(ROOT, "tests", "test_face_ai.py"), run_name="__main__")
    runpy.run_path(os.path.join(ROOT, "tests", "test_face_detect_template.py"), run_name="__main__")
```

If this wrapper exits after the first file because `test_face_ai.py` calls `sys.exit`, instead replace it with a short pointer test runner:

```python
# tests/test_face_detect.py — retired old baseline tests.
# Run active face_detect tests directly:
#   python tests/test_face_ai.py
#   python tests/test_face_detect_template.py

print("tests/test_face_detect.py retired; run test_face_ai.py and test_face_detect_template.py")
```

Use the first version only if it works on host. Use the second version if `sys.exit` prevents the second suite from running.

- [ ] **Step 3: Run active tests**

Run:

```bash
python tests/test_face_ai.py
python tests/test_face_detect_template.py
python tests/test_framework.py
```

Expected: ALL PASS for all three.

- [ ] **Step 4: Run old compatibility file**

Run:

```bash
python tests/test_face_detect.py
```

Expected: Either the wrapper runs successfully, or the retired pointer message prints without failure.

- [ ] **Step 5: Commit**

```bash
git add tests/test_face_detect.py
git commit -m "test(face_detect): retire old dual-thread contracts

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 6: Full host regression and syntax checks

**Files:**
- No source changes expected.

- [ ] **Step 1: Run Phase 1 face tests**

Run:

```bash
python tests/test_face_ai.py
python tests/test_face_detect_template.py
python tests/test_framework.py
```

Expected: ALL PASS.

- [ ] **Step 2: Run adjacent migrated app tests**

Run:

```bash
python tests/test_camera.py
python tests/test_camera_gallery.py
```

Expected: ALL PASS.

- [ ] **Step 3: Parse changed Python files**

Run:

```bash
python -c "import ast, pathlib; files=['core/face_ai.py','scripts/face_detect/app.py','main.py','core/app_runtime.py','tests/test_face_ai.py','tests/test_face_detect_template.py','tests/test_framework.py']; [ast.parse(pathlib.Path(f).read_text(encoding='utf-8'), filename=f) for f in files]; print('AST OK')"
```

Expected: `AST OK`.

- [ ] **Step 4: Check git status**

Run:

```bash
git status --short
```

Expected: clean, or only intentionally uncommitted documentation from Task 7.

---

### Task 7: Update project record after host verification

**Files:**
- Modify: `项目记录.md`

- [ ] **Step 1: Append implementation record**

Append this section to `项目记录.md` after host tests pass:

```markdown

## 2026-06-23 face_detect Phase 1 单线程模板迁移实现（host 验证）

- spec：`docs/superpowers/specs/2026-06-23-face-detect-template-phase1-design.md`
- plan：`docs/superpowers/plans/2026-06-23-face-detect-template-phase1.md`
- 文件：`core/face_ai.py`
  - 从旧 `scripts/face_detect/app.py` 抽出检测复用逻辑：`ALIGN_UP`、`_draw_color`、`FaceDetectionApp`。
  - 保留 K230 draw 颜色 `(A,B,G,R)` 转换；保留 `draw_result(..., recognition_results=None)` 供后续 Phase 3 彩框/ID 复用。
- 文件：`scripts/face_detect/app.py`
  - 改为 `_template` 单线程 `run(runtime)+on_frame(img)`：`snapshot chn0 → on_frame(chn2 AI detect + 画白框) → Display.show_image(OSD1) → lv.task_handler()`。
  - 删除旧裸跑 self-init media/LVGL、`_thread`、face_db、face_reg、id_registry；Phase 1 只做白框检测。
  - anchors/kmodel 在首次 `lv.task_handler()` 前加载，on_frame 内零文件 I/O。
- 文件：`main.py`
  - 删除 `face_detect` 跳过 `runtime.init_app`/`cleanup` 的特殊分支；face_detect 统一走 reset 框架 init/cleanup。
- 文件：`core/app_runtime.py`
  - face_detect 通道保持 chn0 预览 + chn2 NPU 输入，不分配旧 chn1。
- 测试：
  - `tests/test_face_ai.py`
  - `tests/test_face_detect_template.py`
  - `tests/test_framework.py`
  - 旧 `tests/test_face_detect.py` 双线程契约已退役。
- host 验证：
  - `python tests/test_face_ai.py` → ALL PASS
  - `python tests/test_face_detect_template.py` → ALL PASS
  - `python tests/test_framework.py` → ALL PASS
  - `python tests/test_camera.py` → ALL PASS
  - `python tests/test_camera_gallery.py` → ALL PASS
  - changed files AST → OK
- 板端待验收：启动 face_detect → 顶栏/底栏/预览正常 → 对脸白框 → fc 2-5 分钟持续增长不卡 → UI 不消失 → 返回菜单 → 反复进出 3 次稳定。
```

- [ ] **Step 2: Commit project record**

```bash
git add 项目记录.md
git commit -m "docs(face_detect): record phase1 host verification

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 8: Board deployment and acceptance

**Files:**
- Deploy to board:
  - `main.py`
  - `core/app_runtime.py`
  - `core/face_ai.py`
  - `scripts/face_detect/app.py`

- [ ] **Step 1: Deploy files to `/sdcard/CamerAi`**

Copy changed files to the board:

```text
main.py                                      → /sdcard/CamerAi/main.py
core/app_runtime.py                         → /sdcard/CamerAi/core/app_runtime.py
core/face_ai.py                             → /sdcard/CamerAi/core/face_ai.py
scripts/face_detect/app.py                  → /sdcard/CamerAi/scripts/face_detect/app.py
```

- [ ] **Step 2: Hard power cycle**

Power off/on to ensure clean Display/Sensor/MediaManager state.

- [ ] **Step 3: Verify launch path**

Expected serial markers:

```text
[CamerAi] script mode: face_detect
[CamerAi] run_script start: face_detect
[CamerAi] calling mod.run(runtime)...
[face_detect] loading anchors...
[face_detect] loading kmodel...
[face_detect] AI ready
```

Expected absence:

```text
OSError: sensor(2) is already inited
start_new_thread
face_db
id_registry
```

- [ ] **Step 4: Verify UI and detection**

Board checklist:

```text
[ ] 顶栏显示返回按钮 + 人脸检测
[ ] 底栏显示
[ ] 摄像头预览正常透出
[ ] 无人脸时 fc 每 30 帧继续打印
[ ] 对脸后出现白框，且跟随人脸
[ ] 跑 2-5 分钟没有 fc~20/30 卡死
[ ] UI 不消失，不只剩 OSD1 裸画面
[ ] 点击返回回主菜单
[ ] 连续进出 face_detect 3 次稳定
```

- [ ] **Step 5: Record board result**

If board passes, append pass result to `项目记录.md` and commit:

```bash
git add 项目记录.md
git commit -m "docs(face_detect): record phase1 board acceptance

Co-Authored-By: Claude <noreply@anthropic.com>"
```

If board fails, do not patch blindly. Invoke `systematic-debugging` and follow root-cause investigation before changing code.

---

## Self-Review

- Spec coverage: All Phase 1 requirements are covered: `core/face_ai.py`, template app, normal runtime init/cleanup, chn2 NPU input, no registration/DB, host tests, board acceptance.
- Placeholder scan: No `TBD`, `TODO`, or open-ended implementation placeholders remain. Later phases are explicitly out of scope.
- Type consistency: Function names are consistent across tasks: `_init_ai`, `_deinit_ai`, `on_frame(img)`, `run(runtime)`, `FaceDetectionApp.draw_result(..., recognition_results=None)`.
