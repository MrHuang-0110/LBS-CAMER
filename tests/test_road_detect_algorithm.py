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

def _row_centroids(blob_rect, get_pixel_fn, th, step=8):
    """在 blob rect 内每隔 step 行,逐像素判定是否在 LAB 阈值内,求该行 x 均值。

    blob_rect: [x, y, w, h]
    get_pixel_fn(x, y): 返回 (r,g,b) 或 RGB565 int(兼容 color_detect 取色模式)
    th: 6 阈值 (Lmin,Lmax,Amin,Amax,Bmin,Bmax)
    step: 采样行间隔(默认 8)
    返回: [(cx, row_y), ...] 质心点列表
    """
    # --- 内嵌轻量 sRGB→LAB(与 color_detect _rgb_to_lab 同算法,不依赖 import) ---
    def _pixel_in_threshold(px):
        # px 可能是 (r,g,b) tuple 或 int(RGB565)
        if isinstance(px, (tuple, list)):
            r, g, b = int(px[0]), int(px[1]), int(px[2])
        elif isinstance(px, int):
            r = ((px >> 11) & 0x1F) << 3
            g = ((px >> 5) & 0x3F) << 2
            b = (px & 0x1F) << 3
        else:
            return False
        # sRGB→linear
        def _linear(c):
            c = c / 255.0
            return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4
        rl, gl, bl = _linear(r), _linear(g), _linear(b)
        # sRGB→XYZ (D65)
        x = (rl * 0.4124564 + gl * 0.3575761 + bl * 0.1804375) / 0.95047
        y = (rl * 0.2126729 + gl * 0.7151522 + bl * 0.0721750) / 1.00000
        z = (rl * 0.0193339 + gl * 0.1191920 + bl * 0.9503041) / 1.08883
        def _f(t):
            return t ** (1/3) if t > 0.008856 else (7.787 * t + 16/116)
        fx, fy, fz = _f(x), _f(y), _f(z)
        L = 116 * fy - 16
        A = 500 * (fx - fy)
        B = 200 * (fy - fz)
        Lmin, Lmax, Amin, Amax, Bmin, Bmax = th
        return (Lmin <= L <= Lmax) and (Amin <= A <= Amax) and (Bmin <= B <= Bmax)

    x, y, w, h = blob_rect
    centroids = []
    for row_y in range(y, y + h, step):
        sum_x = 0
        cnt = 0
        for col_x in range(int(x), int(x + w)):
            try:
                px = get_pixel_fn(col_x, row_y)
            except Exception:
                continue
            if _pixel_in_threshold(px):
                sum_x += col_x
                cnt += 1
        if cnt > 0:
            centroids.append((sum_x / cnt, row_y))
    return centroids


# === 测试 ===

# 合成像素工厂:给定 map {(x,y): (r,g,b)} 返回 get_pixel_fn
def _make_pixel_fn(pixel_map):
    def _fn(x, y):
        return pixel_map.get((x, y), (0, 0, 0))
    return _fn


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
    """模拟笔直道路:blob 中央一列(50, 0)~(50, 99)为道路颜色,其余为背景。
    期望:每步 8 行质心 x≈50。"""
    road_color = (255, 0, 0)
    bg_color = (0, 0, 0)
    pixel_map = {}
    # 100×100 blob,road at x=50
    for y in range(100):
        for x in range(100):
            pixel_map[(x, y)] = road_color if x == 50 else bg_color
    fn = _make_pixel_fn(pixel_map)
    # Lmin=1 排除纯黑背景(LAB L=0.0 满足 0<=L<=100 会落在 0 起点上);
    # 红(255,0,0)→LAB L≈53.24,仍在 [1,100] 内 → 只命中道路像素。
    th = (1, 100, -128, 127, -128, 127)
    centroids = _row_centroids([0, 0, 100, 100], fn, th, step=8)
    # red pixel at x=50 every row → centroids all near 50
    assert len(centroids) > 0
    for cx, row_y in centroids:
        assert abs(cx - 50) < 1.0, "straight road: centroid should be ~50, got %.1f at row %d" % (cx, row_y)


def test_row_centroids_diagonal_line():
    """模拟斜线道路:第 y 行道路像素在 x=y(45° 对角线)。
    期望:质心随行线性偏移。"""
    road_color = (0, 255, 0)
    bg_color = (0, 0, 0)
    pixel_map = {}
    for y in range(100):
        for x in range(100):
            pixel_map[(x, y)] = road_color if x == y else bg_color
    fn = _make_pixel_fn(pixel_map)
    # Lmin=1 排除纯黑背景(LAB L=0);绿(0,255,0)→LAB L≈87.73 仍在内。
    th = (1, 100, -128, 127, -128, 127)
    centroids = _row_centroids([0, 0, 100, 100], fn, th, step=16)
    for cx, row_y in centroids:
        assert abs(cx - row_y) < 1.0, "diagonal road: centroid ~ row_y, got cx=%.1f at row %d" % (cx, row_y)


def test_row_centroids_no_road_pixels():
    """全背景:无道路像素,质心列表为空。"""
    bg = (0, 0, 0)
    pixel_map = {(x, y): bg for x in range(50) for y in range(50)}
    fn = _make_pixel_fn(pixel_map)
    # Lmin=1 排除纯黑(LAB L=0.0 < 1)→ 全黑场景每行 cnt=0 → 质心列表为空。
    th = (1, 100, -128, 127, -128, 127)
    centroids = _row_centroids([0, 0, 50, 50], fn, th, step=8)
    # 全黑 = 不在阈值内(Lmin=1 把 L=0 挡掉) → 每行 cnt=0 → 跳过
    assert len(centroids) == 0


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
