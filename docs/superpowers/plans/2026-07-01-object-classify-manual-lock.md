# 物体分类:点击锁定任意物体(含未检测)Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 object_classify 的点击锁定支持锁任意物体——包括 YOLOv8n 没检测到的(点空白处也能锁,并 3×3 网格搜索逐帧跟随)。

**Architecture:** 点击命中 YOLO 框 → `locked_mode="yolo"`(走现有特征余弦跟踪,紧框);点击未命中 → `locked_mode="manual"`,在点击点 crop 固定大小区域提特征作 `locked_feature`,之后每帧在上一帧中心周围 3×3 网格(9 候选)crop 提特征,取余弦最高 ≥0.75 为新中心(黄框固定大小跟随);低于阈值自动解锁。纯 Python 的网格生成/匹配逻辑抽到 `object_classify_lock.py`(host 可单测),板端的 crop 提特征加到 `object_classify_ai.py`。

**Tech Stack:** MicroPython + nncase_runtime(K230 NPU);host CPython 3.13 跑纯 Python 单测。

**Spec:** `docs/superpowers/specs/2026-07-01-object-classify-design.md` §12

**已实现基线:** commit `01247b6`(初版 object_classify)。本计划在其上增量改 3 个文件。

**测试运行:** `python tests/<file>.py`(每个测试文件含 test_runner 自跑)。

---

## File Structure

**Modify:**
- `core/object_classify_lock.py` — 加纯 Python 函数 `grid_centers(center, offset, bounds)`、`best_grid_match(locked_feature, features, threshold)`。host 可单测。
- `core/object_classify_ai.py` — `FeatureExtractionApp` 加 `config_preprocess_center(cx, cy, box_size)`(按中心 crop 固定大小);`ObjectClassifyRecognition` 加 `extract_feature_at(img_np, cx, cy)` 与 `extract_features_at_centers(img_np, centers)`(板端专用,无 host 单测)。
- `scripts/object_classify/app.py` — on_frame 点击分支按命中/未命中设 `locked_mode`;跟踪分支按 `locked_mode` 走 yolo/manual 两路径。

**Create:**
- `tests/test_object_classify_lock_manual.py` — `grid_centers` / `best_grid_match` 纯 Python 单测。

**Board-only(无 host 单测,靠板端验收):** `object_classify_ai.py` 的新方法、`app.py` 的 on_frame 改动。

---

## Task 1: 锁定逻辑增量——grid_centers + best_grid_match(TDD)

纯 Python,host 可单测。先写测试 → Red → 实现 → Green → commit。

**Files:**
- Create: `tests/test_object_classify_lock_manual.py`
- Modify: `core/object_classify_lock.py`

- [ ] **Step 1: 写单测(test_object_classify_lock_manual.py)**

Create `tests/test_object_classify_lock_manual.py`:

```python
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
```

- [ ] **Step 2: 跑测试确认 Red**

Run: `python tests/test_object_classify_lock_manual.py`
Expected: FAIL — `ImportError: cannot import name 'grid_centers'` (函数尚未实现)

- [ ] **Step 3: 实现 grid_centers + best_grid_match**

Modify `core/object_classify_lock.py`:在文件末尾(`pick_box_at_point` 之后)追加两个函数。先 Read 确认文件末尾是 `pick_box_at_point` 的 return(第 53-54 行),在其后追加:

Find(文件末尾):
```python
    best_i = None
    best_area = None
    for i, (x, y, w, h) in enumerate(boxes):
        if x <= px <= x + w and y <= py <= y + h:
            a = w * h
            if best_area is None or a < best_area:
                best_area = a
                best_i = i
    return best_i
```

Replace with(追加两个函数):
```python
    best_i = None
    best_area = None
    for i, (x, y, w, h) in enumerate(boxes):
        if x <= px <= x + w and y <= py <= y + h:
            a = w * h
            if best_area is None or a < best_area:
                best_area = a
                best_i = i
    return best_i


def grid_centers(center, offset, bounds):
    """生成 3×3 网格候选中心(供 manual 锁定模式网格搜索)。

    Args:
        center: (cx, cy) 上一帧中心(显示空间或 rgb888p 空间,调用方统一)。
        offset: 搜索步长(各方向 ±offset),与 crop 边长一半同量级。
        bounds: (w, h) 画面尺寸,候选中心 clamp 到 [0, w]×[0, h]。
    Returns:
        list of (cx, cy) int,9 个(3×3),已 clamp 到 bounds 内。
    """
    cx, cy = center
    w, h = bounds
    centers = []
    for dy in (-offset, 0, offset):
        for dx in (-offset, 0, offset):
            nx = max(0, min(w, int(cx + dx)))
            ny = max(0, min(h, int(cy + dy)))
            centers.append((nx, ny))
    return centers


def best_grid_match(locked_feature, features, threshold=OBJECT_CLASSIFY_MATCH_THRESHOLD):
    """在网格候选特征列表 features 中找与 locked_feature 余弦最相似的。

    与 select_lock_index 同义(都是"在一组特征里找最高分"),独立命名以表达 manual
    网格搜索语义;threshold 默认 0.75(同 DB)。
    Returns:
        (index, score):最匹配索引与 score;无/低于阈值 → (None, 0.0)。
    """
    if not features or locked_feature is None:
        return None, 0.0
    best_i = None
    best_score = 0.0
    for i, f in enumerate(features):
        sc = cosine_score(locked_feature, f)
        if sc > best_score:
            best_score = sc
            best_i = i
    if best_score < threshold:
        return None, 0.0
    return best_i, best_score
```

- [ ] **Step 4: 跑测试确认 Green**

Run: `python tests/test_object_classify_lock_manual.py`
Expected: 全部 PASS(9 个测试,末行无 "tests failed")

- [ ] **Step 5: 回归既有 lock 测试**

Run: `python tests/test_object_classify_lock.py`
Expected: 全部 PASS(确认追加未破坏 select_lock_index/pick_box_at_point)

- [ ] **Step 6: Commit**

```bash
git add core/object_classify_lock.py tests/test_object_classify_lock_manual.py
git commit -m "feat(object_classify): manual锁定网格搜索逻辑——grid_centers+best_grid_match(纯Python可单测)

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## Task 2: AI 模块增量——按中心 crop 提特征(板端专用)

`FeatureExtractionApp` 加按任意中心 crop 固定大小区域的方法;`ObjectClassifyRecognition` 加 `extract_feature_at`(单点)与 `extract_features_at_centers`(多点,网格搜索用)。板端专用,无 host 单测,靠 ast 语法校验 + 板端验收。

**Files:**
- Modify: `core/object_classify_ai.py`

- [ ] **Step 1: 实现 config_preprocess_center + extract 方法**

Modify `core/object_classify_ai.py`。两处改动:

**(a) `FeatureExtractionApp` 加 `config_preprocess_center` 方法**(在 `config_preprocess` 方法之后、`_get_crop_param` 之前插入)。

Find:
```python
    def config_preprocess(self, det_box, input_image_size=None):
        gc.collect()
        with ScopedTiming("set preprocess config", self.debug_mode > 0):
            ai2d_input_size = input_image_size if input_image_size else self.rgb888p_size
            self.crop_params = self._get_crop_param(det_box)
            self.ai2d.crop(self.crop_params[0], self.crop_params[1],
                           self.crop_params[2], self.crop_params[3])
            self.ai2d.resize(nn.interp_method.tf_bilinear, nn.interp_mode.half_pixel)
            self.ai2d.build([1, 3, ai2d_input_size[1], ai2d_input_size[0]],
                            [1, 3, self.model_input_size[1], self.model_input_size[0]])

    def _get_crop_param(self, det_box):
```

Replace with(插入 config_preprocess_center):
```python
    def config_preprocess(self, det_box, input_image_size=None):
        gc.collect()
        with ScopedTiming("set preprocess config", self.debug_mode > 0):
            ai2d_input_size = input_image_size if input_image_size else self.rgb888p_size
            self.crop_params = self._get_crop_param(det_box)
            self.ai2d.crop(self.crop_params[0], self.crop_params[1],
                           self.crop_params[2], self.crop_params[3])
            self.ai2d.resize(nn.interp_method.tf_bilinear, nn.interp_mode.half_pixel)
            self.ai2d.build([1, 3, ai2d_input_size[1], ai2d_input_size[0]],
                            [1, 3, self.model_input_size[1], self.model_input_size[0]])

    def config_preprocess_center(self, cx, cy, box_size, input_image_size=None):
        """按中心 (cx,cy) crop box_size×box_size 固定大小区域(供 manual 锁定/网格搜索)。

        与 config_preprocess 区别:crop 区域是以为中心的正方形(边长 box_size),
        不走 _get_crop_param 的 1.26 倍放大。中心 clamp 使 crop 不越画面。
        """
        gc.collect()
        with ScopedTiming("set preprocess config", self.debug_mode > 0):
            ai2d_input_size = input_image_size if input_image_size else self.rgb888p_size
            half = box_size // 2
            x1 = int(max(0, cx - half))
            y1 = int(max(0, cy - half))
            x2 = int(min(self.rgb888p_size[0], cx + half))
            y2 = int(min(self.rgb888p_size[1], cy + half))
            w = x2 - x1
            h = y2 - y1
            if w < 2 or h < 2:
                # 中心贴边致 crop 过小:用最小有效 crop 保 ai2d 不崩
                w = max(2, w)
                h = max(2, h)
            self.crop_params = [x1, y1, w, h]
            self.ai2d.crop(self.crop_params[0], self.crop_params[1],
                           self.crop_params[2], self.crop_params[3])
            self.ai2d.resize(nn.interp_method.tf_bilinear, nn.interp_mode.half_pixel)
            self.ai2d.build([1, 3, ai2d_input_size[1], ai2d_input_size[0]],
                            [1, 3, self.model_input_size[1], self.model_input_size[0]])

    def _get_crop_param(self, det_box):
```

**(b) `ObjectClassifyRecognition` 加两个 extract 方法**(在 `run` 方法之后、`deinit` 之前插入)。先加常量。在文件顶部 `MAX_DET_BOXES = 5` 之后加 manual crop 默认大小常量。

Find:
```python
# 每帧最多提特征的检测框数(控推理量:1 次 yolov8n + N 次 recognition)
MAX_DET_BOXES = 5
```

Replace with:
```python
# 每帧最多提特征的检测框数(控推理量:1 次 yolov8n + N 次 recognition)
MAX_DET_BOXES = 5

# manual 锁定模式:按中心 crop 的固定大小(rgb888p 空间)。
# 显示空间 120×120 × (rgb888p/display 缩放比 ~1.6) ≈ 192;取 192。
MANUAL_CROP_SIZE = 192
# manual 网格搜索步长(±offset),= crop 边长一半。
MANUAL_GRID_OFFSET = MANUAL_CROP_SIZE // 2
```

Then find `run` 方法的结尾 + `deinit` 开头:
```python
            det_res.append(d)
            feat_res.append(feature)
        return det_res, feat_res

    def deinit(self):
```

Replace with(插入两个 extract 方法):
```python
            det_res.append(d)
            feat_res.append(feature)
        return det_res, feat_res

    def extract_feature_at(self, img_np, cx, cy, box_size=MANUAL_CROP_SIZE):
        """按中心 crop 固定大小区域提单特征(供 manual 锁定)。返回特征向量。"""
        self.feature.config_preprocess_center(cx, cy, box_size)
        return self.feature.run(img_np)

    def extract_features_at_centers(self, img_np, centers, box_size=MANUAL_CROP_SIZE):
        """对多个候选中心各 crop 提特征(供 manual 网格搜索)。返回特征列表(与 centers 等长)。"""
        feats = []
        for (cx, cy) in centers:
            self.feature.config_preprocess_center(cx, cy, box_size)
            feats.append(self.feature.run(img_np))
        return feats

    def deinit(self):
```

- [ ] **Step 2: ast 语法校验**

Run: `python -c "import ast; ast.parse(open('core/object_classify_ai.py',encoding='utf-8').read()); print('ast OK')"`
Expected: `ast OK`(板端专用,不导入运行)

- [ ] **Step 3: Commit**

```bash
git add core/object_classify_ai.py
git commit -m "feat(object_classify): AI增量——按中心crop提特征(config_preprocess_center+extract_feature_at+extract_features_at_centers)

板端专用,manual锁定/网格搜索用;无host单测,靠ast+板端验收。

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## Task 3: app.py on_frame 改两路径(yolo/manual)

改 globals(加 `_locked_mode`/`_locked_center`)、点击分支(按命中/未命中设模式)、跟踪分支(按模式走 yolo/manual)。板端专用,靠 ast 校验 + 板端验收。

**Files:**
- Modify: `scripts/object_classify/app.py`

- [ ] **Step 1: 改 import 加 manual 锁定函数 + 常量**

Modify `scripts/object_classify/app.py`。改 import 行(加 `grid_centers`/`best_grid_match` + AI 的 `MANUAL_CROP_SIZE`/`MANUAL_GRID_OFFSET`)。

Find:
```python
from core.object_classify_ai import ObjectClassifyRecognition, \
    OBJ_DET_KMPATH, OBJ_RECO_KMPATH, RGB888P_SIZE, DISPLAY_SIZE
from core.object_classify_db import object_classify_db, database_search, \
    to_feature_list, OBJECT_CLASSIFY_DB_PATH
from core.object_classify_lock import select_lock_index, pick_box_at_point
```

Replace with:
```python
from core.object_classify_ai import ObjectClassifyRecognition, \
    OBJ_DET_KMPATH, OBJ_RECO_KMPATH, RGB888P_SIZE, DISPLAY_SIZE, \
    MANUAL_CROP_SIZE, MANUAL_GRID_OFFSET
from core.object_classify_db import object_classify_db, database_search, \
    to_feature_list, OBJECT_CLASSIFY_DB_PATH
from core.object_classify_lock import select_lock_index, pick_box_at_point, \
    grid_centers, best_grid_match
```

- [ ] **Step 2: 加 manual 黄框固定大小常量(显示空间)**

在 `BOX_LOCK = 0xFFD700` 行之后加 manual 黄框显示空间大小常量。

Find:
```python
BOX_LOCK = 0xFFD700      # 锁定高亮黄框
```

Replace with:
```python
BOX_LOCK = 0xFFD700      # 锁定高亮黄框
# manual 模式黄框固定大小(显示空间 120×120)。rgb888p 的 MANUAL_CROP_SIZE 映射回显示空间。
MANUAL_BOX_DISP = 120
```

- [ ] **Step 3: 加 globals _locked_mode / _locked_center**

改 globals 区(加两个状态变量)。

Find:
```python
_locked_feature = None      # 锁定特征(plain list,跨帧持有);None=未锁定
_pending_click = None       # (x,y) 待处理触摸点(VGA 空间),或 None
```

Replace with:
```python
_locked_feature = None      # 锁定特征(plain list,跨帧持有);None=未锁定
_locked_mode = None         # "yolo"(点中框)/ "manual"(没点中框)/ None(未锁定)
_locked_center = None       # manual 模式上一帧中心(rgb888p 空间);yolo/None 时不用
_pending_click = None       # (x,y) 待处理触摸点(VGA 空间),或 None
```

- [ ] **Step 4: 改 on_frame 的 global 声明**

改 on_frame 顶部 global 声明(加 `_locked_mode`/`_locked_center`)。

Find:
```python
    global _locked_feature, _pending_click
    if _RUNTIME is None or _ocr is None:
        return
```

Replace with:
```python
    global _locked_feature, _locked_mode, _locked_center, _pending_click
    if _RUNTIME is None or _ocr is None:
        return
```

- [ ] **Step 5: 改触摸点击分支(按命中/未命中设模式)**

改 `_pending_click` 处理块。点击命中框 → yolo 模式;未命中 → manual 模式(在点击点 crop 提特征);点空白(无检测框且无法 manual)→ 解锁。

Find:
```python
    # 触摸点击处理(优先,可能改变锁定态)
    if _pending_click is not None:
        px, py = _pending_click
        _pending_click = None
        idx = pick_box_at_point(disp_boxes, px, py)
        if idx is not None and idx < len(features):
            # 拷成 plain list 跨帧持有(避 ulab ndarray 缓冲被 NPU 复用)
            _locked_feature = to_feature_list(features[idx])
            if _RUNTIME.buzzer is not None:
                _RUNTIME.buzzer.beep(ms=50)
        else:
            _locked_feature = None  # 点空白 → 解锁
```

Replace with:
```python
    # 触摸点击处理(优先,可能改变锁定态)
    # 点中 YOLO 框 → yolo 模式(用该框特征);没点中框 → manual 模式(点击点 crop 提特征,
    # 支持 YOLO 没检测到的物体);点空白 → 解锁。
    if _pending_click is not None:
        px, py = _pending_click
        _pending_click = None
        idx = pick_box_at_point(disp_boxes, px, py)
        if idx is not None and idx < len(features):
            # 点中框:用该框已提取特征,yolo 模式紧框跟踪
            _locked_feature = to_feature_list(features[idx])
            _locked_mode = "yolo"
            _locked_center = None
            if _RUNTIME.buzzer is not None:
                _RUNTIME.buzzer.beep(ms=50)
        else:
            # 没点中框:点击点映射到 rgb888p,crop 固定大小提特征 → manual 模式
            cx_ai = px * RGB888P_SIZE[0] // DISPLAY_SIZE[0]
            cy_ai = py * RGB888P_SIZE[1] // DISPLAY_SIZE[1]
            try:
                feat = _ocr.extract_feature_at(img_np, cx_ai, cy_ai)
                _locked_feature = to_feature_list(feat)
                _locked_center = (cx_ai, cy_ai)
                _locked_mode = "manual"
                if _RUNTIME.buzzer is not None:
                    _RUNTIME.buzzer.beep(ms=50)
            except Exception as e:
                print("[object_classify] manual lock error: %s" % e)
                _locked_feature = None
                _locked_mode = None
                _locked_center = None
```

- [ ] **Step 6: 改跟踪分支(按模式走 yolo/manual 两路径)**

改 `if _locked_feature is not None:` 整块(原只有 yolo 路径,改为按 `_locked_mode` 分两条)。

Find:
```python
    if _locked_feature is not None:
        # 锁定模式:特征余弦逐帧匹配锁定目标
        idx, score = select_lock_index(_locked_feature, features)
        if idx is not None and idx < len(disp_boxes):
            x, y, w, h = disp_boxes[idx]
            color = _draw_color(BOX_LOCK)
            img.draw_rectangle(x, y, w, h, color=color, thickness=5)
            img.draw_cross(x + w // 2, y + h // 2,
                           color=(0xFF, 0x00, 0xD7, 0xFF), size=24, thickness=2)
            conf = int(score * 100)
            slot, _sc = database_search(features[idx], _db_features)
            if slot is not None:
                img.draw_string_advanced(x + 2, y - 24, 24,
                                         "ID%d LOCK" % slot, color=color)
                slots[slot - 1] = (slot, x, y, w, h, conf)
            else:
                img.draw_string_advanced(x + 2, y - 24, 24, "LOCK", color=color)
                slots[0] = (0, x, y, w, h, conf)  # id=0 表示锁定但未注册
        else:
            # 锁定丢失:目标离开画面/被遮挡 → 自动解锁
            _locked_feature = None
```

Replace with:
```python
    if _locked_feature is not None and _locked_mode == "yolo":
        # yolo 模式:在 YOLO 检测特征里找余弦最高,用 YOLO 框坐标(紧、变大小)
        idx, score = select_lock_index(_locked_feature, features)
        if idx is not None and idx < len(disp_boxes):
            x, y, w, h = disp_boxes[idx]
            color = _draw_color(BOX_LOCK)
            img.draw_rectangle(x, y, w, h, color=color, thickness=5)
            img.draw_cross(x + w // 2, y + h // 2,
                           color=(0xFF, 0x00, 0xD7, 0xFF), size=24, thickness=2)
            conf = int(score * 100)
            slot, _sc = database_search(features[idx], _db_features)
            if slot is not None:
                img.draw_string_advanced(x + 2, y - 24, 24,
                                         "ID%d LOCK" % slot, color=color)
                slots[slot - 1] = (slot, x, y, w, h, conf)
            else:
                img.draw_string_advanced(x + 2, y - 24, 24, "LOCK", color=color)
                slots[0] = (0, x, y, w, h, conf)  # id=0 表示锁定但未注册
        else:
            # 锁定丢失:目标离开画面/被遮挡 → 自动解锁
            _locked_feature = None
            _locked_mode = None

    elif _locked_feature is not None and _locked_mode == "manual":
        # manual 模式:YOLO 检测不到该物体,自己在上一帧中心周围 3×3 网格搜索
        centers = grid_centers(_locked_center, MANUAL_GRID_OFFSET,
                               (RGB888P_SIZE[0], RGB888P_SIZE[1]))
        try:
            grid_feats = _ocr.extract_features_at_centers(img_np, centers)
        except Exception as e:
            print("[object_classify] manual track error: %s" % e)
            grid_feats = []
        gidx, gscore = best_grid_match(_locked_feature, grid_feats)
        if gidx is not None:
            # 新中心 = 命中的候选中心
            _locked_center = centers[gidx]
            cx_ai, cy_ai = _locked_center
            # 映射回显示空间,画固定大小黄框(以中心为心)
            dx = cx_ai * DISPLAY_SIZE[0] // RGB888P_SIZE[0]
            dy = cy_ai * DISPLAY_SIZE[1] // RGB888P_SIZE[1]
            bx = dx - MANUAL_BOX_DISP // 2
            by = dy - MANUAL_BOX_DISP // 2
            color = _draw_color(BOX_LOCK)
            img.draw_rectangle(bx, by, MANUAL_BOX_DISP, MANUAL_BOX_DISP,
                               color=color, thickness=5)
            img.draw_cross(dx, dy, color=(0xFF, 0x00, 0xD7, 0xFF),
                           size=24, thickness=2)
            conf = int(gscore * 100)
            # 用命中候选的特征查 DB(注册判断)
            slot, _sc = database_search(grid_feats[gidx], _db_features)
            if slot is not None:
                img.draw_string_advanced(bx + 2, by - 24, 24,
                                         "ID%d LOCK" % slot, color=color)
                slots[slot - 1] = (slot, bx, by, MANUAL_BOX_DISP, MANUAL_BOX_DISP, conf)
            else:
                img.draw_string_advanced(bx + 2, by - 24, 24, "LOCK", color=color)
                slots[0] = (0, bx, by, MANUAL_BOX_DISP, MANUAL_BOX_DISP, conf)
        else:
            # 网格搜索全部低于阈值 → 锁定丢失,自动解锁
            _locked_feature = None
            _locked_mode = None
            _locked_center = None
```

- [ ] **Step 7: 改未锁定分支的入口条件**

原 `if _locked_feature is None:` 现在要兼容"locked_feature 非空但模式被清"的情况——改为判断 `_locked_mode is None`。但注意:manual 分支里丢失解锁会同时清 feature 和 mode,所以判断 mode 即可。改入口。

Find:
```python
    if _locked_feature is None:
        # 未锁定模式:每框余弦匹配 DB
        for i, feat in enumerate(features):
```

Replace with:
```python
    if _locked_mode is None:
        # 未锁定模式:每框余弦匹配 DB
        for i, feat in enumerate(features):
```

- [ ] **Step 8: 改 K2 注册块兼容两种模式**

K2 注册当前用 `_locked_feature is not None` 作条件,两模式都满足(只要锁着就注册 `_locked_feature`),逻辑无需改。但确认注册后不解锁(保持当前模式)——当前代码注册后没动 `_locked_feature`/mode,符合"注册后保持锁定"。**此步无需改动**,仅确认。Read 现有 K2 块确认其用 `_locked_feature` 且不改 mode:

```python
    # K2 注册:当前锁定物体特征进空槽(复用 IdRegistry 的 K2 边沿/超时/蜂鸣)
    if _id_registry is not None and _id_registry.has_pending() \
            and _locked_feature is not None:
        try:
            slot = _id_registry.try_register(
                _locked_feature, _RUNTIME.buzzer,
                registrar=object_classify_db.register)
```

确认:条件用 `_locked_feature is not None`(两模式都满足),注册 `_locked_feature`(两模式都正确),注册后不改 `_locked_mode`(保持锁定)。**无需改动。**

- [ ] **Step 9: ast 语法校验**

Run: `python -c "import ast; ast.parse(open('scripts/object_classify/app.py',encoding='utf-8').read()); print('app ast OK')"`
Expected: `app ast OK`

- [ ] **Step 10: 跑既有 AST 契约测试(确认 app 契约未被破坏)**

Run: `python tests/test_object_classify_detect_ast.py`
Expected: 全部 PASS(10 断言:4 基础设施 + 6 app。app 断言查 select_lock_index/pick_box_at_point/host_tick/draw_cross/registrar/object_classify_ai 仍存在)

- [ ] **Step 11: Commit**

```bash
git add scripts/object_classify/app.py
git commit -m "feat(object_classify): 点击锁定任意物体——on_frame两路径(yolo紧框/manual 3×3网格搜索固定黄框)

支持YOLO未检测到的物体:点空白处crop提特征锁定,逐帧3×3网格搜索跟随,丢失自动解锁。

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## Task 4: 全量回归 + 板端验收清单更新

**Files:** 无新增/修改(运行 + 验收 + memory 更新)。

- [ ] **Step 1: 跑全部 object_classify host 单测**

Run:
```bash
python tests/test_object_classify_db.py
python tests/test_object_classify_db_persist.py
python tests/test_object_classify_lock.py
python tests/test_object_classify_lock_manual.py
python tests/test_object_classify_detect_ast.py
```
Expected: 五个文件全部 exit 0(末行无 "tests failed")。

- [ ] **Step 2: 回归相邻脚本单测**

Run:
```bash
python tests/test_host_api.py
python tests/test_icon_cache.py
python tests/test_body_detect_ast.py
```
Expected: 全部 PASS。

- [ ] **Step 3: 板端验收清单(部署到 K230 后人工执行,重点验 manual 模式)**

部署改动文件:`core/object_classify_lock.py`、`core/object_classify_ai.py`、`scripts/object_classify/app.py` 拷到 SD 卡对应路径。

逐项验收:
- [ ] 点中 YOLO 检测到的物体 → 黄框紧贴该物体(yolo 模式),移动跟随,离开自动解锁。(回归:初版行为不变)
- [ ] **点 YOLO 没检测到的物体**(放一个非 COCO80 类小物件/或漏检时)→ 该位置出现固定大小黄框+十字+LOCK(manual 模式),蜂鸣。
- [ ] 缓慢移动该物体 → 黄框跟随(manual 网格搜索逐帧重定位)。
- [ ] 物体快速移出搜索窗 → 自动解锁(黄框消失,回多框白框模式)。
- [ ] manual 锁定状态下按 K2 → 蜂鸣 + 计数+1,该物体特征注册成新 ID。
- [ ] manual 锁定的物体若随后被 YOLO 检测到(变大/入镜清晰)→ 仍是 manual 黄框固定大小(不切 yolo,模式锁定时定);解锁后重新点它会走 yolo 紧框。
- [ ] 点空白处(无物体)→ 解锁当前锁定。
- [ ] 帧率:manual 模式(1 YOLO + 9 reco/帧)是否可接受;若明显卡顿,记录帧率,触发 spec §12.4 降级预案。
- [ ] 上位机串口仍收 0x0A 数据帧(manual 锁定时锁定目标占槽上报)。

- [ ] **Step 4: 板端验收后更新 memory**

更新 `C:\Users\24160\.claude\projects\e--LBS-CAMER-AI\memory\camerai-object-classify.md`:补记 manual 模式(点击锁定任意物体 + 3×3 网格搜索跟踪 + 固定黄框 + 丢失自动解锁)、板端验收结果、降级预案状态。MEMORY.md 索引行同步更新。

- [ ] **Step 5: Commit 计划文件**

```bash
git add docs/superpowers/plans/2026-07-01-object-classify-manual-lock.md
git commit -m "docs(object_classify): 增量计划——点击锁定任意物体(manual 3×3网格搜索)

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## Self-Review 结论

- **Spec coverage(§12):**
  - §12.1 锁定两模式(点中框=yolo/没点中=manual,默认 crop 120×120 显示,边界 clamp)→ Task 3 Step5 点击分支 + Task 2 MANUAL_CROP_SIZE/MANUAL_BOX_DISP ✓
  - §12.2 跟踪两路径(yolo 现有/manual 3×3 网格 9 候选 crop 提特征取最高 ≥0.75)→ Task 3 Step6 + Task 1 grid_centers/best_grid_match + Task 2 extract_features_at_centers ✓
  - §12.3 K2 注册统一/丢失解锁/黄框固定大小/快速移动丢锁 → Task 3 Step6(manual 丢失清 feature+mode+center)/Step8(K2 不改)/MANUAL_BOX_DISP ✓
  - §12.4 降级预案(非初版)→ Task 4 Step3 验收项记录帧率触发 ✓
  - §12.5 实现要点(AI 加按中心提特征方法/lock 加 grid_centers+best_grid_match+host 单测/app on_frame 两路径)→ Task 1/2/3 ✓
- **Placeholder scan:** 无 TBD/TODO;每步含完整代码或确切命令。Step8 明确"无需改动"并给出确认依据。
- **Type consistency:** `grid_centers(center, offset, bounds)`/`best_grid_match(locked_feature, features, threshold)` 在 Task1 定义与 Task3 使用签名一致;`extract_feature_at(img_np, cx, cy, box_size)`/`extract_features_at_centers(img_np, centers, box_size)` 在 Task2 定义与 Task3 使用一致;`_locked_mode`/`_locked_center` 在 globals/声明/点击分支/跟踪分支一致;`MANUAL_CROP_SIZE`/`MANUAL_GRID_OFFSET`/`MANUAL_BOX_DISP` 命名一致。`config_preprocess_center(cx, cy, box_size)` 签名一致。
