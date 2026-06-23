# Face Recognition Business Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add face-ID registration (K2), per-frame recognition (color box + IDn), and clear-all (bottom-bar overlay) to the single-thread face_detect app, with all disk I/O deferred to exit to avoid K230 pitfall #2.

**Architecture:** Move `FaceRegistrationApp` and `database_search` into reusable core modules; rework `face_db.register`/`clear` to memory-only with dirty flags and defer flush/remove to the exit stage; single-thread the `IdRegistry`; extend `on_frame` to recognize+register the largest face; add a deferred-close bottom-bar overlay for clear/save. Zero SD I/O during the running loop.

**Tech Stack:** K230 CanMV MicroPython, LVGL, `media.sensor`, `nncase_runtime`, `ulab.numpy`, `libs.AIBase`, host-side AST contract tests run with `python tests/*.py`.

---

## File Structure

- Modify: `core/face_ai.py`
  - Add `FaceRegistrationApp` (mobile kmodel, umeyama+affine, 512-dim) from old app.py git history.
- Modify: `core/face_db.py`
  - Add `database_search`. Rework `register` to memory-only + `_dirty`. Rework `clear` to memory-only + `_clear_dirty`. `flush_to_disk` handles both dirty-write and clear-remove at exit.
- Modify: `core/id_registry.py`
  - Add `has_pending()`. Update doc to single-thread usage. Drop the long-press concept (clear is overlay-only).
- Modify: `scripts/face_detect/app.py`
  - Load face_reg kmodel + db_features in `_init_ai`; init IdRegistry; per-frame recognize+register in `on_frame`; bottom-bar list icon + overlay with Clear/Save; deferred overlay close; persist at exit.
- Modify: `tests/test_face_ai.py`
  - Assert `FaceRegistrationApp` + mobile kmodel + landm param.
- Create: `tests/test_face_db.py`
  - Assert memory-only register/clear, dirty flags, exit-stage flush/remove, database_search.
- Modify: `tests/test_face_detect_template.py`
  - Reverse Phase 1 exclusion (now imports face_db/id_registry); assert recognition loop, exit persist, zero runtime SD I/O, deferred overlay close.
- Modify: `项目记录.md`
  - Append implementation record after host/board verification.

---

### Task 1: Add `FaceRegistrationApp` to `core/face_ai.py`

**Files:**
- Modify: `tests/test_face_ai.py`
- Modify: `core/face_ai.py`

- [ ] **Step 1: Add failing tests**

Append to `tests/test_face_ai.py` before `test_runner`:

```python
def test_face_ai_defines_registration_app():
    tree = _parse(FACE_AI_PATH)
    _class_node(tree, "FaceRegistrationApp")


def test_registration_app_loads_mobile_kmodel():
    src = open(FACE_AI_PATH, encoding="utf-8").read()
    assert "face_recognition_mobile.kmodel" in src, \
        "FaceRegistrationApp must use mobile kmodel (2.65MB, 512-dim)"
    assert "face_recognition.kmodel\"" not in src and "'face_recognition.kmodel'" not in src, \
        "must NOT use standard 44MB face_recognition.kmodel (OOM deadlock, pitfall #19)"


def test_registration_config_preprocess_takes_landm():
    tree = _parse(FACE_AI_PATH)
    cls = _class_node(tree, "FaceRegistrationApp")
    found = False
    for node in cls.body:
        if isinstance(node, ast.FunctionDef) and node.name == "config_preprocess":
            arg_names = [a.arg for a in node.args.args]
            assert "landm" in arg_names, \
                "config_preprocess must take landm (5-point landmarks for umeyama+affine)"
            found = True
    assert found, "FaceRegistrationApp.config_preprocess missing"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python tests/test_face_ai.py`
Expected: FAIL — `FaceRegistrationApp` not defined.

- [ ] **Step 3: Add `FaceRegistrationApp` to `core/face_ai.py`**

Append to `core/face_ai.py` (after `FaceDetectionApp`). This is the board-validated class from old app.py git history (commit `2a094d6`), verbatim except module-level imports already present:

```python
class FaceRegistrationApp(AIBase):
    """Face feature extraction (face_recognition_mobile.kmodel, 512-dim).

    Uses mobile (2.65MB), not standard (44MB): LVGL leaves ~3.7MB free; standard
    AIBase.__init__ deadlocks. Official main2.py uses standard because it has no
    LVGL. 512-dim features → face_db EXPECTED_BYTES = 512*4.
    """
    def __init__(self, kmodel_path, model_input_size, rgb888p_size=None, debug_mode=0):
        if rgb888p_size is None:
            rgb888p_size = RGB888P_SIZE
        super().__init__(kmodel_path, model_input_size, rgb888p_size, debug_mode)
        self.kmodel_path = kmodel_path
        self.model_input_size = model_input_size
        self.rgb888p_size = [ALIGN_UP(rgb888p_size[0], 16), rgb888p_size[1]]
        self.umeyama_args_112 = [
            38.2946, 51.6963,
            73.5318, 51.5014,
            56.0252, 71.7366,
            41.5493, 92.3655,
            70.7299, 92.2041
        ]
        self.ai2d = Ai2d(debug_mode)
        self.ai2d.set_ai2d_dtype(nn.ai2d_format.NCHW_FMT, nn.ai2d_format.NCHW_FMT,
                                 np.uint8, np.uint8)

    def config_preprocess(self, landm, input_image_size=None):
        import math
        ai2d_input_size = input_image_size if input_image_size else self.rgb888p_size
        affine_matrix = self._get_affine_matrix(landm)
        self.ai2d.affine(nn.interp_method.cv2_bilinear, 0, 0, 127, 1, affine_matrix)
        self.ai2d.build(
            [1, 3, ai2d_input_size[1], ai2d_input_size[0]],
            [1, 3, self.model_input_size[1], self.model_input_size[0]])

    def _get_affine_matrix(self, sparse_points):
        matrix_dst = self._image_umeyama_112(sparse_points)
        return [matrix_dst[0][0], matrix_dst[0][1], matrix_dst[0][2],
                matrix_dst[1][0], matrix_dst[1][1], matrix_dst[1][2]]

    def _image_umeyama_112(self, src):
        SRC_NUM = 5
        src_mean = [0.0, 0.0]
        dst_mean = [0.0, 0.0]
        for i in range(0, SRC_NUM * 2, 2):
            src_mean[0] += src[i]
            src_mean[1] += src[i + 1]
            dst_mean[0] += self.umeyama_args_112[i]
            dst_mean[1] += self.umeyama_args_112[i + 1]
        src_mean[0] /= SRC_NUM
        src_mean[1] /= SRC_NUM
        dst_mean[0] /= SRC_NUM
        dst_mean[1] /= SRC_NUM
        src_demean = [[0.0, 0.0] for _ in range(SRC_NUM)]
        dst_demean = [[0.0, 0.0] for _ in range(SRC_NUM)]
        for i in range(SRC_NUM):
            src_demean[i][0] = src[2 * i] - src_mean[0]
            src_demean[i][1] = src[2 * i + 1] - src_mean[1]
            dst_demean[i][0] = self.umeyama_args_112[2 * i] - dst_mean[0]
            dst_demean[i][1] = self.umeyama_args_112[2 * i + 1] - dst_mean[1]
        A = [[0.0, 0.0], [0.0, 0.0]]
        for i in range(2):
            for k in range(2):
                for j in range(SRC_NUM):
                    A[i][k] += dst_demean[j][i] * src_demean[j][k]
                A[i][k] /= SRC_NUM
        T = [[1, 0, 0], [0, 1, 0], [0, 0, 1]]
        U, S, V = self._svd22([A[0][0], A[0][1], A[1][0], A[1][1]])
        T[0][0] = U[0] * V[0] + U[1] * V[2]
        T[0][1] = U[0] * V[1] + U[1] * V[3]
        T[1][0] = U[2] * V[0] + U[3] * V[2]
        T[1][1] = U[2] * V[1] + U[3] * V[3]
        src_demean_mean = [0.0, 0.0]
        src_demean_var = [0.0, 0.0]
        for i in range(SRC_NUM):
            src_demean_mean[0] += src_demean[i][0]
            src_demean_mean[1] += src_demean[i][1]
        src_demean_mean[0] /= SRC_NUM
        src_demean_mean[1] /= SRC_NUM
        for i in range(SRC_NUM):
            src_demean_var[0] += (src_demean_mean[0] - src_demean[i][0]) ** 2
            src_demean_var[1] += (src_demean_mean[1] - src_demean[i][1]) ** 2
        src_demean_var[0] /= SRC_NUM
        src_demean_var[1] /= SRC_NUM
        scale = 1.0 / (src_demean_var[0] + src_demean_var[1]) * (S[0] + S[1])
        T[0][2] = dst_mean[0] - scale * (T[0][0] * src_mean[0] + T[0][1] * src_mean[1])
        T[1][2] = dst_mean[1] - scale * (T[1][0] * src_mean[0] + T[1][1] * src_mean[1])
        T[0][0] *= scale
        T[0][1] *= scale
        T[1][0] *= scale
        T[1][1] *= scale
        return T

    def _svd22(self, a):
        import math
        s = [0.0, 0.0]
        u = [0.0, 0.0, 0.0, 0.0]
        v = [0.0, 0.0, 0.0, 0.0]
        s[0] = (math.sqrt((a[0] - a[3]) ** 2 + (a[1] + a[2]) ** 2)
                + math.sqrt((a[0] + a[3]) ** 2 + (a[1] - a[2]) ** 2)) / 2
        s[1] = abs(s[0] - math.sqrt((a[0] - a[3]) ** 2 + (a[1] + a[2]) ** 2))
        v[2] = math.sin(math.atan2(
            2 * (a[0] * a[1] + a[2] * a[3]),
            a[0] ** 2 - a[1] ** 2 + a[2] ** 2 - a[3] ** 2) / 2) if s[0] > s[1] else 0
        v[0] = math.sqrt(1 - v[2] ** 2)
        v[1] = -v[2]
        v[3] = v[0]
        u[0] = -(a[0] * v[0] + a[1] * v[2]) / s[0] if s[0] != 0 else 1
        u[2] = -(a[2] * v[0] + a[3] * v[2]) / s[0] if s[0] != 0 else 0
        u[1] = (a[0] * v[1] + a[1] * v[3]) / s[1] if s[1] != 0 else -u[2]
        u[3] = (a[2] * v[1] + a[3] * v[3]) / s[1] if s[1] != 0 else u[0]
        v[0] = -v[0]
        v[2] = -v[2]
        return u, s, v

    def postprocess(self, results):
        return results[0][0]

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

Run: `python tests/test_face_ai.py`
Expected: ALL PASS.

- [ ] **Step 5: Commit**

```bash
git add core/face_ai.py tests/test_face_ai.py
git commit -m "feat(face_ai): add FaceRegistrationApp

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 2: Rework `face_db` for memory-only register/clear + `database_search`

**Files:**
- Create: `tests/test_face_db.py`
- Modify: `core/face_db.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_face_db.py`:

```python
# tests/test_face_db.py — host-side AST tests for face_db memory-only + dirty flags.
# Run with:
#   python tests/test_face_db.py
import ast
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(ROOT, "core", "face_db.py")


def _src():
    with open(DB_PATH, "r", encoding="utf-8") as f:
        return f.read()


def _parse():
    return ast.parse(_src(), filename=DB_PATH)


def _class_node(tree, name):
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == name:
            return node
    raise AssertionError("Class %s missing" % name)


def _method_node(cls, name):
    for node in cls.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError("Method %s missing" % name)


def _method_src(name):
    tree = _parse()
    cls = _class_node(tree, "_FaceDB")
    fn = _method_node(cls, name)
    return ast.get_source_segment(_src(), fn) or ""


def test_face_db_defines_database_search():
    src = _src()
    assert "def database_search" in src, "face_db must define database_search"


def test_register_is_memory_only():
    """register must NOT flush to disk (pitfall #2). Sets _dirty instead."""
    src = _method_src("register")
    assert "flush_to_disk" not in src, \
        "register must not call flush_to_disk (deferred to exit, pitfall #2)"
    assert "_save_next_slot" not in src, \
        "register must not persist next_slot (deferred to exit)"
    assert "_dirty" in src, "register must set _dirty flag"


def test_clear_is_memory_only():
    """clear must NOT os.remove (pitfall #2). Sets _clear_dirty instead."""
    src = _method_src("clear")
    assert "os.remove" not in src, \
        "clear must not os.remove (deferred to exit, pitfall #2)"
    assert "_clear_dirty" in src, "clear must set _clear_dirty flag"


def test_flush_to_disk_handles_clear_dirty_and_dirty():
    src = _method_src("flush_to_disk")
    assert "_clear_dirty" in src, "flush_to_disk must handle _clear_dirty (remove all)"
    assert "_dirty" in src, "flush_to_disk must handle _dirty (write)"
    assert "os.remove" in src or "clear_disk" in src, \
        "flush_to_disk must remove .bin when _clear_dirty"


def test_face_db_has_init_features_and_get_features():
    tree = _parse()
    cls = _class_node(tree, "_FaceDB")
    names = {n.name for n in cls.body if isinstance(n, ast.FunctionDef)}
    for m in ("init_features", "get_features", "register", "clear", "flush_to_disk"):
        assert m in names, "_FaceDB missing method: %s" % m


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

Run: `python tests/test_face_db.py`
Expected: FAIL — `database_search` missing; `register` still calls flush; `clear` still os.remove.

- [ ] **Step 3: Add `database_search` and rework `register`/`clear`/`flush_to_disk`**

In `core/face_db.py`:

3a. Add `database_search` as a module-level function (after the `_FaceDB` class, before `face_db = _FaceDB()`):

```python
def database_search(feature, db_features, threshold=0.75):
    """Cosine-match feature against db_features. Return slot_id or None.

    db_features: {slot_id: np_array} (in-memory, read-only during on_frame).
    Empty / bad / below-threshold → None. Aligns with official main2.py.
    """
    if not db_features:
        return None
    try:
        import ulab.numpy as np
        feat_norm = np.linalg.norm(feature)
        if feat_norm == 0:
            return None
        feature = feature / feat_norm
    except Exception:
        return None
    best_id = None
    best_score = 0.0
    for slot_id, db_feat in db_features.items():
        try:
            norm = np.linalg.norm(db_feat)
            if norm == 0:
                continue
            db_n = db_feat / norm
            score = np.dot(feature, db_n) / 2 + 0.5
        except Exception:
            continue
        if score > best_score:
            best_score = score
            best_id = slot_id
    if best_score < threshold:
        return None
    return best_id
```

3b. In `_FaceDB.__init__`, add dirty flags:

```python
    def __init__(self):
        self._features = {}
        self._loaded = False
        self._next_slot = 1
        self._dirty = False        # register changed memory; flush at exit
        self._clear_dirty = False  # clear requested; remove all .bin at exit
```

3c. Replace `register` (memory-only + dirty):

```python
    def register(self, feature):
        """Register feature to slot (round-robin) in MEMORY only. Returns slot_id(1-4).

        No disk I/O here (pitfall #2: runtime SD write races display DMA flush).
        Sets _dirty; flush_to_disk() persists at exit (task_handler stopped).

        - Empty slot first (do not move _next_slot)
        - No empty slot: overwrite _next_slot, advance pointer (1→2→3→4→1)
        """
        slot = None
        for i in range(1, 5):
            if i not in self._features:
                slot = i
                break
        if slot is None:
            slot = self._next_slot
            self._next_slot = self._next_slot % 4 + 1
        self._features[slot] = feature
        self._dirty = True
        self._clear_dirty = False  # a register after clear cancels the clear intent
        print("[FaceDB] registered → id%d (memory, dirty)" % slot)
        return slot
```

3d. Replace `clear` (memory-only + clear_dirty):

```python
    def clear(self):
        """Clear all features in MEMORY only.

        No os.remove here (pitfall #2). Sets _clear_dirty; flush_to_disk() removes
        all .bin + .next_slot at exit. Cancels any pending _dirty (clear wins).
        """
        self._features.clear()
        self._clear_dirty = True
        self._dirty = False
        self._next_slot = 1
        print("[FaceDB] cleared (memory, clear_dirty)")
```

3e. Replace `flush_to_disk` (exit-stage dispatcher):

```python
    def flush_to_disk(self):
        """Exit-stage persistence. Called after task_handler stopped (pitfall #2 safe).

        - _clear_dirty: remove all .bin + .next_slot, reset pointer (clear intent)
        - _dirty: write all memory features to .bin + save _next_slot
        - neither: no-op
        Resets flags after.
        """
        import os
        if self._clear_dirty:
            for i in range(1, 5):
                try:
                    os.remove(f"{_DB_DIR}/id{i}.bin")
                except Exception:
                    pass
            try:
                os.remove(_NEXT_SLOT_PATH)
            except Exception:
                pass
            self._clear_dirty = False
            self._dirty = False
            print("[FaceDB] exit: cleared disk")
            return
        if not self._dirty:
            return
        if not self._features:
            self._dirty = False
            return
        try:
            os.mkdir(_DB_DIR)
        except Exception:
            pass
        for i, feature in self._features.items():
            path = f"{_DB_DIR}/id{i}.bin"
            try:
                with open(path, 'wb') as f:
                    f.write(feature.tobytes())
                print("[FaceDB] exit: flushed id%d.bin" % i)
            except Exception as e:
                print("[FaceDB] exit: flush id%d failed: %s" % (i, e))
        self._save_next_slot()
        self._dirty = False
```

3f. Remove the old `clear_disk` method (no longer used; flush_to_disk handles removal). If any test references it, none do.

- [ ] **Step 4: Run test to verify it passes**

Run: `python tests/test_face_db.py`
Expected: ALL PASS.

- [ ] **Step 5: Commit**

```bash
git add core/face_db.py tests/test_face_db.py
git commit -m "feat(face_db): memory-only register/clear + database_search

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 3: Single-thread `IdRegistry` + `has_pending()`

**Files:**
- Modify: `tests/test_face_db.py` (or a new test file — keep with face_db tests for simplicity)
- Modify: `core/id_registry.py`

- [ ] **Step 1: Add failing test**

Append to `tests/test_face_db.py` before `test_runner`:

```python
ID_REGISTRY_PATH = os.path.join(ROOT, "core", "id_registry.py")


def test_id_registry_has_pending_method():
    src = open(ID_REGISTRY_PATH, encoding="utf-8").read()
    assert "def has_pending" in src, "IdRegistry must expose has_pending() for on_frame"


def test_id_registry_has_no_long_press_clear():
    """Clear is overlay-only; IdRegistry must not implement long-press clear."""
    src = open(ID_REGISTRY_PATH, encoding="utf-8").read()
    assert "long" not in src.lower() or "long press" not in src.lower(), \
        "IdRegistry must not implement long-press clear (overlay-only now)"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python tests/test_face_db.py`
Expected: FAIL — `has_pending` missing.

- [ ] **Step 3: Add `has_pending()` and update docs**

In `core/id_registry.py`, add a property to `IdRegistry` (after `poll_k2`):

```python
    def has_pending(self):
        """Single-thread on_frame checks this before extracting a register feature.
        Returns True if a K2 short-press is pending (within 2s timeout)."""
        if not self._pending:
            return False
        if time.ticks_diff(time.ticks_ms(), self._pending_time) > 2000:
            self._pending = False
            print("[IdRegistry] pending timeout, discarded")
            return False
        return True
```

Update the module docstring and `try_register` docstring to reflect single-thread usage (poll_k2 on main loop, try_register in on_frame). Do not add long-press logic.

- [ ] **Step 4: Run test to verify it passes**

Run: `python tests/test_face_db.py`
Expected: ALL PASS.

- [ ] **Step 5: Commit**

```bash
git add core/id_registry.py tests/test_face_db.py
git commit -m "feat(id_registry): single-thread has_pending()

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 4: Extend `face_detect` template contracts for recognition

**Files:**
- Modify: `tests/test_face_detect_template.py`

- [ ] **Step 1: Update contracts (reverse Phase 1 exclusions, add recognition)**

In `tests/test_face_detect_template.py`:

1a. Delete `test_face_detect_phase1_excludes_registration_and_db` (it asserted no face_db/id_registry — now reversed).

1b. Replace `test_face_detect_has_no_threads_or_self_media_init` forbidden list — keep the media/thread tokens but they must remain forbidden; no change needed there.

1c. Add these tests before `test_runner`:

```python
def test_face_detect_imports_recognition_assets():
    src = _src()
    assert "FaceRegistrationApp" in src, "must import FaceRegistrationApp"
    assert "face_db" in src, "must use face_db"
    assert "id_registry" in src, "must use id_registry"
    assert "database_search" in src, "must use database_search"


def test_run_loads_face_reg_kmodel_before_loop():
    tree = _parse()
    run_fn = _function_node(tree, "run")
    src = ast.get_source_segment(_src(), run_fn) or ""
    assert "face_recognition_mobile.kmodel" in src, "run() must load mobile reg kmodel"
    assert "FaceRegistrationApp(" in src, "run() must construct FaceRegistrationApp"
    assert src.find("FaceRegistrationApp(") < src.find("while not exit_flag"), \
        "face_reg must be constructed before the main loop (pitfall #18)"


def test_run_main_loop_polls_k2():
    tree = _parse()
    run_fn = _function_node(tree, "run")
    src = ast.get_source_segment(_src(), run_fn) or ""
    assert "poll_k2()" in src, "main loop must call id_registry.poll_k2()"


def test_on_frame_recognizes_largest_face():
    tree = _parse()
    fn = _function_node(tree, "on_frame")
    src = ast.get_source_segment(_src(), fn) or ""
    assert "database_search" in src, "on_frame must match largest face via database_search"
    assert "face_reg.run" in src or "face_reg" in src, "on_frame must extract feature via face_reg"


def test_on_frame_no_runtime_disk_io():
    tree = _parse()
    fn = _function_node(tree, "on_frame")
    src = ast.get_source_segment(_src(), fn) or ""
    for token in ("flush_to_disk", "os.remove", "open("):
        assert token not in src, "on_frame must not do disk I/O (pitfall #2): %s" % token


def test_run_persists_at_exit():
    tree = _parse()
    run_fn = _function_node(tree, "run")
    src = ast.get_source_segment(_src(), run_fn) or ""
    assert "flush_to_disk" in src or "face_db.flush_to_disk" in src, \
        "run() exit must persist face_db (flush or clear)"


def test_overlay_close_is_deferred():
    """Clear/Save button callbacks must not delete overlay (use-after-free)."""
    src = _src()
    assert "_process_overlay_close" in src, "must have deferred overlay-close handler"
    assert "_close_overlay" in src, "must use a close flag, not direct delete in callback"


def test_clear_save_callbacks_no_disk_io():
    src = _src()
    # The clear handler must set clear_dirty + flag, not os.remove directly.
    assert "face_db.clear()" in src or ".clear()" in src, "clear button must call face_db.clear()"
    assert "os.remove" not in src, "app.py must not call os.remove directly (deferred to face_db)"


def test_bottom_bar_has_list_icon_and_overlay():
    src = _src()
    assert "list" in src, "bottom bar must have list icon trigger"
    assert "清除" in src and "保存" in src, "overlay must have Clear/Save buttons"


def test_title_is_recognition():
    src = _src()
    assert "人脸识别" in src, "title should be 人脸识别"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python tests/test_face_detect_template.py`
Expected: FAIL — app.py not yet updated for recognition.

- [ ] **Step 3: Commit the failing contracts**

```bash
git add tests/test_face_detect_template.py
git commit -m "test(face_detect): add recognition contracts

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 5: Implement recognition in `scripts/face_detect/app.py`

**Files:**
- Modify: `scripts/face_detect/app.py`
- Test: `tests/test_face_detect_template.py`

- [ ] **Step 1: Rewrite app.py with recognition + overlay**

Replace `scripts/face_detect/app.py` with:

```python
# scripts/face_detect/app.py — face recognition (register + match + clear) on template.
#
# Single-thread loop: snapshot chn0 → on_frame(chn2 detect + reg + match + draw) →
# show_image → lv.task_handler. K2 short-press registers; bottom-bar list overlay
# clears. All disk I/O deferred to exit (pitfall #2). Per-frame gc (pitfall #16).

import gc
import os
import sys
import time
import lvgl as lv
import ulab.numpy as np
from media.display import Display
from media.sensor import CAM_CHN_ID_0, CAM_CHN_ID_2
from core.icon_cache import icon_cache
from core.font_manager import fonts
from core.face_ai import FaceDetectionApp, FaceRegistrationApp, RGB888P_SIZE, DISPLAY_SIZE
from core.face_db import face_db, database_search
from core.id_registry import IdRegistry

BAR_H = 52
PREVIEW_Y = BAR_H
PREVIEW_H = 376
BAR_BG = 0x1A1A1A
TITLE_TEXT = "人脸识别"

_RUNTIME = None
_screen = None
_top_bar = None
_bottom_bar = None
_preview = None
_face_det = None
_face_reg = None
_db_features = {}
_id_registry = None
_count_label = None
_overlay = None
_clear_btn = None
_save_btn = None
_close_overlay = False


def _init_ai():
    """Load detection + recognition kmodels and db_features before the loop."""
    global _face_det, _face_reg, _db_features
    anchors_path = "/sdcard/examples/utils/prior_data_320.bin"
    det_kmodel = "/sdcard/examples/kmodel/face_detection_320.kmodel"
    reg_kmodel = "/sdcard/examples/kmodel/face_recognition_mobile.kmodel"
    print("[face_detect] loading anchors...")
    anchors = np.fromfile(anchors_path, dtype=np.float)
    anchors = anchors.reshape((4200, 4))
    print("[face_detect] loading det kmodel...")
    _face_det = FaceDetectionApp(det_kmodel, model_input_size=[320, 320], anchors=anchors,
                                 confidence_threshold=0.5, nms_threshold=0.2,
                                 rgb888p_size=RGB888P_SIZE, display_size=DISPLAY_SIZE,
                                 debug_mode=0)
    _face_det.config_preprocess()
    print("[face_detect] loading reg kmodel...")
    try:
        _face_reg = FaceRegistrationApp(reg_kmodel, model_input_size=[112, 112],
                                        rgb888p_size=RGB888P_SIZE, debug_mode=0)
        print("[face_detect] reg kmodel ready (512-dim)")
    except Exception as e:
        print("[face_detect] reg kmodel FAILED: %s" % e)
        sys.print_exception(e)
        _face_reg = None
    _db_features = face_db.init_features()
    print("[face_detect] db loaded: %d face(s)" % len(_db_features))


def _init_registry(fpioa):
    global _id_registry
    _id_registry = IdRegistry(fpioa, pin=0)


def _deinit_ai():
    """Exit-stage: persist face_db (flush or clear), then release NPU."""
    global _face_det, _face_reg
    try:
        face_db.flush_to_disk()
    except Exception as e:
        print("[face_detect] persist warning: %s" % e)
    if _face_det is not None:
        try:
            _face_det.deinit()
        except Exception as e:
            print("[face_detect] det deinit warning: %s" % e)
        _face_det = None
    if _face_reg is not None:
        try:
            _face_reg.deinit()
        except Exception as e:
            print("[face_detect] reg deinit warning: %s" % e)
        _face_reg = None


def on_frame(img):
    """Detect on chn2, recognize + register largest face, draw onto chn0 preview."""
    if _RUNTIME is None or _face_det is None:
        return
    img_ai = _RUNTIME.sensor.snapshot(chn=CAM_CHN_ID_2)
    img_np = img_ai.to_numpy_ref()
    det_boxes, landms = _face_det.run(img_np)

    recognition_results = []
    if det_boxes and landms and _face_reg is not None:
        try:
            max_i = max(range(len(det_boxes)),
                        key=lambda i: det_boxes[i][2] * det_boxes[i][3])
            _face_reg.config_preprocess(landms[max_i])
            feature = _face_reg.run(img_np)
            matched_id = database_search(feature, _db_features)
            recognition_results.append((max_i, matched_id))
            if _id_registry is not None and _id_registry.has_pending():
                slot = _id_registry.try_register(feature, _RUNTIME.buzzer)
                if slot is not None:
                    _db_features[slot] = feature
                    recognition_results = [(max_i, slot)]
                    _refresh_count()
        except Exception as e:
            print("[face_detect] recog error: %s" % e)

    _face_det.draw_result(img, det_boxes, recognition_results)
    gc.collect()


def _refresh_count():
    if _count_label is not None:
        try:
            _count_label.set_text("已注册 %d/4" % len(_db_features))
        except Exception:
            pass


def _build_ui(runtime, exit_flag):
    global _screen, _top_bar, _bottom_bar, _preview, _count_label
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

    # list 图标按钮（底栏左侧）→ 弹出清除/保存浮层
    list_btn = lv.obj(_bottom_bar)
    list_btn.set_size(48, 48)
    list_btn.align(lv.ALIGN.LEFT_MID, 2, 0)
    list_btn.set_style_bg_opa(0, 0)
    list_btn.set_style_border_width(0, 0)
    list_btn.set_style_pad_all(0, 0)
    list_btn.clear_flag(lv.obj.FLAG.SCROLLABLE)
    list_btn.add_flag(lv.obj.FLAG.CLICKABLE)
    list_lbl = lv.label(list_btn)
    list_lbl.set_text("list")
    list_lbl.center()
    list_btn.add_event(_on_list_clicked, lv.EVENT.CLICKED, None)

    _count_label = lv.label(_bottom_bar)
    _count_label.set_text("已注册 %d/4" % len(_db_features))
    _count_label.align(lv.ALIGN.CENTER, 0, 0)
    from ui.theme import make_back_bar_text_style as _bbts
    _count_label.add_style(_bbts(fonts.body), 0)


def _on_list_clicked(e):
    """Open the clear/save overlay. Does not touch disk."""
    global _overlay, _clear_btn, _save_btn
    if e.get_code() != lv.EVENT.CLICKED:
        return
    if _overlay is not None:
        return
    _overlay = lv.obj(lv.scr_act())
    _overlay.set_size(640, BAR_H)
    _overlay.set_pos(0, PREVIEW_Y + PREVIEW_H)
    _overlay.set_style_bg_color(lv.color_hex(BAR_BG), 0)
    _overlay.set_style_bg_opa(255, 0)
    _overlay.set_style_border_width(0, 0)
    _overlay.set_style_pad_all(0, 0)
    _overlay.set_style_radius(0, 0)
    _overlay.clear_flag(lv.obj.FLAG.SCROLLABLE)

    _clear_btn = lv.btn(_overlay)
    _clear_btn.set_size(120, 40)
    _clear_btn.align(lv.ALIGN.LEFT_MID, 20, 0)
    cl = lv.label(_clear_btn)
    cl.set_text("清除")
    cl.center()
    _clear_btn.add_event(_on_clear_clicked, lv.EVENT.CLICKED, None)

    _save_btn = lv.btn(_overlay)
    _save_btn.set_size(120, 40)
    _save_btn.align(lv.ALIGN.RIGHT_MID, -20, 0)
    sv = lv.label(_save_btn)
    sv.set_text("保存")
    sv.center()
    _save_btn.add_event(_on_save_clicked, lv.EVENT.CLICKED, None)


def _on_clear_clicked(e):
    """Clear memory + flag close. No disk I/O, no overlay delete here (deferred)."""
    global _close_overlay
    if e.get_code() != lv.EVENT.CLICKED:
        return
    face_db.clear()
    _db_features.clear()
    _refresh_count()
    if _RUNTIME is not None and _RUNTIME.buzzer is not None:
        _RUNTIME.buzzer.beep(ms=200)
    _close_overlay = True


def _on_save_clicked(e):
    """No-op persistence (auto on exit). Just close overlay (deferred)."""
    global _close_overlay
    if e.get_code() != lv.EVENT.CLICKED:
        return
    _close_overlay = True


def _process_overlay_close():
    """Main-loop deferred overlay close (LVGL use-after-free guard)."""
    global _overlay, _clear_btn, _save_btn, _close_overlay
    if not _close_overlay:
        return
    _close_overlay = False
    for obj in (_clear_btn, _save_btn, _overlay):
        if obj is not None:
            try:
                obj.delete()
            except Exception:
                pass
    _clear_btn = None
    _save_btn = None
    _overlay = None


def _destroy_ui():
    global _screen, _top_bar, _bottom_bar, _preview, _count_label, _overlay, _clear_btn, _save_btn
    for obj in (_overlay, _clear_btn, _save_btn, _top_bar, _bottom_bar, _preview, _count_label):
        if obj is not None:
            try:
                obj.delete()
            except Exception:
                pass
    _overlay = None
    _clear_btn = None
    _save_btn = None
    _top_bar = None
    _bottom_bar = None
    _preview = None
    _count_label = None
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
    _init_registry(runtime.fpioa)
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
            if _id_registry is not None:
                _id_registry.poll_k2()
            _process_overlay_close()
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

- [ ] **Step 2: Run template tests**

Run: `python tests/test_face_detect_template.py`
Expected: ALL PASS.

- [ ] **Step 3: Run face_db + face_ai tests**

Run: `python tests/test_face_db.py` then `python tests/test_face_ai.py`
Expected: ALL PASS.

- [ ] **Step 4: Commit**

```bash
git add scripts/face_detect/app.py
git commit -m "feat(face_detect): add register+match+clear

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 6: Full host regression and syntax checks

**Files:** none expected.

- [ ] **Step 1: Run all face + framework tests**

Run:
```bash
python tests/test_face_ai.py
python tests/test_face_db.py
python tests/test_face_detect_template.py
python tests/test_framework.py
python tests/test_camera.py
python tests/test_camera_gallery.py
```
Expected: ALL PASS for all six.

- [ ] **Step 2: Parse changed files**

Run:
```bash
python -c "import ast, pathlib; files=['core/face_ai.py','core/face_db.py','core/id_registry.py','scripts/face_detect/app.py','tests/test_face_ai.py','tests/test_face_db.py','tests/test_face_detect_template.py']; [ast.parse(pathlib.Path(f).read_text(encoding='utf-8'), filename=f) for f in files]; print('AST OK')"
```
Expected: `AST OK`.

- [ ] **Step 3: Check git status**

Run: `git status --short`
Expected: clean (or only the Task 7 doc edit).

---

### Task 7: Update project record after host verification

**Files:** Modify `项目记录.md`

- [ ] **Step 1: Append record**

Append to `项目记录.md`:

```markdown

## 2026-06-23 人脸识别业务实现（host 验证）

- spec：`docs/superpowers/specs/2026-06-23-face-recognition-design.md`
- plan：`docs/superpowers/plans/2026-06-23-face-recognition.md`
- 文件：`core/face_ai.py` 加 `FaceRegistrationApp`（mobile kmodel 512维 + umeyama+affine）。
- 文件：`core/face_db.py` 加 `database_search`；`register`/`clear` 改内存-only + `_dirty`/`_clear_dirty`；`flush_to_disk` 退出阶段按标志写盘或删盘。
- 文件：`core/id_registry.py` 加 `has_pending()`，单线程化文档。
- 文件：`scripts/face_detect/app.py` 叠加识别业务：`on_frame` 每帧识别最大脸（database_search 彩框 ID）+ K2 pending 注册复用 feature；底栏 list 图标浮层清除/保存，deferred 关浮层；退出 `face_db.flush_to_disk`；标题改"人脸识别"。
- 运行期零 SD I/O（注册/清除只改内存，写盘/删盘 deferred 到退出，避坑#2）。每帧 `gc.collect()` 保留（坑#16）。
- host 验证：face_ai / face_db / face_detect_template / framework / camera / camera_gallery 全绿 + AST OK。
- 板端待验收：注册→彩框 ID→退出→重进仍识别（持久化）；K2 短按注册；list 浮层清除→退出→重进空库；2-5 分钟不卡；返回/反复进出稳定。
```

- [ ] **Step 2: Commit**

```bash
git add 项目记录.md
git commit -m "docs(face_recognition): record host verification

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 8: Board deployment and acceptance

**Files (deploy):**
- `core/face_ai.py`
- `core/face_db.py`
- `core/id_registry.py`
- `scripts/face_detect/app.py`

- [ ] **Step 1: Deploy the four files to `/sdcard/CamerAi`**

```
core/face_ai.py            → /sdcard/CamerAi/core/face_ai.py
core/face_db.py            → /sdcard/CamerAi/core/face_db.py
core/id_registry.py        → /sdcard/CamerAi/core/id_registry.py
scripts/face_detect/app.py → /sdcard/CamerAi/scripts/face_detect/app.py
```

- [ ] **Step 2: Hard power cycle**

- [ ] **Step 3: Verify launch + recognition**

Expected serial:
```
[face_detect] loading anchors...
[face_detect] loading det kmodel...
[face_detect] loading reg kmodel...
[face_detect] reg kmodel ready (512-dim)
[face_detect] db loaded: N face(s)
[face_detect] fc=30 ...
```
No `sensor already inited`, no hang before fc grows past 47.

- [ ] **Step 4: Board checklist**

```
[ ] 标题 人脸识别，底栏 list 图标 + 已注册 N/4
[ ] 无人脸/未注册：白框，fc 持续涨
[ ] K2 短按对脸：蜂鸣 + 彩框 + IDn，已注册 +1
[ ] 注册 4 张后第 5 次轮转覆盖
[ ] 已注册脸持续识别为彩框 ID
[ ] 返回菜单→重进：已注册脸仍识别（持久化）
[ ] 点 list → 浮层清除/保存
[ ] 点清除：蜂鸣 + 已注册 0/4 + 浮层关闭
[ ] 返回→重进：空库（清除持久化）
[ ] 点保存：浮层关闭，无行为变化
[ ] 跑 2-5 分钟不卡，UI 不消失，反复进出稳定
```

- [ ] **Step 5: Record board result**

If pass, append to `项目记录.md` and commit `docs(face_recognition): record board acceptance`. If fail, invoke `systematic-debugging` — do not patch blindly.

---

## Self-Review

- Spec coverage: register (Task 1-3,5), match (Task 2 database_search + Task 5 on_frame), clear (Task 2 clear + Task 5 overlay), deferred persistence (Task 2 flush_to_disk + Task 5 exit), deferred overlay close (Task 5), zero runtime SD I/O (Task 4 contract + Task 5), per-frame gc (kept), tests (Task 4), board acceptance (Task 8). No gap.
- Placeholder scan: no TBD/TODO; every code step shows full code.
- Type consistency: `has_pending()`, `_dirty`/`_clear_dirty`, `flush_to_disk`, `database_search(feature, db_features)`, `IdRegistry(fpioa, pin=0)` consistent across tasks.
