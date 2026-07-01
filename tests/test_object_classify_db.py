# tests/test_object_classify_db.py — ObjectClassifyDB 纯 Python 单测(无 MicroPython 依赖)
import sys, os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

F_A = [1.0, 0.0, 0.0, 0.0]
F_A2 = [0.99, 0.01, 0.0, 0.0]   # 与 A 高度相似(余弦 ~0.9999)
F_B = [0.0, 1.0, 0.0, 0.0]      # 与 A 正交(余弦 0 → score 0.5)
F_C = [0.0, 0.0, 1.0, 0.0]


def test_register_returns_slot_id():
    from core.object_classify_db import ObjectClassifyDB
    db = ObjectClassifyDB()
    slot = db.register(F_A)
    assert slot == 1


def test_register_empty_slots_fill_in_order():
    from core.object_classify_db import ObjectClassifyDB
    db = ObjectClassifyDB()
    assert db.register(F_A) == 1
    assert db.register(F_B) == 2
    assert db.register(F_C) == 3
    assert db.count == 3


def test_register_round_robin_when_full():
    from core.object_classify_db import ObjectClassifyDB
    db = ObjectClassifyDB()
    db.register(F_A)  # slot 1
    db.register(F_B)  # slot 2
    db.register(F_C)  # slot 3
    db.register([0.0, 0.0, 0.0, 1.0])  # slot 4
    slot = db.register([1.0, 1.0, 0.0, 0.0])  # 覆盖 slot 1
    assert slot == 1
    slot = db.register([1.0, 1.0, 1.0, 0.0])  # 覆盖 slot 2
    assert slot == 2
    assert db.count == 4


def test_database_search_match_returns_slot_and_score():
    from core.object_classify_db import ObjectClassifyDB, database_search
    db = ObjectClassifyDB()
    db.register(F_A)
    slot, score = database_search(F_A, db.get_features())
    assert slot == 1
    assert score > 0.99


def test_database_search_similar_matches():
    from core.object_classify_db import ObjectClassifyDB, database_search
    db = ObjectClassifyDB()
    db.register(F_A)
    slot, score = database_search(F_A2, db.get_features(), threshold=0.9)
    assert slot == 1
    assert score > 0.9


def test_database_search_returns_none_for_unmatched():
    from core.object_classify_db import ObjectClassifyDB, database_search
    db = ObjectClassifyDB()
    db.register(F_A)
    slot, score = database_search(F_B, db.get_features(), threshold=0.7)
    assert slot is None
    assert score == 0.0


def test_default_threshold_rejects_orthogonal():
    """默认阈值下正交特征(cos=0 → score=0.5)不得命中(回归 body_db cos≥0 误命中坑)。"""
    from core.object_classify_db import ObjectClassifyDB, database_search
    db = ObjectClassifyDB()
    db.register(F_A)
    slot, score = database_search(F_B, db.get_features())  # 默认阈值
    assert slot is None, "默认阈值过低:正交特征不应命中,得 slot=%s score=%s" % (slot, score)
    assert score == 0.0


def test_database_search_empty_db():
    from core.object_classify_db import ObjectClassifyDB, database_search
    db = ObjectClassifyDB()
    slot, score = database_search(F_A, db.get_features())
    assert slot is None
    assert score == 0.0


def test_cosine_score_mapping():
    """cosine_score = cos/2+0.5:同向量→1.0,正交→0.5,反向→0.0。"""
    from core.object_classify_db import cosine_score
    assert cosine_score(F_A, F_A) > 0.99
    assert abs(cosine_score(F_A, F_B) - 0.5) < 0.01
    assert abs(cosine_score(F_A, [-1.0, 0.0, 0.0, 0.0]) - 0.0) < 0.01


def test_cosine_score_zero_vector():
    from core.object_classify_db import cosine_score
    assert cosine_score([0.0, 0.0], F_A) == 0.0
    assert cosine_score(F_A, [0.0, 0.0, 0.0, 0.0]) == 0.0


def test_to_feature_list_passthrough_and_ndarray_like():
    from core.object_classify_db import to_feature_list
    assert to_feature_list([1, 2, 3]) == [1, 2, 3]
    # 伪装 ndarray:有 tolist 的对象
    class FakeArr:
        def tolist(self):
            return [4, 5, 6]
    assert to_feature_list(FakeArr()) == [4, 5, 6]


def test_clear_resets_all():
    from core.object_classify_db import ObjectClassifyDB, database_search
    db = ObjectClassifyDB()
    db.register(F_A)
    db.register(F_B)
    db.clear()
    assert db.count == 0
    slot, score = database_search(F_A, db.get_features())
    assert slot is None


def test_count_property():
    from core.object_classify_db import ObjectClassifyDB
    db = ObjectClassifyDB()
    assert db.count == 0
    db.register(F_A)
    assert db.count == 1
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
