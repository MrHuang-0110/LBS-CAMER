# tests/test_road_detect_algorithm.py — 道路识别算法纯函数单测(host 端)
import sys, os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


# === 并集阈值(从 color_detect 提取,用于被并集函数调) ===

def _make_threshold(lab):
    """LAB 中心值 -> 6 阈值 (Lmin,Lmax,Amin,Amax,Bmin,Bmax),容差 ±10,裁剪。"""
    L, A, B = lab
    Lmin = max(0, L - 10)
    Lmax = min(100, L + 10)
    Amin = max(-128, A - 10)
    Amax = min(127, A + 10)
    Bmin = max(-128, B - 10)
    Bmax = min(127, B + 10)
    return (Lmin, Lmax, Amin, Amax, Bmin, Bmax)


def _union_threshold(samples):
    """3 个采样的并集阈值。samples 是 [(lab, rgb), ...] 列表,无采样返回 None。

    每个采样取 lab -> _make_threshold ±10,然后 Lmin=min(各Lmin), Lmax=max(各Lmax),
    A/B 同理。少于 3 个时用已有样本并集。
    """
    if not samples:
        return None
    valid = [s for s in samples if s is not None]
    if not valid:
        return None
    ths = [_make_threshold(lab) for lab, _rgb in valid]
    Lmin = min(th[0] for th in ths)
    Lmax = max(th[1] for th in ths)
    Amin = min(th[2] for th in ths)
    Amax = max(th[3] for th in ths)
    Bmin = min(th[4] for th in ths)
    Bmax = max(th[5] for th in ths)
    return (Lmin, Lmax, Amin, Amax, Bmin, Bmax)


def _default_threshold():
    """无采样时的默认全范围(对齐 color_detect 默认)。"""
    return (0, 100, -10, 10, -10, 10)


# === 逐行质心 ===

def _row_centroids(find_blobs_fn, blob_rect, step=8):
    """逐行求道路质心 x。用 find_blobs 逐行 ROI(C 实现)替代逐像素 get_pixel+LAB,
    避免板端每帧上万次 Python 调用导致卡顿(对齐实验12 黑线循迹 demo 做法)。

    find_blobs_fn(row_y) -> [blob, ...]:该行道路 blob 列表(blob 有 cx()/pixels())。
    blob_rect: [x, y, w, h](大道路 blob 的 rect,限定逐行扫描范围)。
    step: 采样行间隔(默认 8)。
    返回: [(cx, row_y), ...] 质心点列表;每行取 pixels 最大的 blob 的 cx。
    """
    x, y, w, h = blob_rect
    centroids = []
    for row_y in range(int(y), int(y + h), step):
        blobs = find_blobs_fn(row_y)
        if blobs:
            best = max(blobs, key=lambda b: b.pixels())
            centroids.append((best.cx(), row_y))
    return centroids


# === 测试 ===

# 合成 find_blobs:_FakeBlob 模拟 K230 blob(有 cx()/pixels() 方法)。
# _make_find_blobs_fn(road_cx_by_row) 返回 find_blobs_fn(row_y) -> [_FakeBlob] or []。
# 用 find_blobs 逐行 ROI(C 实现)替代逐像素 get_pixel+LAB,避免板端卡顿。
class _FakeBlob:
    def __init__(self, cx, pixels):
        self._cx = cx
        self._px = pixels

    def cx(self):
        return self._cx

    def pixels(self):
        return self._px


def _make_find_blobs_fn(road_cx_by_row):
    """road_cx_by_row: {row_y: cx}。返回 find_blobs_fn(row_y) -> [_FakeBlob] or []。"""
    def fn(row_y):
        if row_y in road_cx_by_row:
            return [_FakeBlob(road_cx_by_row[row_y], 10)]
        return []
    return fn


def test_union_threshold_single_sample():
    samples = [((50, 0, 0), 0xFFFFFF)]
    th = _union_threshold(samples)
    # 单样本:±10
    assert th == (40, 60, -10, 10, -10, 10)


def test_union_threshold_two_samples():
    samples = [((30, -50, 0), 0), ((70, 50, 0), 0)]
    th = _union_threshold(samples)
    # sample1 L:20-40, A:-60~-40; sample2 L:60-80, A:40-60
    assert th[0] == 20   # Lmin = min(20, 60)
    assert th[1] == 80   # Lmax = max(40, 80)
    assert th[2] == -60  # Amin = min(-60, 40)
    assert th[3] == 60   # Amax = max(-40, 60)
    assert th[4] == -10  # Bmin = min(-10, -10)
    assert th[5] == 10   # Bmax = max(10, 10)


def test_union_threshold_partial_samples():
    """3 槽未满时用已有样本并集。"""
    samples = [((40, 5, -20), 0), None, None]
    th = _union_threshold(samples)
    assert th == (30, 50, -5, 15, -30, -10)


def test_union_threshold_empty():
    assert _union_threshold([]) is None
    assert _union_threshold([None, None, None]) is None


def test_row_centroids_straight_line():
    """笔直道路:每行道路质心 x=50。期望:所有质心 x=50。"""
    road = {y: 50 for y in range(0, 100, 8)}
    fn = _make_find_blobs_fn(road)
    centroids = _row_centroids(fn, [0, 0, 100, 100], step=8)
    assert len(centroids) > 0
    for cx, row_y in centroids:
        assert cx == 50, "straight road: centroid should be 50, got %r at row %d" % (cx, row_y)


def test_row_centroids_diagonal_line():
    """斜线道路:第 y 行质心 x=y。期望:质心随行线性偏移。"""
    road = {y: y for y in range(0, 100, 16)}
    fn = _make_find_blobs_fn(road)
    centroids = _row_centroids(fn, [0, 0, 100, 100], step=16)
    for cx, row_y in centroids:
        assert cx == row_y, "diagonal road: centroid ~ row_y, got %r at row %d" % (cx, row_y)


def test_row_centroids_no_road_pixels():
    """无道路(每行 find_blobs 返回 []):质心列表为空。"""
    fn = _make_find_blobs_fn({})
    centroids = _row_centroids(fn, [0, 0, 50, 50], step=8)
    assert len(centroids) == 0


def test_row_centroids_picks_largest_blob():
    """一行多 blob 时取 pixels 最大的那个的 cx。"""
    def fn(row_y):
        if row_y == 0:
            return [_FakeBlob(10, 5), _FakeBlob(80, 20)]  # 80 那个更大
        return []
    centroids = _row_centroids(fn, [0, 0, 100, 8], step=8)
    assert len(centroids) == 1
    assert centroids[0][0] == 80
    assert centroids[0][1] == 0


def test_default_threshold():
    th = _default_threshold()
    assert th == (0, 100, -10, 10, -10, 10)


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
