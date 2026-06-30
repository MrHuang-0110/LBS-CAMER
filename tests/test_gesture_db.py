# tests/test_gesture_db.py — GestureDB 纯 Python 单测(无 MicroPython 依赖)
import sys, os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def test_register_returns_slot_id():
    from core.gesture_db import GestureDB
    db = GestureDB()
    slot = db.register(0)  # label_idx=0 (gun)
    assert slot == 1


def test_register_empty_slots_fill_in_order():
    from core.gesture_db import GestureDB
    db = GestureDB()
    assert db.register(0) == 1
    assert db.register(2) == 2  # yeah
    assert db.register(3) == 3  # five
    assert db.register(1) == 4  # other
    assert db.count == 4


def test_register_same_label_returns_existing_slot():
    """同类不重复占槽:已注册的 label_idx 再注册返回原槽,不推进指针。"""
    from core.gesture_db import GestureDB
    db = GestureDB()
    db.register(0)  # slot 1
    db.register(2)  # slot 2
    slot = db.register(0)  # 再注册 gun → 应返回 slot 1,不占新槽
    assert slot == 1
    assert db.count == 2


def test_register_round_robin_when_full():
    """满 4 槽后再注册新手势 → 覆盖 _next_slot(1→2→3→4→1)。"""
    from core.gesture_db import GestureDB
    db = GestureDB()
    db.register(0)  # slot 1: gun
    db.register(2)  # slot 2: yeah
    db.register(3)  # slot 3: five
    db.register(1)  # slot 4: other
    # 满,下一新手势覆盖 slot 1
    slot = db.register(0)  # gun 已存在 slot 1 → 返回 1(同类不重复占槽)
    assert slot == 1
    # 真正的新手势:label 0/2/3/1 都已注册,没有新 label 可注册
    # 注:只有 4 个手势标签,满 4 槽后再注册只能是重复(返回原槽)或覆盖


def test_match_returns_slot_and_score():
    from core.gesture_db import GestureDB
    db = GestureDB()
    db.register(0)  # slot 1: gun
    db.register(2)  # slot 2: yeah
    slot, score = db.match(2)
    assert slot == 2
    assert score == 1.0


def test_match_returns_none_for_unregistered_label():
    from core.gesture_db import GestureDB
    db = GestureDB()
    slot, score = db.match(0)
    assert slot is None
    assert score == 0.0


def test_match_empty_db():
    from core.gesture_db import GestureDB
    db = GestureDB()
    slot, score = db.match(0)
    assert slot is None
    assert score == 0.0


def test_clear_resets_all():
    from core.gesture_db import GestureDB
    db = GestureDB()
    db.register(0)
    db.register(2)
    db.clear()
    assert db.count == 0
    slot, score = db.match(0)
    assert slot is None


def test_count_property():
    from core.gesture_db import GestureDB
    db = GestureDB()
    assert db.count == 0
    db.register(0)
    assert db.count == 1
    db.register(2)
    assert db.count == 2
    db.register(0)  # 同类不重复占槽
    assert db.count == 2
    db.clear()
    assert db.count == 0


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
