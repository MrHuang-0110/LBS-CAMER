# tests/test_body_db.py — BodyDB 纯 Python 单测(无 MicroPython 依赖)
import sys, os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# 测试用特征向量(plain list;database_search 纯 python cosine)
F_A = [1.0, 0.0, 0.0, 0.0]
F_A2 = [0.99, 0.01, 0.0, 0.0]   # 与 A 高度相似(余弦 ~0.9999)
F_B = [0.0, 1.0, 0.0, 0.0]      # 与 A 正交(余弦 0 → score 0.5)
F_C = [0.0, 0.0, 1.0, 0.0]


def test_register_returns_slot_id():
    from core.body_db import BodyDB
    db = BodyDB()
    slot = db.register(F_A)
    assert slot == 1


def test_register_empty_slots_fill_in_order():
    from core.body_db import BodyDB
    db = BodyDB()
    assert db.register(F_A) == 1
    assert db.register(F_B) == 2
    assert db.register(F_C) == 3
    assert db.count == 3


def test_register_round_robin_when_full():
    """满 4 槽后再注册 → 覆盖 _next_slot(1→2→3→4→1)。"""
    from core.body_db import BodyDB
    db = BodyDB()
    db.register(F_A)  # slot 1
    db.register(F_B)  # slot 2
    db.register(F_C)  # slot 3
    db.register([0.0, 0.0, 0.0, 1.0])  # slot 4
    # 满,第 5 个特征覆盖 slot 1(_next_slot=1)
    slot = db.register([1.0, 1.0, 0.0, 0.0])
    assert slot == 1
    # 再注册覆盖 slot 2
    slot = db.register([1.0, 1.0, 1.0, 0.0])
    assert slot == 2
    assert db.count == 4  # 仍是 4 槽


def test_database_search_match_returns_slot_and_score():
    from core.body_db import BodyDB, database_search
    db = BodyDB()
    db.register(F_A)  # slot 1
    slot, score = database_search(F_A, db.get_features())
    assert slot == 1
    assert score > 0.99  # 自匹配 score ~1.0


def test_database_search_similar_matches():
    """相似向量(余弦高)命中已注册槽。"""
    from core.body_db import BodyDB, database_search
    db = BodyDB()
    db.register(F_A)  # slot 1
    slot, score = database_search(F_A2, db.get_features(), threshold=0.9)
    assert slot == 1
    assert score > 0.9


def test_database_search_returns_none_for_unmatched():
    """正交向量(余弦 0 → score 0.5)低于阈值 → (None, 0.0)。"""
    from core.body_db import BodyDB, database_search
    db = BodyDB()
    db.register(F_A)  # slot 1
    slot, score = database_search(F_B, db.get_features(), threshold=0.7)
    assert slot is None
    assert score == 0.0


def test_database_search_empty_db():
    from core.body_db import BodyDB, database_search
    db = BodyDB()
    slot, score = database_search(F_A, db.get_features())
    assert slot is None
    assert score == 0.0


def test_database_search_dimension_agnostic():
    """不同维度向量不崩(维度匹配才计算,不匹配跳过)。"""
    from core.body_db import BodyDB, database_search
    db = BodyDB()
    db.register([1.0, 0.0, 0.0])  # 3 维
    # 4 维查询向量与 3 维库向量 zip 只算 3 个分量,不抛
    slot, score = database_search([1.0, 0.0, 0.0, 0.0], db.get_features())
    # 不崩即通过(具体 score 不断言,因 zip 截断)
    assert slot is None or isinstance(slot, int)


def test_clear_resets_all():
    from core.body_db import BodyDB, database_search
    db = BodyDB()
    db.register(F_A)
    db.register(F_B)
    db.clear()
    assert db.count == 0
    slot, score = database_search(F_A, db.get_features())
    assert slot is None


def test_count_property():
    from core.body_db import BodyDB
    db = BodyDB()
    assert db.count == 0
    db.register(F_A)
    assert db.count == 1
    db.register(F_B)
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
