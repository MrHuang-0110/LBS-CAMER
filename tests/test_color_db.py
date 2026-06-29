# tests/test_color_db.py — ColorDB 纯 Python 单测(无 MicroPython 依赖)
import os, sys
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from core.color_db import ColorDB


def test_register_returns_slot_1_to_4():
    db = ColorDB()
    s1 = db.register(((40,60,-10,10,-10,10),(50,50,50)), rgb=0xFF0000)
    s2 = db.register(((70,90,20,40,10,30),(80,30,20)), rgb=0x00FF00)
    assert s1 == 1 and s2 == 2


def test_register_same_threshold_returns_existing_slot():
    db = ColorDB()
    th = ((40,60,-10,10,-10,10),(50,50,50))
    s1 = db.register(th, rgb=0xFF0000)
    s2 = db.register(th, rgb=0xFF0000)
    assert s1 == s2 == 1
    assert db.count == 1


def test_register_round_robin_after_4():
    db = ColorDB()
    for i in range(4):
        db.register(((i*10,i*10+10,0,0,0,0),(i*10,0,0)), rgb=0x111111*i)
    assert db.count == 4
    s5 = db.register(((100,110,0,0,0,0),(105,0,0)), rgb=0xFFFFFF)
    assert s5 == 1  # 轮转回到 slot 1


def test_match_exact_hit():
    db = ColorDB()
    th = ((40,60,-10,10,-10,10),(50,50,50))
    db.register(th, rgb=0xFF0000)
    slot, score = db.match(th)
    assert slot == 1 and score == 1.0


def test_match_miss():
    db = ColorDB()
    db.register(((40,60,-10,10,-10,10),(50,50,50)), rgb=0xFF0000)
    slot, score = db.match(((70,90,0,0,0,0),(80,0,0)))
    assert slot is None and score == 0.0


def test_clear_resets():
    db = ColorDB()
    db.register(((40,60,-10,10,-10,10),(50,50,50)), rgb=0xFF0000)
    db.clear()
    assert db.count == 0


def test_get_slot_returns_threshold_and_meta():
    db = ColorDB()
    th = ((40,60,-10,10,-10,10),(50,50,50))
    db.register(th, rgb=0xFF0000)
    entry = db.get_slot(1)
    assert entry is not None
    # threshold 字段只存 6 阈值(第一段),中心 LAB 单独存 lab —— find_blobs 直接用。
    assert entry['threshold'] == (40,60,-10,10,-10,10)
    assert entry['rgb'] == 0xFF0000
    assert entry['lab'] == (50,50,50)


def test_iter_slots():
    db = ColorDB()
    db.register(((40,60,-10,10,-10,10),(50,50,50)), rgb=0xFF0000)
    db.register(((70,90,20,40,10,30),(80,30,20)), rgb=0x00FF00)
    slots = list(db.iter_slots())
    assert len(slots) == 2
    assert all('threshold' in e and 'rgb' in e and 'lab' in e for e in slots)


def test_runner():
    failures = 0
    tests = [(n, f) for n, f in sorted(globals().items())
             if n.startswith("test_") and callable(f) and n != "test_runner"]
    for name, fn in tests:
        try:
            fn(); print("PASS %s" % name)
        except Exception as e:
            failures += 1; print("FAIL %s: %s" % (name, e))
    print("\n%s" % ("ALL PASS" if failures == 0 else "%d FAILED" % failures))
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    test_runner()
