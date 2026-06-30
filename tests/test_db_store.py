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
