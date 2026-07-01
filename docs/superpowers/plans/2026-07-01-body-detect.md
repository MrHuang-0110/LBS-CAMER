# 人体识别(body_detect)Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 新增 body_detect 脚本——双 kmodel(person_detect_yolov5n 检测 + recognition 特征提取)→ BodyDB 余弦匹配 → K2 注册 4 槽 → 协议 0x09,UI/持久化/业务复刻 face_detect。

**Architecture:** chn2 XGA RGBP888 AI 推理(同 face_detect);PersonDetectionApp(aicube.anchorbasedet,9 anchors)+ PersonRecognitionApp(crop+resize 提特征,无对齐)+ PersonRecognition 组合类;BodyDB 镜像 face_db(余弦匹配,纯 Python 可单测);app.py 复刻 face_detect/gesture_detect 模板。

**Tech Stack:** K230 MicroPython + LVGL v8 + nncase_runtime + aicube + aidemo + ulab numpy + UART1;host 端纯 Python AST/逻辑单测(无 MicroPython 依赖)。

**Spec:** `docs/superpowers/specs/2026-07-01-body-detect-design.md`

**关键设计决策(plan 内固化):**
- `database_search` 用**纯 Python** cosine(同 face_db 的 `score = dot/2+0.5` 映射),不硬依赖 ulab → host 端可真单测匹配逻辑。板端 4 槽×~256 维纯 python 开销可接受。
- BodyDB 存特征为 **plain list**(register 时转换 ulab→list),JSON 直接存 list(无需 tolist/np.array 往返)。
- `PersonRecognition.run` 返回等长 `(det_boxes, features)`(只含通过边界过滤的框,避 zip 错位,同 gesture_ai 修复)。
- K2 注册复用 `run()` 已提取的 `features[max_i]`(无需像 face_detect 再跑一次 reg),更简洁。
- IdRegistry 用 `registrar=body_db.register`(显式,同 gesture_detect)。

---

## File Structure

**新建:**
- `core/body_db.py` — 人体特征内存库(余弦匹配,纯 Python),全局单例 `body_db` + `BodyDB` 别名 + 模块级 `database_search`。
- `core/body_ai.py` — AI 封装:`PersonDetectionApp`(YOLOv5n 检测)+ `PersonRecognitionApp`(crop+resize 提特征)+ `PersonRecognition` 组合类。模块级常量 `PERSON_DET_KMPATH`/`PERSON_RECO_KMPATH`/`PERSON_ANCHORS`/`PERSON_LABELS`/`RGB888P_SIZE`/`DISPLAY_SIZE`/`BOX_COLORS`/`BOX_UNKNOWN`/`ALIGN_UP`/`_draw_color`。
- `scripts/body_detect/app.py` — 主脚本(~440 行,复刻 face_detect/gesture_detect)。
- `tests/test_body_db.py` — BodyDB 内存逻辑单测(9 项)。
- `tests/test_body_db_persist.py` — BodyDB 持久化单测(4 项)。
- `tests/test_body_ai_ast.py` — body_ai AST 契约(6 项)。
- `tests/test_body_detect_ast.py` — app/基础设施 AST 契约(8 项)。
- `resource/icons/body_detect_icon/back.png` + `list.png` — 从 gesture_detect_icon 复制。

**修改:**
- `comm/host_api.py` — `CATEGORY_TYPE` 加 `"body_detect": TYPE_BODY_DETECT`。
- `core/app_runtime.py` — `_channels_for` 加 body_detect→chn2 XGA RGBP888;`init_app` 加 `preload_body_icons`。
- `core/icon_cache.py` — `_body_icons={}` + `preload_body_icons()` + `get_body_icon(name)`。
- `resource/i18n/zh_CN.json` + `en_US.json` — body_detect 文案块。

---

## Task 1: BodyDB 内存逻辑单测(Red)

**Files:**
- Create: `tests/test_body_db.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_body_db.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python tests/test_body_db.py`
Expected: FAIL — `ModuleNotFoundError: No module named 'core.body_db'`

- [ ] **Step 3: Commit**

```bash
git add tests/test_body_db.py
git commit -m "test(body_db): 9个内存逻辑单测——余弦匹配/轮转/clear/count

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## Task 2: BodyDB 持久化单测(Red)

**Files:**
- Create: `tests/test_body_db_persist.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_body_db_persist.py`:

```python
# tests/test_body_db_persist.py — BodyDB 磁盘持久化测试
import sys, os, tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

F_A = [1.0, 0.0, 0.0, 0.0]
F_B = [0.0, 1.0, 0.0, 0.0]
F_C = [0.0, 0.0, 1.0, 0.0]


def test_flush_and_load_roundtrip():
    from core.body_db import BodyDB, database_search
    db = BodyDB()
    db.register(F_A)  # slot 1
    db.register(F_B)  # slot 2
    db.register(F_C)  # slot 3
    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
        tpath = f.name
    try:
        db.flush_to_disk(tpath)
        db2 = BodyDB()
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
    from core.body_db import BodyDB, database_search
    db = BodyDB()
    db.register(F_A)
    db.clear()
    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
        tpath = f.name
    try:
        db.flush_to_disk(tpath)
        db2 = BodyDB()
        result = db2.load_from_disk(tpath)
        assert result is not None  # load_from_disk returns dict (empty slots)
        assert len(result) == 0
    finally:
        os.unlink(tpath)


def test_load_from_missing_file_returns_none():
    from core.body_db import BodyDB
    db = BodyDB()
    result = db.load_from_disk("/nonexistent/body_db_test.json")
    assert result is None


def test_flush_empty_db_writes_valid_json():
    """flush 空 DB(未 register)照常写盘(镜像 face_db/ObjectDB)。"""
    from core.body_db import BodyDB
    db = BodyDB()
    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
        tpath = f.name
    try:
        db.flush_to_disk(tpath)  # 不应 crash
        db2 = BodyDB()
        result = db2.load_from_disk(tpath)
        assert result is not None  # 文件存在,内容为有效 JSON
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

- [ ] **Step 2: Run tests to verify they fail**

Run: `python tests/test_body_db_persist.py`
Expected: FAIL — `ModuleNotFoundError: No module named 'core.body_db'`

- [ ] **Step 3: Commit**

```bash
git add tests/test_body_db_persist.py
git commit -m "test(body_db): 4个持久化测试——往返/clear写空/缺失文件/空DB写盘

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## Task 3: BodyDB 实现(Green)

**Files:**
- Create: `core/body_db.py`

- [ ] **Step 1: Write the implementation**

Create `core/body_db.py`:

```python
# core/body_db.py — 人体特征 ID 内存数据库
#
# 镜像 face_db 的内存-only + flush_to_disk 模式,但:
#   - 存特征向量(plain list,余弦匹配),非 label_idx 精确匹配
#   - database_search 纯 Python cosine(不硬依赖 ulab)→ host 端可真单测
#   - score = dot/2 + 0.5(余弦 [-1,1] → [0,1],同 face_db)
#   - 轮转覆盖 4 槽(空槽优先,满则覆盖 _next_slot 并推进)
#   - 无"同类不重复占槽"(人体是连续特征,每次注册进新槽或覆盖)
#
# 纯 Python(无 MicroPython 依赖)→ 可在 host 端真单元测试。

from core import db_store

BODY_DB_PATH = "/sdcard/CamerAi/data/body_db.json"


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


def database_search(feature, db_features, threshold=0.5):
    """Cosine-match feature against db_features. Return (slot_id, score) or (None, 0.0).

    纯 Python cosine(host + board 通用)。db_features: {slot_id: list}。
    score = cos/2 + 0.5(余弦 [-1,1] → [0,1],同 face_db)。低于阈值 → (None, 0.0)。
    """
    if not db_features:
        return None, 0.0
    try:
        feat_list = _to_list(feature)
    except Exception:
        return None, 0.0
    feat_norm = sum(x * x for x in feat_list) ** 0.5
    if feat_norm == 0:
        return None, 0.0
    best_id = None
    best_score = 0.0
    for slot_id, db_feat in db_features.items():
        try:
            db_list = _to_list(db_feat)
        except Exception:
            continue
        db_n = sum(x * x for x in db_list) ** 0.5
        if db_n == 0:
            continue
        dot = sum(a * b for a, b in zip(feat_list, db_list))
        score = (dot / (feat_norm * db_n)) / 2 + 0.5
        if score > best_score:
            best_score = score
            best_id = slot_id
    if best_score < threshold:
        return None, 0.0
    return best_id, best_score


class _BodyDB:
    """人体特征内存库。feature 为 plain list(余弦匹配)。"""

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
        print("[BodyDB] registered feature(%d-dim) -> id%d (memory, dirty)" % (len(feat), slot))
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
        print("[BodyDB] cleared (memory, clear_dirty)")

    def _serialize(self):
        return {"next_slot": self._next_slot,
                "slots": {str(k): v for k, v in self._features.items()}}

    def load_from_disk(self, path=BODY_DB_PATH):
        """启动加载。db_store os.stat 预检查,文件不存在返回 None(避 ENOENT)。"""
        data = db_store.load_json(path)
        if data is None:
            return None
        try:
            self._next_slot = data.get("next_slot", 1)
            for slot_str, feat_list in data.get("slots", {}).items():
                self._features[int(slot_str)] = list(feat_list)
        except Exception as e:
            print("[BodyDB] load parse failed: %s" % e)
        return self._features

    def flush_to_disk(self, path=BODY_DB_PATH):
        """注册即写 / 退出兜底。open('w') 不抛 ENOENT。(镜像 face_db,始终写盘)"""
        db_store.save_json(path, self._serialize())
        self._dirty = False
        self._clear_dirty = False
        print("[BodyDB] flushed %d body(s) to %s" % (len(self._features), path))

    def init_features(self, path=BODY_DB_PATH):
        """启动时加载已注册人体特征到内存(同 face_db.init_features)。"""
        self.load_from_disk(path)
        print("[BodyDB] init_features: loaded %d body(s)" % len(self._features))
        return self._features

    @property
    def count(self):
        return len(self._features)


# 全局单例
body_db = _BodyDB()

# 导出供宿主测试 import
BodyDB = _BodyDB
```

- [ ] **Step 2: Run Task 1 tests to verify they pass**

Run: `python tests/test_body_db.py`
Expected: 9 PASS, 0 FAIL

- [ ] **Step 3: Run Task 2 tests to verify they pass**

Run: `python tests/test_body_db_persist.py`
Expected: 4 PASS, 0 FAIL

- [ ] **Step 4: Commit**

```bash
git add core/body_db.py
git commit -m "feat(body_db): 人体特征内存库镜像face_db——纯Python余弦匹配+轮转4槽

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## Task 4: body_ai AST 契约单测(Red)

**Files:**
- Create: `tests/test_body_ai_ast.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_body_ai_ast.py`:

```python
# tests/test_body_ai_ast.py — host-side AST 契约测试(body_ai)
import ast, os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _read(path):
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def test_body_ai_classes_exist():
    """PersonDetectionApp / PersonRecognitionApp / PersonRecognition 类必须定义。"""
    src = _read(os.path.join(ROOT, "core", "body_ai.py"))
    tree = ast.parse(src)
    classes = [n.name for n in ast.walk(tree) if isinstance(n, ast.ClassDef)]
    assert "PersonDetectionApp" in classes
    assert "PersonRecognitionApp" in classes
    assert "PersonRecognition" in classes


def test_person_labels_module_level():
    """PERSON_LABELS 必须在模块级别定义,含 1 个标签 'person'。"""
    src = _read(os.path.join(ROOT, "core", "body_ai.py"))
    tree = ast.parse(src)
    for n in tree.body:
        if isinstance(n, ast.Assign):
            for t in n.targets:
                if isinstance(t, ast.Name) and t.id == "PERSON_LABELS":
                    assert n.value.elts[0].s == "person"
                    return
    assert False, "PERSON_LABELS not found at module level"


def test_person_anchors_module_level():
    """PERSON_ANCHORS 必须在模块级别定义(9 个 anchor,18 个值)。"""
    src = _read(os.path.join(ROOT, "core", "body_ai.py"))
    tree = ast.parse(src)
    for n in tree.body:
        if isinstance(n, ast.Assign):
            for t in n.targets:
                if isinstance(t, ast.Name) and t.id == "PERSON_ANCHORS":
                    assert len(n.value.elts) == 18
                    return
    assert False, "PERSON_ANCHORS not found at module level"


def test_kmodel_paths_in_file():
    """kmodel 路径常量必须在文件中。"""
    src = _read(os.path.join(ROOT, "core", "body_ai.py"))
    assert "/sdcard/examples/kmodel/person_detect_yolov5n.kmodel" in src
    assert "/sdcard/examples/kmodel/recognition.kmodel" in src


def test_person_recognition_postprocess_returns():
    """PersonRecognitionApp.postprocess 必须有 return 语句(返回特征向量)。"""
    src = _read(os.path.join(ROOT, "core", "body_ai.py"))
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == "PersonRecognitionApp":
            for item in node.body:
                if isinstance(item, ast.FunctionDef) and item.name == "postprocess":
                    assert any(isinstance(n, ast.Return) for n in ast.walk(item)), \
                        "postprocess must have a return statement"
                    return
    assert False, "PersonRecognitionApp.postprocess not found"


def test_person_recognition_composite_methods():
    """PersonRecognition 组合类必须有 deinit + run 方法。"""
    src = _read(os.path.join(ROOT, "core", "body_ai.py"))
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == "PersonRecognition":
            methods = [n.name for n in node.body if isinstance(n, ast.FunctionDef)]
            assert "deinit" in methods, "PersonRecognition must have deinit"
            assert "run" in methods, "PersonRecognition must have run"
            return
    assert False, "PersonRecognition class not found"


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

- [ ] **Step 2: Run tests to verify they fail**

Run: `python tests/test_body_ai_ast.py`
Expected: FAIL — `FileNotFoundError` for `core/body_ai.py`

- [ ] **Step 3: Commit**

```bash
git add tests/test_body_ai_ast.py
git commit -m "test(body_ai): 6个AST契约测试——类/标签/anchors/kmodel路径/postprocess/composite

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## Task 5: body_ai 实现(Green)

**Files:**
- Create: `core/body_ai.py`

移植实验5(PersonDetectionApp,YOLOv5n)+ 实验20(PersonRecognitionApp,crop+resize 提特征)。镜像 gesture_ai 的 HandDetectionApp/HandRecognitionApp/HandRecognition 结构。

- [ ] **Step 1: Write the implementation**

Create `core/body_ai.py`:

```python
# core/body_ai.py — 人体检测与特征提取封装(移植 demo 实验5 + 实验20)
#
# 双 kmodel: person_detect_yolov5n.kmodel(人体检测,640×640,9 anchors,YOLOv5n) +
#           recognition.kmodel(通用特征提取,224×224,实验20)
#
# 检测用 aicube.anchorbasedet_post_process(同 gesture_ai 的 HandDetectionApp);
# 特征用 crop 检测框 + resize 224×224(无关键点对齐,人体无 umeyama)。
# PersonRecognition.run 返回 (det_boxes, features) 等长过滤后列表(避 zip 错位)。

import gc
import time

import nncase_runtime as nn
import ulab.numpy as np
import aicube
from libs.AIBase import AIBase
from libs.AI2D import Ai2d
from libs.Utils import ScopedTiming

# AI 通道分辨率(对齐 face_detect 的 chn2 XGA RGBP888)
RGB888P_SIZE = [1024, 768]
DISPLAY_SIZE = [640, 480]

# kmodel 路径(匹配 demo 实验5/实验20 存放位置)
PERSON_DET_KMPATH = "/sdcard/examples/kmodel/person_detect_yolov5n.kmodel"
PERSON_RECO_KMPATH = "/sdcard/examples/kmodel/recognition.kmodel"

# 9 个 hardcode anchors(同 demo 实验5 person_detect_yolov5n)
PERSON_ANCHORS = [10, 13, 16, 30, 33, 23, 30, 61, 62, 45,
                  59, 119, 116, 90, 156, 198, 373, 326]

# 1 类标签(同 demo 实验5)
PERSON_LABELS = ["person"]

# 4 槽颜色(同 face_detect BOX_COLORS)
BOX_COLORS = {
    1: 0x44CC44,
    2: 0x4488FF,
    3: 0xFF8844,
    4: 0xCC44FF,
}
BOX_UNKNOWN = 0xFFFFFF


def ALIGN_UP(x, align=16):
    return (x + align - 1) // align * align


def _draw_color(hex_color):
    """hex 0xRRGGBB → K230 draw_rectangle color tuple (A, B, G, R)。"""
    r = (hex_color >> 16) & 0xFF
    g = (hex_color >> 8) & 0xFF
    b = hex_color & 0xFF
    return (0xFF, b, g, r)


class PersonDetectionApp(AIBase):
    """人体检测(person_detect_yolov5n.kmodel, anchor-based YOLOv5n)。"""

    def __init__(self, kmodel_path, model_input_size, anchors,
                 confidence_threshold=0.2, nms_threshold=0.6,
                 strides=None, rgb888p_size=None, display_size=None, debug_mode=0):
        if strides is None:
            strides = [8, 16, 32]
        if rgb888p_size is None:
            rgb888p_size = RGB888P_SIZE
        if display_size is None:
            display_size = DISPLAY_SIZE
        super().__init__(kmodel_path, model_input_size, rgb888p_size, debug_mode)
        self.kmodel_path = kmodel_path
        self.model_input_size = model_input_size
        self.confidence_threshold = confidence_threshold
        self.nms_threshold = nms_threshold
        self.anchors = anchors
        self.strides = strides
        self.rgb888p_size = [ALIGN_UP(rgb888p_size[0], 16), rgb888p_size[1]]
        self.display_size = [ALIGN_UP(display_size[0], 16), display_size[1]]
        self.debug_mode = debug_mode
        self.ai2d = Ai2d(debug_mode)
        self.ai2d.set_ai2d_dtype(nn.ai2d_format.NCHW_FMT, nn.ai2d_format.NCHW_FMT,
                                 np.uint8, np.uint8)

    def config_preprocess(self, input_image_size=None):
        gc.collect()
        with ScopedTiming("set preprocess config", self.debug_mode > 0):
            ai2d_input_size = input_image_size if input_image_size else self.rgb888p_size
            top, bottom, left, right = self._get_padding_param()
            self.ai2d.pad([0, 0, 0, 0, top, bottom, left, right], 0, [114, 114, 114])
            self.ai2d.resize(nn.interp_method.tf_bilinear, nn.interp_mode.half_pixel)
            self.ai2d.build([1, 3, ai2d_input_size[1], ai2d_input_size[0]],
                            [1, 3, self.model_input_size[1], self.model_input_size[0]])

    def _get_padding_param(self):
        dst_w = self.model_input_size[0]
        dst_h = self.model_input_size[1]
        input_width = self.rgb888p_size[0]
        input_high = self.rgb888p_size[1]
        ratio_w = dst_w / input_width
        ratio_h = dst_h / input_high
        if ratio_w < ratio_h:
            ratio = ratio_w
        else:
            ratio = ratio_h
        new_w = int(ratio * input_width)
        new_h = int(ratio * input_high)
        dw = (dst_w - new_w) / 2
        dh = (dst_h - new_h) / 2
        top = int(round(dh - 0.1))
        bottom = int(round(dh + 0.1))
        left = int(round(dw - 0.1))
        right = int(round(dw + 0.1))
        return top, bottom, left, right

    def postprocess(self, results):
        with ScopedTiming("postprocess", self.debug_mode > 0):
            dets = aicube.anchorbasedet_post_process(
                results[0], results[1], results[2],
                self.model_input_size, self.rgb888p_size,
                self.strides, len(PERSON_LABELS),
                self.confidence_threshold, self.nms_threshold,
                self.anchors, False)
            return dets

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


class PersonRecognitionApp(AIBase):
    """人体特征提取(recognition.kmodel, 224×224 输入, crop 检测框,无对齐)。

    移植实验20 SelfLearningApp 的预处理(crop+resize)与 postprocess(返回特征向量)。
    无关键点→不做 umeyama 仿射对齐(区别于 FaceRegistrationApp)。
    """

    def __init__(self, kmodel_path, model_input_size,
                 rgb888p_size=None, display_size=None, debug_mode=0):
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

    def config_preprocess(self, det, input_image_size=None):
        gc.collect()
        with ScopedTiming("set preprocess config", self.debug_mode > 0):
            ai2d_input_size = input_image_size if input_image_size else self.rgb888p_size
            self.crop_params = self._get_crop_param(det)
            self.ai2d.crop(self.crop_params[0], self.crop_params[1],
                           self.crop_params[2], self.crop_params[3])
            self.ai2d.resize(nn.interp_method.tf_bilinear, nn.interp_mode.half_pixel)
            self.ai2d.build([1, 3, ai2d_input_size[1], ai2d_input_size[0]],
                            [1, 3, self.model_input_size[1], self.model_input_size[0]])

    def _get_crop_param(self, det_box):
        x1, y1, x2, y2 = det_box[2], det_box[3], det_box[4], det_box[5]
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


class PersonRecognition:
    """人体检测+特征提取组合:先检人体再提特征,返回检测框+特征(等长,已过滤)。"""

    def __init__(self, person_det_kmodel, person_rec_kmodel,
                 det_input_size=None, rec_input_size=None,
                 anchors=None,
                 confidence_threshold=0.2, nms_threshold=0.6,
                 strides=None, rgb888p_size=None, display_size=None, debug_mode=0):
        if det_input_size is None:
            det_input_size = [640, 640]
        if rec_input_size is None:
            rec_input_size = [224, 224]
        if anchors is None:
            anchors = PERSON_ANCHORS
        if strides is None:
            strides = [8, 16, 32]
        if rgb888p_size is None:
            rgb888p_size = RGB888P_SIZE
        if display_size is None:
            display_size = DISPLAY_SIZE
        self.person_det_kmodel = person_det_kmodel
        self.person_rec_kmodel = person_rec_kmodel
        self.det_input_size = det_input_size
        self.rec_input_size = rec_input_size
        self.anchors = anchors
        self.confidence_threshold = confidence_threshold
        self.nms_threshold = nms_threshold
        self.strides = strides
        self.rgb888p_size = [ALIGN_UP(rgb888p_size[0], 16), rgb888p_size[1]]
        self.display_size = [ALIGN_UP(display_size[0], 16), display_size[1]]
        self.debug_mode = debug_mode

        # ⚠️ 双 kmodel 顺序根因(坑#19,同 face_detect/gesture_detect):
        # rec kmodel 必须在 det.config_preprocess() 之前加载。
        self.person_rec = PersonRecognitionApp(
            self.person_rec_kmodel, model_input_size=self.rec_input_size,
            rgb888p_size=self.rgb888p_size, display_size=self.display_size)
        self.person_det = PersonDetectionApp(
            self.person_det_kmodel, model_input_size=self.det_input_size,
            anchors=self.anchors,
            confidence_threshold=self.confidence_threshold,
            nms_threshold=self.nms_threshold,
            strides=self.strides,
            rgb888p_size=self.rgb888p_size, display_size=self.display_size,
            debug_mode=0)
        self.person_det.config_preprocess()

    def run(self, img_np):
        """推理当前帧。返回 (det_res, feat_res)(等长,都是已过滤)。

        det_res: 人体检测框列表,每框 [..., x1,y1,x2,y2, ...](仅通过边界过滤的)。
        feat_res: [feature_vec, ...] 每个人体的特征向量(同索引对应)。
        过滤:高度 < 0.1×rgb888p_h 剔除;边缘窄框剔除(同 demo 实验5 逻辑)。
        """
        det_boxes = self.person_det.run(img_np)
        det_res = []
        feat_res = []
        for det_box in det_boxes:
            x1, y1, x2, y2 = det_box[2], det_box[3], det_box[4], det_box[5]
            w, h = int(x2 - x1), int(y2 - y1)
            # 边界过滤(同 demo 实验5)
            if h < (0.1 * self.rgb888p_size[1]):
                continue
            if (w < (0.25 * self.rgb888p_size[0])
                    and ((x1 < (0.03 * self.rgb888p_size[0]))
                         or (x2 > (0.97 * self.rgb888p_size[0])))):
                continue
            if (w < (0.15 * self.rgb888p_size[0])
                    and ((x1 < (0.01 * self.rgb888p_size[0]))
                         or (x2 > (0.99 * self.rgb888p_size[0])))):
                continue
            self.person_rec.config_preprocess(det_box)
            feature = self.person_rec.run(img_np)
            det_res.append(det_box)
            feat_res.append(feature)
        return det_res, feat_res

    def deinit(self):
        try:
            self.person_det.deinit()
        except Exception:
            pass
        try:
            self.person_rec.deinit()
        except Exception:
            pass
```

- [ ] **Step 2: Run Task 4 AST tests to verify they pass**

Run: `python tests/test_body_ai_ast.py`
Expected: 6 PASS, 0 FAIL

- [ ] **Step 3: Commit**

```bash
git add core/body_ai.py
git commit -m "feat(body_ai): 双kmodel人体检测+特征提取——移植实验5(YOLOv5n检测)+实验20(recognition提特征)

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## Task 6: 基础设施(host_api + app_runtime + icon_cache + 图标 + i18n)

**Files:**
- Modify: `comm/host_api.py`
- Modify: `core/app_runtime.py`
- Modify: `core/icon_cache.py`
- Create: `resource/icons/body_detect_icon/back.png`, `list.png`
- Modify: `resource/i18n/zh_CN.json`, `resource/i18n/en_US.json`

- [ ] **Step 1: Add CATEGORY_TYPE mapping in host_api.py**

In `comm/host_api.py`, find the `CATEGORY_TYPE` dict. After the line:

```python
        "gesture_detect": TYPE_GESTURE_DETECT,  # 0x08
```

Add:

```python
        "body_detect":   TYPE_BODY_DETECT,     # 0x09
```

(`TYPE_BODY_DETECT = 0x09` already exists at the top of the file — no new constant needed.)

- [ ] **Step 2: Add body_detect channel branch in app_runtime.py**

In `core/app_runtime.py`, `_channels_for` method. After the `gesture_detect` branch:

```python
        elif category_id == "gesture_detect":
            # chn2 XGA RGBP888 做 AI 推理(同 face_detect AI 通道)
            chs.append((CAM_CHN_ID_2, Sensor.XGA, Sensor.RGBP888))
```

Add a new branch:

```python
        elif category_id == "body_detect":
            # chn2 XGA RGBP888 做 AI 推理(同 face_detect/gesture_detect AI 通道)
            chs.append((CAM_CHN_ID_2, Sensor.XGA, Sensor.RGBP888))
```

- [ ] **Step 3: Add body_detect init_app preload in app_runtime.py**

In `core/app_runtime.py`, `init_app` method. After the `gesture_detect` branch:

```python
        elif category_id == "gesture_detect":
            icon_cache.preload_gesture_icons()
```

Add:

```python
        elif category_id == "body_detect":
            icon_cache.preload_body_icons()
```

- [ ] **Step 4: Add _body_icons slot in icon_cache.py**

In `core/icon_cache.py`, `__init__`. After the line:

```python
        self._gesture_icons = {}     # name -> (data, dsc)
```

Add:

```python
        self._body_icons = {}     # name -> (data, dsc)
```

- [ ] **Step 5: Add preload_body_icons + get_body_icon in icon_cache.py**

In `core/icon_cache.py`, after the `get_gesture_icon` method (before the `# 全局单例` line):

```python
    def preload_body_icons(self):
        """预读人体识别APP图标（在首次 task_handler 之前调用）"""
        base = "/sdcard/CamerAi/resource/icons/body_detect_icon/"
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
                self._body_icons[name] = (data, dsc)
                print(f"[IconCache] body/{name} OK ({len(data)} bytes)")
            except Exception as e:
                print(f"[IconCache] body/{name} FAILED: {e}")

    def get_body_icon(self, name):
        """获取人体识别图标 (data, dsc)，未缓存返回 (None, None)"""
        return self._body_icons.get(name, (None, None))
```

- [ ] **Step 6: Copy icons**

Copy `resource/icons/gesture_detect_icon/back.png` → `resource/icons/body_detect_icon/back.png` and `list.png` → `resource/icons/body_detect_icon/list.png` (directory `resource/icons/body_detect_icon/` already exists but is empty).

Run (PowerShell):
```powershell
Copy-Item resource\icons\gesture_detect_icon\back.png resource\icons\body_detect_icon\back.png
Copy-Item resource\icons\gesture_detect_icon\list.png resource\icons\body_detect_icon\list.png
```

Or (bash):
```bash
cp resource/icons/gesture_detect_icon/back.png resource/icons/body_detect_icon/back.png
cp resource/icons/gesture_detect_icon/list.png resource/icons/body_detect_icon/list.png
```

- [ ] **Step 7: Add body_detect i18n block**

In `resource/i18n/zh_CN.json`, find the `gesture_detect` block. After it (sibling key at the same nesting level), add a `body_detect` block with the same keys. Use the existing `gesture_detect` block as the structural template; only change the texts:

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
```

In `resource/i18n/en_US.json`, add the corresponding English block:

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
```

⚠️ Verify the JSON remains valid (trailing commas, bracket matching). Open both files, locate the `gesture_detect` block, and insert the `body_detect` block immediately after it at the same indentation.

- [ ] **Step 8: Verify JSON validity**

Run:
```bash
python -c "import json; json.load(open('resource/i18n/zh_CN.json',encoding='utf-8')); json.load(open('resource/i18n/en_US.json',encoding='utf-8')); print('JSON OK')"
```
Expected: `JSON OK`

- [ ] **Step 9: Commit**

```bash
git add comm/host_api.py core/app_runtime.py core/icon_cache.py resource/icons/body_detect_icon/ resource/i18n/zh_CN.json resource/i18n/en_US.json
git commit -m "feat(body_detect): 基础设施——CATEGORY_TYPE 0x09+chn2通道+preload_body_icons+图标+i18n

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## Task 7: body_detect app AST 契约单测(Red)

**Files:**
- Create: `tests/test_body_detect_ast.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_body_detect_ast.py`:

```python
# tests/test_body_detect_ast.py -- host-side AST 契约测试(body_detect)
import ast, os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP_RUNTIME_PATH = os.path.join(ROOT, "core", "app_runtime.py")
ICON_CACHE_PATH = os.path.join(ROOT, "core", "icon_cache.py")
HOST_API_PATH = os.path.join(ROOT, "comm", "host_api.py")


def _read(path):
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def test_body_detect_in_category_type_map():
    """CATEGORY_TYPE 必须包含 'body_detect': TYPE_BODY_DETECT。"""
    src = _read(HOST_API_PATH)
    assert '"body_detect":' in src
    after = src.split('"body_detect":')[1][:80]
    assert "TYPE_BODY_DETECT" in after


def test_channels_for_body_detect():
    """_channels_for 的 body_detect 分支 append chn2 XGA RGBP888。"""
    src = _read(APP_RUNTIME_PATH)
    start = src.find("def _channels_for(")
    body = src[start:start + 1800]
    assert "body_detect" in body, "_channels_for must handle body_detect"
    after = body.split('"body_detect"')[1][:300]
    assert "append" in after, "body_detect should append AI channel"
    assert "CAM_CHN_ID_2" in after, "body_detect must use CAM_CHN_ID_2 for AI"
    assert "XGA" in after, "body_detect AI channel must use XGA framesize"
    assert "RGBP888" in after, "body_detect AI channel must use RGBP888 pixformat"


def test_preload_body_icons_in_init_app():
    """init_app 必须对 body_detect 调 preload_body_icons。"""
    src = _read(APP_RUNTIME_PATH)
    assert '"body_detect"' in src
    assert 'preload_body_icons' in src


def test_icon_cache_has_body_methods():
    """icon_cache 必须有 preload_body_icons + get_body_icon + _body_icons 槽。"""
    src = _read(ICON_CACHE_PATH)
    assert "def preload_body_icons" in src
    assert "def get_body_icon" in src
    assert "_body_icons" in src


def test_app_imports_body_ai():
    """app.py 必须导入 body_ai 的 PersonRecognition 类。"""
    app_path = os.path.join(ROOT, "scripts", "body_detect", "app.py")
    src = _read(app_path)
    assert "body_ai" in src, "app must import from body_ai"
    assert "PersonRecognition" in src, "app must import PersonRecognition"


def test_on_frame_uses_registrar():
    """app.py on_frame 必须使用 try_register(..., registrar=body_db.register)。"""
    app_path = os.path.join(ROOT, "scripts", "body_detect", "app.py")
    src = _read(app_path)
    assert "registrar" in src, "app must use registrar pattern for K2 registration"


def test_has_host_tick():
    """app.py on_frame 必须有 host_tick 调用。"""
    app_path = os.path.join(ROOT, "scripts", "body_detect", "app.py")
    src = _read(app_path)
    assert "host_tick" in src, "app must call host_tick for protocol 0x09"


def test_has_draw_cross():
    """app.py on_frame 必须有 draw_cross 调用(居中绿色十字)。"""
    app_path = os.path.join(ROOT, "scripts", "body_detect", "app.py")
    src = _read(app_path)
    assert "draw_cross" in src, "app must call draw_cross for center crosshair"


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

- [ ] **Step 2: Run tests to verify they fail**

Run: `python tests/test_body_detect_ast.py`
Expected: FAIL — `FileNotFoundError` for `scripts/body_detect/app.py` (and infrastructure assertions may also fail until Task 6 is done — but Task 6 should be done first; if running standalone, the host_api/icon_cache/app_runtime assertions should already pass from Task 6, only the app.py assertions fail).

- [ ] **Step 3: Commit**

```bash
git add tests/test_body_detect_ast.py
git commit -m "test(body_detect): 8个AST契约测试——channels/icon/registrar/host_tick/cross

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## Task 8: body_detect 主脚本(Green)

**Files:**
- Create: `scripts/body_detect/app.py`

复刻 `scripts/face_detect/app.py` + `scripts/gesture_detect/app.py`。业务:chn2 检测+提特征 → database_search 余弦匹配 → 画框/ID/十字 → K2 注册 → host_tick(协议 0x09)。

- [ ] **Step 1: Write the implementation**

Create `scripts/body_detect/app.py`:

```python
# scripts/body_detect/app.py — 人体识别(双 kmodel + K2 注册 4 槽 + 协议 0x09)
#
# 复刻 face_detect 模式: chn2 AI 检测+提特征 → database_search 余弦匹配
# → 填 slots → K2 registrar → host_tick。画十字 + 彩色框 + ID 标签。

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
from core.body_ai import PersonRecognition, PERSON_DET_KMPATH, PERSON_RECO_KMPATH, \
    PERSON_ANCHORS, RGB888P_SIZE, DISPLAY_SIZE
from core.body_db import body_db, database_search, BODY_DB_PATH

BAR_H = 52
PREVIEW_Y = BAR_H
PREVIEW_H = 376
BAR_BG = 0x1A1A1A

# 4 槽颜色(同 face_detect BOX_COLORS)
BOX_COLORS = {
    1: 0x44CC44,   # 绿
    2: 0x4488FF,   # 蓝
    3: 0xFF8844,   # 橙
    4: 0xCC44FF,   # 紫
}
BOX_UNKNOWN = 0xFFFFFF   # 未注册白框


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
_person_rec = None
_db_features = {}
_overlay = None
_clear_btn = None
_save_btn = None
_close_overlay = False


def _init_ai():
    """Load BOTH kmodels before the loop.

    ⚠️ 双 kmodel 顺序根因:rec kmodel 必须在 det.config_preprocess()
    之前加载,否则破坏共享 NPU/AI2D 状态(坑#19,同 face_detect/gesture_detect)。
    PersonRecognition.__init__ 已按此顺序加载。
    """
    global _person_rec, _db_features
    print("[body_detect] loading person detection + recognition models...")
    _person_rec = PersonRecognition(
        PERSON_DET_KMPATH, PERSON_RECO_KMPATH,
        det_input_size=[640, 640], rec_input_size=[224, 224],
        anchors=PERSON_ANCHORS,
        confidence_threshold=0.2, nms_threshold=0.6,
        rgb888p_size=RGB888P_SIZE, display_size=DISPLAY_SIZE,
        debug_mode=0)
    _db_features = body_db.init_features()
    print("[body_detect] AI ready, loaded %d body(s)" % len(_db_features))


def _init_registry(fpioa):
    global _id_registry
    _id_registry = IdRegistry(fpioa, pin=0)


def _deinit_ai():
    global _person_rec
    if _person_rec is not None:
        try:
            _person_rec.deinit()
        except Exception as e:
            print("[body_detect] deinit warning: %s" % e)
        _person_rec = None


def on_frame(img):
    """chn2 检测+提特征 → 每个人体余弦匹配 → 画框 + ID 标签 → host_tick。

    对每个检测到的人体:database_search 匹配 DB → 找到 slot 则彩色框+ID#序号标签;
    未注册白框+person 标签。K2 注册当前帧最大人体的特征(复用 run() 已提取的 feature)。
    """
    if _RUNTIME is None or _person_rec is None:
        return
    img_ai = _RUNTIME.sensor.snapshot(chn=CAM_CHN_ID_2)
    img_np = img_ai.to_numpy_ref()
    try:
        det_boxes, features = _person_rec.run(img_np)
    except Exception as e:
        print("[body_detect] run error: %s" % e)
        det_boxes, features = [], []

    slots = [None, None, None, None]
    filled_slots = set()  # 本帧已填充的 slot(防多人体匹配同一 slot 覆盖)

    for det_box, feature in zip(det_boxes, features):
        x1, y1, x2, y2 = det_box[2], det_box[3], det_box[4], det_box[5]
        # 缩放到 VGA
        x = int(x1) * DISPLAY_SIZE[0] // RGB888P_SIZE[0]
        y = int(y1) * DISPLAY_SIZE[1] // RGB888P_SIZE[1]
        w = int(x2 - x1) * DISPLAY_SIZE[0] // RGB888P_SIZE[0]
        h = int(y2 - y1) * DISPLAY_SIZE[1] // RGB888P_SIZE[1]
        slot, score = database_search(feature, _db_features)
        if slot is not None and slot not in filled_slots:
            color = _draw_color(BOX_COLORS.get(slot, BOX_UNKNOWN))
            img.draw_rectangle(x, y, w, h, color=color, thickness=4)
            conf = int(score * 100)
            img.draw_string_advanced(x + 2, y - 24, 24,
                                     "ID%d person" % slot, color=color)
            slots[slot - 1] = (slot, x, y, w, h, conf)
            filled_slots.add(slot)
        else:
            color = _draw_color(BOX_UNKNOWN)
            img.draw_rectangle(x, y, w, h, color=color, thickness=2)
            img.draw_string_advanced(x + 2, y - 24, 24, "person", color=color)

    # 屏幕居中绿色十字(对准参考):VGA 640×480 中心 (320, 240)
    img.draw_cross(320, 240, color=(0xFF, 0x00, 0xFF, 0x00), size=20, thickness=2)

    # K2 注册:当前帧最大人体的特征(复用 run() 已提取的 features[max_i])
    if _id_registry is not None and _id_registry.has_pending() and det_boxes:
        max_i = max(range(len(det_boxes)),
                    key=lambda j: (det_boxes[j][4] - det_boxes[j][2])
                                  * (det_boxes[j][5] - det_boxes[j][3]))
        if max_i < len(features):
            feature = features[max_i]
            try:
                slot = _id_registry.try_register(
                    feature, _RUNTIME.buzzer,
                    registrar=body_db.register)
                if slot is not None:
                    body_db.flush_to_disk()
                    _db_features[slot] = body_db.get_features().get(slot)
                    _refresh_count()
            except Exception as e:
                print("[body_detect] register error: %s" % e)

    if _RUNTIME is not None and _RUNTIME.host is not None:
        _RUNTIME.host_tick(slots)
    gc.collect()


def _refresh_count():
    if _count_label is not None:
        try:
            _count_label.set_text(
                _RUNTIME.lang.t("body_detect.registered", len(_db_features)))
        except Exception:
            pass


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
    cl.set_text(_RUNTIME.lang.t("body_detect.clear"))
    cl.add_style(make_back_bar_text_style(fonts.body), 0)
    cl.center()
    _clear_btn.add_event(_on_clear_clicked, lv.EVENT.CLICKED, None)

    _save_btn = lv.btn(_overlay)
    _save_btn.set_size(120, 40)
    _save_btn.align(lv.ALIGN.RIGHT_MID, -20, 0)
    sv = lv.label(_save_btn)
    sv.set_text(_RUNTIME.lang.t("body_detect.save"))
    sv.add_style(make_back_bar_text_style(fonts.body), 0)
    sv.center()
    _save_btn.add_event(_on_save_clicked, lv.EVENT.CLICKED, None)


def _on_overlay_clicked(e):
    global _close_overlay
    if e.get_code() != lv.EVENT.CLICKED:
        return
    _close_overlay = True


def _on_screen_clicked(e):
    global _close_overlay
    if e.get_code() != lv.EVENT.CLICKED:
        return
    if _overlay is not None:
        _close_overlay = True


def _on_clear_clicked(e):
    global _close_overlay
    if e.get_code() != lv.EVENT.CLICKED:
        return
    body_db.clear()
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
    """Build top bar, transparent preview area, and bottom bar."""
    global _screen, _top_bar, _bottom_bar, _preview, _count_label
    screen = lv.scr_act()
    screen.set_style_bg_opa(0, 0)
    screen.add_flag(lv.obj.FLAG.CLICKABLE)
    screen.add_event(_on_screen_clicked, lv.EVENT.CLICKED, None)
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

    icon_data, icon_dsc = icon_cache.get_body_icon("back")
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
    title.set_text(runtime.lang.t("category.body_detect"))
    title.align(lv.ALIGN.CENTER, 0, 0)
    from ui.theme import make_back_bar_text_style
    title.add_style(make_back_bar_text_style(fonts.body), 0)

    _preview = lv.obj(screen)
    _preview.set_size(lv.pct(100), PREVIEW_H)
    _preview.set_pos(0, PREVIEW_Y)
    _preview.set_style_bg_opa(0, 0)
    _preview.set_style_border_width(0, 0)
    _preview.set_style_pad_all(0, 0)
    _preview.set_style_radius(0, 0)
    _preview.clear_flag(lv.obj.FLAG.SCROLLABLE)
    _preview.clear_flag(lv.obj.FLAG.CLICKABLE)

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
    list_icon_data, list_icon_dsc = icon_cache.get_body_icon("list")
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
    count_label.set_text(runtime.lang.t("body_detect.registered", len(_db_features)))
    count_label.add_style(make_back_bar_text_style(fonts.body), 0)
    count_label.align(lv.ALIGN.CENTER, 0, 0)
    _count_label = count_label


def _destroy_ui():
    """Delete LVGL objects and restore screen opacity for the menu."""
    global _screen, _top_bar, _bottom_bar, _preview, _count_label, _overlay, _clear_btn, _save_btn
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
                print("[body_detect] on_frame error: %s" % e)
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
                print("[body_detect] fc=%d" % fc)
    finally:
        _deinit_ai()
        _destroy_ui()
        _RUNTIME = None
        body_db.flush_to_disk()  # 退出兜底写盘
```

- [ ] **Step 2: Run Task 7 AST tests to verify they pass**

Run: `python tests/test_body_detect_ast.py`
Expected: 8 PASS, 0 FAIL

- [ ] **Step 3: Commit**

```bash
git add scripts/body_detect/app.py
git commit -m "feat(body_detect): 主脚本——复刻face_detect双kmodel+K2 registrar+4槽+协议0x09

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## Task 9: 全量回归

**Files:** none (verification only)

- [ ] **Step 1: Run all body tests**

Run:
```bash
python tests/test_body_db.py && python tests/test_body_db_persist.py && python tests/test_body_ai_ast.py && python tests/test_body_detect_ast.py
```
Expected: All PASS (9 + 4 + 6 + 8 = 27 tests), exit 0.

- [ ] **Step 2: Run full existing test suite to verify zero regression**

Run all test files in `tests/`:
```bash
for f in tests/test_*.py; do echo "=== $f ==="; python "$f" || echo "FAILED: $f"; done
```
Expected: every test file reports 0 failures. No existing test (face_db/gesture_db/object_db/tag/color/road/host_api/etc.) regresses.

- [ ] **Step 3: Verify app.py syntax (Python parse, no MicroPython imports needed)**

Run:
```bash
python -c "import ast; ast.parse(open('scripts/body_detect/app.py',encoding='utf-8').read()); ast.parse(open('core/body_ai.py',encoding='utf-8').read()); ast.parse(open('core/body_db.py',encoding='utf-8').read()); print('PARSE OK')"
```
Expected: `PARSE OK`

- [ ] **Step 4: Commit regression record (if any test fixture changes) or empty-ammendment**

If no file changes in this task, skip commit. If a test or fixture was touched, commit it:
```bash
git add -A
git commit -m "test(body_detect): 全量回归——27新测试PASS+所有已有测试零退化

Co-Authored-By: Claude <noreply@anthropic.com>"
```

- [ ] **Step 5: Push (only if user requests)**

```bash
git push origin main
```
(Do NOT push without explicit user request.)

---

## Self-Review Notes

**Spec coverage:**
- §3.1 BodyDB(余弦匹配,纯 python,4槽轮转,flush/clear/init)→ Task 3 ✓
- §3.2 body_ai(PersonDetectionApp aicube.anchorbasedet + PersonRecognitionApp crop+resize + PersonRecognition 组合类,加载顺序坑#19)→ Task 5 ✓
- §3.3 app.py(chn2→run→database_search→画框/十字→K2 registrar→host_tick 0x09)→ Task 8 ✓
- §3.4 基础设施(host_api CATEGORY_TYPE + app_runtime channels/init + icon_cache + 图标 + i18n)→ Task 6 ✓
- §4 测试(9+4+6+8=27)→ Task 1/2/4/7 ✓
- §5 风险(加载顺序、维度未知、阈值)→ 代码注释 + spec 记录,板端验收 ✓

**Type consistency check:**
- `BodyDB.register(feature)` 返回 slot_id(int 1-4) — Task 3 实现 + Task 1 测试一致 ✓
- `database_search(feature, db_features, threshold=0.5)` 返回 (slot, score) — Task 3 实现 + Task 1/2 测试一致 ✓
- `body_db.get_features()` 返回 dict 引用 — Task 3 实现 + Task 1/8 调用一致 ✓
- `PersonRecognition.run(img_np)` 返回 (det_boxes, features) — Task 5 实现 + Task 8 调用一致 ✓
- `PersonRecognitionApp.config_preprocess(det, ...)` 签名 — Task 5 实现 + Task 5 PersonRecognition.run 调用一致 ✓
- `icon_cache.get_body_icon(name)` 返回 (data, dsc) — Task 6 实现 + Task 8 调用一致 ✓
- `_id_registry.try_register(feature, buzzer, registrar=body_db.register)` — Task 8 调用,IdRegistry 签名 `try_register(self, feature, buzzer=None, registrar=None)` 已存在 ✓

**关键设计决策固化(已写入 plan 顶部 + 各 Task):**
1. `database_search` 纯 Python cosine(不硬依赖 ulab)→ host 可单测。Task 3 实现 + Task 1 测试用 plain list 验证余弦。
2. BodyDB 存 plain list(register 时 `_to_list` 转换)。Task 3。
3. `PersonRecognition.run` 返回等长过滤后 (det_boxes, features),避 zip 错位。Task 5。
4. K2 注册复用 `features[max_i]`(无需像 face_detect 再跑一次 reg)。Task 8。
5. 加载顺序:PersonRecognitionApp 在 PersonDetectionApp.config_preprocess() 前加载(坑#19)。Task 5 `__init__` 顺序固化。

**潜在问题(板端验收,非 plan bug):**
- recognition.kmodel 维度未知 → database_search 纯 python 对任意维度 list 都工作,板端首帧 print `len(feature)` 确认。
- recognition.kmodel 水果训练 → 人体准确率未验证 → 阈值 0.5 起步,板端调;若误匹配高降级(仅最大人体提特征,但本 plan 已每框提特征)。
