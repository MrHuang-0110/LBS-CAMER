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
