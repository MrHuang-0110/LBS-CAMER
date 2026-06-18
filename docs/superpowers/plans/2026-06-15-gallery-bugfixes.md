# Gallery Bugfix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix camera gallery thumbnails rendering black/not loading, and make remaining photos move up immediately after a delete.

**Architecture:** Keep all runtime changes in `scripts/camera/app.py`. Add host-side regression tests that avoid K230-only imports by checking source-level invariants and a pure list-reflow helper. Deletion updates the in-memory gallery model, removes empty date groups, then rebuilds the gallery list from the model instead of manually moving individual LVGL rows.

**Tech Stack:** K230 MicroPython, LVGL v8, CanMV `image` module, host-side Python tests.

---

### Task 1: Thumbnail Loader Regression

**Files:**
- Test: `tests/test_camera_gallery.py`
- Modify: `scripts/camera/app.py`

- [ ] **Step 1: Write failing test**

Add a host-side test that asserts `scripts/camera/app.py` imports the `image` module because `_load_thumbnail()` calls `image.Image(path)`.

- [ ] **Step 2: Verify RED**

Run: `python tests/test_camera_gallery.py`

Expected: FAIL with `camera app must import image`.

- [ ] **Step 3: Implement minimal fix**

Add `import image` near the top of `scripts/camera/app.py`.

- [ ] **Step 4: Verify GREEN**

Run: `python tests/test_camera_gallery.py`

Expected: thumbnail import test passes.

---

### Task 2: Delete Reflow Regression

**Files:**
- Test: `tests/test_camera_gallery.py`
- Modify: `scripts/camera/app.py`

- [ ] **Step 1: Write failing test**

Add a pure-data test for a helper that removes a photo from gallery groups and drops empty date groups.

- [ ] **Step 2: Verify RED**

Run: `python tests/test_camera_gallery.py`

Expected: FAIL because `_remove_photo_from_groups` is missing.

- [ ] **Step 3: Implement minimal fix**

Add `_remove_photo_from_groups(groups, photo)` as a static helper and call it from `_on_delete_photo()` after successful `os.remove()`.

- [ ] **Step 4: Rebuild list after delete**

Add `_rebuild_gallery_ui()` that deletes the existing list, clears stale UI object references, and calls `_build_gallery_ui(self._gallery_groups)`. Call it after removing the deleted photo and thumbnail reference.

- [ ] **Step 5: Verify GREEN**

Run: `python tests/test_camera_gallery.py` and `python tests/test_font_coverage.py`.

Expected: all tests pass.
