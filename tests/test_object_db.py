# tests/test_object_db.py — ObjectDB 真单元测试(纯 Python 可导入)
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "core"))

from object_db import ObjectDB


def test_register_fills_empty_slot_first():
    db = ObjectDB()
    s1 = db.register(0)   # class_id=0 (person)
    s2 = db.register(39)  # class_id=39 (bottle)
    assert s1 == 1 and s2 == 2, "empty slots filled in order 1,2"
    assert db.count == 2


def test_register_round_robin_after_full():
    db = ObjectDB()
    for i in range(4):
        assert db.register(i) == i + 1
    # 满4后覆盖 _next_slot(初始1),推进 1->2->3->4->1
    s5 = db.register(10)
    assert s5 == 1, "full db overwrites slot 1 (round-robin), got %r" % s5
    s6 = db.register(11)
    assert s6 == 2, "next overwrite slot 2"


def test_register_same_class_returns_existing_slot():
    """同类不重复占槽:同一 class_id 再注册返回原槽,不推进指针。"""
    db = ObjectDB()
    s1 = db.register(0)
    s2 = db.register(0)   # 同类 person 再注册
    assert s1 == s2 == 1, "same class_id must return same slot, got %r/%r" % (s1, s2)
    assert db.count == 1, "same class must not occupy new slot, count=1"


def test_match_hit_returns_slot_and_score_one():
    db = ObjectDB()
    db.register(0)
    slot, score = db.match(0)
    assert slot == 1, "matched slot 1"
    assert score == 1.0, "exact match score = 1.0"


def test_match_miss_returns_none_zero():
    db = ObjectDB()
    db.register(0)
    slot, score = db.match(39)
    assert slot is None, "miss -> None slot"
    assert score == 0.0, "miss -> 0.0 score"


def test_match_empty_db():
    db = ObjectDB()
    slot, score = db.match(0)
    assert slot is None and score == 0.0


def test_clear_empties_db():
    db = ObjectDB()
    db.register(0)
    db.register(39)
    db.clear()
    assert db.count == 0
    assert db.match(0) == (None, 0.0)


def test_flush_to_disk_is_noop_safe():
    """flush_to_disk 当前 no-op(持久化预留),调用不崩。"""
    db = ObjectDB()
    db.register(0)
    db.clear()
    db.flush_to_disk()  # must not raise


def test_runner():
    failures = 0
    for name in sorted(n for n in globals() if n.startswith("test_") and n != "test_runner"):
        try:
            globals()[name]()
            print("PASS %s" % name)
        except Exception as e:
            failures += 1
            print("FAIL %s: %s" % (name, e))
    print("\n%s" % ("ALL PASS" if failures == 0 else "%d FAILED" % failures))
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    test_runner()
