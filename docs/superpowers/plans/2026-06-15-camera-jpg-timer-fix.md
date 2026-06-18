# Camera: JPG Capture + Gallery Thumbnail Fix + Timer Reposition

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Change photo capture from BMP to JPG format, fix gallery thumbnails to decode JPG as raw pixels (LVGL on K230 lacks `LV_USE_JPEG`), and move the recording countdown timer from the top of the preview area to the right of the shutter button.

**Architecture:** Capture saves `.jpg` via `img.save(path)` (K230 RGB565 channel supports JPG save). Gallery thumbnails decode JPG via `image.Image(path)` → `to_rgb888()` → raw-pixel `lv.img_dsc_t` (header set manually since LVGL can't auto-detect raw pixel format). Timer label created in `_build_bottom_bar()` instead of `_build_preview_area()`, positioned right of shutter with `align_to()`.

**Tech Stack:** K230 MicroPython, LVGL v8, image module (K230 hardware JPEG decoder)

---

### Task 1: Update test expectations for JPG capture

**Files:**
- Modify: `tests/test_camera_gallery.py:143-181`

- [ ] **Step 1: Rewrite `test_photo_capture_saves_as_bmp_not_jpeg` → `test_photo_capture_saves_as_jpg`**

Replace the test function (lines 143-181) with:

```python
def test_photo_capture_saves_as_jpg():
    """_capture_photo must save as .jpg because user wants JPEG format.
    LVGL on K230 lacks LV_USE_JPEG, so gallery thumbnails decode JPG
    via image.Image() into raw pixels for display."""
    tree = _camera_app_tree()
    capture_method = _method_node(_camera_app_class(tree), "_capture_photo")

    found_jpg = False
    found_bmp = False

    for node in ast.walk(capture_method):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            val = node.value
            if val.endswith('.jpg') or val.endswith('.jpeg'):
                found_jpg = True
            if val.endswith('.bmp'):
                found_bmp = True

    assert found_jpg, (
        "_capture_photo must save as .jpg — "
        "user wants JPEG format for photos"
    )
    assert not found_bmp, (
        "_capture_photo must NOT save as .bmp — "
        "user wants JPEG format, not BMP"
    )
```

- [ ] **Step 2: Run test to verify it fails**

```bash
python tests/test_camera_gallery.py
```
Expected: FAIL on `test_photo_capture_saves_as_jpg` (still saves as `.bmp`)

---

### Task 2: Update test expectations for thumbnail loader

**Files:**
- Modify: `tests/test_camera_gallery.py:50-113`

- [ ] **Step 1: Rewrite thumbnail loader test to allow `image.Image()`**

Replace `test_thumbnail_loader_uses_raw_file_bytes_not_image_module` (lines 50-95) with:

```python
def test_thumbnail_loader_decodes_jpg_via_image_module():
    """_load_thumbnail must use image.Image() to decode JPG files into
    raw RGB888 pixels, since LVGL on K230 lacks LV_USE_JPEG.
    Raw file bytes go to image.Image() for decode, NOT to LVGL decoder."""
    tree = _camera_app_tree()
    load_method = _method_node(_camera_app_class(tree), "_load_thumbnail")

    attr_calls = []
    name_calls = []
    for node in ast.walk(load_method):
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Attribute):
                attr_calls.append(node.func.attr)
            elif isinstance(node.func, ast.Name):
                name_calls.append(node.func.id)

    # Must use image.Image() for JPG decode → raw pixels
    assert "Image" in attr_calls, (
        "_load_thumbnail must call image.Image() to decode JPG photos — "
        "LVGL on K230 lacks LV_USE_JPEG, cannot auto-decode JPG"
    )
    # Must call to_rgb888() to get raw pixel bytes for lv.img_dsc_t
    assert "to_rgb888" in attr_calls, (
        "_load_thumbnail must call img.to_rgb888() to get raw RGB888 bytes "
        "for raw-pixel lv.img_dsc_t"
    )
    # Must use open() to verify file exists before decode
    assert "open" in name_calls, (
        "_load_thumbnail must use open() to read raw image bytes (pre-check)"
    )
```

Replace `test_camera_app_does_not_import_image_module` (lines 105-113) with:

```python
def test_camera_app_imports_image_module():
    """camera app MUST import 'image' — _load_thumbnail uses image.Image()
    to decode JPG photos into raw RGB888 pixels for LVGL display, since
    LVGL on K230 lacks LV_USE_JPEG built-in decoder."""
    tree = _camera_app_tree()
    imports = _top_level_imports(tree)
    assert "image" in imports, (
        "camera app must import image module — "
        "_load_thumbnail uses image.Image() + to_rgb888() "
        "to decode JPG → raw pixels for lv.img_dsc_t"
    )
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python tests/test_camera_gallery.py
```
Expected: FAIL on both updated tests (code still uses old approach)

---

### Task 3: Change `_capture_photo()` to save as JPG

**Files:**
- Modify: `scripts/camera/app.py:486-520`

- [ ] **Step 1: Change file extension from `.bmp` to `.jpg`**

Edit `scripts/camera/app.py`, line 498:

```python
# Old:
fname = f"IMG_{t[0]:04d}{t[1]:02d}{t[2]:02d}_{t[3]:02d}{t[4]:02d}{t[5]:02d}.bmp"

# New:
fname = f"IMG_{t[0]:04d}{t[1]:02d}{t[2]:02d}_{t[3]:02d}{t[4]:02d}{t[5]:02d}.jpg"
```

- [ ] **Step 2: Update the comment block (lines 504-507)**

Replace lines 504-507:

```python
# Old:
            # chn1 = SXGAM/RGB565（支持 jpg/bmp save）。首帧偶发未就绪，
            # 短重试几次；持久失败才上报（配置正确时一般首次即成功）。
            # 保存为 BMP：LVGL 内置 BMP 解码器总是可用（JPEG 需 LV_USE_JPEG，
            # K230 固件未编译），避免缩略图空白。

# New:
            # chn1 = VGA/RGB565（支持 jpg save）。首帧偶发未就绪，
            # 短重试几次；持久失败才上报（配置正确时一般首次即成功）。
            # 保存为 JPG：文件体积小。图库通过 image.Image() 软解码
            # 为 raw RGB888 像素再交给 LVGL 渲染（K230 固件未编译 LV_USE_JPEG）。
```

- [ ] **Step 3: Run test to verify capture format**

```bash
python tests/test_camera_gallery.py
```
Expected: `test_photo_capture_saves_as_jpg` PASS; thumbnail tests still FAIL

---

### Task 4: Rewrite `_load_thumbnail()` to decode JPG via `image.Image()`

**Files:**
- Modify: `scripts/camera/app.py:555-604`
- Modify: `scripts/camera/app.py:14` (add import)

- [ ] **Step 1: Add `import image` at top of file**

After line 14 (`import struct`), add:

```python
import image as _image_lib      # K230 JPEG 硬件解码（lv.task_handler 外安全）
```

- [ ] **Step 2: Replace `_load_thumbnail()` method body (lines 555-604)**

Replace from line 555 to line 604 with:

```python
    def _load_thumbnail(self, path):
        """加载照片缩略图，构造 raw-pixel LVGL img_dsc_t。

        K230 固件未编译 LV_USE_JPEG，LVGL 内置解码器无法解 JPG。
        因此走 raw-pixel 路径：image.Image() 硬件解码 → to_rgb888()
        → 手动构造 img_dsc_t header（CF_TRUE_COLOR + w/h）→ LVGL 直接
        拷贝像素，无需解码器。

        image.Image() 在此处由 _enter_gallery() 调用，此时相机 UI 已隐藏、
        屏幕 bg 为纯色，显示 DMA 空闲，不与 SD 文件 I/O 竞争。

        Returns: dict {pixel_data, w, h, img_dsc} 或 None
        """
        import os as _os

        try:
            _os.exitpoint()
            low = path.lower()

            if not (low.endswith('.jpg') or low.endswith('.jpeg')
                    or low.endswith('.bmp')):
                return None

            # ── image.Image() 硬件解码 JPG/BMP → raw RGB888 ──
            img = _image_lib.Image(path)
            w = img.width()
            h = img.height()

            if w <= 0 or h <= 0:
                print(f"[Gallery] bad dimensions in: {path}")
                return None

            raw = img.to_rgb888()
            if not raw:
                print(f"[Gallery] to_rgb888 empty: {path}")
                return None

            # ── 构造 raw-pixel lv.img_dsc_t ──
            # LVGL v8 CF_TRUE_COLOR = 4 (RGB888)
            CF_TRUE_COLOR = getattr(lv, 'CF', None)
            if CF_TRUE_COLOR is not None:
                cf = CF_TRUE_COLOR.TRUE_COLOR
            else:
                cf = 4  # LV_IMG_CF_TRUE_COLOR

            dsc = lv.img_dsc_t()
            dsc.header.cf = cf
            dsc.header.w = w
            dsc.header.h = h
            dsc.data_size = len(raw)
            dsc.data = raw

            _os.exitpoint()
            # 缩略图显示目标尺寸（LVGL lv.img.set_size 缩放渲染）
            tw, th = self._fit_thumb_size(w, h)
            return {
                'pixel_data': raw,     # GC 保活：raw bytes 被 img_dsc.data 引用
                'w': tw,
                'h': th,
                'img_dsc': dsc,
            }

        except Exception as e:
            print(f"[Gallery] load thumbnail failed for {path}: {e}")
            import sys
            try:
                sys.print_exception(e)
            except Exception:
                pass
            return None
```

- [ ] **Step 3: Run tests to verify thumbnail tests pass**

```bash
python tests/test_camera_gallery.py
```
Expected: all tests PASS

---

### Task 5: Move recording timer from preview area to bottom bar (right of shutter)

**Files:**
- Modify: `scripts/camera/app.py:301-330` (remove timer creation)
- Modify: `scripts/camera/app.py:385-420` (add timer after shutter)

- [ ] **Step 1: Remove timer creation from `_build_preview_area()`**

Delete lines 301-330 (the entire timer creation block inside `_build_preview_area`). The method should end after setting `self._preview_bg = preview` (line 299).

```python
# Remove lines 301-330 — the timer block starting with:
        # 录制计时器（初始隐藏）
        timer = lv.label(preview)
        ...
        self._timer_label = timer
```

- [ ] **Step 2: Add timer creation to `_build_bottom_bar()` after shutter button**

After line 385 (`self._shutter_btn = shutter_btn`) and before line 387 (`# ── 模式按钮（右）──`), insert:

```python
        # ── 录制计时器（快门右侧，初始隐藏）──
        timer = lv.label(bar)
        timer.set_text("")
        timer.align_to(shutter_btn, lv.ALIGN.OUT_RIGHT_MID, 16, 0)
        timer_style = make_back_bar_text_style(fonts.body)
        timer.add_style(timer_style, 0)
        timer.set_style_text_color(lv.color_hex(RED), 0)
        timer.set_style_text_opa(255, 0)
        # 清除默认主题的描边/阴影（避免红字带黑边）
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
        self._timer_label = timer
```

- [ ] **Step 3: Verify timer reference in `_enter_gallery()` still works**

The `_enter_gallery()` method at line 761 hides timer:
```python
if self._timer_label is not None:
    self._timer_label.add_flag(lv.obj.FLAG.HIDDEN)
```
This still works — `_timer_label` is now a child of `_bottom_bar`, and hiding the parent also hides children. The explicit hide is a no-op but harmless. No change needed.

- [ ] **Step 4: Verify timer reference in `_show_timer()` and `_update_timer()` still work**

Both methods (lines 712-738) operate on `self._timer_label` directly using `set_text()`, `clear_flag(FLAG.HIDDEN)`, `add_flag(FLAG.HIDDEN)`. These work the same regardless of parent. No change needed.

- [ ] **Step 5: Run full test suite**

```bash
python tests/test_camera_gallery.py
```
Expected: all tests PASS

---

### Task 6: Final verification — run full test suite

**Files:**
- (none modified, verification only)

- [ ] **Step 1: Run all camera tests**

```bash
python tests/test_camera_gallery.py
```
Expected output:
```
PASS test_camera_app_defines_delete_reflow_helper
PASS test_camera_app_imports_image_module
PASS test_delete_handler_uses_reflow_helper_and_rebuilds_ui
PASS test_photo_capture_saves_as_jpg
PASS test_thumbnail_loader_decodes_jpg_via_image_module

ALL PASS
```

- [ ] **Step 2: Commit all changes**

```bash
git add scripts/camera/app.py tests/test_camera_gallery.py
git commit -m "feat(camera): save JPG, decode thumbs via image.Image, timer → shutter right

- _capture_photo(): .bmp → .jpg (K230 RGB565 chn supports JPG save)
- _load_thumbnail(): image.Image() + to_rgb888() → raw-pixel img_dsc_t
  (LVGL K230 lacks LV_USE_JPEG, cannot auto-decode JPG)
- Timer label: moved from preview TOP_MID → bottom bar right of shutter
  (align_to(OUT_RIGHT_MID))
- Tests updated: JPG format, image.Image import, to_rgb888() calls"
```

---

### Summary of changes

| File | Lines | Change |
|------|-------|--------|
| `scripts/camera/app.py` | +15 | Add `import image` |
| `scripts/camera/app.py` | 498 | `.bmp` → `.jpg` |
| `scripts/camera/app.py` | 504-507 | Update comment |
| `scripts/camera/app.py` | 555-604 | Rewrite `_load_thumbnail()` → raw-pixel decode |
| `scripts/camera/app.py` | 301-330 | Remove timer from `_build_preview_area()` |
| `scripts/camera/app.py` | +385 | Add timer to `_build_bottom_bar()` (shutter right) |
| `tests/test_camera_gallery.py` | 50-113 | Rewrite thumbnail tests for image.Image() |
| `tests/test_camera_gallery.py` | 143-181 | Rewrite capture test for JPG |
