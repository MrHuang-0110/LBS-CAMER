# tests/test_object_classify_lock_manual.py — manual 锁定模式网格搜索纯 Python 逻辑单测
import sys, os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

F_A = [1.0, 0.0, 0.0, 0.0]
F_A2 = [0.99, 0.01, 0.0, 0.0]   # 与 A 高度相似
F_B = [0.0, 1.0, 0.0, 0.0]      # 与 A 正交


def test_grid_centers_3x3_count():
    """3×3 网格 = 9 个候选中心。"""
    from core.object_classify_lock import grid_centers
    centers = grid_centers((100, 100), offset=20, bounds=(640, 480))
    assert len(centers) == 9


def test_grid_centers_relative_to_center():
    """候选中心 = center ± offset(3×3)。"""
    from core.object_classify_lock import grid_centers
    centers = grid_centers((100, 100), offset=20, bounds=(640, 480))
    assert (100, 100) in centers          # 中心本身
    assert (80, 80) in centers            # 左上
    assert (120, 120) in centers          # 右下
    assert (80, 100) in centers           # 正左
    assert (100, 120) in centers          # 正下


def test_grid_centers_clamps_to_bounds():
    """靠近边缘的候选中心 clamp 到画面内(不越界)。"""
    from core.object_classify_lock import grid_centers
    centers = grid_centers((5, 5), offset=20, bounds=(640, 480))
    for (cx, cy) in centers:
        assert 0 <= cx <= 640
        assert 0 <= cy <= 480
    # 左上候选 (5-20,5-20)=(-15,-15) clamp 到 (0,0)
    assert (0, 0) in centers


def test_grid_centers_integer():
    """候选中心为 int(ai2d.crop 需整数)。"""
    from core.object_classify_lock import grid_centers
    centers = grid_centers((100, 100), offset=20, bounds=(640, 480))
    for (cx, cy) in centers:
        assert isinstance(cx, int) and isinstance(cy, int)


def test_best_grid_match_finds_similar():
    """9 候选特征里找与 locked 最相似的(≥阈值)。"""
    from core.object_classify_lock import best_grid_match
    # 9 个候选,索引 4(中心)是与 A 相似的 F_A2,其余正交 F_B
    feats = [F_B, F_B, F_B, F_B, F_A2, F_B, F_B, F_B, F_B]
    idx, score = best_grid_match(F_A, feats)
    assert idx == 4
    assert score > 0.9


def test_best_grid_match_below_threshold_returns_none():
    """所有候选都正交(cos=0 → 0.5)<0.75 → 丢失,(None,0)。"""
    from core.object_classify_lock import best_grid_match
    feats = [F_B, F_B, F_B]
    idx, score = best_grid_match(F_A, feats)
    assert idx is None
    assert score == 0.0


def test_best_grid_match_empty():
    from core.object_classify_lock import best_grid_match
    idx, score = best_grid_match(F_A, [])
    assert idx is None
    assert score == 0.0


def test_best_grid_match_none_locked():
    from core.object_classify_lock import best_grid_match
    idx, score = best_grid_match(None, [F_A, F_B])
    assert idx is None
    assert score == 0.0


def test_best_grid_match_threshold_override():
    """降阈值后正交(0.5)可在 threshold=0.4 下命中。"""
    from core.object_classify_lock import best_grid_match
    idx, score = best_grid_match(F_A, [F_B], threshold=0.4)
    assert idx == 0
    assert abs(score - 0.5) < 0.01


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
