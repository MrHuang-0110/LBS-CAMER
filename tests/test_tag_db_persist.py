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
