# tests/test_object_classify_lock.py — 锁定/点选纯 Python 逻辑单测
import sys, os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

F_A = [1.0, 0.0, 0.0, 0.0]
F_A2 = [0.99, 0.01, 0.0, 0.0]   # 与 A 高度相似
F_B = [0.0, 1.0, 0.0, 0.0]      # 与 A 正交
F_C = [0.0, 0.0, 1.0, 0.0]


def test_select_lock_index_finds_most_similar():
    from core.object_classify_lock import select_lock_index
    idx, score = select_lock_index(F_A2, [F_B, F_A, F_C])
    assert idx == 1
    assert score > 0.9


def test_select_lock_index_below_threshold_returns_none():
    """锁定特征与所有检测框都正交(cos=0 → score=0.5)<0.75 → 丢失,返回 (None,0)。"""
    from core.object_classify_lock import select_lock_index
    idx, score = select_lock_index(F_A, [F_B, F_C])
    assert idx is None
    assert score == 0.0


def test_select_lock_index_empty_features():
    from core.object_classify_lock import select_lock_index
    idx, score = select_lock_index(F_A, [])
    assert idx is None
    assert score == 0.0


def test_select_lock_index_none_locked():
    from core.object_classify_lock import select_lock_index
    idx, score = select_lock_index(None, [F_A, F_B])
    assert idx is None
    assert score == 0.0


def test_select_lock_index_threshold_override():
    """降阈值后正交特征(cos=0 → 0.5)可在 threshold=0.4 下命中。"""
    from core.object_classify_lock import select_lock_index
    idx, score = select_lock_index(F_A, [F_B], threshold=0.4)
    assert idx == 0
    assert abs(score - 0.5) < 0.01


def test_pick_box_at_point_hits():
    from core.object_classify_lock import pick_box_at_point
    boxes = [(10, 10, 100, 100), (200, 200, 50, 50)]
    assert pick_box_at_point(boxes, 50, 50) == 0
    assert pick_box_at_point(boxes, 220, 220) == 1


def test_pick_box_at_point_picks_smallest_containing():
    """点击嵌套框时,选最小(最具体)的那个。"""
    from core.object_classify_lock import pick_box_at_point
    boxes = [(0, 0, 400, 300), (100, 100, 80, 80)]  # 大框包小框
    assert pick_box_at_point(boxes, 140, 140) == 1  # 命中小框


def test_pick_box_at_point_miss_returns_none():
    from core.object_classify_lock import pick_box_at_point
    boxes = [(10, 10, 100, 100)]
    assert pick_box_at_point(boxes, 500, 500) is None
    assert pick_box_at_point([], 50, 50) is None


def test_pick_box_at_point_edge_inclusive():
    from core.object_classify_lock import pick_box_at_point
    boxes = [(10, 10, 100, 100)]
    assert pick_box_at_point(boxes, 10, 10) == 0   # 左上角含
    assert pick_box_at_point(boxes, 110, 110) == 0  # 右下角含


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
