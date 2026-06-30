# tests/test_road_db.py — RoadDB 纯 Python 单测(无 MicroPython 依赖)
import sys, os, json, tempfile

# 测试时用内存路径,不需要真实文件系统
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

f_th = (10, 90, -20, 30, -30, 40)  # 标准 6 阈值
f_lab = (50, 5, 5)
f_rgb = 0xFF8844
f_samples = [((50, 5, 5), 0xFF8844), ((45, 3, 8), 0xEE7733), ((52, 6, 3), 0xDD6622)]


def test_save_returns_1():
    from core.road_db import RoadDB
    db = RoadDB()
    slot = db.save(f_th, f_lab, f_rgb, f_samples)
    assert slot == 1


def test_save_overwrites():
    from core.road_db import RoadDB
    db = RoadDB()
    db.save(f_th, f_lab, f_rgb, f_samples)
    th2 = (20, 80, -10, 20, -20, 50)
    lab2 = (40, 10, 10)
    rgb2 = 0x00FF00
    samples2 = [((40, 10, 10), 0x00FF00)]
    slot = db.save(th2, lab2, rgb2, samples2)
    assert slot == 1
    entry = db.get()
    assert entry is not None
    assert entry['threshold'] == th2
    assert entry['lab'] == lab2
    assert entry['rgb'] == rgb2
    assert entry['samples'] == samples2


def test_get_returns_none_when_empty():
    from core.road_db import RoadDB
    db = RoadDB()
    assert db.get() is None


def test_get_returns_entry_after_save():
    from core.road_db import RoadDB
    db = RoadDB()
    db.save(f_th, f_lab, f_rgb, f_samples)
    entry = db.get()
    assert entry is not None
    assert entry['threshold'] == f_th
    assert entry['lab'] == f_lab
    assert entry['rgb'] == f_rgb
    assert entry['samples'] == f_samples


def test_saved_is_false_initially():
    from core.road_db import RoadDB
    db = RoadDB()
    assert db.saved is False


def test_saved_is_true_after_save():
    from core.road_db import RoadDB
    db = RoadDB()
    db.save(f_th, f_lab, f_rgb, f_samples)
    assert db.saved is True


def test_clear_resets():
    from core.road_db import RoadDB
    db = RoadDB()
    db.save(f_th, f_lab, f_rgb, f_samples)
    db.clear()
    assert db.get() is None
    assert db.saved is False


def test_save_sets_dirty():
    from core.road_db import RoadDB
    db = RoadDB()
    assert not db._dirty
    db.save(f_th, f_lab, f_rgb, f_samples)
    assert db._dirty


def test_clear_sets_clear_dirty():
    from core.road_db import RoadDB
    db = RoadDB()
    db.save(f_th, f_lab, f_rgb, f_samples)
    db.clear()
    assert db._clear_dirty
    assert not db._dirty


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
                print("  FAIL %s: %s" % (n, e))
                fails += 1
    assert fails == 0, "%d tests failed" % fails


if __name__ == "__main__":
    test_runner()
