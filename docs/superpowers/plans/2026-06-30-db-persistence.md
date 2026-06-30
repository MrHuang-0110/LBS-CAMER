# DB Persistence Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Persist face/tag/object/color registration data to SD card as JSON, loaded at startup and written immediately on each KEY2 register — without re-introducing the open-ENOENT freeze (pitfall #18 variant).

**Architecture:** A shared `core/db_store.py` centralizes ENOENT-safe JSON load (os.stat precheck, return None if absent) and save (ensure data dir + json.dump). Each DB adds `load_from_disk(path)` / `flush_to_disk(path)` using db_store, plus its own serialize/deserialize. Scripts call load at startup and flush right after a successful `try_register` (inside on_frame, before task_handler = pitfall #2 safe window).

**Tech Stack:** MicroPython on K230, JSON, ulab.numpy (face features, lazy import). PC-side tests run via `python` inline runner (pytest NOT installed; use Bash heredoc runner). Tests use `tempfile` for tmp paths.

---

## File Structure

- Create: `core/db_store.py` — shared ENOENT-safe JSON store: `ensure_data_dir()`, `load_json(path)`, `save_json(path, obj)`. Imports only `os`/`json` (PC-importable).
- Modify: `core/face_db.py` — add `_serialize()`/`load_from_disk(path)`/`flush_to_disk(path)`; `init_features()` loads from disk; ulab lazy.
- Modify: `core/tag_db.py` — add serialize/load/flush.
- Modify: `core/object_db.py` — add serialize/load/flush.
- Modify: `core/color_db.py` — add serialize/load/flush.
- Modify: `scripts/face_detect/app.py` — flush after try_register (line ~139); `init_features` already loads.
- Modify: `scripts/tag_detect/app.py` — load at startup (line ~433); flush after try_register (line ~131); exit flush pass path (line ~462).
- Modify: `scripts/object_detect/app.py` — load at startup (line ~409); flush after try_register (line ~149); exit flush pass path (line ~439).
- Modify: `scripts/color_detect/app.py` — load at startup (line ~717); flush after try_register (line ~667); exit flush pass path (line ~745).
- Create: `tests/test_db_store.py`, `tests/test_face_db_persist.py`, `tests/test_tag_db_persist.py`, `tests/test_object_db_persist.py`, `tests/test_color_db_persist.py`.

Path constants (defined where the DB is instantiated, i.e. in each script app.py, since tag_db has two instances with different paths):
- face: `/sdcard/CamerAi/data/face_db.json`
- tag april: `/sdcard/CamerAi/data/tag_april.json`
- tag qr: `/sdcard/CamerAi/data/tag_qr.json`
- object: `/sdcard/CamerAi/data/object_db.json`
- color: `/sdcard/CamerAi/data/color_db.json`

face_db is a singleton with a fixed path → define `FACE_DB_PATH` constant in `core/face_db.py`.

---

### Task 1: Shared ENOENT-safe JSON store

**Files:**
- Create: `core/db_store.py`
- Test: `tests/test_db_store.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_db_store.py`:

```python
# tests/test_db_store.py — ENOENT-safe JSON store (pitfall #18 guard)
import os
import tempfile

import importlib.util


def _load_db_store():
    spec = importlib.util.spec_from_file_location(
        "db_store", os.path.join(os.path.dirname(__file__), "..", "core", "db_store.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_load_json_absent_returns_none_without_open():
    """load_json on a non-existent path must return None via os.stat precheck,
    NOT raise / NOT call open (pitfall #18: open ENOENT pollutes K230 state)."""
    db_store = _load_db_store()
    absent = os.path.join(tempfile.gettempdir(), "definitely_absent_db_store.json")
    if os.path.exists(absent):
        os.remove(absent)
    assert db_store.load_json(absent) is None


def test_save_then_load_roundtrip():
    db_store = _load_db_store()
    path = os.path.join(tempfile.gettempdir(), "test_db_store_rt.json")
    try:
        db_store.save_json(path, {"next_slot": 3, "slots": {"1": 7, "2": 9}})
        loaded = db_store.load_json(path)
        assert loaded == {"next_slot": 3, "slots": {"1": 7, "2": 9}}
    finally:
        if os.path.exists(path):
            os.remove(path)


def test_save_json_creates_data_dir():
    db_store = _load_db_store()
    nested = os.path.join(tempfile.gettempdir(), "db_store_subdir", "x.json")
    try:
        db_store.save_json(nested, {"a": 1})
        assert os.path.exists(nested)
        assert db_store.load_json(nested) == {"a": 1}
    finally:
        if os.path.exists(nested):
            os.remove(nested)
        d = os.path.dirname(nested)
        if os.path.exists(d):
            os.rmdir(d)
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
python - <<'EOF'
import importlib.util, sys, os
spec = importlib.util.spec_from_file_location('t', 'tests/test_db_store.py')
m = importlib.util.module_from_spec(spec)
try:
    spec.loader.exec_module(m)
except Exception as e:
    print("IMPORT FAIL:", e); sys.exit(2)
fails = 0
for n in sorted(x for x in dir(m) if x.startswith('test_')):
    try:
        getattr(m, n)(); print('PASS', n)
    except AssertionError as e:
        fails += 1; print('FAIL', n, e)
    except Exception as e:
        fails += 1; print('FAIL', n, type(e).__name__, e)
sys.exit(1 if fails else 0)
EOF
```

Expected: FAIL — `core/db_store.py` does not exist (import error).

- [ ] **Step 3: Implement db_store**

Create `core/db_store.py`:

```python
# core/db_store.py — ENOENT-safe JSON persistence for ID databases
#
# 坑#18 变体红线：open() 对不存在文件抛 ENOENT 会污染 K230 FATFS/DMA 状态，
# 导致后续 Display/MediaManager/LVGL 在 GC 后卡死。本模块所有读盘走 os.stat
# 预检查，文件不存在直接返回 None，绝不触发 open(ENOENT)。
#
# 写盘 open(path,'w') 文件不存在会创建（不抛 ENOENT），但目录不存在会抛，
# 故 save_json 先 ensure_data_dir。
#
# 纯 Python（os/json），PC 可直接单元测试。

import os
import json

DATA_DIR = "/sdcard/CamerAi/data"


def ensure_data_dir(path=None):
    """确保 path 所在目录存在。path=None 时确保 DATA_DIR。MicroPython 无 os.makedirs。"""
    d = DATA_DIR if path is None else os.path.dirname(path)
    if not d:
        return
    try:
        os.stat(d)
    except Exception:
        try:
            os.mkdir(d)
        except Exception as e:
            print("[db_store] mkdir %s failed: %s" % (d, e))


def load_json(path):
    """读 JSON。os.stat 预检查，文件不存在返回 None（不 open，避 ENOENT）。
    损坏/解析失败返回 None，不抛。"""
    try:
        os.stat(path)
    except Exception:
        return None
    try:
        with open(path, 'r') as f:
            return json.load(f)
    except Exception as e:
        print("[db_store] load %s failed: %s" % (path, e))
        return None


def save_json(path, obj):
    """写 JSON。先 ensure 目录，再 open('w')（不存在会创建，不抛 ENOENT）。
    失败打印不抛（注册数据丢失可接受，卡死不可接受）。"""
    ensure_data_dir(path)
    try:
        with open(path, 'w') as f:
            json.dump(obj, f)
    except Exception as e:
        print("[db_store] save %s failed: %s" % (path, e))
```

- [ ] **Step 4: Run test to verify it passes**

Run the same heredoc runner as Step 2.

Expected: 3 PASS.

- [ ] **Step 5: Commit**

```bash
git add core/db_store.py tests/test_db_store.py
git commit -m "feat(db_store): ENOENT-safe JSON store for ID databases"
```

---

### Task 2: face_db persistence

**Files:**
- Modify: `core/face_db.py`
- Test: `tests/test_face_db_persist.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_face_db_persist.py`:

```python
# tests/test_face_db_persist.py — face_db JSON round-trip + ENOENT safety
import os
import tempfile

import importlib.util


def _load_face_db():
    spec = importlib.util.spec_from_file_location(
        "face_db", os.path.join(os.path.dirname(__file__), "..", "core", "face_db.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_face_db_flush_load_roundtrip():
    mod = _load_face_db()
    db = mod._FaceDB()
    feat = [0.1, 0.2, 0.3, 0.4]  # PC: plain list stands in for ulab ndarray
    slot = db.register(feat)
    assert slot == 1
    path = os.path.join(tempfile.gettempdir(), "test_face_rt.json")
    try:
        db.flush_to_disk(path)
        db2 = mod._FaceDB()
        loaded = db2.load_from_disk(path)
        assert loaded is not None
        assert 1 in db2._features
        got = db2._features[1]
        got_list = got.tolist() if hasattr(got, 'tolist') else list(got)
        assert [round(v, 4) for v in got_list] == [0.1, 0.2, 0.3, 0.4]
        # next_slot persisted too
        assert db2._next_slot == db._next_slot
    finally:
        if os.path.exists(path):
            os.remove(path)


def test_face_db_load_absent_returns_none():
    mod = _load_face_db()
    db = mod._FaceDB()
    absent = os.path.join(tempfile.gettempdir(), "absent_face_db.json")
    if os.path.exists(absent):
        os.remove(absent)
    assert db.load_from_disk(absent) is None
    assert db._features == {}


def test_face_db_clear_flushes_empty():
    mod = _load_face_db()
    db = mod._FaceDB()
    db.register([0.5, 0.6])
    path = os.path.join(tempfile.gettempdir(), "test_face_clear.json")
    try:
        db.flush_to_disk(path)
        db.clear()
        db.flush_to_disk(path)
        db2 = mod._FaceDB()
        db2.load_from_disk(path)
        assert db2._features == {}
        assert db2._next_slot == 1
    finally:
        if os.path.exists(path):
            os.remove(path)
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
python - <<'EOF'
import importlib.util, sys
spec = importlib.util.spec_from_file_location('t', 'tests/test_face_db_persist.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
fails = 0
for n in sorted(x for x in dir(m) if x.startswith('test_')):
    try:
        getattr(m, n)(); print('PASS', n)
    except (AssertionError, Exception) as e:
        fails += 1; print('FAIL', n, type(e).__name__, e)
sys.exit(1 if fails else 0)
EOF
```

Expected: FAIL — `_FaceDB` has no `flush_to_disk(path)` / `load_from_disk(path)` (current flush_to_disk takes no args, no load method).

- [ ] **Step 3: Implement face_db persistence**

In `core/face_db.py`:

3a. Add at top (after the existing `_DB_DIR`/`_NEXT_SLOT_PATH` lines, before `class _FaceDB`):

```python
import os
from core import db_store

FACE_DB_PATH = "/sdcard/CamerAi/data/face_db.json"
```

3b. Replace the `init_features` method body with:

```python
    def init_features(self):
        """加载已注册特征到内存（启动期，首次 task_handler 前的安全窗口）。

        从 FACE_DB_PATH 读 JSON（db_store os.stat 预检查，文件不存在返回空库，
        避坑#18 open ENOENT 污染）。
        """
        self._features = {}
        self._loaded = True
        self.load_from_disk(FACE_DB_PATH)
        print("[FaceDB] init_features: loaded %d face(s) from disk" % len(self._features))
        return self._features
```

3c. Replace the `flush_to_disk` method with:

```python
    def _serialize(self):
        """序列化为 JSON 可存结构。特征 ulab ndarray → list(float)。"""
        slots = {}
        for slot_id, feat in self._features.items():
            try:
                slots[str(slot_id)] = feat.tolist()
            except Exception:
                slots[str(slot_id)] = list(feat)
        return {"next_slot": self._next_slot, "slots": slots}

    def load_from_disk(self, path):
        """启动加载。db_store os.stat 预检查，文件不存在返回 None（空库，避 ENOENT）。"""
        data = db_store.load_json(path)
        if data is None:
            return None
        try:
            self._next_slot = data.get("next_slot", 1)
            slots = data.get("slots", {})
            for slot_str, feat_list in slots.items():
                try:
                    import ulab.numpy as np
                    feat = np.array(feat_list, dtype=np.float)
                except Exception:
                    feat = list(feat_list)  # PC / ulab 缺失兜底
                self._features[int(slot_str)] = feat
        except Exception as e:
            print("[FaceDB] load parse failed: %s" % e)
        return self._features

    def flush_to_disk(self, path=FACE_DB_PATH):
        """写盘。注册即写（on_frame 内 task_handler 前，坑#2 安全窗口），
        也作退出兜底。open('w') 不抛 ENOENT。"""
        db_store.save_json(path, self._serialize())
        self._dirty = False
        self._clear_dirty = False
        print("[FaceDB] flushed %d face(s) to %s" % (len(self._features), path))
```

- [ ] **Step 4: Run test to verify it passes**

Run the Step 2 heredoc.

Expected: 3 PASS.

- [ ] **Step 5: Commit**

```bash
git add core/face_db.py tests/test_face_db_persist.py
git commit -m "feat(face_db): JSON persistence with ENOENT-safe load"
```

---

### Task 3: tag_db persistence

**Files:**
- Modify: `core/tag_db.py`
- Test: `tests/test_tag_db_persist.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_tag_db_persist.py`:

```python
# tests/test_tag_db_persist.py — tag_db JSON round-trip + ENOENT safety
import os
import tempfile

import importlib.util


def _load_tag_db():
    spec = importlib.util.spec_from_file_location(
        "tag_db", os.path.join(os.path.dirname(__file__), "..", "core", "tag_db.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_tag_db_roundtrip_int_and_str():
    mod = _load_tag_db()
    db = mod.TagDB()
    db.register(7)          # AprilTag id (int)
    db.register("QR-PAY")   # QR payload (str)
    path = os.path.join(tempfile.gettempdir(), "test_tag_rt.json")
    try:
        db.flush_to_disk(path)
        db2 = mod.TagDB()
        db2.load_from_disk(path)
        assert db2._features[1] == 7
        assert db2._features[2] == "QR-PAY"
        assert db2._next_slot == db._next_slot
    finally:
        if os.path.exists(path):
            os.remove(path)


def test_tag_db_load_absent_empty():
    mod = _load_tag_db()
    db = mod.TagDB()
    absent = os.path.join(tempfile.gettempdir(), "absent_tag.json")
    if os.path.exists(absent):
        os.remove(absent)
    assert db.load_from_disk(absent) is None
    assert db._features == {}


def test_tag_db_clear_flushes_empty():
    mod = _load_tag_db()
    db = mod.TagDB()
    db.register(3)
    path = os.path.join(tempfile.gettempdir(), "test_tag_clear.json")
    try:
        db.flush_to_disk(path)
        db.clear()
        db.flush_to_disk(path)
        db2 = mod.TagDB()
        db2.load_from_disk(path)
        assert db2._features == {}
        assert db2._next_slot == 1
    finally:
        if os.path.exists(path):
            os.remove(path)
```

- [ ] **Step 2: Run test to verify it fails**

Run the inline heredoc runner (same pattern as Task 2 Step 2, pointing at `tests/test_tag_db_persist.py`).

Expected: FAIL — `TagDB` has no `load_from_disk` / `flush_to_disk(path)`.

- [ ] **Step 3: Implement tag_db persistence**

In `core/tag_db.py`, add at top (after the module docstring, before `class TagDB`):

```python
from core import db_store
```

Replace the `flush_to_disk` method with:

```python
    def _serialize(self):
        return {"next_slot": self._next_slot,
                "slots": {str(k): v for k, v in self._features.items()}}

    def load_from_disk(self, path):
        """启动加载。db_store os.stat 预检查，文件不存在返回 None（避 ENOENT）。"""
        data = db_store.load_json(path)
        if data is None:
            return None
        try:
            self._next_slot = data.get("next_slot", 1)
            for slot_str, code_id in data.get("slots", {}).items():
                self._features[int(slot_str)] = code_id
        except Exception as e:
            print("[TagDB] load parse failed: %s" % e)
        return self._features

    def flush_to_disk(self, path):
        """注册即写 / 退出兜底。open('w') 不抛 ENOENT。"""
        db_store.save_json(path, self._serialize())
        self._dirty = False
        self._clear_dirty = False
        print("[TagDB] flushed %d code(s) to %s" % (len(self._features), path))
```

- [ ] **Step 4: Run test to verify it passes**

Run the Step 2 heredoc.

Expected: 3 PASS.

- [ ] **Step 5: Commit**

```bash
git add core/tag_db.py tests/test_tag_db_persist.py
git commit -m "feat(tag_db): JSON persistence with ENOENT-safe load"
```

---

### Task 4: object_db persistence

**Files:**
- Modify: `core/object_db.py`
- Test: `tests/test_object_db_persist.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_object_db_persist.py`:

```python
# tests/test_object_db_persist.py — object_db JSON round-trip + ENOENT safety
import os
import tempfile

import importlib.util


def _load_object_db():
    spec = importlib.util.spec_from_file_location(
        "object_db", os.path.join(os.path.dirname(__file__), "..", "core", "object_db.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_object_db_roundtrip_dedup():
    mod = _load_object_db()
    db = mod.ObjectDB()
    db.register(0)   # person
    db.register(2)   # car
    db.register(0)   # dedup → returns slot 1, no new slot
    assert db.count == 2
    path = os.path.join(tempfile.gettempdir(), "test_object_rt.json")
    try:
        db.flush_to_disk(path)
        db2 = mod.ObjectDB()
        db2.load_from_disk(path)
        assert db2._features[1] == 0
        assert db2._features[2] == 2
        assert db2.count == 2
    finally:
        if os.path.exists(path):
            os.remove(path)


def test_object_db_load_absent_empty():
    mod = _load_object_db()
    db = mod.ObjectDB()
    absent = os.path.join(tempfile.gettempdir(), "absent_object.json")
    if os.path.exists(absent):
        os.remove(absent)
    assert db.load_from_disk(absent) is None
    assert db._features == {}
```

- [ ] **Step 2: Run test to verify it fails**

Run the inline heredoc runner pointing at `tests/test_object_db_persist.py`.

Expected: FAIL — `ObjectDB` has no `load_from_disk` / `flush_to_disk(path)`.

- [ ] **Step 3: Implement object_db persistence**

In `core/object_db.py`, add at top:

```python
from core import db_store
```

Replace the `flush_to_disk` method with:

```python
    def _serialize(self):
        return {"next_slot": self._next_slot,
                "slots": {str(k): v for k, v in self._features.items()}}

    def load_from_disk(self, path):
        """启动加载。db_store os.stat 预检查，文件不存在返回 None（避 ENOENT）。"""
        data = db_store.load_json(path)
        if data is None:
            return None
        try:
            self._next_slot = data.get("next_slot", 1)
            for slot_str, class_id in data.get("slots", {}).items():
                self._features[int(slot_str)] = class_id
        except Exception as e:
            print("[ObjectDB] load parse failed: %s" % e)
        return self._features

    def flush_to_disk(self, path):
        """注册即写 / 退出兜底。open('w') 不抛 ENOENT。"""
        db_store.save_json(path, self._serialize())
        self._dirty = False
        self._clear_dirty = False
        print("[ObjectDB] flushed %d class(es) to %s" % (len(self._features), path))
```

- [ ] **Step 4: Run test to verify it passes**

Run the Step 2 heredoc.

Expected: 2 PASS.

- [ ] **Step 5: Commit**

```bash
git add core/object_db.py tests/test_object_db_persist.py
git commit -m "feat(object_db): JSON persistence with ENOENT-safe load"
```

---

### Task 5: color_db persistence

**Files:**
- Modify: `core/color_db.py`
- Test: `tests/test_color_db_persist.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_color_db_persist.py`:

```python
# tests/test_color_db_persist.py — color_db JSON round-trip + ENOENT safety
import os
import tempfile

import importlib.util


def _load_color_db():
    spec = importlib.util.spec_from_file_location(
        "color_db", os.path.join(os.path.dirname(__file__), "..", "core", "color_db.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_color_db_roundtrip():
    mod = _load_color_db()
    db = mod.ColorDB()
    th = ((10, 20, -5, 5, -10, 10), (15, 0, 0))
    db.register(th, rgb=0xFF0000)
    path = os.path.join(tempfile.gettempdir(), "test_color_rt.json")
    try:
        db.flush_to_disk(path)
        db2 = mod.ColorDB()
        db2.load_from_disk(path)
        entry = db2.get_slot(1)
        assert entry is not None
        assert entry['threshold'] == (10, 20, -5, 5, -10, 10)
        assert entry['lab'] == [15, 0, 0] or entry['lab'] == (15, 0, 0)
        assert entry['rgb'] == 0xFF0000
    finally:
        if os.path.exists(path):
            os.remove(path)


def test_color_db_load_absent_empty():
    mod = _load_color_db()
    db = mod.ColorDB()
    absent = os.path.join(tempfile.gettempdir(), "absent_color.json")
    if os.path.exists(absent):
        os.remove(absent)
    assert db.load_from_disk(absent) is None
    assert db._features == {}
```

- [ ] **Step 2: Run test to verify it fails**

Run the inline heredoc runner pointing at `tests/test_color_db_persist.py`.

Expected: FAIL — `ColorDB` has no `load_from_disk` / `flush_to_disk(path)`.

- [ ] **Step 3: Implement color_db persistence**

In `core/color_db.py`, add at top:

```python
from core import db_store
```

Replace the `flush_to_disk` method with:

```python
    def _serialize(self):
        slots = {}
        for slot_id, entry in self._features.items():
            slots[str(slot_id)] = {
                "threshold": list(entry['threshold']),
                "lab": list(entry['lab']),
                "rgb": entry['rgb'],
            }
        return {"next_slot": self._next_slot, "slots": slots}

    def load_from_disk(self, path):
        """启动加载。db_store os.stat 预检查，文件不存在返回 None（避 ENOENT）。"""
        data = db_store.load_json(path)
        if data is None:
            return None
        try:
            self._next_slot = data.get("next_slot", 1)
            for slot_str, entry in data.get("slots", {}).items():
                # threshold 转回 tuple（find_blobs 比较用；json 存 list）
                self._features[int(slot_str)] = {
                    'threshold': tuple(entry['threshold']),
                    'lab': tuple(entry['lab']),
                    'rgb': entry['rgb'],
                }
        except Exception as e:
            print("[ColorDB] load parse failed: %s" % e)
        return self._features

    def flush_to_disk(self, path):
        """注册即写 / 退出兜底。open('w') 不抛 ENOENT。"""
        db_store.save_json(path, self._serialize())
        self._dirty = False
        self._clear_dirty = False
        print("[ColorDB] flushed %d color(s) to %s" % (len(self._features), path))
```

- [ ] **Step 4: Run test to verify it passes**

Run the Step 2 heredoc.

Expected: 2 PASS.

- [ ] **Step 5: Commit**

```bash
git add core/color_db.py tests/test_color_db_persist.py
git commit -m "feat(color_db): JSON persistence with ENOENT-safe load"
```

---

### Task 6: Wire face_detect to flush after register

**Files:**
- Modify: `scripts/face_detect/app.py` (around line 139 try_register, line 438 exit flush)
- Test: `tests/test_face_db_persist.py` (existing, regression) — no new test; behavior verified on board.

- [ ] **Step 1: Read the try_register call site**

Open `scripts/face_detect/app.py` around line 139. The current code:

```python
                slot = _id_registry.try_register(feature, _RUNTIME.buzzer)
```

- [ ] **Step 2: Add flush after successful register**

Replace that line with:

```python
                slot = _id_registry.try_register(feature, _RUNTIME.buzzer)
                if slot is not None:
                    face_db.flush_to_disk()  # 注册即写（on_frame 内，task_handler 前，坑#2 安全窗口）
```

- [ ] **Step 3: Fix the exit flush call**

At line ~438, the exit calls `face_db.flush_to_disk()` (no args). With the new signature `flush_to_disk(path=FACE_DB_PATH)`, the no-arg call still works (default arg). Verify the line reads:

```python
        face_db.flush_to_disk()
```

No change needed — the default arg handles it. (If the line passes an arg already, leave it.)

- [ ] **Step 4: Compile-check**

Run:

```bash
python -m compileall scripts/face_detect/app.py core/face_db.py
```

Expected: compiles, no error.

- [ ] **Step 5: Commit**

```bash
git add scripts/face_detect/app.py
git commit -m "feat(face_detect): flush face_db on register (persist immediately)"
```

---

### Task 7: Wire tag_detect to load + flush

**Files:**
- Modify: `scripts/tag_detect/app.py` (line ~131 try_register, ~433 DB instantiation, ~462 exit flush)

- [ ] **Step 1: Add path constants + load at startup**

At line ~433-434 (where `_april_db = TagDB()` / `_qr_db = TagDB()` are created), replace:

```python
    _april_db = TagDB()
    _qr_db = TagDB()
```

with:

```python
    _APRIL_DB_PATH = "/sdcard/CamerAi/data/tag_april.json"
    _QR_DB_PATH = "/sdcard/CamerAi/data/tag_qr.json"
    _april_db = TagDB()
    _qr_db = TagDB()
    _april_db.load_from_disk(_APRIL_DB_PATH)  # 启动加载（首次 task_handler 前安全窗口）
    _qr_db.load_from_disk(_QR_DB_PATH)
```

- [ ] **Step 2: Determine which DB is active at register time**

The register call at line ~131 uses `db.register` where `db` is the active DB (April or QR depending on selected mode). Read lines ~120-145 to see how the active DB is selected (likely `_active_db()`). The flush after register must flush the SAME db that was registered to.

- [ ] **Step 3: Add flush after successful register**

At line ~131-132, replace:

```python
        slot = _id_registry.try_register(code_id, _RUNTIME.buzzer,
                                         registrar=db.register)
```

with:

```python
        slot = _id_registry.try_register(code_id, _RUNTIME.buzzer,
                                         registrar=db.register)
        if slot is not None:
            _path = _APRIL_DB_PATH if db is _april_db else _QR_DB_PATH
            db.flush_to_disk(_path)  # 注册即写（on_frame 内，task_handler 前）
```

- [ ] **Step 4: Fix exit flush calls to pass path**

At lines ~462-464, replace:

```python
            _april_db.flush_to_disk()
            _qr_db.flush_to_disk()
```

with:

```python
            _april_db.flush_to_disk(_APRIL_DB_PATH)
            _qr_db.flush_to_disk(_QR_DB_PATH)
```

- [ ] **Step 5: Compile-check**

Run:

```bash
python -m compileall scripts/tag_detect/app.py core/tag_db.py
```

Expected: compiles, no error.

- [ ] **Step 6: Commit**

```bash
git add scripts/tag_detect/app.py
git commit -m "feat(tag_detect): load on startup + flush on register"
```

---

### Task 8: Wire object_detect to load + flush

**Files:**
- Modify: `scripts/object_detect/app.py` (line ~149 try_register, ~409 DB instantiation, ~439 exit flush)

- [ ] **Step 1: Add path constant + load at startup**

At line ~409, replace:

```python
    _db = ObjectDB()
```

with:

```python
    _OBJ_DB_PATH = "/sdcard/CamerAi/data/object_db.json"
    _db = ObjectDB()
    _db.load_from_disk(_OBJ_DB_PATH)  # 启动加载（首次 task_handler 前安全窗口）
```

- [ ] **Step 2: Add flush after successful register**

At lines ~149-150, replace:

```python
            slot = _id_registry.try_register(max_cid, _RUNTIME.buzzer,
                                             registrar=_db.register)
```

with:

```python
            slot = _id_registry.try_register(max_cid, _RUNTIME.buzzer,
                                             registrar=_db.register)
            if slot is not None:
                _db.flush_to_disk(_OBJ_DB_PATH)  # 注册即写（on_frame 内，task_handler 前）
```

- [ ] **Step 3: Fix exit flush call to pass path**

At line ~439, replace:

```python
            _db.flush_to_disk()
```

with:

```python
            _db.flush_to_disk(_OBJ_DB_PATH)
```

- [ ] **Step 4: Compile-check**

Run:

```bash
python -m compileall scripts/object_detect/app.py core/object_db.py
```

Expected: compiles, no error.

- [ ] **Step 5: Commit**

```bash
git add scripts/object_detect/app.py
git commit -m "feat(object_detect): load on startup + flush on register"
```

---

### Task 9: Wire color_detect to load + flush

**Files:**
- Modify: `scripts/color_detect/app.py` (line ~667 try_register, ~717 DB instantiation, ~745 exit flush)

- [ ] **Step 1: Add path constant + load at startup**

At line ~717, replace:

```python
    _color_db = ColorDB()
```

with:

```python
    _COLOR_DB_PATH = "/sdcard/CamerAi/data/color_db.json"
    _color_db = ColorDB()
    _color_db.load_from_disk(_COLOR_DB_PATH)  # 启动加载（首次 task_handler 前安全窗口）
```

- [ ] **Step 2: Add flush after successful register**

At lines ~667-669, replace:

```python
        slot = _id_registry.try_register(
            ...
            registrar=lambda th: _color_db.register(th, rgb=latest_rgb))
```

with (preserving the existing lambda and surrounding lines — only add the flush after):

```python
        slot = _id_registry.try_register(
            ...
            registrar=lambda th: _color_db.register(th, rgb=latest_rgb))
        if slot is not None:
            _color_db.flush_to_disk(_COLOR_DB_PATH)  # 注册即写（on_frame 内，task_handler 前）
```

(Read the exact lines 665-675 first; insert the `if slot is not None:` flush block immediately after the `try_register(...)` call, before the existing `print("[color_detect] registered -> ID%d ...")` line.)

- [ ] **Step 3: Fix exit flush call to pass path**

At line ~745, replace:

```python
            _color_db.flush_to_disk()
```

with:

```python
            _color_db.flush_to_disk(_COLOR_DB_PATH)
```

- [ ] **Step 4: Compile-check**

Run:

```bash
python -m compileall scripts/color_detect/app.py core/color_db.py
```

Expected: compiles, no error.

- [ ] **Step 5: Commit**

```bash
git add scripts/color_detect/app.py
git commit -m "feat(color_detect): load on startup + flush on register"
```

---

### Task 10: Full regression + board validation

**Files:**
- All test files + all changed sources.

- [ ] **Step 1: Run all DB persistence tests**

Run:

```bash
python - <<'EOF'
import importlib.util, sys
paths = [
    'tests/test_db_store.py',
    'tests/test_face_db_persist.py',
    'tests/test_tag_db_persist.py',
    'tests/test_object_db_persist.py',
    'tests/test_color_db_persist.py',
]
fails = 0
for p in paths:
    spec = importlib.util.spec_from_file_location('t', p)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    for n in sorted(x for x in dir(m) if x.startswith('test_')):
        try:
            getattr(m, n)(); print('PASS', p, n)
        except (AssertionError, Exception) as e:
            fails += 1; print('FAIL', p, n, type(e).__name__, e)
print('=== %d fail(s) ===' % fails)
sys.exit(1 if fails else 0)
EOF
```

Expected: all PASS.

- [ ] **Step 2: Compile-check all changed sources**

Run:

```bash
python -m compileall core/db_store.py core/face_db.py core/tag_db.py core/object_db.py core/color_db.py scripts/face_detect/app.py scripts/tag_detect/app.py scripts/object_detect/app.py scripts/color_detect/app.py
```

Expected: all compile, no error.

- [ ] **Step 3: Deploy to board**

Copy to SD card:

```text
/sdcard/CamerAi/core/db_store.py
/sdcard/CamerAi/core/face_db.py
/sdcard/CamerAi/core/tag_db.py
/sdcard/CamerAi/core/object_db.py
/sdcard/CamerAi/core/color_db.py
/sdcard/CamerAi/scripts/face_detect/app.py
/sdcard/CamerAi/scripts/tag_detect/app.py
/sdcard/CamerAi/scripts/object_detect/app.py
/sdcard/CamerAi/scripts/color_detect/app.py
```

(Delete any stale `/sdcard/CamerAi/data/*.json` first to test the absent-file path.)

- [ ] **Step 4: Board validation — first run (no data files)**

Boot, enter each of face/tag/object/color. Expected: no crash, no freeze (os.stat precheck returns empty DB, no open ENOENT). Register a few items.

- [ ] **Step 5: Board validation — restart persists**

Reset the board, re-enter the same script. Expected: previously registered items are loaded and recognized (face matches, tag/object/color registered items show colored ID boxes).

- [ ] **Step 6: Board validation — clear persists**

Use the list overlay to clear, then reset. Re-enter: expected empty DB.

- [ ] **Step 7: Board validation — main menu GC still safe**

Return to main menu, scroll/idle through a proactive GC (re-enable `MENU_DIAG_MEM=True` / `MENU_DIAG_FORCE_GC_AT_SEQ=5` temporarily). Expected: GC does not freeze (no new open ENOENT path introduced).

- [ ] **Step 8: Record result + commit**

Append to `项目记录.md`:

```text
## 2026-06-30 DB 持久化(子项目A)完成
- face/tag/object/color 注册数据 JSON 持久化,data/<db>.json
- 注册即写(on_frame 内 task_handler 前,坑#2 安全窗口)+ 启动加载(os.stat 预检查,避坑#18 ENOENT)
- 共享 core/db_store.py 集中 ENOENT-safe load/save
- PC 测试:test_db_store/face/tag/object/color_db_persist 全 PASS
- 板端:首次(无文件)不卡死 + 重启保留 + clear 重启空 + 主菜单 GC 仍不死
```

Commit:

```bash
git add 项目记录.md
git commit -m "docs(db_persist): record DB persistence completion"
```

---

## Self-Review

- Spec coverage: shared ENOENT-safe store (Task 1); 4 DBs load/flush (Tasks 2-5); face ulab handling (Task 2); scripts load-at-startup + flush-on-register (Tasks 6-9); data dir ensured (db_store.ensure_data_dir in save_json); ENOENT red line (db_store os.stat precheck, asserted in test_db_store + each DB's load_absent test); board validation incl. first-run-no-crash + GC-still-safe (Task 10). All spec sections covered.
- Placeholder scan: Task 7 Step 2 / Task 9 Step 2 instruct to read exact lines before editing — this is because the try_register call spans multiple lines (lambda) and the engineer must preserve surrounding code; the instruction is explicit ("read lines X-Y first; insert the if-block immediately after"). No TBD/TODO. All code blocks complete.
- Type consistency: `load_from_disk(path)` / `flush_to_disk(path)` signatures consistent across all 4 DBs and all script call sites. face_db `flush_to_disk(path=FACE_DB_PATH)` default preserves the existing no-arg exit call. `_serialize()` / `load_from_disk` / `flush_to_disk` names match between tests and implementation. db_store `load_json`/`save_json`/`ensure_data_dir` match between db_store.py and DB modules.
- One risk: Task 7 Step 2 notes tag_detect selects active DB via `_active_db()` — the flush uses `db is _april_db` identity check which is robust regardless of selection mechanism.
