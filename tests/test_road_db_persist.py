# tests/test_road_db_persist.py — RoadDB 磁盘持久化测试
import sys, os, tempfile, json

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

f_th = (10, 90, -20, 30, -30, 40)
f_lab = (50, 5, 5)
f_rgb = 0xFF8844
f_samples = [((50, 5, 5), 0xFF8844), ((45, 3, 8), 0xEE7733), ((52, 6, 3), 0xDD6622)]


def test_flush_and_load_roundtrip():
    from core.road_db import RoadDB
    db = RoadDB()
    db.save(f_th, f_lab, f_rgb, f_samples)
    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
        tpath = f.name
    try:
        db.flush_to_disk(tpath)
        os.stat(tpath)  # 文件必须存在

        db2 = RoadDB()
        entry = db2.load_from_disk(tpath)
        assert entry is not None
        assert entry['threshold'] == f_th
        assert entry['lab'] == f_lab
        assert entry['rgb'] == f_rgb
        assert len(entry['samples']) == 3
        assert db2.saved is True
    finally:
        os.unlink(tpath)


def test_flush_clear_writes_empty_file():
    from core.road_db import RoadDB
    db = RoadDB()
    db.save(f_th, f_lab, f_rgb, f_samples)
    db.clear()
    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
        tpath = f.name
    try:
        db.flush_to_disk(tpath)
        # clear 后应写 None
        db2 = RoadDB()
        result = db2.load_from_disk(tpath)
        assert result is None
    finally:
        os.unlink(tpath)


def test_load_from_missing_file_returns_none():
    from core.road_db import RoadDB
    db = RoadDB()
    result = db.load_from_disk("/nonexistent/road_db_test.json")
    assert result is None
    assert db.saved is False


def test_flush_no_change_skips():
    from core.road_db import RoadDB
    db = RoadDB()
    # 不 save,直接 flush —— dirty=False,clear_dirty=False → 跳过
    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
        tpath = f.name
    try:
        db.flush_to_disk(tpath)  # 不应 crash
        assert not os.path.exists(tpath) or os.path.getsize(tpath) == 0  # 未写入
    finally:
        if os.path.exists(tpath):
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
