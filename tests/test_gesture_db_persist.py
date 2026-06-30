# tests/test_gesture_db_persist.py — GestureDB 磁盘持久化测试
import sys, os, tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def test_flush_and_load_roundtrip():
    from core.gesture_db import GestureDB
    db = GestureDB()
    db.register(0)  # slot 1: gun
    db.register(2)  # slot 2: yeah
    db.register(3)  # slot 3: five
    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
        tpath = f.name
    try:
        db.flush_to_disk(tpath)
        db2 = GestureDB()
        result = db2.load_from_disk(tpath)
        assert result is not None
        assert len(result) == 3
        slot, score = db2.match(0)
        assert slot == 1
        slot, score = db2.match(2)
        assert slot == 2
    finally:
        os.unlink(tpath)


def test_flush_clear_writes_empty():
    from core.gesture_db import GestureDB
    db = GestureDB()
    db.register(0)
    db.clear()
    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
        tpath = f.name
    try:
        db.flush_to_disk(tpath)
        db2 = GestureDB()
        result = db2.load_from_disk(tpath)
        assert result is not None  # load_from_disk returns dict (empty slots)
        assert len(result) == 0
    finally:
        os.unlink(tpath)


def test_load_from_missing_file_returns_none():
    from core.gesture_db import GestureDB
    db = GestureDB()
    result = db.load_from_disk("/nonexistent/gesture_db_test.json")
    assert result is None


def test_flush_empty_db_writes_valid_json():
    """flush 空 DB(未 register)照常写盘(镜像 ObjectDB)。"""
    from core.gesture_db import GestureDB
    db = GestureDB()
    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
        tpath = f.name
    try:
        db.flush_to_disk(tpath)  # 不应 crash
        db2 = GestureDB()
        result = db2.load_from_disk(tpath)
        assert result is not None  # 文件存在，内容为有效 JSON
        assert len(result) == 0
    finally:
        os.unlink(tpath)


def test_runner():
    import sys
    mod = sys.modules[__name__]
    fails = 0
    for n in sorted(dir(mod)):
        if n.startswith("test_") and callable(getattr(mod, n)) and n != "test_runner":
            try:
                getattr(mod, n)()
                print("  PASS %s" % n)
            except Exception as e:
                import traceback
                traceback.print_exc()
                print("  FAIL %s: %s" % (n, e))
                fails += 1
    assert fails == 0, "%d tests failed" % fails


if __name__ == "__main__":
    test_runner()
