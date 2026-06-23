# Face Recognition Business Design (Register + Match + Clear)

## Goal

On top of face_detect Phase 1 (single-thread template, white-box detection, board-accepted), add the recognition business: register face IDs via K2, match faces against the in-memory feature DB, and clear all records through a bottom-bar overlay. Persistence is deferred to exit to avoid K230 pitfall #2 (SD I/O racing display DMA flush).

## Background

Phase 1 proved the single-thread `run(runtime)+on_frame(img)` template is stable with per-frame `gc.collect()` (pitfall #16). It only detects faces and draws white boxes. Recognition needs:

- Feature extraction (`FaceRegistrationApp`, mobile kmodel, 512-dim, umeyama+affine) — exists in old app.py git history, not yet in `core/face_ai.py`.
- Cosine matching (`database_search`) — exists in old app.py git history.
- Feature persistence (`core/face_db.py` register/clear/init_features/flush_to_disk) — exists, but `register` flushes to disk immediately (pitfall #2 risk in on_frame path, never validated under single-thread template).
- K2 register controller (`core/id_registry.py`) — exists, assumes dual-thread (main poll_k2 + AI thread try_register); needs single-thread usage.

## Scope

### In scope

- Move `FaceRegistrationApp` and `database_search` into `core/face_ai.py` / `core/face_db.py`.
- Rework `face_db.register` to memory-only (no immediate flush); add `_dirty` flag.
- Rework `face_db.clear` to memory-only (no immediate `os.remove`); add `_clear_dirty` flag.
- Defer all disk writes/removes to the exit stage (`_deinit_ai`, after `task_handler` stopped).
- `IdRegistry` single-thread usage: `poll_k2` + `try_register` both on the main loop.
- `on_frame`: per-frame largest-face recognition (`database_search`), color box + `IDn` label on hit, white box otherwise.
- K2 short-press → register current largest-face feature (reuse extracted feature, zero extra NPU).
- Bottom-bar `list.png` icon → overlay (bottom-bar sized) with Clear and Save buttons.
- Clear button: clear in-memory features + `_clear_dirty` + close overlay (deferred) + beep.
- Save button: no-op (persistence is automatic on exit).
- Exit stage: flush dirty features to disk, or remove all .bin if `_clear_dirty`.
- Host-side AST contract tests.

### Out of scope

- UART/host reporting of recognition results.
- Threshold tuning beyond the existing 0.75 default.
- Multi-face recognition (only largest face per frame).
- Per-slot manual delete from UI (clear is all-or-nothing).

## Architecture

### Main loop (extends Phase 1)

```
run(runtime):
    _RUNTIME = runtime
    _init_ai()              # face_det + face_reg kmodel (main thread, before loop) + face_db.init_features
    _init_registry()        # IdRegistry(fpioa, pin=0)
    _build_ui(...)
    while not exit_flag[0]:
        os.exitpoint()
        img = snapshot(chn0)
        try: on_frame(img)
        except: print(...)
        id_registry.poll_k2()          # K2 edge detect on main loop
        _process_overlay_close()       # deferred overlay close (LVGL use-after-free guard)
        Display.show_image(img, OSD1)
        time.sleep_ms(lv.task_handler())
    finally:
        _deinit_ai()        # face_db persist: flush dirty OR remove all .bin if clear_dirty
        _destroy_ui()
```

### on_frame

```
img_ai = snapshot(chn2)
img_np = img_ai.to_numpy_ref()
det_boxes, landms = face_det.run(img_np)

recognition_results = []
feature = None
if det_boxes and landms and face_reg is not None:
    max_i = largest face by w*h
    face_reg.config_preprocess(landms[max_i])
    feature = face_reg.run(img_np)             # reuse img_np, zero extra snapshot
    matched_id = database_search(feature, db_features)
    recognition_results = [(max_i, matched_id)]

    # K2 pending: register current largest-face feature (reuse feature, zero extra NPU)
    if id_registry.has_pending():
        slot = id_registry.try_register(feature, buzzer)
        if slot is not None:
            recognition_results = [(max_i, slot)]   # immediate color feedback

face_det.draw_result(img, det_boxes, recognition_results)
gc.collect()
```

Key points:
- Both kmodels loaded in `_init_ai` (main thread, before first `task_handler`): pitfall #18/#19.
- Mobile kmodel (2.65MB, 512-dim) — standard 44MB OOM-deadlocks under LVGL (pitfall #19).
- Per-frame `gc.collect()` retained (pitfall #16, Phase 1 validated mandatory).
- `poll_k2` on main loop, `try_register` in on_frame — single thread, no lock. If K2 pressed this frame, poll sets pending; try_register consumes it same/next frame.
- Largest face only (aligns with old Step 5/7).

## Reusable asset integration

| Asset | Current | This round |
|-------|---------|-----------|
| `FaceDetectionApp` | in `core/face_ai.py` | reuse |
| `FaceRegistrationApp` (mobile, umeyama+affine, 512-dim) | old app.py git history | move to `core/face_ai.py` |
| `database_search` (cosine) | old app.py git history | move to `core/face_db.py` (data + match together) |
| `face_db.init_features`/`register`/`clear`/`flush_to_disk` | exists, register flushes immediately | register → memory-only + `_dirty`; clear → memory-only + `_clear_dirty` |
| `IdRegistry` poll_k2/try_register | exists, dual-thread doc | single-thread usage doc; add `has_pending()` |

### face_db changes

- `register(feature)`: append to `_features` (slot 1-4 round-robin), advance `_next_slot`, set `_dirty=True`, return slot. No `flush_to_disk`, no `_save_next_slot` here.
- `clear()`: `_features.clear()`, set `_clear_dirty=True`, `_dirty=False`. No `os.remove` here.
- `flush_to_disk()`: exit stage. If `_clear_dirty`: remove all .bin + .next_slot + reset `_next_slot=1`. Else if `_dirty`: write .bin per slot + `_save_next_slot()`. Reset flags after.

### IdRegistry changes

- Add `has_pending()` property (on_frame checks before extracting feature for register).
- `try_register(feature, buzzer)`: unchanged logic, but doc reflects single-thread (called from on_frame).
- No long-press clear (clear is overlay-only now).

## Clear trigger + persistence timing

Clear is overlay-only (bottom-bar `list.png` icon → overlay → Clear/Save buttons). K2 is register-only.

| Operation | Runtime (on_frame / callback) | Exit stage (_deinit_ai) |
|-----------|-------------------------------|-------------------------|
| Register | memory `_features[slot]=feature` + `_dirty=True` | `flush_to_disk()` writes dirty .bin + `_save_next_slot` |
| Match | read memory `db_features` | — |
| Clear (Clear btn) | memory `_features.clear()` + `_clear_dirty=True` + close overlay + beep | `os.remove` all .bin + .next_slot + pointer reset |

Exit stage decision:
- If `_clear_dirty` → remove all .bin, do NOT flush (user intent: empty).
- Else if `_dirty` → flush_to_disk.

Register and clear are mutually exclusive at exit: clear wins (if same session registered then cleared, clear's intent prevails).

## UI / output

- Top bar: back button + title `人脸识别`.
- Bottom bar: `list.png` icon button (bottom-left) + center hint `已注册 N/4`.
- Overlay (after list tap, bottom-bar sized, stacked above bottom bar):
  - Clear button (left)
  - Save button (right) — no-op
- Preview: white box (unknown/unregistered) / color box + `IDn` (matched).
- Beep: register success 80ms / fail 200ms / clear long beep.
- Hint text refreshed once after register/clear (not per-frame, to keep on_frame off LVGL).

## LVGL callback constraint

Clear/Save button CLICKED callbacks must NOT:
- call `os.remove` / `flush_to_disk` (pitfall #2).
- delete the overlay (use-after-free: overlay is ancestor of the button).

Clear callback: clear memory dict + `_clear_dirty` + set `_close_overlay=True` + beep.
Save callback: set `_close_overlay=True` (no-op persistence).
Main loop `_process_overlay_close()`: if `_close_overlay`, delete overlay LVGL objects, reset flag. (Mirrors camera deferred-delete pattern.)

## Error handling

- kmodel load failure (face_det/face_reg): `_init_ai` prints + re-raises → reset framework returns to menu. face_reg failure degrades to detection-only (face_reg=None guard, on_frame skips match/register).
- `face_db.init_features`: existing listdir pre-check + tolerant (pitfall #18), 0-face zero-open. Reuse.
- on_frame exception: try/except isolates, loop continues (Phase 1).
- Register failure (no face / bad feature): IdRegistry try/except + beep 200ms.
- Disk failure (exit stage flush/remove): best-effort, print warning, do not raise (exit failure must not block reset to menu).
- Overlay close use-after-free: deferred to main loop via flag.

## Host-side tests

`tests/test_face_ai.py` extend:
- `core/face_ai.py` defines `FaceRegistrationApp`.
- `FaceRegistrationApp` loads `face_recognition_mobile.kmodel`, not standard 44MB.
- `FaceRegistrationApp.config_preprocess` takes `landm` param.

`tests/test_face_db.py` (new):
- defines `register`, `flush_to_disk`, `clear`, `init_features`, `database_search`.
- `register` does NOT call `flush_to_disk` (memory-only); sets `_dirty`.
- `clear` does NOT call `os.remove` (memory-only); sets `_clear_dirty`.
- `flush_to_disk` handles `_clear_dirty` (remove) vs `_dirty` (write).

`tests/test_face_detect_template.py` extend:
- app.py imports `FaceRegistrationApp`, `database_search`, `face_db`, `id_registry` (reverse Phase 1 exclusion).
- `run()` loads face_reg kmodel main-thread before loop.
- `run()` main loop calls `id_registry.poll_k2()`.
- `run()` exit calls face_db persist (flush or clear-disk).
- `on_frame` per-frame `database_search` on largest face.
- `on_frame` does NOT call `flush_to_disk` / `os.remove` (zero runtime SD I/O).
- Clear/Save button callbacks do NOT call `os.remove` / delete overlay (deferred).
- Bottom bar has list icon + overlay + Clear/Save buttons.
- Per-frame `gc.collect()` retained.

## Board acceptance

1. Enter face_detect → title `人脸识别`, bottom bar list icon + `已注册 0/4`.
2. No registered faces: all white boxes, fc keeps rising.
3. K2 short-press on a face → beep + color box + `IDn`, hint `已注册 1/4`.
4. Register up to 4 faces (slot round-robin on 5th).
5. Recognized face → persistent color box + `IDn`.
6. Back to menu, re-enter → previously registered faces still recognized (persistence).
7. Tap list icon → overlay with Clear/Save.
8. Tap Clear → beep + hint `已注册 0/4`, overlay closes.
9. Back to menu, re-enter → empty DB (clear persisted).
10. Tap Save → overlay closes, no behavior change.
11. Run 2-5 min without hang; UI does not disappear; back/re-enter stable.
