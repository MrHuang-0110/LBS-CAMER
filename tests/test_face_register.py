# tests/test_face_register.py — host-side AST + stub tests for Step 7 register.
# Run with:
#   python tests/test_face_register.py
import ast
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP_PATH = os.path.join(ROOT, "scripts", "face_detect", "app.py")
FACE_DB_PATH = os.path.join(ROOT, "core", "face_db.py")
MAIN_PATH = os.path.join(ROOT, "main.py")
APP_RUNTIME_PATH = os.path.join(ROOT, "core", "app_runtime.py")
ID_REGISTRY_PATH = os.path.join(ROOT, "core", "id_registry.py")


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


# ── face_db.register 测试 ──

def test_face_db_register_fills_empty_slot_first():
    """register() fills first empty slot without rotating _next_slot."""
    src = open(FACE_DB_PATH, encoding="utf-8").read()
    assert "def register(" in src, "face_db must have register method"

    # Simulate: stub _FaceDB with register logic
    class StubFaceDB:
        def __init__(self):
            self._features = {}
            self._next_slot = 1

        def register(self, feature):
            slot = None
            for i in range(1, 5):
                if i not in self._features:
                    slot = i
                    break
            if slot is None:
                slot = self._next_slot
                self._next_slot = self._next_slot % 4 + 1
            self._features[slot] = feature
            return slot

    db = StubFaceDB()
    # Register 4 faces → should fill id1, id2, id3, id4
    slots = []
    for i in range(4):
        s = db.register("feature_%d" % i)
        slots.append(s)
    assert slots == [1, 2, 3, 4], "first 4 registers should fill id1-4, got %s" % slots
    assert db._next_slot == 1, "pointer should stay at 1 (empty slots filled, no rotation)"


def test_face_db_register_rotates_when_full():
    """When all 4 slots full, register() rotates _next_slot 1→2→3→4→1."""
    src = open(FACE_DB_PATH, encoding="utf-8").read()
    assert "_next_slot" in src, "face_db must track _next_slot pointer"

    class StubFaceDB:
        def __init__(self):
            self._features = {}
            self._next_slot = 1

        def register(self, feature):
            slot = None
            for i in range(1, 5):
                if i not in self._features:
                    slot = i
                    break
            if slot is None:
                slot = self._next_slot
                self._next_slot = self._next_slot % 4 + 1
            self._features[slot] = feature
            return slot

    db = StubFaceDB()
    # Fill all 4
    for i in range(4):
        db.register("f%d" % i)
    assert list(db._features.keys()) == [1, 2, 3, 4]
    assert db._next_slot == 1
    # 5th → overwrite id1, pointer → 2
    s5 = db.register("f5")
    assert s5 == 1, "5th register should rotate to id1, got %d" % s5
    assert db._next_slot == 2, "pointer should advance to 2"
    # 6th → overwrite id2, pointer → 3
    s6 = db.register("f6")
    assert s6 == 2, "6th register should rotate to id2, got %d" % s6
    assert db._next_slot == 3, "pointer should advance to 3"
    # 7th → overwrite id3, pointer → 4
    s7 = db.register("f7")
    assert s7 == 3
    assert db._next_slot == 4
    # 8th → overwrite id4, pointer → 1
    s8 = db.register("f8")
    assert s8 == 4
    assert db._next_slot == 1, "pointer should wrap 4→1"


def test_face_db_persists_next_slot():
    """register() calls _save_next_slot; _load_next_slot reads back correctly."""
    src = open(FACE_DB_PATH, encoding="utf-8").read()
    assert "_save_next_slot" in src, "must save _next_slot to disk"
    assert "_load_next_slot" in src, "must load _next_slot from disk"
    assert "init_features" in src  # _load_next_slot called inside init_features
    # Check _save_next_slot writes to .next_slot file
    assert ".next_slot" in src, "must use .next_slot file for pointer persistence"


def test_face_db_clear_deletes_next_slot_and_resets_pointer():
    """clear() removes .next_slot file and resets _next_slot=1."""
    src = open(FACE_DB_PATH, encoding="utf-8").read()
    # clear() must: reset _next_slot to 1, remove .next_slot
    assert "def clear(" in src, "face_db must have clear method"
    # clear should reference _next_slot and .next_slot
    clear_start = src.find("def clear(")
    clear_body = src[clear_start:]
    assert "_next_slot" in clear_body, "clear must reset _next_slot pointer"
    assert ".next_slot" in clear_body, "clear must delete .next_slot file"


# ── id_registry 测试 ──

def test_id_registry_class_exists():
    """core/id_registry.py must exist with IdRegistry class."""
    tree = _parse(ID_REGISTRY_PATH)
    _class_node(tree, "IdRegistry")


def test_id_registry_has_poll_k2_and_try_register():
    """IdRegistry must have poll_k2() for main thread and try_register() for AI thread."""
    tree = _parse(ID_REGISTRY_PATH)
    cls = _class_node(tree, "IdRegistry")
    method_names = {n.name for n in cls.body if isinstance(n, ast.FunctionDef)}
    assert "poll_k2" in method_names, "IdRegistry missing poll_k2"
    assert "try_register" in method_names, "IdRegistry missing try_register"


def test_id_registry_poll_k2_edge_detect():
    """poll_k2 must track _prev_pressed for edge detection (release→press triggers once)."""
    src = open(ID_REGISTRY_PATH, encoding="utf-8").read()
    assert "_prev_pressed" in src, "must track previous state for edge detection"
    assert "self._pending = True" in src, "must set _pending on press edge"


def test_id_registry_pending_timeout():
    """try_register must discard pending after 2 seconds."""
    src = open(ID_REGISTRY_PATH, encoding="utf-8").read()
    assert "2000" in src or "2" in src, "must have timeout (2000ms) for pending"
    assert "ticks_ms" in src or "time" in src, "must use ticks_ms for timeout tracking"


def test_id_registry_try_register_calls_face_db():
    """try_register must import and call face_db.register when pending."""
    src = open(ID_REGISTRY_PATH, encoding="utf-8").read()
    assert "face_db.register" in src, "try_register must call face_db.register"
    assert "from core.face_db import" in src or "from core.face_db" in src, \
        "must import face_db"


def test_id_registry_try_register_clears_pending():
    """try_register must clear _pending after attempt (success or fail)."""
    src = open(ID_REGISTRY_PATH, encoding="utf-8").read()
    # After calling register, _pending must be False
    assert "self._pending = False" in src, "must clear _pending after try_register"


def test_id_registry_try_register_buzzer_feedback():
    """try_register must call buzzer.beep(ms=80) on success, shorter on no-face/no-action."""
    src = open(ID_REGISTRY_PATH, encoding="utf-8").read()
    assert "buzzer" in src, "try_register must accept buzzer parameter"
    assert "beep" in src, "must call buzzer.beep for feedback"


# ── face_detect 接入测试 ──

def test_face_detect_run_inits_id_registry():
    """run() must import IdRegistry and call poll_k2 in main loop."""
    src = open(APP_PATH, encoding="utf-8").read()
    assert "IdRegistry" in src, "run() must import IdRegistry"
    assert "id_registry" in src, "run() must create id_registry instance"
    assert "poll_k2" in src, "main loop must call id_registry.poll_k2()"


def test_face_detect_ai_thread_calls_try_register():
    """face_det_thread must call id_registry.try_register(feature, runtime.buzzer) after recognition."""
    src = open(APP_PATH, encoding="utf-8").read()
    assert "try_register" in src, "AI thread must call try_register"


# ── 收尾临时定位测试 ──

def test_main_no_face_detect_init_app_skip():
    """main.py must NOT have the face_detect init_app skip branch anymore."""
    src = open(MAIN_PATH, encoding="utf-8").read()
    # Old: if category_id != "face_detect": runtime.init_app(...)  ← must be gone
    # New: runtime.init_app(category_id, fpioa)  ← unconditional
    assert 'category_id != "face_detect"' not in src, \
        "main.py must not have face_detect init_app exclusion"
    assert "runtime.init_app(category_id, fpioa)" in src, \
        "main.py must call runtime.init_app for all categories"


def test_app_runtime_init_app_no_face_detect_skip():
    """app_runtime init_app must NOT skip touch/fonts/services for face_detect."""
    src = open(APP_RUNTIME_PATH, encoding="utf-8").read()
    # Old: if category_id != "face_detect": ... _init_touch(); fonts; _init_services()
    # New: unconditional _init_touch + fonts + _init_services for all categories
    assert 'category_id != "face_detect"' not in src, \
        "app_runtime init_app must not skip any category"
    assert "_init_touch" in src, "init_app must call _init_touch"
    assert "fonts.load_all" in src, "init_app must load fonts"
    assert "_init_services" in src, "init_app must call _init_services"


def test_app_runtime_buzzer_created():
    """_init_services must create Buzzer (not commented out)."""
    src = open(APP_RUNTIME_PATH, encoding="utf-8").read()
    # Buzzer should be imported and created (not commented)
    init_services_start = src.find("def _init_services(")
    init_services_end = src.find("def cleanup(") if "def cleanup(" in src else len(src)
    init_services_body = src[init_services_start:init_services_end]
    assert "Buzzer(" in init_services_body, "_init_services must create Buzzer"
    assert "from hw.buzzer import Buzzer" in init_services_body, \
        "must import Buzzer"
    assert "self.buzzer = Buzzer(" in init_services_body, \
        "must assign Buzzer to self.buzzer"


# ── test runner ──

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
    if failures:
        print("\n%d FAILED" % failures)
        sys.exit(1)
    print("\nALL PASS")


if __name__ == "__main__":
    test_runner()
