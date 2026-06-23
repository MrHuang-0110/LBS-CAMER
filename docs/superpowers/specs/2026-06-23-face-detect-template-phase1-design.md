# Face Detect Template Phase 1 Design

## Goal

Migrate `face_detect` to the proven `_template` single-thread `run(runtime)` pattern and restore stable face detection only. Phase 1 draws white detection boxes on the camera preview; it does not implement ID registration, feature matching, or clearing saved records.

## Background

`settings` and `camera` now use the reset framework successfully. `face_detect` is still the remaining special-case script: `main.py` skips `runtime.init_app("face_detect")`, and `scripts/face_detect/app.py` performs its own Display/Sensor/MediaManager/LVGL initialization in an official-style dual-thread baseline.

That old face_detect shape is intentionally shelved. Its AI thread performs sensor snapshots, NPU inference, drawing, and `Display.show_image(..., LAYER_OSD1)`, while the main thread runs `lv.task_handler()` and flushes OSD2. This reintroduces the dual-writer display DMA competition that caused the historical `fc~20-35` hangs.

The `_template` app has already been board-validated as a stable AI script base. Its key property is a single serial loop:

```python
img = runtime.sensor.snapshot(chn=CAM_CHN_ID_0)
try:
    on_frame(img)
except Exception as e:
    print("[template] on_frame error: %s" % e)
Display.show_image(img, 0, 0, Display.LAYER_OSD1)
time.sleep_ms(lv.task_handler())
```

Phase 1 uses that structure for face detection.

## Scope

### In scope

- Replace `scripts/face_detect/app.py` with a `_template`-style `run(runtime)` script.
- Use `on_frame(img)` to run face detection inline on the main loop.
- Draw white boxes for detected faces on the chn0 preview image.
- Extract reusable AI code into `core/face_ai.py`.
- Remove the `face_detect` skip path in `main.py` so it uses `runtime.init_app("face_detect", fpioa)` and `runtime.cleanup()` like other apps.
- Keep `_channels_for("face_detect")` in `core/app_runtime.py` as chn0 preview + chn2 NPU input.
- Add host-side AST contract tests for the migration and framework constraints.

### Out of scope

- ID registration.
- `FaceRegistrationApp` usage in the app.
- Feature matching / `database_search` in the app.
- Loading or writing `core.face_db` data.
- `core.id_registry` / K2 registration.
- Clearing saved face records.
- UART reporting.
- Performance optimization beyond avoiding the known deadlock class.

Those belong to later phases after Phase 1 board validation.

## Architecture

### Runtime path

`main.py` should treat `face_detect` as a normal reset-framework app:

```python
runtime = AppRuntime()
runtime.init_app(category_id, fpioa)
mod = _load_script(category_id)
mod.run(runtime)
runtime.cleanup()
_clear_next_script()
machine.reset()
```

No `face_detect` branch should skip `init_app` or `cleanup`.

### Sensor channels

`core/app_runtime.py` already has the correct category shape:

- chn0: `Sensor.VGA`, `Sensor.RGB888` for the display preview.
- chn2: `Sensor.XGA`, `Sensor.RGBP888` for NPU input.

Phase 1 must not add chn1 for face_detect. chn1 was present in the old baseline but not used for detection.

### `scripts/face_detect/app.py`

The app follows `_template` directly:

- Top bar with back button and title `人脸检测`.
- Transparent preview area so OSD1 camera frames show through.
- Empty bottom bar for Phase 1.
- `_build_ui(runtime, exit_flag)` creates LVGL objects.
- `_destroy_ui()` deletes LVGL objects and restores screen opacity.
- `run(runtime)` owns all initialization, loop, AI cleanup, and UI cleanup.
- `on_frame(img)` only performs detection and draws on the provided `img`.

Expected loop shape:

```python
def run(runtime):
    global _RUNTIME
    _RUNTIME = runtime
    exit_flag = [False]
    _init_ai()
    _build_ui(runtime, exit_flag)
    while not exit_flag[0]:
        os.exitpoint()
        img = runtime.sensor.snapshot(chn=CAM_CHN_ID_0)
        try:
            on_frame(img)
        except Exception as e:
            print("[face_detect] on_frame error: %s" % e)
        Display.show_image(img, 0, 0, Display.LAYER_OSD1)
        time.sleep_ms(lv.task_handler())
    _deinit_ai()
    _destroy_ui()
```

`on_frame(img)` gets the NPU input from chn2:

```python
def on_frame(img):
    img_ai = _RUNTIME.sensor.snapshot(chn=CAM_CHN_ID_2)
    img_np = img_ai.to_numpy_ref()
    det_boxes, landms = _face_det.run(img_np)
    _face_det.draw_result(img, det_boxes)
```

`on_frame` must not call `Display.show_image`, create/delete LVGL objects, read/write files, or start threads.

### `core/face_ai.py`

Create `core/face_ai.py` as the reusable AI module for Phase 1 and later phases. Phase 1 requires:

- `ALIGN_UP(x, align=16)`.
- `_draw_color(hex_color)` converting `0xRRGGBB` into the K230 draw tuple `(0xFF, B, G, R)`.
- `FaceDetectionApp`, moved from the existing face_detect implementation with the same preprocess/postprocess/draw_result behavior.

`FaceDetectionApp.draw_result(osd_img, dets, recognition_results=None)` should keep the current signature so Phase 3 can reuse it for ID-colored boxes. In Phase 1, callers pass only `dets`, and every face is drawn with the unknown/white color.

`FaceRegistrationApp` can remain out of `core/face_ai.py` for Phase 1, or can be moved later in Phase 3. Phase 1 tests only require `FaceDetectionApp`.

## K230 safety constraints

### File I/O timing

All file I/O must happen before the first `lv.task_handler()` call:

- anchors: `/sdcard/examples/utils/prior_data_320.bin`
- kmodel: `/sdcard/examples/kmodel/face_detection_320.kmodel`

`_init_ai()` runs in `run()` before `_build_ui()` and before entering the main loop. `on_frame()` performs zero file I/O.

### Single display writer

Phase 1 must not import `_thread` or start any AI thread. The only loop is the `run(runtime)` loop. `Display.show_image(..., LAYER_OSD1)` happens once per frame after `on_frame()` returns. LVGL OSD2 flushing happens only inside `lv.task_handler()` in the same thread.

### LVGL object lifetime

`on_frame()` must not create or delete LVGL objects. UI objects are created in `_build_ui()` and deleted in `_destroy_ui()` only. This avoids runtime LVGL finalizers and use-after-free patterns.

### GC strategy

The old dual-thread implementation called `gc.collect()` every AI-thread frame to avoid NPU/native buffer accumulation. However, runtime `gc.collect()` can also trigger LVGL object finalizers and has caused display/DMA deadlocks in past investigations.

Phase 1 should not add per-frame `gc.collect()` preemptively. If board validation shows a gradual NPU stall, debug that separately and introduce the smallest verified GC strategy with a dedicated test and board check.

## Error handling

- `_init_ai()` should fail loudly during startup if anchors or kmodel load fails. It may print the exception and re-raise so the reset framework returns to the menu instead of running a half-initialized loop.
- `on_frame()` exceptions are caught by the main loop and printed as `[face_detect] on_frame error: ...`; the preview loop continues.
- `_deinit_ai()` should be best-effort and safe if `_face_det` was never initialized.
- `_destroy_ui()` should be best-effort and restore the LVGL screen background opacity to 255.

## Host-side tests

Add `tests/test_face_detect_template.py` with AST/string contract tests:

- `scripts/face_detect/app.py` defines module-level `run(runtime)`.
- It defines `on_frame(img)`.
- It imports/uses `FaceDetectionApp` from `core.face_ai`.
- It does not import `_thread`.
- It does not define or call `media_init`, `lvgl_init`, or `disp_drv_flush_cb`.
- It does not call `Display.init`, `MediaManager.init`, or `sensor.run`.
- It does not call `Display.show_image` inside `on_frame()`.
- It snapshots chn0 in `run()` and chn2 in `on_frame()`.
- It calls `lv.task_handler()` in the main loop.
- It has `_init_ai()` and `_deinit_ai()` helpers.
- It does not reference `face_db`, `id_registry`, or `database_search` in Phase 1 app code.

Extend `tests/test_framework.py`:

- `main.py` no longer has a `category_id == "face_detect"` branch that skips `runtime.init_app`.
- `runtime.cleanup()` is not skipped for `face_detect`.
- `_channels_for("face_detect")` includes `CAM_CHN_ID_2`.
- `_channels_for("face_detect")` does not add `CAM_CHN_ID_1`.

Add `tests/test_face_ai.py`:

- `core/face_ai.py` defines `FaceDetectionApp`.
- `core/face_ai.py` defines `ALIGN_UP`.
- `core/face_ai.py` preserves the ABGR draw color conversion `(0xFF, b, g, r)`.
- `FaceDetectionApp.draw_result` accepts `recognition_results=None` for future Phase 3 compatibility.

## Board acceptance

Deploy the Phase 1 files and hard power cycle. Then verify:

1. Main menu launches `face_detect` through the normal `runtime.init_app` path.
2. No `sensor already inited` error.
3. Top bar shows back button + `人脸检测`.
4. Bottom bar is visible.
5. Camera preview remains visible behind the transparent preview area.
6. With no face, frames continue and `fc` logs increase.
7. With a face, a white detection box appears and follows the face.
8. Run for 2-5 minutes without the historical `fc~20/30` hang.
9. LVGL UI does not disappear after dynamic drawing.
10. Back button returns to menu.
11. Re-enter and exit `face_detect` three times without init/deinit failures.

## Later phases

### Phase 2: ID registration

Register the current largest face as ID1-ID4. This requires a separate design because registration needs feature extraction and persistent writes. Runtime writes may trigger the K230 file I/O / display DMA deadlock, so Phase 2 must choose a write strategy explicitly: memory-only until exit, deferred main-loop write, or immediate write with board validation.

### Phase 3: feature matching

Load saved features, run `FaceRegistrationApp` on the current largest face, call `database_search`, and draw colored `IDn` boxes. This depends on Phase 2 persistence being stable.

### Phase 4: clear records

Clear all saved face records. This also requires careful file deletion timing; the click handler must not directly remove files or rebuild heavy UI structures.
