# tests/test_tag_db.py — TagDB 真单元测试(纯 Python 可导入)
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "core"))

from tag_db import TagDB


def test_register_fills_empty_slot_first():
    db = TagDB()
    s1 = db.register(101)
    s2 = db.register(102)
    assert s1 == 1 and s2 == 2, "empty slots filled in order 1,2"
    assert db.count == 2


def test_register_round_robin_after_full():
    db = TagDB()
    for i in range(1, 5):
        assert db.register(i * 10) == i
    # 满4后覆盖 _next_slot(初始1),推进 1->2->3->4->1
    s5 = db.register(999)
    assert s5 == 1, "full db overwrites slot 1 (round-robin), got %r" % s5
    s6 = db.register(888)
    assert s6 == 2, "next overwrite slot 2"


def test_match_hit_returns_slot_and_score_one():
    db = TagDB()
    db.register(42)
    slot, score = db.match(42)
    assert slot == 1, "matched slot 1"
    assert score == 1.0, "exact match score = 1.0"


def test_match_miss_returns_none_zero():
    db = TagDB()
    db.register(42)
    slot, score = db.match(999)
    assert slot is None, "miss -> None slot"
    assert score == 0.0, "miss -> 0.0 score"


def test_match_empty_db():
    db = TagDB()
    slot, score = db.match(1)
    assert slot is None and score == 0.0


def test_match_qr_string_code_id():
    """QR payload 是字符串,code_id 类型由调用方决定。"""
    db = TagDB()
    db.register("http://example.com")
    slot, score = db.match("http://example.com")
    assert slot == 1 and score == 1.0
    assert db.match("other") == (None, 0.0)


def test_clear_empties_db():
    db = TagDB()
    db.register(1)
    db.register(2)
    db.clear()
    assert db.count == 0
    assert db.match(1) == (None, 0.0)


def test_flush_to_disk_is_noop_safe():
    """flush_to_disk 当前 no-op(持久化预留),调用不崩。"""
    db = TagDB()
    db.register(1)
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
