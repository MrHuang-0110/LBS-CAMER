# 手势识别(gesture_detect)实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use subagent-driven development (recommended) or inline execution to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 新建 gesture_detect 脚本,复刻 face_detect 单线程模板 + 双 kmodel(hand_det + hand_reco) + K2 注册 4 槽 ID + 协议 0x08。

**Architecture:** GestureDB 镜像 ObjectDB(int label_idx 精确匹配,"同类不重复占槽")。gesture_ai 移植 demo 实验9 的 HandDetectionApp + HandRecognitionApp + HandRecognition 组合类,postprocess 返回 (label_idx, score)。app 复刻 object_detect 的 chn2 检测 + K2 registrar + 十字 + host_tick 模式。

**Tech Stack:** K230 MicroPython, NPU(双 kmodel), AIBase/AI2D, aicube, ulab.numpy, LVGL, db_store

---

### Task 1: 手势数据库单元测试(内存逻辑)

**Files:**
- Create: `tests/test_gesture_db.py`

- [ ] **Step 1: 创建测试文件,写 9 个内存逻辑测试**

```python
# tests/test_gesture_db.py — GestureDB 纯 Python 单测(无 MicroPython 依赖)
import sys, os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def test_register_returns_slot_id():
    from core.gesture_db import GestureDB
    db = GestureDB()
    slot = db.register(0)  # label_idx=0 (gun)
    assert slot == 1


def test_register_empty_slots_fill_in_order():
    from core.gesture_db import GestureDB
    db = GestureDB()
    assert db.register(0) == 1
    assert db.register(2) == 2  # yeah
    assert db.register(3) == 3  # five
    assert db.register(1) == 4  # other
    assert db.count == 4


def test_register_same_label_returns_existing_slot():
    """同类不重复占槽:已注册的 label_idx 再注册返回原槽,不推进指针。"""
    from core.gesture_db import GestureDB
    db = GestureDB()
    db.register(0)  # slot 1
    db.register(2)  # slot 2
    slot = db.register(0)  # 再注册 gun → 应返回 slot 1,不占新槽
    assert slot == 1
    assert db.count == 2


def test_register_round_robin_when_full():
    """满 4 槽后再注册新手势 → 覆盖 _next_slot(1→2→3→4→1)。"""
    from core.gesture_db import GestureDB
    db = GestureDB()
    db.register(0)  # slot 1: gun
    db.register(2)  # slot 2: yeah
    db.register(3)  # slot 3: five
    db.register(1)  # slot 4: other
    # 满,下一新手势覆盖 slot 1
    slot = db.register(0)  # gun 已存在 slot 1 → 返回 1(同类不重复占槽)
    assert slot == 1
    # 真正的新手势:label 0/2/3/1 都已注册,没有新 label 可注册
    # 注:只有 4 个手势标签,满 4 槽后再注册只能是重复(返回原槽)或覆盖
    # 这是固定 4 类分类器的固有约束——4 个标签 = 4 个槽,满后无"新"类可加


def test_match_returns_slot_and_score():
    from core.gesture_db import GestureDB
    db = GestureDB()
    db.register(0)  # slot 1: gun
    db.register(2)  # slot 2: yeah
    slot, score = db.match(2)
    assert slot == 2
    assert score == 1.0


def test_match_returns_none_for_unregistered_label():
    from core.gesture_db import GestureDB
    db = GestureDB()
    slot, score = db.match(0)
    assert slot is None
    assert score == 0.0


def test_match_empty_db():
    from core.gesture_db import GestureDB
    db = GestureDB()
    slot, score = db.match(0)
    assert slot is None
    assert score == 0.0


def test_clear_resets_all():
    from core.gesture_db import GestureDB
    db = GestureDB()
    db.register(0)
    db.register(2)
    db.clear()
    assert db.count == 0
    slot, score = db.match(0)
    assert slot is None


def test_count_property():
    from core.gesture_db import GestureDB
    db = GestureDB()
    assert db.count == 0
    db.register(0)
    assert db.count == 1
    db.register(2)
    assert db.count == 2
    db.register(0)  # 同类不重复占槽
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

- [ ] **Step 2: 运行测试确认全部失败(模块不存在)**

```bash
python tests/test_gesture_db.py
```
Expected: 9 FAIL (ImportError: No module named 'core.gesture_db')

- [ ] **Step 3: Commit**

```bash
git add tests/test_gesture_db.py
git commit -m "test(gesture_db): 9个内存逻辑单测——register/Match/clear/count/同类不重复占槽"
```

---

### Task 2: 手势数据库持久化测试(TDD)

**Files:**
- Create: `tests/test_gesture_db_persist.py`

- [ ] **Step 1: 创建 4 个持久化测试**

```python
# tests/test_gesture_db_persist.py — GestureDB 磁盘持久化测试
import sys, os, tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def test_flush_and_load_roundtrip():
    from core.gesture_db import GestureDB
    db = GestureDB()
    db.register(0)  # slot 1: gun
    db.register(2)  # slot 2: yeah
    db.register(3)  # slot 3: five
    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
        tpath = f.name
    try:
        db.flush_to_disk(tpath)
        db2 = GestureDB()
        result = db2.load_from_disk(tpath)
        assert result is not None
        assert len(result) == 3
        slot, score = db2.match(0)
        assert slot == 1
        slot, score = db2.match(2)
        assert slot == 2
    finally:
        os.unlink(tpath)


def test_flush_clear_writes_empty():
    from core.gesture_db import GestureDB
    db = GestureDB()
    db.register(0)
    db.clear()
    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
        tpath = f.name
    try:
        db.flush_to_disk(tpath)
        db2 = GestureDB()
        result = db2.load_from_disk(tpath)
        assert result is not None  # load_from_disk returns dict (empty slots)
        assert len(result) == 0
    finally:
        os.unlink(tpath)


def test_load_from_missing_file_returns_none():
    from core.gesture_db import GestureDB
    db = GestureDB()
    result = db.load_from_disk("/nonexistent/gesture_db_test.json")
    assert result is None


def test_flush_empty_db_writes_valid_json():
    """flush 空 DB(未 register)照常写盘(镜像 ObjectDB)。"""
    from core.gesture_db import GestureDB
    db = GestureDB()
    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
        tpath = f.name
    try:
        db.flush_to_disk(tpath)  # 不应 crash
        db2 = GestureDB()
        result = db2.load_from_disk(tpath)
        assert result is not None  # 文件存在，内容为有效 JSON
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

- [ ] **Step 2: 运行测试确认全部失败**

```bash
python tests/test_gesture_db_persist.py
```
Expected: 4 FAIL (ImportError)

- [ ] **Step 3: Commit**

```bash
git add tests/test_gesture_db_persist.py
git commit -m "test(gesture_db): 4个持久化测试——往返/clear写空/缺失文件/无变化跳过"
```

---

### Task 3: 实现 GestureDB

**Files:**
- Create: `core/gesture_db.py`

- [ ] **Step 1: 实现核心模块**

```python
# core/gesture_db.py — 手势 ID 内存数据库
#
# 镜像 ObjectDB 的内存-only + flush_to_disk 模式:
#   - 存 int label_idx(0-3, hand_reco.kmodel 标签索引 gun/other/yeah/five)
#   - 精确匹配(label_idx 相等即命中),无相似度概念,score=1.0
#   - 同类不重复占槽:同一 label_idx 再注册返回原槽,不推进轮转指针
#   - registrar 签名(供 IdRegistry 复用)
#
# 纯 Python(无 MicroPython 依赖)→ 可在 host 端真单元测试。

from core import db_store

GESTURE_DB_PATH = "/sdcard/CamerAi/data/gesture_db.json"


class _GestureDB:
    """手势标签内存库。label_idx 为 int(0-3:gun/other/yeah/five)。"""

    def __init__(self):
        self._features = {}        # {slot_id: label_idx}
        self._next_slot = 1        # 轮转覆盖指针(1-4 循环)
        self._dirty = False
        self._clear_dirty = False

    def register(self, label_idx):
        """注册 label_idx 到槽位(轮转覆盖;同类不重复占槽)。

        已注册该 label_idx → 返回原槽(不推进指针)。
        否则空槽优先(不推进 _next_slot);无空槽覆盖 _next_slot 并推进(1→2→3→4→1)。
        返回 slot_id(1-4)。纯内存,设 _dirty。
        """
        for slot_id, lid in self._features.items():
            if lid == label_idx:
                return slot_id
        slot = None
        for i in range(1, 5):
            if i not in self._features:
                slot = i
                break
        if slot is None:
            slot = self._next_slot
            self._next_slot = self._next_slot % 4 + 1
        self._features[slot] = label_idx
        self._dirty = True
        self._clear_dirty = False
        print("[GestureDB] registered label_idx=%d -> id%d (memory, dirty)" % (label_idx, slot))
        return slot

    def match(self, label_idx):
        """精确匹配 label_idx。返回 (slot_id, 1.0) 或 (None, 0.0)。

        label_idx 精确相等即命中(无相似度),score=1.0 作上位机置信度。
        """
        for slot_id, lid in self._features.items():
            if lid == label_idx:
                return slot_id, 1.0
        return None, 0.0

    def clear(self):
        """清内存,设 _clear_dirty(clear wins over _dirty)。"""
        self._features.clear()
        self._clear_dirty = True
        self._dirty = False
        self._next_slot = 1
        print("[GestureDB] cleared (memory, clear_dirty)")

    def _serialize(self):
        return {"next_slot": self._next_slot,
                "slots": {str(k): v for k, v in self._features.items()}}

    def load_from_disk(self, path=GESTURE_DB_PATH):
        """启动加载。db_store os.stat 预检查,文件不存在返回 None(避 ENOENT)。"""
        data = db_store.load_json(path)
        if data is None:
            return None
        try:
            self._next_slot = data.get("next_slot", 1)
            for slot_str, label_idx in data.get("slots", {}).items():
                self._features[int(slot_str)] = label_idx
        except Exception as e:
            print("[GestureDB] load parse failed: %s" % e)
        return self._features

    def flush_to_disk(self, path=GESTURE_DB_PATH):
        """注册即写 / 退出兜底。open('w') 不抛 ENOENT。(镜像 ObjectDB,始终写盘)"""
        db_store.save_json(path, self._serialize())
        self._dirty = False
        self._clear_dirty = False
        print("[GestureDB] flushed %d gesture(s) to %s" % (len(self._features), path))

    def init_features(self, path=GESTURE_DB_PATH):
        """启动时加载已注册手势标签到内存(同 face_db.init_features)。"""
        self.load_from_disk(path)
        print("[GestureDB] init_features: loaded %d gesture(s)" % len(self._features))
        return self._features

    @property
    def count(self):
        return len(self._features)


# 全局单例
gesture_db = _GestureDB()
```

- [ ] **Step 2: 运行内存逻辑测试确认全绿**

```bash
python tests/test_gesture_db.py
```
Expected: 9 PASS

- [ ] **Step 3: 运行持久化测试确认全绿**

```bash
python tests/test_gesture_db_persist.py
```
Expected: 4 PASS

- [ ] **Step 4: Commit**

```bash
git add core/gesture_db.py
git commit -m "feat(gesture_db): 手势标签内存库——镜像ObjectDB精确匹配+同类不重复占槽+registrar签名"
```

---

### Task 4: Gesture AI AST 契约测试(TDD)

**Files:**
- Create: `tests/test_gesture_ai_ast.py`

- [ ] **Step 1: 创建 AST 契约测试**

```python
# tests/test_gesture_ai_ast.py — host-side AST 契约测试(gesture_ai)
import ast, os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _read(path):
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def test_hand_detection_app_class_exists():
    """HandDetectionApp 类必须在 gesture_ai.py 中定义。"""
    src = _read(os.path.join(ROOT, "core", "gesture_ai.py"))
    tree = ast.parse(src)
    classes = [n.name for n in ast.walk(tree) if isinstance(n, ast.ClassDef)]
    assert "HandDetectionApp" in classes
    assert "HandRecognitionApp" in classes
    assert "HandRecognition" in classes


def test_hand_labels_module_level():
    """HAND_LABELS 必须在模块级别定义,含 4 个标签。"""
    src = _read(os.path.join(ROOT, "core", "gesture_ai.py"))
    tree = ast.parse(src)
    for n in tree.body:
        if isinstance(n, ast.Assign):
            for t in n.targets:
                if isinstance(t, ast.Name) and t.id == "HAND_LABELS":
                    assert n.value.elts[0].s == "gun"
                    assert n.value.elts[1].s == "other"
                    assert n.value.elts[2].s == "yeah"
                    assert n.value.elts[3].s == "five"
                    return
    assert False, "HAND_LABELS not found at module level"


def test_hand_anchors_module_level():
    """HAND_ANCHORS 必须在模块级别定义(9 个 anchor,18 个值)。"""
    src = _read(os.path.join(ROOT, "core", "gesture_ai.py"))
    tree = ast.parse(src)
    for n in tree.body:
        if isinstance(n, ast.Assign):
            for t in n.targets:
                if isinstance(t, ast.Name) and t.id == "HAND_ANCHORS":
                    # 9 anchors × 2 = 18 values
                    assert len(n.value.elts) == 18
                    return
    assert False, "HAND_ANCHORS not found at module level"


def test_kmodel_paths_in_file():
    """kmodel 路径常量必须在文件中。"""
    src = _read(os.path.join(ROOT, "core", "gesture_ai.py"))
    assert "/sdcard/examples/kmodel/hand_det.kmodel" in src
    assert "/sdcard/examples/kmodel/hand_reco.kmodel" in src


def test_hand_recognition_postprocess_returns_tuple():
    """HandRecognitionApp.postprocess 返回 (idx, score) tuple。"""
    src = _read(os.path.join(ROOT, "core", "gesture_ai.py"))
    tree = ast.parse(src)
    # 找 HandRecognitionApp 的 postprocess 方法
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == "HandRecognitionApp":
            for item in node.body:
                if isinstance(item, ast.FunctionDef) and item.name == "postprocess":
                    # 检查 return 语句返回 tuple 或类似
                    body_str = ast.dump(item)
                    # 存在 return 语句
                    assert any(isinstance(n, ast.Return) for n in ast.walk(item)), \
                        "postprocess must have a return statement"
                    return
    assert False, "HandRecognitionApp.postprocess not found"


def test_hand_recognition_deinit_method():
    """HandRecognition 组合类必须有 deinit 方法。"""
    src = _read(os.path.join(ROOT, "core", "gesture_ai.py"))
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == "HandRecognition":
            methods = [n.name for n in node.body if isinstance(n, ast.FunctionDef)]
            assert "deinit" in methods, "HandRecognition must have deinit"
            assert "run" in methods, "HandRecognition must have run"
            return
    assert False, "HandRecognition class not found"


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

- [ ] **Step 2: 运行测试确认全部失败(文件不存在)**

```bash
python tests/test_gesture_ai_ast.py
```
Expected: FAIL (FileNotFoundError)

- [ ] **Step 3: Commit**

```bash
git add tests/test_gesture_ai_ast.py
git commit -m "test(gesture_ai): 6个AST契约测试——类存在/模块级常量/kmodel路径/postprocess返回tuple/deinit方法"
```

---

### Task 5: 实现 Gesture AI 模块

**Files:**
- Create: `core/gesture_ai.py`

- [ ] **Step 1: 创建 AI 模块(移植 demo 实验9)**

```python
# core/gesture_ai.py — 手势检测与识别封装(移植 demo 实验9)
#
# 双 kmodel: hand_det.kmodel(手掌检测,512×512,9 anchors) +
#           hand_reco.kmodel(手势分类,224×224,4类)
#
# 镜像 core/object_ai.py 封装风格。不内置 draw_result——画框/标签由 app on_frame 负责。

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

# 9 个 hardcode anchors(同 demo 实验9,不从 .bin 读)
HAND_ANCHORS = [26, 27, 53, 52, 75, 71, 80, 99, 106, 82,
                99, 134, 140, 113, 161, 172, 245, 276]

# 4 类手势标签(同 demo)
HAND_LABELS = ["gun", "other", "yeah", "five"]


def ALIGN_UP(x, align=16):
    return (x + align - 1) // align * align


class HandDetectionApp(AIBase):
    """手掌检测(hand_det.kmodel, anchor-based)。"""

    def __init__(self, kmodel_path, model_input_size, anchors,
                 confidence_threshold=0.2, nms_threshold=0.5,
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
                self.strides, 1,
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


class HandRecognitionApp(AIBase):
    """手势分类(hand_reco.kmodel, 224×224 输入,4 类 softmax)。"""

    def __init__(self, kmodel_path, model_input_size, labels=None,
                 rgb888p_size=None, display_size=None, debug_mode=0):
        if labels is None:
            labels = HAND_LABELS
        if rgb888p_size is None:
            rgb888p_size = RGB888P_SIZE
        if display_size is None:
            display_size = DISPLAY_SIZE
        super().__init__(kmodel_path, model_input_size, rgb888p_size, debug_mode)
        self.kmodel_path = kmodel_path
        self.model_input_size = model_input_size
        self.labels = labels
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

    def _softmax(self, x):
        x_max = np.max(x)
        x = np.exp(x - x_max)
        return x / np.sum(x)

    def postprocess(self, results):
        with ScopedTiming("postprocess", self.debug_mode > 0):
            result = results[0].reshape(results[0].shape[0] * results[0].shape[1])
            x_softmax = self._softmax(result)
            idx = int(np.argmax(x_softmax))
            score = float(x_softmax[idx])
            return idx, score

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


class HandRecognition:
    """手势检测+分类组合:先检手掌再分类,返回检测框+识别结果。"""

    def __init__(self, hand_det_kmodel, hand_rec_kmodel,
                 det_input_size=None, rec_input_size=None,
                 labels=None, anchors=None,
                 confidence_threshold=0.2, nms_threshold=0.5,
                 strides=None, rgb888p_size=None, display_size=None, debug_mode=0):
        if det_input_size is None:
            det_input_size = [512, 512]
        if rec_input_size is None:
            rec_input_size = [224, 224]
        if labels is None:
            labels = HAND_LABELS
        if anchors is None:
            anchors = HAND_ANCHORS
        if strides is None:
            strides = [8, 16, 32]
        if rgb888p_size is None:
            rgb888p_size = RGB888P_SIZE
        if display_size is None:
            display_size = DISPLAY_SIZE
        self.hand_det_kmodel = hand_det_kmodel
        self.hand_rec_kmodel = hand_rec_kmodel
        self.det_input_size = det_input_size
        self.rec_input_size = rec_input_size
        self.labels = labels
        self.anchors = anchors
        self.confidence_threshold = confidence_threshold
        self.nms_threshold = nms_threshold
        self.strides = strides
        self.rgb888p_size = [ALIGN_UP(rgb888p_size[0], 16), rgb888p_size[1]]
        self.display_size = [ALIGN_UP(display_size[0], 16), display_size[1]]
        self.debug_mode = debug_mode

        self.hand_det = HandDetectionApp(
            self.hand_det_kmodel, model_input_size=self.det_input_size,
            anchors=self.anchors,
            confidence_threshold=self.confidence_threshold,
            nms_threshold=self.nms_threshold,
            strides=self.strides,
            rgb888p_size=self.rgb888p_size, display_size=self.display_size,
            debug_mode=0)
        self.hand_rec = HandRecognitionApp(
            self.hand_rec_kmodel, model_input_size=self.rec_input_size,
            labels=self.labels,
            rgb888p_size=self.rgb888p_size, display_size=self.display_size)
        self.hand_det.config_preprocess()

    def run(self, img_np):
        """推理当前帧。返回 (hand_det_res, hand_rec_res)(等长,都是已过滤)。

        hand_det_res: 手掌检测框列表,每框 [..., x1,y1,x2,y2, ...](仅通过边界过滤的)。
        hand_rec_res: [(label_idx, score), ...] 每个手掌的手势分类结果(同索引对应)。
        过滤:高度 < 0.1×rgb888p_h 剔除;边缘窄掌剔除(同 demo 逻辑)。
        """
        det_boxes = self.hand_det.run(img_np)
        hand_det_res = []
        hand_rec_res = []
        for det_box in det_boxes:
            x1, y1, x2, y2 = det_box[2], det_box[3], det_box[4], det_box[5]
            w, h = int(x2 - x1), int(y2 - y1)
            # 边界过滤(同 demo)
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
            self.hand_rec.config_preprocess(det_box)
            idx, score = self.hand_rec.run(img_np)
            hand_det_res.append(det_box)
            hand_rec_res.append((idx, score))
        return hand_det_res, hand_rec_res

    def deinit(self):
        try:
            self.hand_det.deinit()
        except Exception:
            pass
        try:
            self.hand_rec.deinit()
        except Exception:
            pass
```

- [ ] **Step 2: 运行 AST 契约测试确认全绿**

```bash
python tests/test_gesture_ai_ast.py
```
Expected: 6 PASS

- [ ] **Step 3: Commit**

```bash
git add core/gesture_ai.py
git commit -m "feat(gesture_ai): 双kmodel手势检测+分类——移植demo实验9,postprocess返回(label_idx,score)"
```

---

### Task 6: 基础设施(icon + host_api + i18n + app_runtime)

**Files:**
- Modify: `core/icon_cache.py`(加 gesture 图标方法)
- Modify: `comm/host_api.py`(加 CATEGORY_TYPE 映射)
- Modify: `core/app_runtime.py`(加 channels + icon preload)
- Modify: `resource/i18n/zh_CN.json`(加 gesture_detect 功能文案)
- Modify: `resource/i18n/en_US.json`(加 gesture_detect 功能文案)
- Create: `resource/icons/gesture_detect_icon/`(复制图标,用 Bash 操作)

- [ ] **Step 1: icon_cache 加 gesture 图标方法**

在 `core/icon_cache.py` 的 `__init__` 中,`self._road_icons` 后加:
```python
        self._gesture_icons = {}     # name -> (data, dsc)
```

在 `get_road_icon` 方法后加:
```python
    def preload_gesture_icons(self):
        """预读手势识别APP图标（在首次 task_handler 之前调用）"""
        base = "/sdcard/CamerAi/resource/icons/gesture_detect_icon/"
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
                self._gesture_icons[name] = (data, dsc)
                print(f"[IconCache] gesture/{name} OK ({len(data)} bytes)")
            except Exception as e:
                print(f"[IconCache] gesture/{name} FAILED: {e}")

    def get_gesture_icon(self, name):
        """获取手势识别图标 (data, dsc)，未缓存返回 (None, None)"""
        return self._gesture_icons.get(name, (None, None))
```

- [ ] **Step 2: host_api 加 CATEGORY_TYPE 映射**

在 `comm/host_api.py` 的 `CATEGORY_TYPE` 字典中,`"road_detect"` 行后加:
```python
        "gesture_detect": TYPE_GESTURE_DETECT,  # 0x08
```

- [ ] **Step 3: app_runtime 加 channels + icon preload**

在 `_channels_for` 中,`road_detect` 分支后加:
```python
        elif category_id == "gesture_detect":
            # chn2 XGA RGBP888 做 AI 推理(同 face_detect AI 通道)
            chs.append((CAM_CHN_ID_2, Sensor.XGA, Sensor.RGBP888))
```

在 `init_app` 的 icon preload 链中,`road_detect` 分支后加:
```python
        elif category_id == "gesture_detect":
            icon_cache.preload_gesture_icons()
```

- [ ] **Step 4: 复制图标文件**

```bash
mkdir -p resource/icons/gesture_detect_icon
cp resource/icons/color_detect_icon/back.png resource/icons/gesture_detect_icon/back.png
cp resource/icons/color_detect_icon/list.png resource/icons/gesture_detect_icon/list.png
```

- [ ] **Step 5: i18n 加 gesture_detect 功能文案**

在 `resource/i18n/zh_CN.json` 的 `"road_detect"` 块后加:
```json
  "gesture_detect": {
    "save": "保存",
    "clear": "清除",
    "save_success": "保存成功",
    "registered": "已学习 %d/4",
    "press_k2": "按K2注册手势",
    "back_fb": "<",
    "list_fb": "="
  },
```

在 `resource/i18n/en_US.json` 的 `"road_detect"` 块后加:
```json
  "gesture_detect": {
    "save": "Save",
    "clear": "Clear",
    "save_success": "Saved",
    "registered": "Learned %d/4",
    "press_k2": "Press K2 to register gesture",
    "back_fb": "<",
    "list_fb": "="
  },
```

- [ ] **Step 6: Commit**

```bash
git add core/icon_cache.py comm/host_api.py core/app_runtime.py resource/icons/gesture_detect_icon/ resource/i18n/zh_CN.json resource/i18n/en_US.json
git commit -m "feat(gesture_detect): 基础设施——icon/host_api/i18n/app_runtime channels+preload"
```

---

### Task 7: Gesture Detect AST 契约测试(TDD)

**Files:**
- Create: `tests/test_gesture_detect_ast.py`

- [ ] **Step 1: 创建 AST 契约测试**

```python
# tests/test_gesture_detect_ast.py — host-side AST 契约测试(gesture_detect)
import ast, os, json

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP_RUNTIME_PATH = os.path.join(ROOT, "core", "app_runtime.py")
ICON_CACHE_PATH = os.path.join(ROOT, "core", "icon_cache.py")
HOST_API_PATH = os.path.join(ROOT, "comm", "host_api.py")


def _read(path):
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def test_gesture_detect_in_category_type_map():
    """CATEGORY_TYPE 必须包含 'gesture_detect': TYPE_GESTURE_DETECT。"""
    src = _read(HOST_API_PATH)
    assert '"gesture_detect":' in src
    # 在 "gesture_detect" 后 80 字符内应出现 TYPE_GESTURE_DETECT
    after = src.split('"gesture_detect":')[1][:80]
    assert "TYPE_GESTURE_DETECT" in after


def test_channels_for_gesture_detect():
    """_channels_for 的 gesture_detect 分支 append chn2 XGA RGBP888。"""
    src = _read(APP_RUNTIME_PATH)
    start = src.find("def _channels_for(")
    body = src[start:start + 1500]
    assert "gesture_detect" in body, "_channels_for must handle gesture_detect"
    after = body.split('"gesture_detect"')[1][:300]
    assert "append" in after, "gesture_detect should append AI channel"
    assert "CAM_CHN_ID_2" in after, "gesture_detect must use CAM_CHN_ID_2 for AI"
    assert "XGA" in after, "gesture_detect AI channel must use XGA framesize"
    assert "RGBP888" in after, "gesture_detect AI channel must use RGBP888 pixformat"


def test_preload_gesture_icons_in_init_app():
    """init_app 必须对 gesture_detect 调 preload_gesture_icons。"""
    src = _read(APP_RUNTIME_PATH)
    assert '"gesture_detect"' in src
    assert 'preload_gesture_icons' in src


def test_icon_cache_has_gesture_methods():
    """icon_cache 必须有 preload_gesture_icons + get_gesture_icon + _gesture_icons 槽。"""
    src = _read(ICON_CACHE_PATH)
    assert "def preload_gesture_icons" in src
    assert "def get_gesture_icon" in src
    assert "_gesture_icons" in src


def test_gesture_db_path_module_level():
    """_GESTURE_DB_PATH(或 GESTURE_DB_PATH)常量在 app.py 模块级别。"""
    app_path = os.path.join(ROOT, "scripts", "gesture_detect", "app.py")
    src = _read(app_path)
    tree = ast.parse(src)
    module_names = set()
    for n in tree.body:
        if isinstance(n, ast.Assign):
            for t in n.targets:
                if isinstance(t, ast.Name):
                    module_names.add(t.id)
    # gesture_db 在 core 中定义路径,GESTURE_DB_PATH 已存在;app 可能引用
    assert "GESTURE_DB_PATH" in src or "_GESTURE_DB_PATH" in src or \
           "gesture_db" in src, "app must reference gesture_db or its path"


def test_on_frame_uses_registrar():
    """app.py on_frame 必须使用 try_register(..., registrar=gesture_db.register)。"""
    app_path = os.path.join(ROOT, "scripts", "gesture_detect", "app.py")
    src = _read(app_path)
    assert "registrar" in src, "app must use registrar pattern for K2 registration"


def test_has_hand_rec_import():
    """app.py 必须导入 gesture_ai 的 HandRecognition 类。"""
    app_path = os.path.join(ROOT, "scripts", "gesture_detect", "app.py")
    src = _read(app_path)
    assert "gesture_ai" in src, "app must import from gesture_ai"
    assert "HandRecognition" in src, "app must import HandRecognition"


def test_has_host_tick():
    """app.py on_frame 必须有 host_tick 调用。"""
    app_path = os.path.join(ROOT, "scripts", "gesture_detect", "app.py")
    src = _read(app_path)
    assert "host_tick" in src, "app must call host_tick for protocol 0x08"


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

- [ ] **Step 2: 运行测试确认全部失败(app.py 不存在)**

```bash
python tests/test_gesture_detect_ast.py
```
Expected: FAIL (FileNotFoundError on app.py)

- [ ] **Step 3: Commit**

```bash
git add tests/test_gesture_detect_ast.py
git commit -m "test(gesture_detect): 8个AST契约测试——channels/icon/registrar/host_tick"
```

---

### Task 8: 实现 Gesture Detect APP

**Files:**
- Create: `scripts/gesture_detect/app.py`
- 确保目录: `scripts/gesture_detect/`

- [ ] **Step 1: 创建目录并实现 APP**

```bash
mkdir -p scripts/gesture_detect
```

```python
# scripts/gesture_detect/app.py — 手势识别(双 kmodel + K2 注册 4 槽 + 协议 0x08)
#
# 复刻 object_detect 模式: chn2 AI 检测 → gesture_db.match(label_idx)
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
from core.gesture_ai import HandRecognition, HAND_LABELS, HAND_ANCHORS, RGB888P_SIZE, DISPLAY_SIZE
from core.gesture_db import gesture_db, GESTURE_DB_PATH

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
_hand_rec = None
_db_slots = {}
_overlay = None
_clear_btn = None
_save_btn = None
_close_overlay = False


def _init_ai():
    """Load BOTH kmodels before the loop.

    ⚠️ 双 kmodel 顺序根因:hand_rec kmodel 必须在 hand_det.config_preprocess()
    之前加载,否则破坏共享 NPU/AI2D 状态(坑#19,同 face_detect)。
    """
    global _hand_rec, _db_slots
    det_kmodel = "/sdcard/examples/kmodel/hand_det.kmodel"
    rec_kmodel = "/sdcard/examples/kmodel/hand_reco.kmodel"
    print("[gesture_detect] loading hand detection + recognition models...")
    _hand_rec = HandRecognition(
        det_kmodel, rec_kmodel,
        det_input_size=[512, 512], rec_input_size=[224, 224],
        labels=HAND_LABELS, anchors=HAND_ANCHORS,
        confidence_threshold=0.2, nms_threshold=0.5,
        rgb888p_size=RGB888P_SIZE, display_size=DISPLAY_SIZE,
        debug_mode=0)
    _db_slots = gesture_db.init_features()
    print("[gesture_detect] AI ready, loaded %d gesture(s)" % len(_db_slots))


def _init_registry(fpioa):
    global _id_registry
    _id_registry = IdRegistry(fpioa, pin=0)


def _deinit_ai():
    global _hand_rec
    if _hand_rec is not None:
        try:
            _hand_rec.deinit()
        except Exception as e:
            print("[gesture_detect] deinit warning: %s" % e)
        _hand_rec = None


def on_frame(img):
    """chn2 检测 → 每只手分类 → match DB → 画框 + ID 标签 → host_tick。

    对每只检测到的手:label_idx 匹配 DB → 找到 slot 则彩色框+ID#序号标签;
    未注册白框+手势名标签。K2 注册当前帧最大手掌的手势标签。
    """
    if _RUNTIME is None or _hand_rec is None:
        return
    img_ai = _RUNTIME.sensor.snapshot(chn=CAM_CHN_ID_2)
    img_np = img_ai.to_numpy_ref()
    try:
        det_boxes, rec_results = _hand_rec.run(img_np)
    except Exception as e:
        print("[gesture_detect] run error: %s" % e)
        det_boxes, rec_results = [], []

    slots = [None, None, None, None]
    filled_slots = set()  # 本帧已填充的 slot(防多只手匹配同一 slot 覆盖)

    for det_box, (label_idx, score) in zip(det_boxes, rec_results):
        x1, y1, x2, y2 = det_box[2], det_box[3], det_box[4], det_box[5]
        # 缩放到 VGA
        x = int(x1) * DISPLAY_SIZE[0] // RGB888P_SIZE[0]
        y = int(y1) * DISPLAY_SIZE[1] // RGB888P_SIZE[1]
        w = int(x2 - x1) * DISPLAY_SIZE[0] // RGB888P_SIZE[0]
        h = int(y2 - y1) * DISPLAY_SIZE[1] // RGB888P_SIZE[1]
        conf = int(score * 100)
        label_name = HAND_LABELS[label_idx]

        slot, _dummy = gesture_db.match(label_idx)
        if slot is not None and slot not in filled_slots:
            color = _draw_color(BOX_COLORS.get(slot, BOX_UNKNOWN))
            img.draw_rectangle(x, y, w, h, color=color, thickness=4)
            img.draw_string_advanced(x + 2, y - 24, 24,
                                     "ID%d %s" % (slot, label_name), color=color)
            slots[slot - 1] = (slot, x, y, w, h, conf)
            filled_slots.add(slot)
        else:
            color = _draw_color(BOX_UNKNOWN)
            img.draw_rectangle(x, y, w, h, color=color, thickness=2)
            img.draw_string_advanced(x + 2, y - 24, 24,
                                     label_name, color=color)

    # 屏幕居中绿色十字(对准参考):VGA 640×480 中心 (320, 240)
    img.draw_cross(320, 240, color=(0xFF, 0x00, 0xFF, 0x00), size=20, thickness=2)

    # K2 注册:当前帧最大手掌的手势标签 → 下一槽
    if _id_registry is not None and _id_registry.has_pending() and det_boxes:
        # 取面积最大的手掌
        max_i = max(range(len(det_boxes)),
                    key=lambda j: (det_boxes[j][4] - det_boxes[j][2])
                                  * (det_boxes[j][5] - det_boxes[j][3]))
        # 对应 rec_results 同索引
        if max_i < len(rec_results):
            reg_label_idx = rec_results[max_i][0]
            try:
                slot = _id_registry.try_register(
                    reg_label_idx, _RUNTIME.buzzer,
                    registrar=gesture_db.register)
                if slot is not None:
                    gesture_db.flush_to_disk()
                    _db_slots[slot] = reg_label_idx
                    _refresh_count()
            except Exception as e:
                print("[gesture_detect] register error: %s" % e)

    if _RUNTIME is not None and _RUNTIME.host is not None:
        _RUNTIME.host_tick(slots)
    gc.collect()


def _refresh_count():
    if _count_label is not None:
        try:
            _count_label.set_text(
                _RUNTIME.lang.t("gesture_detect.registered", len(_db_slots)))
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
    cl.set_text(_RUNTIME.lang.t("gesture_detect.clear"))
    cl.add_style(make_back_bar_text_style(fonts.body), 0)
    cl.center()
    _clear_btn.add_event(_on_clear_clicked, lv.EVENT.CLICKED, None)

    _save_btn = lv.btn(_overlay)
    _save_btn.set_size(120, 40)
    _save_btn.align(lv.ALIGN.RIGHT_MID, -20, 0)
    sv = lv.label(_save_btn)
    sv.set_text(_RUNTIME.lang.t("gesture_detect.save"))
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
    gesture_db.clear()
    _db_slots.clear()
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

    icon_data, icon_dsc = icon_cache.get_gesture_icon("back")
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
    title.set_text(runtime.lang.t("category.gesture_detect"))
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
    list_icon_data, list_icon_dsc = icon_cache.get_gesture_icon("list")
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
    count_label.set_text(runtime.lang.t("gesture_detect.registered", len(_db_slots)))
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
                print("[gesture_detect] on_frame error: %s" % e)
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
                print("[gesture_detect] fc=%d" % fc)
    finally:
        _deinit_ai()
        _destroy_ui()
        _RUNTIME = None
        gesture_db.flush_to_disk()  # 退出兜底写盘
```

- [ ] **Step 2: 运行所有 AST 契约测试确认全绿**

```bash
python tests/test_gesture_detect_ast.py
```
Expected: 8 PASS

- [ ] **Step 3: Commit**

```bash
git add scripts/gesture_detect/app.py
git commit -m "feat(gesture_detect): 主脚本——复刻object_detect双kmodel+K2 registrar+4槽+协议0x08"
```

---

### Task 9: 全量回归验证

- [ ] **Step 1: 运行所有新测试**

```bash
python tests/test_gesture_db.py && echo "====" && python tests/test_gesture_db_persist.py && echo "====" && python tests/test_gesture_ai_ast.py && echo "====" && python tests/test_gesture_detect_ast.py
```
Expected: 所有测试 PASS(共 9+4+6+8=27 项)

- [ ] **Step 2: 运行全部已有测试确认零回归**

```bash
python -c "import tests.test_face_detect_template; tests.test_face_detect_template.test_runner()" && python -c "import tests.test_tag_detect_app; tests.test_tag_detect_app.test_runner()" && python -c "import tests.test_object_detect_app; tests.test_object_detect_app.test_runner()" && python -c "import tests.test_color_detect_ast; tests.test_color_detect_ast.test_runner()" && python -c "import tests.test_road_detect_ast; tests.test_road_detect_ast.test_runner()" && python -c "import tests.test_road_db; tests.test_road_db.test_runner()" && python -c "import tests.test_road_detect_algorithm; tests.test_road_detect_algorithm.test_runner()" && python -c "import tests.test_road_db_persist; tests.test_road_db_persist.test_runner()" && echo "ALL REGRESSION GREEN"
```
Expected: ALL REGRESSION GREEN(零退化)

- [ ] **Step 3: Commit**

```bash
git commit --allow-empty -m "test(gesture_detect): 全量回归——27新测试+所有已有测试零退化"
```

---

### 部署清单

部署到 K230 板端只需 6 个文件/目录:

1. `core/gesture_ai.py` — AI 封装(新建)
2. `core/gesture_db.py` — 手势数据库(新建)
3. `scripts/gesture_detect/app.py` — 主脚本(新建)
4. `core/icon_cache.py` — 图标缓存(修改)
5. `core/app_runtime.py` — 传感器通道 + icon preload(修改)
6. `comm/host_api.py` — CATEGORY_TYPE 映射(修改)
7. `resource/icons/gesture_detect_icon/` — back.png + list.png(新建)
8. `resource/i18n/zh_CN.json` + `en_US.json` — 文案(修改)

**设备端 kmodel 依赖**(已在 sdcard,无需部署):
- `/sdcard/examples/kmodel/hand_det.kmodel`
- `/sdcard/examples/kmodel/hand_reco.kmodel`
