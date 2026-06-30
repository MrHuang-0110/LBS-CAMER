# 道路识别(road_detect) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 新建 `scripts/road_detect/app.py` + `core/road_db.py`,复刻 color_detect UI/持久化,换并集阈值+逐行质心绿线算法,协议 0x07,单槽 ID1 无 KEY2。

**Architecture:** Host 侧 TDD 先写纯逻辑单测(RoadDB/并集阈值/逐行质心)→ 实现 core/road_db.py → 写 AST 契约测试 → 复制 color_detect 骨架改造 app.py → 接入 host_api/app_runtime/icon_cache → 逐项验通过。

**Tech Stack:** Python 3(host 测试) + K230 MicroPython(板端)。无 NPU / AI 依赖,纯 find_blobs + 逐行质心。db_store 安全 JSON 持久化。

---

## 文件分解

| 操作 | 文件 | 职责 |
|------|------|------|
| 新建 | `core/road_db.py` | RoadDB:单槽内存 DB,接口 save/get/clear,load/flush 通过 db_store |
| 新建 | `scripts/road_detect/app.py` | 主脚本:UI(复刻 color_detect)+on_frame(并集阈值+质心绿线)+save/clear |
| 新建 | `tests/test_road_db.py` | RoadDB 内存逻辑单测 |
| 新建 | `tests/test_road_db_persist.py` | RoadDB 磁盘持久化测试 |
| 新建 | `tests/test_road_detect_algorithm.py` | 并集阈值+逐行质心纯函数单测 |
| 新建 | `tests/test_road_detect_ast.py` | AST 契约验证(host_api/icon_cache/app_runtime 接入完整性) |
| 改 | `comm/host_api.py` | CATEGORY_TYPE 加 `"road_detect": TYPE_ROAD_DETECT` |
| 改 | `core/app_runtime.py` | `_channels_for` 加 road_detect(chn1 QVGA RGB565);`init_app` 加 preload_road_icons |
| 改 | `core/icon_cache.py` | 加 `preload_road_icons()` + `get_road_icon()`(读 road_detect_icon/) |
| 复制 | `resource/icons/road_detect_icon/` | 从 color_detect_icon/ 复制 back.png+list.png 到 road_detect_icon/ |

---

### Task 1: RoadDB 内存逻辑 + 测试

**Files:**
- Create: `tests/test_road_db.py`
- Create: `core/road_db.py`

- [ ] **Step 1: Write failing test for RoadDB.save**

在 `tests/test_road_db.py` 中:

```python
# tests/test_road_db.py — RoadDB 纯 Python 单测(无 MicroPython 依赖)
import sys, os, json, tempfile

# 测试时用内存路径,不需要真实文件系统
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

f_th = (10, 90, -20, 30, -30, 40)  # 标准 6 阈值
f_lab = (50, 5, 5)
f_rgb = 0xFF8844
f_samples = [((50, 5, 5), 0xFF8844), ((45, 3, 8), 0xEE7733), ((52, 6, 3), 0xDD6622)]


def test_save_returns_1():
    from core.road_db import RoadDB
    db = RoadDB()
    slot = db.save(f_th, f_lab, f_rgb, f_samples)
    assert slot == 1


def test_save_overwrites():
    from core.road_db import RoadDB
    db = RoadDB()
    db.save(f_th, f_lab, f_rgb, f_samples)
    th2 = (20, 80, -10, 20, -20, 50)
    lab2 = (40, 10, 10)
    rgb2 = 0x00FF00
    samples2 = [((40, 10, 10), 0x00FF00)]
    slot = db.save(th2, lab2, rgb2, samples2)
    assert slot == 1
    entry = db.get()
    assert entry is not None
    assert entry['threshold'] == th2
    assert entry['lab'] == lab2
    assert entry['rgb'] == rgb2
    assert entry['samples'] == samples2


def test_get_returns_none_when_empty():
    from core.road_db import RoadDB
    db = RoadDB()
    assert db.get() is None


def test_get_returns_entry_after_save():
    from core.road_db import RoadDB
    db = RoadDB()
    db.save(f_th, f_lab, f_rgb, f_samples)
    entry = db.get()
    assert entry is not None
    assert entry['threshold'] == f_th
    assert entry['lab'] == f_lab
    assert entry['rgb'] == f_rgb
    assert entry['samples'] == f_samples


def test_saved_is_false_initially():
    from core.road_db import RoadDB
    db = RoadDB()
    assert db.saved is False


def test_saved_is_true_after_save():
    from core.road_db import RoadDB
    db = RoadDB()
    db.save(f_th, f_lab, f_rgb, f_samples)
    assert db.saved is True


def test_clear_resets():
    from core.road_db import RoadDB
    db = RoadDB()
    db.save(f_th, f_lab, f_rgb, f_samples)
    db.clear()
    assert db.get() is None
    assert db.saved is False


def test_save_sets_dirty():
    from core.road_db import RoadDB
    db = RoadDB()
    assert not db._dirty
    db.save(f_th, f_lab, f_rgb, f_samples)
    assert db._dirty


def test_clear_sets_clear_dirty():
    from core.road_db import RoadDB
    db = RoadDB()
    db.save(f_th, f_lab, f_rgb, f_samples)
    db.clear()
    assert db._clear_dirty
    assert not db._dirty


def test_runner():
    import sys
    mod = sys.modules[__name__]
    fails = 0
    for n in sorted(dir(mod)):
        if n.startswith("test_") and callable(getattr(mod, n)):
            try:
                getattr(mod, n)()
                print("  PASS %s" % n)
            except Exception as e:
                print("  FAIL %s: %s" % (n, e))
                fails += 1
    assert fails == 0, "%d tests failed" % fails


if __name__ == "__main__":
    test_runner()
```

- [ ] **Step 2: Run test — verify it FAILS (RoadDB not found)**

Run: `python tests/test_road_db.py`
Expected: FAIL with `ModuleNotFoundError: No module named 'core.road_db'`

- [ ] **Step 3: Write minimal RoadDB implementation**

在 `core/road_db.py` 中:

```python
# core/road_db.py — 道路识别 ID 内存数据库
#
# 镜像 ColorDB 的内存 + flush_to_disk 模式,但:
#   - 只存 1 个配置(单片 ID1)
#   - save() 覆盖写入(不轮转)
#   - 多存 samples 数组(左表 3 槽采色历史,供重启还原 UI)
#
# 纯 Python(无 MicroPython 依赖)→ 可在 host 端真单元测试。

from core import db_store


class RoadDB:
    """道路配置内存库。单槽存 6 阈值 + 中心 LAB + RGB + 采色历史。"""

    def __init__(self):
        self._entry = None         # dict: {'threshold':th,'lab':lab,'rgb':rgb,'samples':samples}
        self._dirty = False
        self._clear_dirty = False

    @property
    def saved(self):
        """是否已保存(有配置)。"""
        return self._entry is not None

    def save(self, threshold, lab, rgb, samples):
        """覆盖保存配置到 slot 1。返回 1。设 _dirty。"""
        self._entry = {
            'threshold': threshold,
            'lab': lab,
            'rgb': rgb,
            'samples': samples,
        }
        self._dirty = True
        self._clear_dirty = False
        print("[RoadDB] saved lab=%r -> ID1 (memory, dirty)" % (lab,))
        return 1

    def get(self):
        """取当前配置 dict 或 None。"""
        return self._entry

    def clear(self):
        """清内存,设 _clear_dirty。"""
        self._entry = None
        self._clear_dirty = True
        self._dirty = False
        print("[RoadDB] cleared (memory, clear_dirty)")

    def load_from_disk(self, path):
        """从磁盘加载配置。ENOENT 安全(通过 db_store)。无配置返回 None。"""
        data = db_store.load_json(path)
        if data is None:
            return None
        try:
            self._entry = {
                'threshold': tuple(data['threshold']),
                'lab': tuple(data['lab']),
                'rgb': int(data['rgb']),
                'samples': [(tuple(s[0]), int(s[1])) for s in data['samples']],
            }
            self._dirty = False
            self._clear_dirty = False
            print("[RoadDB] loaded from disk")
        except Exception as e:
            print("[RoadDB] load corrupt: %s" % e)
            self._entry = None
        return self._entry

    def flush_to_disk(self, path):
        """持久化到磁盘。dirty=True 写入;clear_dirty=True 写空文件(清磁盘);无变化跳过。"""
        if self._clear_dirty:
            db_store.save_json(path, None)
            self._clear_dirty = False
            print("[RoadDB] flushed clear to disk")
        elif self._dirty and self._entry is not None:
            data = {
                'threshold': list(self._entry['threshold']),
                'lab': list(self._entry['lab']),
                'rgb': self._entry['rgb'],
                'samples': [(list(s[0]), s[1]) for s in self._entry['samples']],
            }
            db_store.save_json(path, data)
            self._dirty = False
            print("[RoadDB] flushed to disk")
```

- [ ] **Step 4: Run tests — verify they PASS**

Run: `python tests/test_road_db.py`
Expected: all 8 tests PASS

- [ ] **Step 5: Commit**

```bash
git add core/road_db.py tests/test_road_db.py
git commit -m "feat(road_db): 道路识别单配置DB——save/get/clear+内存测试

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 2: RoadDB 磁盘持久化测试

**Files:**
- Create: `tests/test_road_db_persist.py`

- [ ] **Step 1: Write persistence test**

在 `tests/test_road_db_persist.py` 中:

```python
# tests/test_road_db_persist.py — RoadDB 磁盘持久化测试
import sys, os, tempfile, json

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

f_th = (10, 90, -20, 30, -30, 40)
f_lab = (50, 5, 5)
f_rgb = 0xFF8844
f_samples = [((50, 5, 5), 0xFF8844), ((45, 3, 8), 0xEE7733), ((52, 6, 3), 0xDD6622)]


def test_flush_and_load_roundtrip():
    from core.road_db import RoadDB
    db = RoadDB()
    db.save(f_th, f_lab, f_rgb, f_samples)
    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
        tpath = f.name
    try:
        db.flush_to_disk(tpath)
        os.stat(tpath)  # 文件必须存在

        db2 = RoadDB()
        entry = db2.load_from_disk(tpath)
        assert entry is not None
        assert entry['threshold'] == f_th
        assert entry['lab'] == f_lab
        assert entry['rgb'] == f_rgb
        assert len(entry['samples']) == 3
        assert db2.saved is True
    finally:
        os.unlink(tpath)


def test_flush_clear_writes_empty_file():
    from core.road_db import RoadDB
    db = RoadDB()
    db.save(f_th, f_lab, f_rgb, f_samples)
    db.clear()
    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
        tpath = f.name
    try:
        db.flush_to_disk(tpath)
        # clear 后应写 None
        db2 = RoadDB()
        result = db2.load_from_disk(tpath)
        assert result is None
    finally:
        os.unlink(tpath)


def test_load_from_missing_file_returns_none():
    from core.road_db import RoadDB
    db = RoadDB()
    result = db.load_from_disk("/nonexistent/road_db_test.json")
    assert result is None
    assert db.saved is False


def test_flush_no_change_skips():
    from core.road_db import RoadDB
    db = RoadDB()
    # 不 save,直接 flush —— dirty=False,clear_dirty=False → 跳过
    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
        tpath = f.name
    try:
        db.flush_to_disk(tpath)  # 不应 crash
        assert not os.path.exists(tpath) or os.path.getsize(tpath) == 0  # 未写入
    finally:
        if os.path.exists(tpath):
            os.unlink(tpath)


def test_runner():
    import sys
    mod = sys.modules[__name__]
    fails = 0
    for n in sorted(dir(mod)):
        if n.startswith("test_") and callable(getattr(mod, n)):
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
```

- [ ] **Step 2: Run test — verify PASS**

Run: `python tests/test_road_db_persist.py`
Expected: all 4 tests PASS

- [ ] **Step 3: Commit**

```bash
git add tests/test_road_db_persist.py
git commit -m "test(road_db): 磁盘持久化往返+clear写空+缺失文件+无变化跳过

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 3: 算法纯函数测试(并集阈值+逐行质心)

**Files:**
- Create: `tests/test_road_detect_algorithm.py`

- [ ] **Step 1: Write algorithm test**

在 `tests/test_road_detect_algorithm.py` 中:

```python
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
    th = (0, 100, -128, 127, -128, 127)  # 全范围,靠像素颜色区分
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
    th = (0, 100, -128, 127, -128, 127)
    centroids = _row_centroids([0, 0, 100, 100], fn, th, step=16)
    for cx, row_y in centroids:
        assert abs(cx - row_y) < 1.0, "diagonal road: centroid ~ row_y, got cx=%.1f at row %d" % (cx, row_y)


def test_row_centroids_no_road_pixels():
    """全背景:无道路像素,质心列表为空。"""
    bg = (0, 0, 0)
    pixel_map = {(x, y): bg for x in range(50) for y in range(50)}
    fn = _make_pixel_fn(pixel_map)
    th = (0, 100, -128, 127, -128, 127)
    centroids = _row_centroids([0, 0, 50, 50], fn, th, step=8)
    # 全黑 = 不在阈值内(任意阈值都不命中) → 每行 cnt=0 → 跳过
    assert len(centroids) == 0


def test_default_threshold():
    th = _default_threshold()
    assert th == (0, 100, -10, 10, -10, 10)


def test_runner():
    import sys
    mod = sys.modules[__name__]
    fails = 0
    for n in sorted(dir(mod)):
        if n.startswith("test_") and callable(getattr(mod, n)):
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
```

- [ ] **Step 2: Run test — verify PASS**

Run: `python tests/test_road_detect_algorithm.py`
Expected: all 7 tests PASS

- [ ] **Step 3: Commit**

```bash
git add tests/test_road_detect_algorithm.py
git commit -m "test(road_detect): 并集阈值+逐行质心纯函数单测(直道/斜道/空/部分采样)

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 4: AST 契约测试(接入验证)

**Files:**
- Create: `tests/test_road_detect_ast.py`

- [ ] **Step 1: Write AST contract test**

在 `tests/test_road_detect_ast.py` 中:

```python
# tests/test_road_detect_ast.py — host-side AST 契约测试(road_detect)
import ast, os, json

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP_RUNTIME_PATH = os.path.join(ROOT, "core", "app_runtime.py")
ICON_CACHE_PATH = os.path.join(ROOT, "core", "icon_cache.py")
HOST_API_PATH = os.path.join(ROOT, "comm", "host_api.py")
CATEGORIES_PATH = os.path.join(ROOT, "config", "categories.json")


def _read(path):
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def test_road_detect_in_categories_enabled():
    """categories.json 必须有 road_detect 条目且 enabled。"""
    with open(CATEGORIES_PATH, encoding="utf-8") as f:
        data = json.load(f)
    cats = data.get("categories", [])
    c = [x for x in cats if x.get("id") == "road_detect"]
    assert len(c) == 1, "road_detect must be in categories.json"
    assert c[0].get("enabled", False), "road_detect must be enabled"
    assert c[0]["script"] == "road_detect"
    assert c[0]["name_key"] == "category.road_detect"


def test_type_road_detect_in_host_api():
    """host_api 必须有 TYPE_ROAD_DETECT = 0x07。"""
    src = _read(HOST_API_PATH)
    assert "TYPE_ROAD_DETECT" in src
    assert "0x07" in src


def test_road_detect_in_category_type_map():
    """CATEGORY_TYPE 必须包含 'road_detect'。"""
    src = _read(HOST_API_PATH)
    assert '"road_detect":' in src
    assert "TYPE_ROAD_DETECT" in src.split('"road_detect":')[1][:80]


def test_channels_for_road_detect_qvga_rgb565():
    """_channels_for 必须为 road_detect 配 chn1 QVGA RGB565(同 color_detect)。"""
    src = _read(APP_RUNTIME_PATH)
    start = src.find("def _channels_for(")
    body = src[start:start + 1200]
    assert "road_detect" in body, "_channels_for must handle road_detect"
    assert "QVGA" in body.split('"road_detect"')[1][:200], "road_detect must use QVGA"
    assert "RGB565" in body.split('"road_detect"')[1][:200], "road_detect must use RGB565"


def test_preload_road_icons_in_init_app():
    """init_app 必须对 road_detect 调 preload_road_icons。"""
    src = _read(APP_RUNTIME_PATH)
    assert '"road_detect"' in src
    assert 'preload_road_icons' in src


def test_icon_cache_has_road_methods():
    """icon_cache 必须有 preload_road_icons + get_road_icon + _road_icons 槽。"""
    src = _read(ICON_CACHE_PATH)
    assert "def preload_road_icons" in src
    assert "def get_road_icon" in src
    assert "_road_icons" in src


def test_road_db_path_is_module_level():
    """_ROAD_DB_PATH 必须在模块级别定义。"""
    app_path = os.path.join(ROOT, "scripts", "road_detect", "app.py")
    src = _read(app_path)
    tree = ast.parse(src)
    module_names = []
    for n in tree.body:
        if isinstance(n, ast.Assign):
            for t in n.targets:
                if isinstance(t, ast.Name):
                    module_names.append(t.id)
    assert "_ROAD_DB_PATH" in module_names


def test_runner():
    import sys
    mod = sys.modules[__name__]
    fails = 0
    for n in sorted(dir(mod)):
        if n.startswith("test_") and callable(getattr(mod, n)):
            try:
                getattr(mod, n)()
                print("  PASS %s" % n)
            except Exception as e:
                print("  FAIL %s: %s" % (n, e))
                fails += 1
    assert fails == 0, "%d tests failed" % fails


if __name__ == "__main__":
    test_runner()
```

- [ ] **Step 2: Run test — verify FAILS (no road_detect app.py yet)**

Run: `python tests/test_road_detect_ast.py`
Expected: FAIL on `test_road_db_path_is_module_level` (app.py not found) and possibly on CATEGORY_TYPE/host_api/channel tests

- [ ] **Step 3: Commit**

```bash
git add tests/test_road_detect_ast.py
git commit -m "test(road_detect): AST契约测试(categories/host_api/channels/icon_cache/DB_path)

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 5: 基础设施修改(host_api + app_runtime + icon_cache + 图标)

**Files:**
- Modify: `comm/host_api.py`
- Modify: `core/app_runtime.py`
- Modify: `core/icon_cache.py`
- Copy files: `resource/icons/color_detect_icon/back.png` → `resource/icons/road_detect_icon/back.png`
- Copy files: `resource/icons/color_detect_icon/list.png` → `resource/icons/road_detect_icon/list.png`

- [ ] **Step 1: Add road_detect to host_api CATEGORY_TYPE**

在 `comm/host_api.py` 的 `CATEGORY_TYPE` dict 中,紧接 `"color_detect": TYPE_COLOR_DETECT` 之后添加一行:

```python
        "road_detect":  TYPE_ROAD_DETECT,   # 0x07
```

- [ ] **Step 2: Add road_detect to app_runtime _channels_for**

在 `core/app_runtime.py` 的 `_channels_for` 方法中,紧接 `elif category_id == "color_detect":` 分支之后添加:

```python
        elif category_id == "road_detect":
            # chn1 QVGA RGB565 专做 find_blobs 道路检测(同 color_detect)；
            # chn0 VGA RGB888 显示+取色。blob rect ×2 映射显示。
            chs.append((CAM_CHN_ID_1, Sensor.QVGA, Sensor.RGB565))
```

- [ ] **Step 3: Add road_detect icon preloading to init_app**

在 `core/app_runtime.py` 的 `init_app` 方法中,紧接 `elif category_id == "color_detect":` 分支之后添加:

```python
        elif category_id == "road_detect":
            icon_cache.preload_road_icons()
```

- [ ] **Step 4: Add preload_road_icons + get_road_icon to icon_cache**

在 `core/icon_cache.py` 的 `__init__` 中,紧接 `self._color_icons = {}` 之后添加一行:

```python
        self._road_icons = {}
```

添加方法(紧接 `get_color_icon` 方法之后):

```python
    def preload_road_icons(self):
        """预读道路识别APP图标（在首次 task_handler 之前调用）"""
        base = "/sdcard/CamerAi/resource/icons/road_detect_icon/"
        icons = {
            "list": base + "list.png",
            "back": base + "back.png",
        }
        for name, path in icons.items():
            try:
                with open(path, 'rb') as f:
                    data = bytearray(f.read())
                dsc = lv.img_dsc_t({
                    'data_size': len(data),
                    'data': data,
                })
                self._road_icons[name] = (data, dsc)
                print(f"[IconCache] road/{name} OK ({len(data)} bytes)")
            except Exception as e:
                print(f"[IconCache] road/{name} FAILED: {e}")

    def get_road_icon(self, name):
        """获取道路识别图标 (data, dsc)，未缓存返回 (None, None)"""
        return self._road_icons.get(name, (None, None))
```

- [ ] **Step 5: Copy icons from color_detect_icon to road_detect_icon**

```powershell
Copy-Item resource/icons/color_detect_icon/back.png resource/icons/road_detect_icon/back.png
Copy-Item resource/icons/color_detect_icon/list.png resource/icons/road_detect_icon/list.png
```

- [ ] **Step 6: Run AST tests — verify CATEGORY_TYPE/icon_cache/channels tests PASS**

Run: `python tests/test_road_detect_ast.py`
Expected: PASS on all tests EXCEPT `test_road_db_path_is_module_level` (still needs app.py)

- [ ] **Step 7: Commit**

```bash
git add comm/host_api.py core/app_runtime.py core/icon_cache.py resource/icons/road_detect_icon/
git commit -m "feat(road_detect): 基础设施——host_api CATEGORY_TYPE+channels+icon预加载+图标

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 6: 主脚本 `scripts/road_detect/app.py`

**Files:**
- Create: `scripts/road_detect/app.py`
- Create: `scripts/road_detect/__init__.py` (空文件,使模块可 import)

- [ ] **Step 1: Create __init__.py**

```python
# scripts/road_detect/__init__.py
```

- [ ] **Step 2: Write road_detect/app.py**

复制 `scripts/color_detect/app.py` 骨架,做以下适配:

1. 文件头注释改为 road_detect
2. import 替换:去掉 `from core.color_db import ColorDB`,替换为 `from core.road_db import RoadDB`;去掉 `from core.id_registry import IdRegistry`
3. 全局常量:`_ROAD_DB_PATH = "/sdcard/CamerAi/data/road_db.json"`
4. 去掉 `_id_registry`、`BOX_COLORS`(单槽 ID1,不用多色),`BOX_UNKNOWN`(统一绿色框)
5. 全局变量:去掉 `_id_registry`,加 `_road_db`
6. `_on_list_clicked` + `_on_save_clicked` + `_on_clear_clicked`:按第 6 节描述重写(保存=持久化当前并集阈值,清除=清 RoadDB + flush)
7. `_make_threshold`:`_apply_sample` 改为:取色更新 `_swatch` 后调 `_union_threshold` 更新 6 阈值格 + 滑块
8. `on_frame`:去掉 KEY2 注册逻辑 + 去掉注册色遍历,替换为:
   - `cur_th = _current_threshold_tuple()`
   - `find_blobs` → 最大 blob → 画道路 bbox 细绿框(thickness=2)
   - `_row_centroids(blob_rect, _get_pixel_fn, cur_th, step=8)` → `draw_line` 连绿折线 ×2 映射
   - 居中十字(同 color_detect)
   - `slots[0] = (1, bx*2, by*2, bw*2, bh*2, 100)` 若有 blob,否则 None
   - `host_tick(slots)`
9. `run()`:去掉 `_init_registry` 调用,去掉 `poll_k2`,加 `_process_overlay_close()`
10. `_destroy_ui`:去掉 `_id_registry` 相关
11. 计数标签:`_refresh_count` 显示"已保存 ID1"或"ID1"文案

完整 `app.py` 代码(因长度限制,关键差异区):

```python
# scripts/road_detect/app.py — 道路识别(LAB 阈值 find_blobs + 逐行质心绿线)。
#
# 复用 _template 单线程主循环 + color_detect UI。chn0 VGA RGB888 显示+取色,
# chn1 QVGA RGB565 find_blobs 检测。屏幕点击取色→RGB→LAB→±10 容差→3采样并集阈值。
# 逐行求道路像素质心 x,连绿色折线。默认 ID1,list 浮层"保存"直接持久化,协议 0x07。
# 左表 3 槽采色历史(底色=采样色),无 KEY2/IdRegistry。

import os
import sys
import time
import image
import lvgl as lv
from media.display import Display
from media.sensor import CAM_CHN_ID_0, CAM_CHN_ID_1
from core.icon_cache import icon_cache
from core.font_manager import fonts
from core.road_db import RoadDB

BAR_H = 52
PREVIEW_Y = BAR_H
PREVIEW_H = 376
BAR_BG = 0x1A1A1A
CARD_BG = 0x2A2A2A
CARD_ACTIVE = 0x2E7D32
DET_SCALE = 2
TOLERANCE = 10
_ROAD_DB_PATH = "/sdcard/CamerAi/data/road_db.json"
L_LO, L_HI = 0, 100
AB_LO, AB_HI = -128, 127

# 道路绿线/框颜色(ABGR):绿色
ROAD_GREEN = (0xFF, 0x00, 0xFF, 0x00)

THRESH_CELLS = [
    ("Lmin", "Lmin", 0, 100, 0),
    ("Lmax", "Lmax", 0, 100, 100),
    ("Amin", "Amin", -128, 127, -10),
    ("Amax", "Amax", -128, 127, 10),
    ("Bmin", "Bmin", -128, 127, -10),
    ("Bmax", "Bmax", -128, 127, 10),
]


def _draw_color(hex_color):
    r = (hex_color >> 16) & 0xFF
    g = (hex_color >> 8) & 0xFF
    b = hex_color & 0xFF
    return (0xFF, b, g, r)


def _rgb_to_lab(r, g, b):
    """sRGB [0,255] -> Lab。L:0-100, A/B:-128~127。"""
    def _linear(c):
        c = c / 255.0
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4
    rl = _linear(r); gl = _linear(g); bl = _linear(b)
    x = (rl * 0.4124564 + gl * 0.3575761 + bl * 0.1804375) / 0.95047
    y = (rl * 0.2126729 + gl * 0.7151522 + bl * 0.0721750) / 1.00000
    z = (rl * 0.0193339 + gl * 0.1191920 + bl * 0.9503041) / 1.08883
    def _f(t):
        return t ** (1/3) if t > 0.008856 else (7.787 * t + 16/116)
    fx = _f(x); fy = _f(y); fz = _f(z)
    L = 116 * fy - 16; A = 500 * (fx - fy); B = 200 * (fy - fz)
    L = max(0, min(100, round(L)))
    A = max(-128, min(127, round(A)))
    B = max(-128, min(127, round(B)))
    return (L, A, B)


def _make_threshold(lab):
    """LAB 中心值 -> 6 阈值 ±10 容差,裁剪。"""
    L, A, B = lab
    Lmin = max(0, L - 10); Lmax = min(100, L + 10)
    Amin = max(-128, A - 10); Amax = min(127, A + 10)
    Bmin = max(-128, B - 10); Bmax = min(127, B + 10)
    return (Lmin, Lmax, Amin, Amax, Bmin, Bmax)


def _union_threshold(samples):
    """3 采样并集阈值。"""
    if not samples:
        return None
    valid = [s for s in samples if s is not None]
    if not valid:
        return None
    ths = [_make_threshold(lab) for lab, _rgb in valid]
    Lmin = min(th[0] for th in ths); Lmax = max(th[1] for th in ths)
    Amin = min(th[2] for th in ths); Amax = max(th[3] for th in ths)
    Bmin = min(th[4] for th in ths); Bmax = max(th[5] for th in ths)
    return (Lmin, Lmax, Amin, Amax, Bmin, Bmax)


def _row_centroids(blob_rect, img_det, th, step=8):
    """逐行求道路像素 x 质心。"""
    import image as _img_mod
    x, y, w, h = blob_rect
    centroids = []
    for row_y in range(int(y), int(y + h), step):
        sum_x = 0; cnt = 0
        for col_x in range(int(x), int(x + w)):
            try:
                px = img_det.get_pixel(col_x, row_y)
            except Exception:
                continue
            if isinstance(px, (tuple, list)):
                r, g, b = int(px[0]), int(px[1]), int(px[2])
            elif isinstance(px, int):
                r = ((px >> 11) & 0x1F) << 3
                g = ((px >> 5) & 0x3F) << 2
                b = (px & 0x1F) << 3
            else:
                continue
            lab = _rgb_to_lab(r, g, b)
            L, A, B = lab
            Lmin, Lmax, Amin, Amax, Bmin, Bmax = th
            if (Lmin <= L <= Lmax) and (Amin <= A <= Amax) and (Bmin <= B <= Bmax):
                sum_x += col_x
                cnt += 1
        if cnt > 0:
            centroids.append((sum_x / cnt, row_y))
    return centroids


_RUNTIME = None
_screen = None
_top_bar = None
_bottom_bar = None
_preview = None
_table = None
_table_cells = {}
_table_rows = [None, None, None]
_count_label = None
_road_db = None
_slider = None
_thresh_labels = {}
_thresh_cells = {}
_selected_key = "Lmin"
_thresh_values = {"Lmin": 0, "Lmax": 100, "Amin": -10, "Amax": 10,
                  "Bmin": -10, "Bmax": 10}
_pending_click = None
_swatch = [None, None, None]
_overlay = None
_clear_btn = None
_save_btn = None
_close_overlay = False


def _select_cell(key):
    global _selected_key
    if _selected_key == key:
        _selected_key = None
        key = None
    else:
        _selected_key = key
    for k, cell in _thresh_cells.items():
        try:
            cell.set_style_bg_color(
                lv.color_hex(CARD_ACTIVE if k == key else CARD_BG), 0)
        except Exception:
            pass
    if _slider is not None:
        if key is not None:
            _slider.clear_flag(lv.obj.FLAG.HIDDEN)
            for k, _label, lo, hi, _dflt in THRESH_CELLS:
                if k == key:
                    _slider.set_range(lo, hi)
                    _slider.set_value(_thresh_values.get(key, lo), lv.ANIM.OFF)
                    break
        else:
            _slider.add_flag(lv.obj.FLAG.HIDDEN)


def _on_slider_changed(e):
    if e.get_code() != lv.EVENT.VALUE_CHANGED:
        return
    if _slider is None or _selected_key is None:
        return
    val = _slider.get_value()
    _thresh_values[_selected_key] = val
    lbl = _thresh_labels.get(_selected_key)
    if lbl is not None:
        try:
            lbl.set_text(str(val))
        except Exception:
            pass


def _make_cell(parent, key, label_text, lo, hi, dflt, align_x, cell_w):
    from ui.theme import make_back_bar_text_style
    cell = lv.btn(parent)
    cell.set_size(cell_w, 44)
    cell.align(lv.ALIGN.LEFT_MID, align_x, 0)
    cell.set_style_bg_color(
        lv.color_hex(CARD_ACTIVE if key == _selected_key else CARD_BG), 0)
    cell.set_style_bg_opa(255, 0)
    cell.set_style_text_color(lv.color_hex(0xFFFFFF), 0)
    cell.set_style_radius(6, 0)
    cell.set_style_border_width(0, 0)
    cell.set_style_shadow_width(0, 0)
    cell.set_style_pad_all(2, 0)

    name_lbl = lv.label(cell)
    name_lbl.set_text(label_text)
    name_lbl.add_style(make_back_bar_text_style(fonts.caption), 0)
    name_lbl.align(lv.ALIGN.TOP_MID, 0, 0)

    val_lbl = lv.label(cell)
    val_lbl.set_text(str(_thresh_values.get(key, dflt)))
    val_lbl.add_style(make_back_bar_text_style(fonts.body), 0)
    val_lbl.align(lv.ALIGN.BOTTOM_MID, 0, 0)
    _thresh_labels[key] = val_lbl

    def _on_click(e, _k=key):
        if e.get_code() == lv.EVENT.CLICKED:
            _select_cell(_k)
    cell.add_event(_on_click, lv.EVENT.CLICKED, None)
    _thresh_cells[key] = cell
    return cell


def _refresh_table():
    if _table is None:
        return
    for col in range(3):
        lbl = _table_cells.get((0, col))
        if lbl is not None:
            try:
                lbl.set_text(["L", "A", "B"][col])
            except Exception:
                pass
    for i in range(3):
        entry = _swatch[i]
        row_obj = _table_rows[i]
        if row_obj is not None:
            try:
                if entry is not None:
                    rgb = entry[1]
                    row_obj.set_style_bg_color(lv.color_hex(rgb), 0)
                    row_obj.set_style_bg_opa(255, 0)
                else:
                    row_obj.set_style_bg_color(lv.color_hex(0x222222), 0)
                    row_obj.set_style_bg_opa(180, 0)
            except Exception:
                pass
        for col in range(3):
            lbl = _table_cells.get((i + 1, col))
            if lbl is None:
                continue
            try:
                if entry is not None:
                    lbl.set_text(str(entry[0][col]))
                else:
                    lbl.set_text("-")
            except Exception:
                pass


def _refresh_count():
    if _count_label is not None and _RUNTIME is not None:
        try:
            saved = _road_db.saved if _road_db is not None else False
            key = "road_detect.saved" if saved else "road_detect.id1"
            _count_label.set_text(_RUNTIME.lang.t(key))
        except Exception:
            pass


def _on_preview_clicked(e):
    global _pending_click
    if e.get_code() != lv.EVENT.CLICKED:
        return
    if _overlay is not None:
        global _close_overlay
        _close_overlay = True
        return
    try:
        indev = lv.indev_get_act()
        if indev is not None:
            pt = lv.point_t()
            indev.get_point(pt)
            _pending_click = (pt.x, pt.y)
    except Exception as ex:
        print("[road_detect] get_point error: %s" % ex)


def _on_list_clicked(e):
    global _overlay, _clear_btn, _save_btn
    if e.get_code() != lv.EVENT.CLICKED:
        return
    if _overlay is not None:
        return
    from ui.theme import make_back_bar_text_style
    _overlay = lv.obj(lv.scr_act())
    _overlay.set_size(lv.pct(100), BAR_H)
    _overlay.set_pos(0, PREVIEW_Y + PREVIEW_H - BAR_H)
    _overlay.set_style_bg_color(lv.color_hex(BAR_BG), 0)
    _overlay.set_style_bg_opa(255, 0)
    _overlay.set_style_border_width(0, 0)
    _overlay.set_style_pad_all(0, 0)
    _overlay.set_style_radius(0, 0)
    _overlay.clear_flag(lv.obj.FLAG.SCROLLABLE)
    _overlay.add_flag(lv.obj.FLAG.CLICKABLE)
    _overlay.add_event(_on_overlay_clicked, lv.EVENT.CLICKED, None)

    _clear_btn = lv.btn(_overlay)
    _clear_btn.set_size(120, 40)
    _clear_btn.align(lv.ALIGN.LEFT_MID, 20, 0)
    cl = lv.label(_clear_btn)
    cl.set_text(_RUNTIME.lang.t("color_detect.clear"))
    cl.add_style(make_back_bar_text_style(fonts.body), 0)
    cl.center()
    _clear_btn.add_event(_on_clear_clicked, lv.EVENT.CLICKED, None)

    _save_btn = lv.btn(_overlay)
    _save_btn.set_size(120, 40)
    _save_btn.align(lv.ALIGN.RIGHT_MID, -20, 0)
    sv = lv.label(_save_btn)
    sv.set_text(_RUNTIME.lang.t("color_detect.save"))
    sv.add_style(make_back_bar_text_style(fonts.body), 0)
    sv.center()
    _save_btn.add_event(_on_save_clicked, lv.EVENT.CLICKED, None)


def _on_overlay_clicked(e):
    global _close_overlay
    if e.get_code() != lv.EVENT.CLICKED:
        return
    _close_overlay = True


def _on_clear_clicked(e):
    global _close_overlay
    if e.get_code() != lv.EVENT.CLICKED:
        return
    _road_db.clear()
    _road_db.flush_to_disk(_ROAD_DB_PATH)
    _refresh_count()
    if _RUNTIME is not None and _RUNTIME.buzzer is not None:
        _RUNTIME.buzzer.beep(ms=200)
    _close_overlay = True


def _on_save_clicked(e):
    global _close_overlay
    if e.get_code() != lv.EVENT.CLICKED:
        return
    cur_th = _current_threshold_tuple()
    lab_mid = ((cur_th[0] + cur_th[1]) // 2,
               (cur_th[2] + cur_th[3]) // 2,
               (cur_th[4] + cur_th[5]) // 2)
    latest_rgb = _swatch[2][1] if _swatch[2] is not None else 0xFFFFFF
    _road_db.save(cur_th, lab_mid, latest_rgb, list(_swatch))
    _road_db.flush_to_disk(_ROAD_DB_PATH)
    _refresh_count()
    if _RUNTIME is not None and _RUNTIME.buzzer is not None:
        _RUNTIME.buzzer.beep(ms=200)
    print("[road_detect] saved -> ID1 (lab=%r)" % (lab_mid,))
    _close_overlay = True


def _process_overlay_close():
    global _overlay, _clear_btn, _save_btn, _close_overlay
    if not _close_overlay:
        return
    _close_overlay = False
    for obj in (_clear_btn, _save_btn, _overlay):
        if obj is not None:
            try:
                obj.delete()
            except Exception:
                pass
    _clear_btn = None
    _save_btn = None
    _overlay = None


def _build_ui(runtime, exit_flag):
    global _screen, _top_bar, _bottom_bar, _preview, _table, _count_label, _slider
    screen = lv.scr_act()
    screen.set_style_bg_opa(0, 0)
    screen.add_flag(lv.obj.FLAG.CLICKABLE)
    _screen = screen

    _top_bar = lv.obj(screen)
    _top_bar.set_size(lv.pct(100), BAR_H)
    _top_bar.set_pos(0, 0)
    _top_bar.set_style_bg_color(lv.color_hex(BAR_BG), 0)
    _top_bar.set_style_bg_opa(255, 0)
    _top_bar.set_style_border_width(0, 0)
    _top_bar.set_style_pad_all(0, 0)
    _top_bar.set_style_radius(0, 0)
    _top_bar.clear_flag(lv.obj.FLAG.SCROLLABLE)

    btn = lv.obj(_top_bar)
    btn.set_size(64, 64)
    btn.align(lv.ALIGN.LEFT_MID, 2, 0)
    btn.set_style_bg_opa(0, 0)
    btn.set_style_border_width(0, 0)
    btn.set_style_shadow_width(0, 0)
    btn.set_style_outline_width(0, 0)
    btn.set_style_outline_opa(0, 0)
    btn.set_style_pad_all(0, 0)
    btn.clear_flag(lv.obj.FLAG.SCROLLABLE)
    btn.add_flag(lv.obj.FLAG.CLICKABLE)
    icon_data, icon_dsc = icon_cache.get_road_icon("back")
    if icon_dsc is not None and icon_data is not None:
        import struct
        w = h = 64
        if len(icon_data) >= 24:
            w = struct.unpack('>I', icon_data[16:20])[0]
            h = struct.unpack('>I', icon_data[20:24])[0]
        target = int(64 * 0.85)
        zoom = int(min(target / w, target / h) * 256) if w > 0 and h > 0 else 256
        zoom = max(8, min(zoom, 256))
        icon_img = lv.img(btn)
        icon_img.set_src(icon_dsc)
        icon_img.set_zoom(zoom)
        icon_img.center()
    else:
        lbl = lv.label(btn)
        lbl.set_text("<")
        lbl.center()

    def _on_back(e):
        if e.get_code() == lv.EVENT.CLICKED:
            if _RUNTIME is not None and _RUNTIME.buzzer is not None:
                _RUNTIME.buzzer.beep(ms=50)
            exit_flag[0] = True
    btn.add_event(_on_back, lv.EVENT.CLICKED, None)

    title = lv.label(_top_bar)
    title.set_text(runtime.lang.t("category.road_detect"))
    title.align(lv.ALIGN.CENTER, 0, 0)
    from ui.theme import make_back_bar_text_style
    title.add_style(make_back_bar_text_style(fonts.body), 0)

    _TABLE_X = 4
    _TABLE_Y = BAR_H + 4
    _TABLE_W = 150
    _ROW_H = 26
    _table = lv.obj(screen)
    _table.set_size(_TABLE_W, _ROW_H * 4)
    _table.set_pos(_TABLE_X, _TABLE_Y)
    _table.set_style_bg_opa(0, 0)
    _table.set_style_border_width(1, 0)
    _table.set_style_border_color(lv.color_hex(0x444444), 0)
    _table.set_style_pad_all(2, 0)
    _table.set_style_radius(0, 0)
    _table.clear_flag(lv.obj.FLAG.SCROLLABLE)
    _table.clear_flag(lv.obj.FLAG.CLICKABLE)
    _table_cells.clear()
    col_w = (_TABLE_W - 4) // 3
    for r in range(4):
        if r == 0:
            row_obj = _table
            row_obj.set_style_bg_color(lv.color_hex(0x333333), 0)
            row_obj.set_style_bg_opa(200, 0)
        else:
            row_obj = lv.obj(_table)
            row_obj.set_size(_TABLE_W - 4, _ROW_H)
            row_obj.set_pos(0, r * _ROW_H)
            row_obj.set_style_bg_color(lv.color_hex(0x222222), 0)
            row_obj.set_style_bg_opa(180, 0)
            row_obj.set_style_border_width(0, 0)
            row_obj.set_style_pad_all(0, 0)
            row_obj.set_style_radius(0, 0)
            row_obj.clear_flag(lv.obj.FLAG.SCROLLABLE)
            row_obj.clear_flag(lv.obj.FLAG.CLICKABLE)
            _table_rows[r - 1] = row_obj
        for c in range(3):
            cell_lbl = lv.label(row_obj)
            cell_lbl.set_pos(c * col_w + 2, 4)
            cell_lbl.add_style(make_back_bar_text_style(fonts.caption), 0)
            cell_lbl.set_style_text_color(lv.color_hex(0xFFFFFF), 0)
            _table_cells[(r, c)] = cell_lbl
    _refresh_table()

    _PREVIEW_W = 600
    _preview = lv.obj(screen)
    _preview.set_size(_PREVIEW_W, PREVIEW_H)
    _preview.set_pos(0, PREVIEW_Y)
    _preview.set_style_bg_opa(0, 0)
    _preview.set_style_border_width(0, 0)
    _preview.set_style_pad_all(0, 0)
    _preview.set_style_radius(0, 0)
    _preview.clear_flag(lv.obj.FLAG.SCROLLABLE)
    _preview.add_flag(lv.obj.FLAG.CLICKABLE)
    _preview.add_event(_on_preview_clicked, lv.EVENT.CLICKED, None)

    _slider = lv.slider(screen)
    _slider.set_size(20, 300)
    _slider.set_pos(612, 90)
    _slider.set_range(0, 100)
    _slider.set_value(_thresh_values[_selected_key], lv.ANIM.OFF)
    _slider.add_event(_on_slider_changed, lv.EVENT.VALUE_CHANGED, None)

    _bottom_bar = lv.obj(screen)
    _bottom_bar.set_size(lv.pct(100), BAR_H)
    _bottom_bar.set_pos(0, PREVIEW_Y + PREVIEW_H)
    _bottom_bar.set_style_bg_color(lv.color_hex(BAR_BG), 0)
    _bottom_bar.set_style_bg_opa(255, 0)
    _bottom_bar.set_style_border_width(0, 0)
    _bottom_bar.set_style_pad_all(0, 0)
    _bottom_bar.set_style_radius(0, 0)
    _bottom_bar.clear_flag(lv.obj.FLAG.SCROLLABLE)

    list_btn = lv.obj(_bottom_bar)
    list_btn.set_size(48, 48)
    list_btn.align(lv.ALIGN.LEFT_MID, 2, 0)
    list_btn.set_style_bg_opa(0, 0)
    list_btn.set_style_border_width(0, 0)
    list_btn.set_style_pad_all(0, 0)
    list_btn.clear_flag(lv.obj.FLAG.SCROLLABLE)
    list_btn.add_flag(lv.obj.FLAG.CLICKABLE)
    list_btn.add_event(_on_list_clicked, lv.EVENT.CLICKED, None)
    list_icon_data, list_icon_dsc = icon_cache.get_road_icon("list")
    if list_icon_dsc is not None and list_icon_data is not None:
        import struct
        iw = ih = 64
        if len(list_icon_data) >= 24:
            iw = struct.unpack('>I', list_icon_data[16:20])[0]
            ih = struct.unpack('>I', list_icon_data[20:24])[0]
        ltarget = int(48 * 0.85)
        lzoom = int(min(ltarget / iw, ltarget / ih) * 256) if iw > 0 and ih > 0 else 256
        lzoom = max(8, min(lzoom, 256))
        list_img = lv.img(list_btn)
        list_img.set_src(list_icon_dsc)
        list_img.set_zoom(lzoom)
        list_img.center()

    _COUNT_W = 90
    _cells_start = 56
    _cells_total = 640 - _cells_start - _COUNT_W
    _cell_w = _cells_total // 6
    for i, (key, label_text, lo, hi, dflt) in enumerate(THRESH_CELLS):
        _make_cell(_bottom_bar, key, label_text, lo, hi, dflt,
                   _cells_start + i * _cell_w, _cell_w - 4)

    count_label = lv.label(_bottom_bar)
    count_label.set_text(runtime.lang.t("road_detect.id1"))
    count_label.add_style(make_back_bar_text_style(fonts.body), 0)
    count_label.align(lv.ALIGN.RIGHT_MID, -8, 0)
    _count_label = count_label


def _current_threshold_tuple():
    return (_thresh_values["Lmin"], _thresh_values["Lmax"],
            _thresh_values["Amin"], _thresh_values["Amax"],
            _thresh_values["Bmin"], _thresh_values["Bmax"])


def _apply_sample(lab, rgb):
    """采样色压入左表 3 槽,并集刷新 6 阈值。"""
    global _swatch
    _swatch = [_swatch[1], _swatch[2], (lab, rgb)]
    # 并集阈值:从 3 槽计算并集
    union_th = _union_threshold(_swatch)
    if union_th is not None:
        _thresh_values["Lmin"] = union_th[0]
        _thresh_values["Lmax"] = union_th[1]
        _thresh_values["Amin"] = union_th[2]
        _thresh_values["Amax"] = union_th[3]
        _thresh_values["Bmin"] = union_th[4]
        _thresh_values["Bmax"] = union_th[5]
    for key in _thresh_labels:
        lbl = _thresh_labels[key]
        if lbl is not None:
            try:
                lbl.set_text(str(_thresh_values[key]))
            except Exception:
                pass
    if _slider is not None:
        for k, _label, lo, hi, _dflt in THRESH_CELLS:
            if k == _selected_key:
                _slider.set_range(lo, hi)
                _slider.set_value(_thresh_values[_selected_key], lv.ANIM.OFF)
                break
    _refresh_table()


def _find_largest_blob(img_det, th):
    """find_blobs 取最大 blob,无返回 None。"""
    try:
        th_list = [int(v) for v in th]
        blobs = img_det.find_blobs([th_list], pixels_threshold=30,
                                   area_threshold=30, merge=True)
    except Exception as e:
        print("[road_detect] find_blobs error: %s (th=%r)" % (e, th))
        return None
    if not blobs:
        return None
    best = max(blobs, key=lambda b: b.pixels())
    return best.rect()


def on_frame(img):
    """chn1 find_blobs 道路检测 -> 并集阈值 + 逐行质心绿线 + bbox -> host_tick(0x07)。"""
    if _RUNTIME is None:
        return
    img_det = _RUNTIME.sensor.snapshot(chn=CAM_CHN_ID_1)
    slots = [None, None, None, None]
    cur_th = _current_threshold_tuple()

    # 处理 pending_click 取色(chn1 RGB565 get_pixel)
    global _pending_click
    if _pending_click is not None:
        cx, cy = _pending_click
        _pending_click = None
        try:
            qx = max(0, min(cx // DET_SCALE, img_det.width() - 1))
            qy = max(0, min(cy // DET_SCALE, img_det.height() - 1))
            pixel = img_det.get_pixel(qx, qy)
            if isinstance(pixel, (tuple, list)):
                r, g, b = int(pixel[0]), int(pixel[1]), int(pixel[2])
            elif isinstance(pixel, int):
                r = ((pixel >> 11) & 0x1F) << 3
                g = ((pixel >> 5) & 0x3F) << 2
                b = (pixel & 0x1F) << 3
            else:
                raise ValueError("get_pixel returned %r" % type(pixel))
            lab = _rgb_to_lab(r, g, b)
            rgb_hex = ((r & 0xFF) << 16) | ((g & 0xFF) << 8) | (b & 0xFF)
            _apply_sample(lab, rgb_hex)
            cur_th = _current_threshold_tuple()
        except Exception as e:
            print("[road_detect] sample error: %s" % e)

    # 道路检测:find_blobs 取最大 blob
    rect = _find_largest_blob(img_det, cur_th)
    if rect is not None:
        x, y, w, h = [int(v) for v in rect]
        # 画道路 bbox 绿框(ch0 VGA, ×2 缩放)
        img.draw_rectangle(x * DET_SCALE, y * DET_SCALE,
                           w * DET_SCALE, h * DET_SCALE,
                           color=ROAD_GREEN, thickness=2)
        # 逐行质心:绿色折线
        centroids = _row_centroids([x, y, w, h], img_det, cur_th, step=8)
        if len(centroids) >= 2:
            for i in range(len(centroids) - 1):
                cx1 = int(centroids[i][0] * DET_SCALE)
                cy1 = int(centroids[i][1] * DET_SCALE)
                cx2 = int(centroids[i + 1][0] * DET_SCALE)
                cy2 = int(centroids[i + 1][1] * DET_SCALE)
                img.draw_line(cx1, cy1, cx2, cy2, color=ROAD_GREEN, thickness=4)
        # 报槽 1
        slots[0] = (1, x * DET_SCALE, y * DET_SCALE,
                    w * DET_SCALE, h * DET_SCALE, 100)

    # 居中绿色十字
    img.draw_cross(320, 240, color=ROAD_GREEN, size=20, thickness=2)

    if _RUNTIME is not None and _RUNTIME.host is not None:
        _RUNTIME.host_tick(slots)


def _destroy_ui():
    global _screen, _top_bar, _bottom_bar, _preview, _table, _count_label, _slider
    global _overlay, _clear_btn, _save_btn, _table_rows
    for obj in (_clear_btn, _save_btn, _overlay, _slider, _table,
                _top_bar, _bottom_bar, _preview, _count_label):
        if obj is not None:
            try:
                obj.delete()
            except Exception:
                pass
    _clear_btn = None; _save_btn = None; _overlay = None
    _slider = None; _table = None; _top_bar = None
    _bottom_bar = None; _preview = None; _count_label = None
    _thresh_labels.clear()
    _thresh_cells.clear()
    _table_cells.clear()
    _table_rows = [None, None, None]
    try:
        from ui.theme import Colors
        scr = lv.scr_act()
        scr.set_style_bg_color(lv.color_hex(Colors.BG), 0)
        scr.set_style_bg_opa(255, 0)
    except Exception:
        pass
    _screen = None


def run(runtime):
    global _RUNTIME, _road_db
    _RUNTIME = runtime
    _road_db = RoadDB()
    entry = _road_db.load_from_disk(_ROAD_DB_PATH)
    if entry is not None:
        # 还原 6 阈值
        th = entry['threshold']
        _thresh_values.update({
            "Lmin": th[0], "Lmax": th[1],
            "Amin": th[2], "Amax": th[3],
            "Bmin": th[4], "Bmax": th[5],
        })
        # 还原左表 3 槽
        global _swatch
        samples = entry.get('samples', [])
        while len(samples) < 3:
            samples.append(None)
        _swatch = samples[:3]
    exit_flag = [False]
    _build_ui(runtime, exit_flag)
    _refresh_count()
    # 还原后刷新 6 格标签
    for key in _thresh_labels:
        lbl = _thresh_labels[key]
        if lbl is not None:
            try:
                lbl.set_text(str(_thresh_values[key]))
            except Exception:
                pass
    _refresh_table()
    fc = 0
    try:
        while not exit_flag[0]:
            os.exitpoint()
            img = runtime.sensor.snapshot(chn=CAM_CHN_ID_0)
            try:
                on_frame(img)
            except Exception as e:
                print("[road_detect] on_frame error: %s" % e)
                try:
                    sys.print_exception(e)
                except Exception:
                    pass
            _process_overlay_close()
            Display.show_image(img, 0, 0, Display.LAYER_OSD1)
            time.sleep_ms(lv.task_handler())
            fc += 1
            if fc % 30 == 0:
                print("[road_detect] fc=%d" % fc)
    finally:
        _destroy_ui()
        if _road_db is not None:
            _road_db.flush_to_disk(_ROAD_DB_PATH)
        _RUNTIME = None
```

- [ ] **Step 3: Add i18n keys**

`resource/i18n/zh_CN.json`:在 `"color_detect": {...}` 块的最后一行 `"Bmax": "B最大"` 之后的 `},` 和 `"common":` 之间插入:

```json
  "road_detect": {
    "id1": "ID1",
    "saved": "已保存 ID1"
  },
```

结果如下:

```json
  },
  "color_detect": {
    "registered": "已学习 %d/4",
    ...
    "Bmax": "B最大"
  },
  "road_detect": {
    "id1": "ID1",
    "saved": "已保存 ID1"
  },
  "common": {
```

`resource/i18n/en_US.json`:在 `"color_detect": {...}` 块最后一行 `"Bmax": "Bmax"` 之后的 `},` 和 `"common":` 之间插入:

```json
  "road_detect": {
    "id1": "ID1",
    "saved": "Saved ID1"
  },
```

- [ ] **Step 4: Run AST tests — verify ALL PASS**

Run: `python tests/test_road_detect_ast.py`
Expected: all 7 tests PASS (all 7 include test_road_db_path_is_module_level now)

- [ ] **Step 5: Run all road_detect tests**

```bash
python tests/test_road_db.py
python tests/test_road_db_persist.py
python tests/test_road_detect_algorithm.py
python tests/test_road_detect_ast.py
```

Expected: all PASS

- [ ] **Step 6: Commit**

```bash
git add scripts/road_detect/__init__.py scripts/road_detect/app.py resource/i18n/zh_CN.json resource/i18n/en_US.json
git commit -m "feat(road_detect): 主脚本——复刻color_detect UI+并集阈值逐行质心绿线+RoadDB持久化+协议0x07

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 7: 集成测试 — 运行全部已有测试确保无回归

- [ ] **Step 1: Run full test suite**

```bash
python -m pytest tests/ -v --tb=short 2>&1 | head -80
```

或逐个运行:

```bash
for f in tests/test_*.py; do echo "=== $f ===" && python "$f"; done
```

- [ ] **Step 2: Verify host_api CATEGORY_TYPE test still passes**

```bash
python tests/test_host_api.py
```

- [ ] **Step 3: Verify no other test regressions**

Expected: all pre-existing tests still PASS; new road_detect tests all PASS.

- [ ] **Step 4: Final commit**

```bash
git add -A
git commit -m "test(road_detect): 全量回归通过——road_detect新测试+已有测试无回归

Co-Authored-By: Claude <noreply@anthropic.com>"
```
