# 物体分类脚本(object_classify) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 新增 `scripts/object_classify/` 脚本:YOLOv8n 检测任意物体 → recognition.kmodel 提特征 → 余弦匹配分辨为已注册 ID;支持点击锁定跟踪单一物体,KEY2 把锁定物体学习进 4 槽。

**Architecture:** 复刻 body_detect 单线程模板 + 双 kmodel 管线,检测器换为 `yolov8n_320.kmodel`(COCO80),特征器仍用 `recognition.kmodel`。DB 模块逐字复刻 body_db(余弦匹配+4槽轮转)。新增纯 Python 锁定逻辑模块(`select_lock_index`/`pick_box_at_point`)与预览区触摸点击(复用 color_detect 的 `lv.point_t` 取点模式)。

**Tech Stack:** MicroPython + LVGL + nncase_runtime(K230 NPU) + ulab.numpy + aicube;host 端 CPython 3.13 跑纯 Python 单测(无 MicroPython 依赖)。

**Spec:** `docs/superpowers/specs/2026-07-01-object-classify-design.md`

**已有脚手架(无需新建,仅需补全):**
- `config/categories.json` 已含 `object_classify`(order 10)
- `resource/icons/menu_icon/object_classify.png` 已存在
- `comm/host_api.py` 已定义 `TYPE_OBJECT_CLASSIFY = 0x0A`(但 `CATEGORY_TYPE` 映射缺失)
- `resource/i18n/zh_CN.json` / `en_US.json` 已含 `category.object_classify`(但缺 `object_classify` 文案段)

**测试运行方式:** `python tests/<test_file>.py`(每个测试文件含 `test_runner()` 自跑全部 `test_` 函数)。Python 3.13.7。

---

## File Structure

**Create:**
- `core/object_classify_db.py` — 物体特征 ID 库(复刻 body_db)。纯 Python,host 可单测。导出 `ObjectClassifyDB`、`object_classify_db`(单例)、`database_search`、`cosine_score`、`to_feature_list`、`OBJECT_CLASSIFY_DB_PATH`、`OBJECT_CLASSIFY_MATCH_THRESHOLD`。
- `core/object_classify_lock.py` — 纯 Python 锁定/点选逻辑。host 可单测。导出 `select_lock_index`、`pick_box_at_point`。
- `core/object_classify_ai.py` — YOLOv8n 检测 + recognition 特征组合封装(板端专用,导入 nncase/ulab,host 不可单测)。导出 `ObjectClassifyRecognition`、`OBJ_DET_KMPATH`、`OBJ_RECO_KMPATH`、`RGB888P_SIZE`、`DISPLAY_SIZE`。
- `scripts/object_classify/app.py` — 主脚本(板端专用)。导出 `run(runtime)`。
- `tests/test_object_classify_db.py` — DB 纯 Python 单测。
- `tests/test_object_classify_db_persist.py` — DB 持久化单测。
- `tests/test_object_classify_lock.py` — 锁定逻辑单测。
- `tests/test_object_classify_detect_ast.py` — AST 契约测试(基础设施 + app.py 契约)。
- `resource/icons/object_classify_icon/back.png`、`list.png` — 脚本顶栏/底栏图标(从 body_detect_icon 复制占位)。

**Modify:**
- `comm/host_api.py` — `CATEGORY_TYPE` 加 `"object_classify": TYPE_OBJECT_CLASSIFY`。
- `core/app_runtime.py` — `_channels_for` 加 object_classify 分支(chn2 XGA RGBP888);`init_app` 加 `preload_object_classify_icons` 分支。
- `core/icon_cache.py` — 加 `_object_classify_icons` 槽 + `preload_object_classify_icons` + `get_object_classify_icon`。
- `resource/i18n/zh_CN.json` — 加 `object_classify` 文案段。
- `resource/i18n/en_US.json` — 加 `object_classify` 文案段。

**Board-only(无 host 单测,靠 AST 契约 + 板端验收):** `core/object_classify_ai.py`、`scripts/object_classify/app.py`。

---

## Task 1: DB 模块(TDD)

复刻 `core/body_db.py` 为 `core/object_classify_db.py`,改名 + 暴露 `cosine_score`/`to_feature_list` 供锁定逻辑复用。先写全部测试(Red)再实现(Green)。

**Files:**
- Create: `tests/test_object_classify_db.py`
- Create: `tests/test_object_classify_db_persist.py`
- Create: `core/object_classify_db.py`

- [ ] **Step 1: 写 DB 单测(test_object_classify_db.py)**

Create `tests/test_object_classify_db.py`:

```python
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
```

- [ ] **Step 2: 写持久化单测(test_object_classify_db_persist.py)**

Create `tests/test_object_classify_db_persist.py`:

```python
# tests/test_object_classify_db_persist.py — ObjectClassifyDB 磁盘持久化测试
import sys, os, tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

F_A = [1.0, 0.0, 0.0, 0.0]
F_B = [0.0, 1.0, 0.0, 0.0]
F_C = [0.0, 0.0, 1.0, 0.0]


def test_flush_and_load_roundtrip():
    from core.object_classify_db import ObjectClassifyDB, database_search
    db = ObjectClassifyDB()
    db.register(F_A)
    db.register(F_B)
    db.register(F_C)
    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
        tpath = f.name
    try:
        db.flush_to_disk(tpath)
        db2 = ObjectClassifyDB()
        result = db2.load_from_disk(tpath)
        assert result is not None
        assert len(result) == 3
        slot, score = database_search(F_A, db2.get_features())
        assert slot == 1
        slot, score = database_search(F_B, db2.get_features())
        assert slot == 2
    finally:
        os.unlink(tpath)


def test_flush_clear_writes_empty():
    from core.object_classify_db import ObjectClassifyDB, database_search
    db = ObjectClassifyDB()
    db.register(F_A)
    db.clear()
    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
        tpath = f.name
    try:
        db.flush_to_disk(tpath)
        db2 = ObjectClassifyDB()
        result = db2.load_from_disk(tpath)
        assert result is not None
        assert len(result) == 0
    finally:
        os.unlink(tpath)


def test_load_from_missing_file_returns_none():
    from core.object_classify_db import ObjectClassifyDB
    db = ObjectClassifyDB()
    result = db.load_from_disk("/nonexistent/object_classify_db_test.json")
    assert result is None


def test_flush_empty_db_writes_valid_json():
    from core.object_classify_db import ObjectClassifyDB
    db = ObjectClassifyDB()
    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
        tpath = f.name
    try:
        db.flush_to_disk(tpath)
        db2 = ObjectClassifyDB()
        result = db2.load_from_disk(tpath)
        assert result is not None
        assert len(result) == 0
    finally:
        os.unlink(tpath)


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

- [ ] **Step 3: 跑测试确认 Red(模块不存在)**

Run: `python tests/test_object_classify_db.py`
Expected: FAIL — `ModuleNotFoundError: No module named 'core.object_classify_db'`

Run: `python tests/test_object_classify_db_persist.py`
Expected: FAIL — 同上 ModuleNotFoundError

- [ ] **Step 4: 实现 core/object_classify_db.py**

Create `core/object_classify_db.py`(复刻 body_db,改名 + 暴露 `cosine_score`/`to_feature_list`):

```python
# core/object_classify_db.py — 物体分类特征 ID 内存数据库
#
# 镜像 body_db 的内存-only + flush_to_disk 模式(逐字复刻,仅改名 + 暴露
# cosine_score/to_feature_list 供 object_classify_lock 复用):
#   - 存特征向量(plain list,余弦匹配),非 label_idx 精确匹配
#   - database_search 纯 Python cosine(不硬依赖 ulab)→ host 端可真单测
#   - score = dot/2 + 0.5(余弦 [-1,1] → [0,1],同 face_db/body_db)
#   - 轮转覆盖 4 槽(空槽优先,满则覆盖 _next_slot 并推进)
#
# 纯 Python(无 MicroPython 依赖)→ 可在 host 端真单元测试。

from core import db_store

OBJECT_CLASSIFY_DB_PATH = "/sdcard/CamerAi/data/object_classify_db.json"

# 默认匹配阈值(score=cos/2+0.5 映射后)。0.75 ⇒ cos≥0.5,对齐 body_db/face_db。
# ⚠️ 勿用 0.5:0.5 ⇒ cos≥0,CNN 自然图像特征几乎总正相关 → 所有人都命中同一 ID。
OBJECT_CLASSIFY_MATCH_THRESHOLD = 0.75


def _to_list(feat):
    """特征归一为 plain list。板端 ulab ndarray → .tolist();host list 直通。"""
    if isinstance(feat, list):
        return feat
    try:
        return list(feat.tolist())   # ulab ndarray
    except Exception:
        try:
            return list(feat)
        except Exception:
            return feat


def to_feature_list(feat):
    """公开版 _to_list:供 app 把板端 ndarray 特征拷成 plain list 跨帧持有(锁定态)。"""
    return _to_list(feat)


def cosine_score(a, b):
    """余弦相似度映射 score = cos/2 + 0.5。返回 [0,1] 或 0.0(零向量/异常)。

    纯 Python(host + board 通用)。供 database_search 与 object_classify_lock 复用。
    """
    try:
        al = _to_list(a)
        bl = _to_list(b)
    except Exception:
        return 0.0
    an = sum(x * x for x in al) ** 0.5
    bn = sum(x * x for x in bl) ** 0.5
    if an == 0 or bn == 0:
        return 0.0
    dot = sum(p * q for p, q in zip(al, bl))
    return (dot / (an * bn)) / 2 + 0.5


def database_search(feature, db_features, threshold=OBJECT_CLASSIFY_MATCH_THRESHOLD):
    """Cosine-match feature against db_features. Return (slot_id, score) or (None, 0.0)。

    纯 Python cosine(host + board 通用)。db_features: {slot_id: list}。
    score = cos/2 + 0.5。低于阈值 → (None, 0.0)。
    """
    if not db_features:
        return None, 0.0
    best_id = None
    best_score = 0.0
    for slot_id, db_feat in db_features.items():
        sc = cosine_score(feature, db_feat)
        if sc > best_score:
            best_score = sc
            best_id = slot_id
    if best_score < threshold:
        return None, 0.0
    return best_id, best_score


class _ObjectClassifyDB:
    """物体特征内存库。feature 为 plain list(余弦匹配)。"""

    def __init__(self):
        self._features = {}        # {slot_id: list}
        self._next_slot = 1        # 轮转覆盖指针(1-4 循环)
        self._dirty = False
        self._clear_dirty = False

    def register(self, feature):
        """注册 feature 到槽位(轮转覆盖)。返回 slot_id(1-4)。纯内存,设 _dirty。

        空槽优先(不推进 _next_slot);无空槽覆盖 _next_slot 并推进(1→2→3→4→1)。
        feature 转 plain list 存储(避 ulab ndarray 不可 JSON 序列化)。
        """
        feat = _to_list(feature)
        slot = None
        for i in range(1, 5):
            if i not in self._features:
                slot = i
                break
        if slot is None:
            slot = self._next_slot
            self._next_slot = self._next_slot % 4 + 1
        self._features[slot] = feat
        self._dirty = True
        self._clear_dirty = False
        print("[ObjectClassifyDB] registered feature(%d-dim) -> id%d (memory, dirty)" % (len(feat), slot))
        return slot

    def get_features(self):
        """返回特征字典的引用(运行时匹配用)。"""
        return self._features

    def clear(self):
        """清内存,设 _clear_dirty(clear wins over _dirty)。"""
        self._features.clear()
        self._clear_dirty = True
        self._dirty = False
        self._next_slot = 1
        print("[ObjectClassifyDB] cleared (memory, clear_dirty)")

    def _serialize(self):
        return {"next_slot": self._next_slot,
                "slots": {str(k): v for k, v in self._features.items()}}

    def load_from_disk(self, path=OBJECT_CLASSIFY_DB_PATH):
        """启动加载。db_store os.stat 预检查,文件不存在返回 None(避 ENOENT)。"""
        data = db_store.load_json(path)
        if data is None:
            return None
        try:
            self._next_slot = data.get("next_slot", 1)
            for slot_str, feat_list in data.get("slots", {}).items():
                self._features[int(slot_str)] = list(feat_list)
        except Exception as e:
            print("[ObjectClassifyDB] load parse failed: %s" % e)
        return self._features

    def flush_to_disk(self, path=OBJECT_CLASSIFY_DB_PATH):
        """注册即写 / 退出兜底。open('w') 不抛 ENOENT。(镜像 body_db,始终写盘)"""
        db_store.save_json(path, self._serialize())
        self._dirty = False
        self._clear_dirty = False
        print("[ObjectClassifyDB] flushed %d object(s) to %s" % (len(self._features), path))

    def init_features(self, path=OBJECT_CLASSIFY_DB_PATH):
        """启动时加载已注册物体特征到内存(同 body_db.init_features)。"""
        self.load_from_disk(path)
        print("[ObjectClassifyDB] init_features: loaded %d object(s)" % len(self._features))
        return self._features

    @property
    def count(self):
        return len(self._features)


# 全局单例
object_classify_db = _ObjectClassifyDB()

# 导出供宿主测试 import
ObjectClassifyDB = _ObjectClassifyDB
```

- [ ] **Step 5: 跑测试确认 Green**

Run: `python tests/test_object_classify_db.py`
Expected: 全部 PASS(末行无 "tests failed")

Run: `python tests/test_object_classify_db_persist.py`
Expected: 全部 PASS

- [ ] **Step 6: Commit**

```bash
git add core/object_classify_db.py tests/test_object_classify_db.py tests/test_object_classify_db_persist.py
git commit -m "feat(object_classify): ObjectClassifyDB——复刻body_db的cosine匹配+4槽轮转+持久化

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## Task 2: 锁定逻辑模块(TDD)

纯 Python,host 可单测。`select_lock_index` 用 Task 1 的 `cosine_score` 在检测特征列表里找锁定目标;`pick_box_at_point` 把屏幕点击映射到检测框索引。

**Files:**
- Create: `tests/test_object_classify_lock.py`
- Create: `core/object_classify_lock.py`

- [ ] **Step 1: 写锁定逻辑单测**

Create `tests/test_object_classify_lock.py`:

```python
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
```

- [ ] **Step 2: 跑测试确认 Red**

Run: `python tests/test_object_classify_lock.py`
Expected: FAIL — `ModuleNotFoundError: No module named 'core.object_classify_lock'`

- [ ] **Step 3: 实现 core/object_classify_lock.py**

Create `core/object_classify_lock.py`:

```python
# core/object_classify_lock.py — 物体分类锁定/点选纯 Python 逻辑
#
# 锁定跟踪:点击某检测框 → 其特征存为 locked_feature;后续每帧在检测特征列表里
# 用余弦相似度找最匹配的那个作为锁定目标(score≥阈值才继续锁,否则丢失解锁)。
# 点选:屏幕触摸坐标(VGA 显示空间)→ 命中的检测框索引(最小包含矩形)。
#
# 纯 Python(复用 object_classify_db.cosine_score)→ host 端可单测。

from core.object_classify_db import cosine_score, OBJECT_CLASSIFY_MATCH_THRESHOLD


def select_lock_index(locked_feature, features, threshold=OBJECT_CLASSIFY_MATCH_THRESHOLD):
    """在检测特征列表 features 中找与 locked_feature 余弦最相似的。

    Args:
        locked_feature: 锁定特征(plain list)或 None。
        features: list[feature],本帧各检测框的特征(与 det_boxes 等长同序)。
        threshold: score=cos/2+0.5 的命中阈值(默认 0.75,同 DB)。
    Returns:
        (index, score):最匹配的特征在 features 中的索引与 score;无/低于阈值 → (None, 0.0)。
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


def pick_box_at_point(boxes, px, py):
    """屏幕点 (px,py) 命中哪个检测框。

    Args:
        boxes: list of (x, y, w, h),显示空间(VGA 640×480)矩形。
        px, py: 触摸点(显示空间)。
    Returns:
        命中矩形中最小面积(最具体)者的索引;未命中 → None。
    """
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

- [ ] **Step 4: 跑测试确认 Green**

Run: `python tests/test_object_classify_lock.py`
Expected: 全部 PASS

- [ ] **Step 5: Commit**

```bash
git add core/object_classify_lock.py tests/test_object_classify_lock.py
git commit -m "feat(object_classify): 锁定/点选纯Python逻辑——select_lock_index+pick_box_at_point

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## Task 3: 基础设施接线 + AST 契约测试(基础设施部分)

把 object_classify 接入协议/通道/图标/i18n。先写 AST 契约测试(基础设施断言)→ Red → 改基础设施 → Green。

**Files:**
- Create: `tests/test_object_classify_detect_ast.py`
- Modify: `comm/host_api.py`(CATEGORY_TYPE 加映射)
- Modify: `core/app_runtime.py`(`_channels_for` + `init_app` preload 分支)
- Modify: `core/icon_cache.py`(图标槽 + preload/get)
- Modify: `resource/i18n/zh_CN.json`(object_classify 段)
- Modify: `resource/i18n/en_US.json`(object_classify 段)
- Create: `resource/icons/object_classify_icon/back.png`、`list.png`(从 body_detect_icon 复制占位)

- [ ] **Step 1: 写 AST 契约测试(基础设施断言)**

Create `tests/test_object_classify_detect_ast.py`(Task 5 会向此文件追加 app.py 断言):

```python
# tests/test_object_classify_detect_ast.py -- host-side AST 契约测试(object_classify)
import ast, os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP_RUNTIME_PATH = os.path.join(ROOT, "core", "app_runtime.py")
ICON_CACHE_PATH = os.path.join(ROOT, "core", "icon_cache.py")
HOST_API_PATH = os.path.join(ROOT, "comm", "host_api.py")


def _read(path):
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def test_object_classify_in_category_type_map():
    """CATEGORY_TYPE 必须包含 'object_classify': TYPE_OBJECT_CLASSIFY。"""
    src = _read(HOST_API_PATH)
    assert '"object_classify":' in src
    after = src.split('"object_classify":')[1][:80]
    assert "TYPE_OBJECT_CLASSIFY" in after


def test_channels_for_object_classify():
    """_channels_for 的 object_classify 分支 append chn2 XGA RGBP888。"""
    src = _read(APP_RUNTIME_PATH)
    start = src.find("def _channels_for(")
    body = src[start:start + 2200]
    assert "object_classify" in body, "_channels_for must handle object_classify"
    after = body.split('"object_classify"')[1][:300]
    assert "append" in after, "object_classify should append AI channel"
    assert "CAM_CHN_ID_2" in after, "object_classify must use CAM_CHN_ID_2 for AI"
    assert "XGA" in after, "object_classify AI channel must use XGA framesize"
    assert "RGBP888" in after, "object_classify AI channel must use RGBP888 pixformat"


def test_preload_object_classify_icons_in_init_app():
    """init_app 必须对 object_classify 调 preload_object_classify_icons。"""
    src = _read(APP_RUNTIME_PATH)
    assert '"object_classify"' in src
    assert 'preload_object_classify_icons' in src


def test_icon_cache_has_object_classify_methods():
    """icon_cache 必须有 preload_object_classify_icons + get_object_classify_icon + 槽。"""
    src = _read(ICON_CACHE_PATH)
    assert "def preload_object_classify_icons" in src
    assert "def get_object_classify_icon" in src
    assert "_object_classify_icons" in src


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
                print("  FAIL %s: %s" % (n, e))
                fails += 1
    assert fails == 0, "%d tests failed" % fails


if __name__ == "__main__":
    test_runner()
```

- [ ] **Step 2: 跑测试确认 Red**

Run: `python tests/test_object_classify_detect_ast.py`
Expected: 4 个 FAIL(CATEGORY_TYPE 无映射 / _channels_for 无分支 / init_app 无 preload / icon_cache 无方法)

- [ ] **Step 3: host_api.py 加 CATEGORY_TYPE 映射**

Modify `comm/host_api.py`:在 `CATEGORY_TYPE` 字典的 `"body_detect": TYPE_BODY_DETECT,` 行之后加入 object_classify 映射。

Find:
```python
        "body_detect":   TYPE_BODY_DETECT,     # 0x09
        "_template":  TYPE_MAIN_MENU,     # 0x01（默认）
```

Replace with:
```python
        "body_detect":     TYPE_BODY_DETECT,       # 0x09
        "object_classify": TYPE_OBJECT_CLASSIFY,   # 0x0A
        "_template":  TYPE_MAIN_MENU,     # 0x01（默认）
```

- [ ] **Step 4: app_runtime.py 加 _channels_for 分支 + init_app preload 分支**

Modify `core/app_runtime.py`:`_channels_for` 的 body_detect 分支之后加 object_classify 分支。

Find:
```python
        elif category_id == "body_detect":
            # chn2 XGA RGBP888 做 AI 推理(同 face_detect/gesture_detect AI 通道)
            chs.append((CAM_CHN_ID_2, Sensor.XGA, Sensor.RGBP888))
        elif category_id == "_template":
```

Replace with:
```python
        elif category_id == "body_detect":
            # chn2 XGA RGBP888 做 AI 推理(同 face_detect/gesture_detect AI 通道)
            chs.append((CAM_CHN_ID_2, Sensor.XGA, Sensor.RGBP888))
        elif category_id == "object_classify":
            # chn2 XGA RGBP888 做 AI 推理(同 body_detect:YOLOv8n 检测 + recognition 特征)
            chs.append((CAM_CHN_ID_2, Sensor.XGA, Sensor.RGBP888))
        elif category_id == "_template":
```

Then in `init_app`,加 preload 分支。Find:
```python
        elif category_id == "body_detect":
            icon_cache.preload_body_icons()
        self._init_services(fpioa)
```

Replace with:
```python
        elif category_id == "body_detect":
            icon_cache.preload_body_icons()
        elif category_id == "object_classify":
            icon_cache.preload_object_classify_icons()
        self._init_services(fpioa)
```

- [ ] **Step 5: icon_cache.py 加 object_classify 图标槽 + preload/get**

Modify `core/icon_cache.py`:在 `__init__` 加槽。

Find:
```python
        self._body_icons = {}     # name -> (data, dsc)
```

Replace with:
```python
        self._body_icons = {}     # name -> (data, dsc)
        self._object_classify_icons = {}  # name -> (data, dsc)
```

在 `get_body_icon` 方法之后(get_gesture 之后、`# 全局单例` 之前)加 preload/get。Find:

```python
    def get_body_icon(self, name):
        """获取人体识别图标 (data, dsc)，未缓存返回 (None, None)"""
        return self._body_icons.get(name, (None, None))


# 全局单例
icon_cache = _IconCache()
```

Replace with:
```python
    def get_body_icon(self, name):
        """获取人体识别图标 (data, dsc)，未缓存返回 (None, None)"""
        return self._body_icons.get(name, (None, None))

    def preload_object_classify_icons(self):
        """预读物体分类APP图标（在首次 task_handler 之前调用）"""
        base = "/sdcard/CamerAi/resource/icons/object_classify_icon/"
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
                self._object_classify_icons[name] = (data, dsc)
                print(f"[IconCache] object_classify/{name} OK ({len(data)} bytes)")
            except Exception as e:
                print(f"[IconCache] object_classify/{name} FAILED: {e}")

    def get_object_classify_icon(self, name):
        """获取物体分类图标 (data, dsc)，未缓存返回 (None, None)"""
        return self._object_classify_icons.get(name, (None, None))


# 全局单例
icon_cache = _IconCache()
```

- [ ] **Step 6: zh_CN.json 加 object_classify 文案段**

Modify `resource/i18n/zh_CN.json`:在 `body_detect` 段之后、`common` 段之前插入 object_classify 段。

Find:
```json
  "body_detect": {
    "save": "保存",
    "clear": "清除",
    "save_success": "已保存",
    "registered": "已学习 %d/4",
    "press_k2": "按 K2 注册人体",
    "back_fb": "返回",
    "list_fb": "列表"
  },
  "common": {
```

Replace with:
```json
  "body_detect": {
    "save": "保存",
    "clear": "清除",
    "save_success": "已保存",
    "registered": "已学习 %d/4",
    "press_k2": "按 K2 注册人体",
    "back_fb": "返回",
    "list_fb": "列表"
  },
  "object_classify": {
    "save": "保存",
    "clear": "清除",
    "save_success": "已保存",
    "registered": "已学习 %d/4",
    "press_k2": "按 K2 学习锁定物体",
    "lock_hint": "点击物体锁定",
    "back_fb": "返回",
    "list_fb": "列表"
  },
  "common": {
```

- [ ] **Step 7: en_US.json 加 object_classify 文案段**

Modify `resource/i18n/en_US.json`:在 `body_detect` 段之后、`common` 段之前插入。

Find:
```json
  "body_detect": {
    "save": "Save",
    "clear": "Clear",
    "save_success": "Saved",
    "registered": "Learned %d/4",
    "press_k2": "Press K2 to register body",
    "back_fb": "Back",
    "list_fb": "List"
  },
  "common": {
```

Replace with:
```json
  "body_detect": {
    "save": "Save",
    "clear": "Clear",
    "save_success": "Saved",
    "registered": "Learned %d/4",
    "press_k2": "Press K2 to register body",
    "back_fb": "Back",
    "list_fb": "List"
  },
  "object_classify": {
    "save": "Save",
    "clear": "Clear",
    "save_success": "Saved",
    "registered": "Learned %d/4",
    "press_k2": "Press K2 to learn locked object",
    "lock_hint": "Tap object to lock",
    "back_fb": "Back",
    "list_fb": "List"
  },
  "common": {
```

- [ ] **Step 8: 复制占位图标**

从 body_detect 图标复制占位(顶栏 back + 底栏 list;后续可替换专属图标):

```bash
mkdir -p resource/icons/object_classify_icon
cp resource/icons/body_detect_icon/back.png resource/icons/object_classify_icon/back.png
cp resource/icons/body_detect_icon/list.png resource/icons/object_classify_icon/list.png
```

- [ ] **Step 9: 跑 AST 测试确认 Green**

Run: `python tests/test_object_classify_detect_ast.py`
Expected: 全部 PASS(4 个基础设施断言通过;app.py 断言将在 Task 5 追加)

- [ ] **Step 10: 回归既有测试**

Run: `python tests/test_host_api.py && python tests/test_icon_cache.py`
Expected: 全部 PASS(确认改动未破坏 body_detect 等既有脚本)

- [ ] **Step 11: Commit**

```bash
git add comm/host_api.py core/app_runtime.py core/icon_cache.py resource/i18n/zh_CN.json resource/i18n/en_US.json resource/icons/object_classify_icon/ tests/test_object_classify_detect_ast.py
git commit -m "feat(object_classify): 基础设施——CATEGORY_TYPE 0x0A+chn2通道+图标preload+i18n+占位图标

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## Task 4: AI 模块(板端专用)

`core/object_classify_ai.py`:YOLOv8n 检测(`ObjectDetectionApp` from object_ai)+ recognition.kmodel 特征(复刻 body_ai 的 `PersonRecognitionApp`,适配 object 检测框格式 `[l,t,r,b,...]`)。板端专用(导入 nncase/ulab),无 host 单测,靠板端验收 + app.py 的 AST 契约。

**Files:**
- Create: `core/object_classify_ai.py`

- [ ] **Step 1: 实现 core/object_classify_ai.py**

Create `core/object_classify_ai.py`:

```python
# core/object_classify_ai.py — 物体分类:YOLOv8n 检测 + recognition.kmodel 特征
#
# 双 kmodel: yolov8n_320.kmodel(COCO80 物体检测,320×320,复用 object_ai.ObjectDetectionApp)
#           + recognition.kmodel(通用特征提取,224×224,复刻 body_ai.PersonRecognitionApp)
#
# 检测输出 [l,t,r,b,score,class_id](object_ai 格式,rgb888p 坐标,float);
# 特征用 crop 检测框 + resize 224×224(无对齐,同 body_ai)。
# ObjectClassifyRecognition.run 返回 (det_boxes, features) 等长列表(≤MAX_DET_BOXES,
# 已按检测顺序截断,控每帧特征提取量)。
#
# 板端专用(导入 nncase/ulab),无 host 单测。

import gc
import time

import nncase_runtime as nn
import ulab.numpy as np
from libs.AIBase import AIBase
from libs.AI2D import Ai2d
from libs.Utils import ScopedTiming
from core.object_ai import ObjectDetectionApp, COCO_LABELS

# AI 通道分辨率(对齐 body_detect/object_detect 的 chn2 XGA RGBP888)
RGB888P_SIZE = [1024, 768]
DISPLAY_SIZE = [640, 480]

# kmodel 路径(匹配 demo 存放位置;同 object_ai / body_ai)
OBJ_DET_KMPATH = "/sdcard/examples/kmodel/yolov8n_320.kmodel"
OBJ_RECO_KMPATH = "/sdcard/examples/kmodel/recognition.kmodel"

# 每帧最多提特征的检测框数(控推理量:1 次 yolov8n + N 次 recognition)
MAX_DET_BOXES = 5


def ALIGN_UP(x, align=16):
    return (x + align - 1) // align * align


class FeatureExtractionApp(AIBase):
    """recognition.kmodel 特征提取(224×224, crop 检测框, 无对齐)。

    镜像 body_ai.PersonRecognitionApp,区别:config_preprocess 接收 (x1,y1,x2,y2)
    四元组(object_ai 检测框 [l,t,r,b] 格式,非 person anchor 格式 det_box[2:6])。
    """

    def __init__(self, kmodel_path, model_input_size=None, rgb888p_size=None,
                 display_size=None, debug_mode=0):
        if model_input_size is None:
            model_input_size = [224, 224]
        if rgb888p_size is None:
            rgb888p_size = RGB888P_SIZE
        if display_size is None:
            display_size = DISPLAY_SIZE
        super().__init__(kmodel_path, model_input_size, rgb888p_size, debug_mode)
        self.kmodel_path = kmodel_path
        self.model_input_size = model_input_size
        self.rgb888p_size = [ALIGN_UP(rgb888p_size[0], 16), rgb888p_size[1]]
        self.display_size = [ALIGN_UP(display_size[0], 16), display_size[1]]
        self.crop_params = []
        self.debug_mode = debug_mode
        self.ai2d = Ai2d(debug_mode)
        self.ai2d.set_ai2d_dtype(nn.ai2d_format.NCHW_FMT, nn.ai2d_format.NCHW_FMT,
                                 np.uint8, np.uint8)

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
        x1, y1, x2, y2 = det_box[0], det_box[1], det_box[2], det_box[3]
        w, h = int(x2 - x1), int(y2 - y1)
        length = max(w, h) / 2
        cx = (x1 + x2) / 2
        cy = (y1 + y2) / 2
        ratio_num = 1.26 * length
        x1_kp = int(max(0, cx - ratio_num))
        y1_kp = int(max(0, cy - ratio_num))
        x2_kp = int(min(self.rgb888p_size[0] - 1, cx + ratio_num))
        y2_kp = int(min(self.rgb888p_size[1] - 1, cy + ratio_num))
        w_kp = int(x2_kp - x1_kp + 1)
        h_kp = int(y2_kp - y1_kp + 1)
        return [x1_kp, y1_kp, w_kp, h_kp]

    def postprocess(self, results):
        with ScopedTiming("postprocess", self.debug_mode > 0):
            return results[0][0]

    def deinit(self):
        try:
            del self.kpu
        except Exception:
            pass
        try:
            del self.ai2d
        except Exception:
            pass
        try:
            self.tensors.clear()
            del self.tensors
        except Exception:
            pass
        gc.collect()
        time.sleep_ms(50)


class ObjectClassifyRecognition:
    """物体检测+特征提取组合:先 YOLOv8n 检任意物体,再对每框提特征。

    返回 (det_boxes, features):等长列表(≤max_boxes),按检测顺序对应。
    det_boxes 元素为 [l,t,r,b,score,class_id](rgb888p 坐标);features 为特征向量。
    """

    def __init__(self, det_kmodel=OBJ_DET_KMPATH, rec_kmodel=OBJ_RECO_KMPATH,
                 det_input_size=None, rec_input_size=None,
                 max_boxes=MAX_DET_BOXES, confidence_threshold=0.5, nms_threshold=0.2,
                 rgb888p_size=None, display_size=None, debug_mode=0):
        if det_input_size is None:
            det_input_size = [320, 320]
        if rec_input_size is None:
            rec_input_size = [224, 224]
        if rgb888p_size is None:
            rgb888p_size = RGB888P_SIZE
        if display_size is None:
            display_size = DISPLAY_SIZE
        self.det_input_size = det_input_size
        self.rec_input_size = rec_input_size
        self.max_boxes = max_boxes
        self.rgb888p_size = [ALIGN_UP(rgb888p_size[0], 16), rgb888p_size[1]]
        self.display_size = [ALIGN_UP(display_size[0], 16), display_size[1]]
        self.debug_mode = debug_mode

        # ⚠️ 双 kmodel 顺序根因(坑#19,同 face/body/gesture_detect):
        # rec kmodel 必须在 det.config_preprocess() 之前加载,否则破坏共享 NPU/AI2D 状态。
        self.feature = FeatureExtractionApp(
            rec_kmodel, model_input_size=self.rec_input_size,
            rgb888p_size=self.rgb888p_size, display_size=self.display_size)
        self.detector = ObjectDetectionApp(
            det_kmodel, labels=COCO_LABELS, model_input_size=self.det_input_size,
            confidence_threshold=confidence_threshold, nms_threshold=nms_threshold,
            rgb888p_size=self.rgb888p_size, display_size=self.display_size,
            debug_mode=0)
        self.detector.config_preprocess()

    def run(self, img_np):
        """推理当前帧。返回 (det_res, feat_res) 等长(≤max_boxes)。

        det_res: 物体检测框列表,每框 [l,t,r,b,score,class_id](rgb888p 坐标)。
        feat_res: [feature_vec, ...] 每个物体的特征向量(同索引对应)。
        过滤:过小框(<2px)跳过(避提特征崩)。
        """
        det_boxes = self.detector.run(img_np)
        det_res = []
        feat_res = []
        try:
            n = len(det_boxes)
        except Exception:
            n = 0
        for i in range(n):
            if i >= self.max_boxes:
                break
            d = det_boxes[i]
            l, t, r, b = int(d[0]), int(d[1]), int(d[2]), int(d[3])
            if r - l < 2 or b - t < 2:
                continue  # 过小框跳过
            self.feature.config_preprocess((l, t, r, b))
            feature = self.feature.run(img_np)
            det_res.append(d)
            feat_res.append(feature)
        return det_res, feat_res

    def deinit(self):
        try:
            self.detector.deinit()
        except Exception:
            pass
        try:
            self.feature.deinit()
        except Exception:
            pass
```

- [ ] **Step 2: Commit**

```bash
git add core/object_classify_ai.py
git commit -m "feat(object_classify): AI封装——YOLOv8n检测+recognition特征(复刻body_ai双kmodel)

板端专用,无host单测;靠AST契约+板端验收。

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## Task 5: 主脚本 app.py + AST 契约(app 部分)

`scripts/object_classify/app.py`:复刻 body_detect 单线程模板 + on_frame,检测换 yolov8n、新增点击锁定 + KEY2 注册锁定物体。板端专用。完成后向 AST 测试追加 app.py 契约断言。

**Files:**
- Create: `scripts/object_classify/app.py`
- Modify: `tests/test_object_classify_detect_ast.py`(追加 app 断言)

- [ ] **Step 1: 实现 scripts/object_classify/app.py**

Create `scripts/object_classify/app.py`:

```python
# scripts/object_classify/app.py — 物体分类(双 kmodel + 点击锁定 + K2 注册 4 槽 + 协议 0x0A)
#
# 复刻 body_detect 模式: chn2 YOLOv8n 检测任意物体 + recognition 提特征
# → database_search 余弦匹配分辨 ID → 画框 + ID 标签 → host_tick。
# 增量: 点预览区任意物体锁定(只跟踪该物体,特征余弦逐帧匹配);K2 注册锁定物体进 4 槽。

import gc
import os
import sys
import time
import lvgl as lv
from media.display import Display
from media.sensor import CAM_CHN_ID_0, CAM_CHN_ID_2
from core.icon_cache import icon_cache
from core.font_manager import fonts
from core.id_registry import IdRegistry
from core.object_classify_ai import ObjectClassifyRecognition, \
    OBJ_DET_KMPATH, OBJ_RECO_KMPATH, RGB888P_SIZE, DISPLAY_SIZE
from core.object_classify_db import object_classify_db, database_search, \
    to_feature_list, OBJECT_CLASSIFY_DB_PATH
from core.object_classify_lock import select_lock_index, pick_box_at_point

BAR_H = 52
PREVIEW_Y = BAR_H
PREVIEW_H = 376
BAR_BG = 0x1A1A1A

# 4 槽颜色(同 face_detect/body_detect BOX_COLORS)
BOX_COLORS = {
    1: 0x44CC44,   # 绿
    2: 0x4488FF,   # 蓝
    3: 0xFF8844,   # 橙
    4: 0xCC44FF,   # 紫
}
BOX_UNKNOWN = 0xFFFFFF   # 未注册白框
BOX_LOCK = 0xFFD700      # 锁定高亮黄框


def _draw_color(hex_color):
    """hex 0xRRGGBB → K230 draw_rectangle color tuple (A, B, G, R)。"""
    r = (hex_color >> 16) & 0xFF
    g = (hex_color >> 8) & 0xFF
    b = hex_color & 0xFF
    return (0xFF, b, g, r)


_RUNTIME = None
_screen = None
_top_bar = None
_bottom_bar = None
_preview = None
_count_label = None
_id_registry = None
_ocr = None                 # ObjectClassifyRecognition
_db_features = {}
_locked_feature = None      # 锁定特征(plain list,跨帧持有);None=未锁定
_pending_click = None       # (x,y) 待处理触摸点(VGA 空间),或 None
_overlay = None
_clear_btn = None
_save_btn = None
_close_overlay = False


def _init_ai():
    """Load BOTH kmodels before the loop.

    ⚠️ 双 kmodel 顺序根因(坑#19):rec kmodel 必须在 det.config_preprocess()
    之前加载。ObjectClassifyRecognition.__init__ 已按此顺序加载。
    """
    global _ocr, _db_features
    print("[object_classify] loading yolov8n detection + recognition models...")
    _ocr = ObjectClassifyRecognition(
        OBJ_DET_KMPATH, OBJ_RECO_KMPATH,
        det_input_size=[320, 320], rec_input_size=[224, 224],
        confidence_threshold=0.5, nms_threshold=0.2,
        rgb888p_size=RGB888P_SIZE, display_size=DISPLAY_SIZE,
        debug_mode=0)
    _db_features = object_classify_db.init_features()
    print("[object_classify] AI ready, loaded %d object(s)" % len(_db_features))


def _init_registry(fpioa):
    global _id_registry
    _id_registry = IdRegistry(fpioa, pin=0)


def _deinit_ai():
    global _ocr
    if _ocr is not None:
        try:
            _ocr.deinit()
        except Exception as e:
            print("[object_classify] deinit warning: %s" % e)
        _ocr = None


def on_frame(img):
    """chn2 检测+提特征 → 锁定跟踪或余弦匹配 → 画框 + ID 标签 → host_tick。

    锁定时:在检测特征里找与 _locked_feature 余弦最相似的(≥0.75),只画该框(黄+十字+
    LOCK);低于阈值 → 锁定丢失,自动解锁。未锁定:每框 database_search,命中槽画彩框+
    ID#,未命中白框。触摸点击命中框 → 锁定该框特征;点空白 → 解锁。K2 注册当前锁定特征。
    """
    global _locked_feature, _pending_click
    if _RUNTIME is None or _ocr is None:
        return
    img_ai = _RUNTIME.sensor.snapshot(chn=CAM_CHN_ID_2)
    img_np = img_ai.to_numpy_ref()
    try:
        det_boxes, features = _ocr.run(img_np)
    except Exception as e:
        print("[object_classify] run error: %s" % e)
        det_boxes, features = [], []

    # 检测框(rgb888p) → 显示坐标(VGA)
    disp_boxes = []
    for d in det_boxes:
        l, t, r, b = int(d[0]), int(d[1]), int(d[2]), int(d[3])
        x = l * DISPLAY_SIZE[0] // RGB888P_SIZE[0]
        y = t * DISPLAY_SIZE[1] // RGB888P_SIZE[1]
        w = (r - l) * DISPLAY_SIZE[0] // RGB888P_SIZE[0]
        h = (b - t) * DISPLAY_SIZE[1] // RGB888P_SIZE[1]
        disp_boxes.append((x, y, w, h))

    slots = [None, None, None, None]
    filled = set()

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

    if _locked_feature is None:
        # 未锁定模式:每框余弦匹配 DB
        for i, feat in enumerate(features):
            x, y, w, h = disp_boxes[i]
            slot, sc = database_search(feat, _db_features)
            if slot is not None and slot not in filled:
                color = _draw_color(BOX_COLORS.get(slot, BOX_UNKNOWN))
                img.draw_rectangle(x, y, w, h, color=color, thickness=4)
                img.draw_string_advanced(x + 2, y - 24, 24,
                                         "ID%d" % slot, color=color)
                slots[slot - 1] = (slot, x, y, w, h, int(sc * 100))
                filled.add(slot)
            else:
                color = _draw_color(BOX_UNKNOWN)
                img.draw_rectangle(x, y, w, h, color=color, thickness=2)
                img.draw_string_advanced(x + 2, y - 24, 24, "object", color=color)

    # 屏幕居中绿色十字(对准参考):VGA 640×480 中心 (320, 240)
    img.draw_cross(320, 240, color=(0xFF, 0x00, 0xFF, 0x00), size=20, thickness=2)

    # K2 注册:当前锁定物体特征进空槽(复用 IdRegistry 的 K2 边沿/超时/蜂鸣)
    if _id_registry is not None and _id_registry.has_pending() \
            and _locked_feature is not None:
        try:
            slot = _id_registry.try_register(
                _locked_feature, _RUNTIME.buzzer,
                registrar=object_classify_db.register)
            if slot is not None:
                object_classify_db.flush_to_disk()
                _db_features[slot] = object_classify_db.get_features().get(slot)
                _refresh_count()
        except Exception as e:
            print("[object_classify] register error: %s" % e)

    if _RUNTIME is not None and _RUNTIME.host is not None:
        _RUNTIME.host_tick(slots)
    gc.collect()


def _refresh_count():
    if _count_label is not None:
        try:
            _count_label.set_text(
                _RUNTIME.lang.t("object_classify.registered", len(_db_features)))
        except Exception:
            pass


def _on_preview_clicked(e):
    """点预览区:记录屏幕坐标(VGA 空间),on_frame 里 pick_box_at_point。"""
    global _pending_click
    if e.get_code() != lv.EVENT.CLICKED:
        return
    if _overlay is not None:
        global _close_overlay
        _close_overlay = True
        return
    try:
        # K230 MicroPython LVGL 绑定:get_point 需传预分配 point_t 填充(同 color_detect)。
        indev = lv.indev_get_act()
        if indev is not None:
            pt = lv.point_t()
            indev.get_point(pt)
            _pending_click = (pt.x, pt.y)
    except Exception as ex:
        print("[object_classify] get_point error: %s" % ex)


def _on_list_clicked(e):
    """弹出清除/保存浮层(叠加在底栏上方)。"""
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
    cl.set_text(_RUNTIME.lang.t("object_classify.clear"))
    cl.add_style(make_back_bar_text_style(fonts.body), 0)
    cl.center()
    _clear_btn.add_event(_on_clear_clicked, lv.EVENT.CLICKED, None)

    _save_btn = lv.btn(_overlay)
    _save_btn.set_size(120, 40)
    _save_btn.align(lv.ALIGN.RIGHT_MID, -20, 0)
    sv = lv.label(_save_btn)
    sv.set_text(_RUNTIME.lang.t("object_classify.save"))
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
    object_classify_db.clear()
    _db_features.clear()
    _refresh_count()
    if _RUNTIME is not None and _RUNTIME.buzzer is not None:
        _RUNTIME.buzzer.beep(ms=200)
    _close_overlay = True


def _on_save_clicked(e):
    global _close_overlay
    if e.get_code() != lv.EVENT.CLICKED:
        return
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
    """Build top bar, transparent clickable preview area, and bottom bar."""
    global _screen, _top_bar, _bottom_bar, _preview, _count_label
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

    icon_data, icon_dsc = icon_cache.get_object_classify_icon("back")
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
    title.set_text(runtime.lang.t("category.object_classify"))
    title.align(lv.ALIGN.CENTER, 0, 0)
    from ui.theme import make_back_bar_text_style
    title.add_style(make_back_bar_text_style(fonts.body), 0)

    # 透明预览区(透出 OSD1,可点击锁定物体)
    _preview = lv.obj(screen)
    _preview.set_size(lv.pct(100), PREVIEW_H)
    _preview.set_pos(0, PREVIEW_Y)
    _preview.set_style_bg_opa(0, 0)
    _preview.set_style_border_width(0, 0)
    _preview.set_style_pad_all(0, 0)
    _preview.set_style_radius(0, 0)
    _preview.clear_flag(lv.obj.FLAG.SCROLLABLE)
    _preview.add_flag(lv.obj.FLAG.CLICKABLE)
    _preview.add_event(_on_preview_clicked, lv.EVENT.CLICKED, None)

    _bottom_bar = lv.obj(screen)
    _bottom_bar.set_size(lv.pct(100), BAR_H)
    _bottom_bar.set_pos(0, PREVIEW_Y + PREVIEW_H)
    _bottom_bar.set_style_bg_color(lv.color_hex(BAR_BG), 0)
    _bottom_bar.set_style_bg_opa(255, 0)
    _bottom_bar.set_style_border_width(0, 0)
    _bottom_bar.set_style_pad_all(0, 0)
    _bottom_bar.set_style_radius(0, 0)
    _bottom_bar.clear_flag(lv.obj.FLAG.SCROLLABLE)

    # list 图标按钮(底栏左侧) → 点击弹出清除/保存浮层
    list_btn = lv.obj(_bottom_bar)
    list_btn.set_size(48, 48)
    list_btn.align(lv.ALIGN.LEFT_MID, 2, 0)
    list_btn.set_style_bg_opa(0, 0)
    list_btn.set_style_border_width(0, 0)
    list_btn.set_style_pad_all(0, 0)
    list_btn.clear_flag(lv.obj.FLAG.SCROLLABLE)
    list_btn.add_flag(lv.obj.FLAG.CLICKABLE)
    list_icon_data, list_icon_dsc = icon_cache.get_object_classify_icon("list")
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
    else:
        list_lbl = lv.label(list_btn)
        list_lbl.set_text("=")
        list_lbl.add_style(make_back_bar_text_style(fonts.body), 0)
        list_lbl.center()
    list_btn.add_event(_on_list_clicked, lv.EVENT.CLICKED, None)

    count_label = lv.label(_bottom_bar)
    count_label.set_text(runtime.lang.t("object_classify.registered", len(_db_features)))
    count_label.add_style(make_back_bar_text_style(fonts.body), 0)
    count_label.align(lv.ALIGN.CENTER, 0, 0)
    _count_label = count_label


def _destroy_ui():
    """Delete LVGL objects and restore screen opacity for the menu."""
    global _screen, _top_bar, _bottom_bar, _preview, _count_label
    global _overlay, _clear_btn, _save_btn
    for obj in (_overlay, _clear_btn, _save_btn, _top_bar, _bottom_bar, _preview, _count_label):
        if obj is not None:
            try:
                obj.delete()
            except Exception:
                pass
    _overlay = None
    _clear_btn = None
    _save_btn = None
    _top_bar = None
    _bottom_bar = None
    _preview = None
    _count_label = None
    try:
        from ui.theme import Colors
        scr = lv.scr_act()
        scr.set_style_bg_color(lv.color_hex(Colors.BG), 0)
        scr.set_style_bg_opa(255, 0)
    except Exception:
        pass
    _screen = None


def run(runtime):
    """Entry point called by reset-framework main.py."""
    global _RUNTIME
    _RUNTIME = runtime
    exit_flag = [False]
    _init_ai()
    _init_registry(runtime.fpioa)
    _build_ui(runtime, exit_flag)
    fc = 0
    try:
        while not exit_flag[0]:
            os.exitpoint()
            img = runtime.sensor.snapshot(chn=CAM_CHN_ID_0)
            try:
                on_frame(img)
            except Exception as e:
                print("[object_classify] on_frame error: %s" % e)
                try:
                    sys.print_exception(e)
                except Exception:
                    pass
            if _id_registry is not None:
                _id_registry.poll_k2()
            _process_overlay_close()
            Display.show_image(img, 0, 0, Display.LAYER_OSD1)
            time.sleep_ms(lv.task_handler())
            fc += 1
            if fc % 30 == 0:
                print("[object_classify] fc=%d" % fc)
    finally:
        _deinit_ai()
        _destroy_ui()
        _RUNTIME = None
        object_classify_db.flush_to_disk()  # 退出兜底写盘
```

- [ ] **Step 2: 向 AST 测试追加 app.py 契约断言**

Modify `tests/test_object_classify_detect_ast.py`:在 `test_icon_cache_has_object_classify_methods` 之后、`test_runner` 之前追加 app 断言。

Find:
```python
def test_icon_cache_has_object_classify_methods():
    """icon_cache 必须有 preload_object_classify_icons + get_object_classify_icon + 槽。"""
    src = _read(ICON_CACHE_PATH)
    assert "def preload_object_classify_icons" in src
    assert "def get_object_classify_icon" in src
    assert "_object_classify_icons" in src


def test_runner():
```

Replace with:
```python
def test_icon_cache_has_object_classify_methods():
    """icon_cache 必须有 preload_object_classify_icons + get_object_classify_icon + 槽。"""
    src = _read(ICON_CACHE_PATH)
    assert "def preload_object_classify_icons" in src
    assert "def get_object_classify_icon" in src
    assert "_object_classify_icons" in src


def _app_src():
    app_path = os.path.join(ROOT, "scripts", "object_classify", "app.py")
    return _read(app_path)


def test_app_imports_object_classify_ai():
    """app.py 必须导入 object_classify_ai 的 ObjectClassifyRecognition 类。"""
    src = _app_src()
    assert "object_classify_ai" in src, "app must import from object_classify_ai"
    assert "ObjectClassifyRecognition" in src, "app must import ObjectClassifyRecognition"


def test_on_frame_uses_registrar():
    """app.py on_frame 必须使用 try_register(..., registrar=object_classify_db.register)。"""
    src = _app_src()
    assert "registrar" in src, "app must use registrar pattern for K2 registration"
    assert "object_classify_db.register" in src


def test_has_host_tick():
    """app.py on_frame 必须有 host_tick 调用(协议 0x0A)。"""
    src = _app_src()
    assert "host_tick" in src, "app must call host_tick for protocol 0x0A"


def test_has_draw_cross():
    """app.py on_frame 必须有 draw_cross 调用(居中十字)。"""
    src = _app_src()
    assert "draw_cross" in src, "app must call draw_cross for center crosshair"


def test_has_lock_logic():
    """app.py 必须用锁定逻辑(select_lock_index + _locked_feature)。"""
    src = _app_src()
    assert "select_lock_index" in src, "app must use select_lock_index for lock tracking"
    assert "_locked_feature" in src


def test_has_touch_pick():
    """app.py 必须用 pick_box_at_point + _pending_click 处理点击锁定。"""
    src = _app_src()
    assert "pick_box_at_point" in src, "app must use pick_box_at_point for touch lock"
    assert "_pending_click" in src


def test_runner():
```

- [ ] **Step 3: 跑 AST 测试确认 Green(全部断言)**

Run: `python tests/test_object_classify_detect_ast.py`
Expected: 全部 PASS(4 基础设施 + 6 app 断言)

- [ ] **Step 4: Commit**

```bash
git add scripts/object_classify/app.py tests/test_object_classify_detect_ast.py
git commit -m "feat(object_classify): 主脚本——复刻body_detect双kmodel+点击锁定+K2注册4槽+协议0x0A

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## Task 6: 全量回归 + 板端验收清单

跑全部相关 host 单测,确认无回归;输出板端验收清单(板端执行,非 host)。

**Files:** 无新增/修改(仅运行 + 验收)。

- [ ] **Step 1: 跑全部 object_classify host 单测**

Run:
```bash
python tests/test_object_classify_db.py
python tests/test_object_classify_db_persist.py
python tests/test_object_classify_lock.py
python tests/test_object_classify_detect_ast.py
```
Expected: 四个文件全部 PASS(末行无 "tests failed")。

- [ ] **Step 2: 回归相邻脚本单测(确认接线改动未破坏既有)**

Run:
```bash
python tests/test_host_api.py
python tests/test_icon_cache.py
python tests/test_body_db.py
python tests/test_body_detect_ast.py
python tests/test_object_db.py
```
Expected: 全部 PASS。

- [ ] **Step 3: 板端验收清单(部署到 K230 后人工执行)**

部署:`core/object_classify_db.py`、`core/object_classify_lock.py`、`core/object_classify_ai.py`、`scripts/object_classify/app.py` + 改动的 `comm/host_api.py`、`core/app_runtime.py`、`core/icon_cache.py`、`resource/i18n/*`、`resource/icons/object_classify_icon/*` 拷到 SD 卡对应路径。

逐项验收(每项打勾):
- [ ] 主菜单出现"物体分类"卡片,图标正常,点击进入脚本。
- [ ] 顶栏标题"物体分类"+ 返回按钮;底栏 list 按钮 + 计数"已学习 0/4"。
- [ ] 画面中放常见物体(杯子/手机/瓶),出现检测白框 + "object" 标签(未锁定、未注册)。
- [ ] 点击某个物体 → 该物体变黄框 + 十字 + "LOCK",其余框消失(只显示锁定物体)。
- [ ] 移动该物体 → 黄框跟随(特征余弦逐帧匹配,不靠框位置)。
- [ ] 物体离开画面 → 自动解锁(黄框消失,回到多框白框模式)。
- [ ] 锁定状态下按 K2 → 蜂鸣 + 计数变"已学习 1/4";此后该物体显示为 ID1 绿框(锁定态下显示 "ID1 LOCK")。
- [ ] 再锁另一个物体 + K2 → ID2 蓝框。满 4 槽后再注册轮转覆盖。
- [ ] 点空白处 → 解锁当前锁定。
- [ ] 底栏 list → 清除浮层 → 清空后计数回 0/4。
- [ ] 退出脚本再进 → 已学习 ID 仍在(持久化生效)。
- [ ] 帧率稳定不卡顿(参考 body_detect;若卡顿严重,记录帧率,考虑 spec §7 降级预案 N=1)。
- [ ] 上位机串口收到 0x0A 类型数据帧(4 槽 ×10 字节大端)。

- [ ] **Step 4: 板端验收通过后更新 memory**

在 `C:\Users\24160\.claude\projects\e--LBS-CAMER-AI\memory\` 新建 `camerai-object-classify.md`,并在 `MEMORY.md` 索引追加一行。内容模板:

```markdown
---
name: camerai-object-classify
description: 物体分类脚本——YOLOv8n检测+recognition特征+点击锁定+KEY注册4槽
metadata:
  type: project
---

2026-07-01 object_classify(双kmodel: yolov8n_320检测+recognition特征)TDD实现;
复刻body_detect双kmodel+cosine+4槽+协议0x0A;增量=点击锁定跟踪(特征余弦逐帧匹配,
select_lock_index/pick_box_at_point纯Python可单测)+K2注册锁定物体;
ObjectClassifyDB复刻body_db(cosine_score/to_feature_list复用);板端验收<通过/待验收>。
坑:<记录板端验收发现的新坑>。关联 [[camerai-body-detect]] [[camerai-object-detect]]。
```

- [ ] **Step 5: Commit memory + 验收记录**

```bash
git add docs/superpowers/plans/2026-07-01-object-classify.md
git commit -m "docs(object_classify): 实施计划+板端验收清单

Co-Authored-By: Claude <noreply@anthropic.com>"
```
(memory 文件位于用户 home,不入项目 git;若需记录验收结果到项目,另起 commit。)

---

## Self-Review 结论

- **Spec coverage:**
  - §2 交互模型(点击锁定/点空白解锁/丢失自动解锁/KEY2注册锁定/锁定只显示一个)→ Task 5 on_frame + Task 2 lock 逻辑 ✓
  - §3 架构(双kmodel/chn2/0x0A/4槽/阈值0.75)→ Task 4 + Task 3 + Task 1 ✓
  - §4 每帧流程 → Task 5 on_frame ✓
  - §5 DB + 持久化 → Task 1 ✓
  - §6 UI 布局 + 触摸 → Task 5 _build_ui + _on_preview_clicked ✓
  - §7 模型选型 + 性能(MAX_DET_BOXES=5)→ Task 4 ✓;降级预案 N=1 标为非目标,板端卡顿时启用 ✓
  - §8 K230 坑(gc.collect/坑#19顺序/不读像素/不滚屏)→ Task 4/5 代码体现 ✓
  - §9 改动现有文件 → Task 3 全覆盖 ✓
  - §10 测试策略(DB/lock/协议契约)→ Task 1/2/3/5 ✓
  - §11 非目标(不报COCO类名/不做Kalman/单锁定/降级不在初版)→ 遵守 ✓
- **Placeholder scan:** 无 TBD/TODO;每步含完整代码或确切命令。
- **Type consistency:** `ObjectClassifyRecognition`、`object_classify_db`、`database_search`、`cosine_score`、`to_feature_list`、`select_lock_index`、`pick_box_at_point`、`get_object_classify_icon`、`preload_object_classify_icons`、`_locked_feature`、`_pending_click` 在定义处与使用处命名一致。`disp_boxes` 元素 `(x,y,w,h)` 与 `pick_box_at_point` 签名一致。
