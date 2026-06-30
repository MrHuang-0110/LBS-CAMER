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
