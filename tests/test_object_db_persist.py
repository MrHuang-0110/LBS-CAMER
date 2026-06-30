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
