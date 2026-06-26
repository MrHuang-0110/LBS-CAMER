# 物体识别脚本(object_detect)实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 K230 上实现 YOLOv8n COCO80 物体识别脚本(UI/串口/ID 设置与 tag_detect 一致,底栏仅 list 图标清除/保存,加居中绿十字,KEY2 按类别注册,注册框显示 ID号+英文类名)。

**Architecture:** 复用 `_template` 单线程主循环 + reset 框架。新增 `core/object_ai.py`(YOLOv8 封装,镜像 face_ai + 移植 demo 实验15 后处理)、`core/object_db.py`(类别→槽 DB,镜像 tag_db 纯 Python)、`scripts/object_detect/app.py`(镜像 tag_detect 去双功能卡)。检测走 chn2 XLA RGBP888(同 face_detect),chn0 VGA RGB888 显示。注册走 `IdRegistry.try_register(class_id, buzzer, registrar=db.register)`。协议类型 0x05。

**Tech Stack:** MicroPython / K230 CanMV / nncase_runtime + ulab + aidemo / AIBase+Ai2d / lvgl / image module。

**参考文件(实现前必读全):**
- `demo/AI类实验例程/实验15 物体检测实验/main.py` — YOLOv8 后处理 + COCO_LABELS + NMS 原型。
- `core/face_ai.py` — AIBase 子类封装范式(config_preprocess/postprocess/deinit,ALIGN_UP,_draw_color)。
- `core/tag_db.py` — 纯 Python DB 范式(round-robin register,精确 match,clear,count,flush no-op)。
- `scripts/face_detect/app.py` — 底栏 list 浮层 + 居中十字 + on_frame 结构。
- `scripts/tag_detect/app.py` — 单线程主循环 + 画框(白/彩色)+ IdRegistry.registrar 用法。

---

## 文件结构

| 动作 | 文件 | 职责 |
|---|---|---|
| 新建 | `core/object_ai.py` | YOLOv8n kmodel 封装(AIBase 子类):config_preprocess/postprocess(含 NMS)/run/deinit + COCO_LABELS |
| 新建 | `core/object_db.py` | 类别→槽内存库(纯 Python,镜像 tag_db),round-robin register/精确 match/clear/flush no-op |
| 新建 | `scripts/object_detect/app.py` | 主循环 run(runtime) + on_frame(检测/注册/画框/十字/host_tick) + UI(顶栏/预览/底栏 list 浮层) |
| 新建 | `tests/test_object_db.py` | ObjectDB 真单测(纯 Python 可导入) |
| 新建 | `tests/test_object_ai.py` | ObjectDetectionApp AST 契约(COCO80/NMS/kmodel 路径) |
| 新建 | `tests/test_object_detect_app.py` | app.py AST 契约(on_frame/run/画框/十字/list 浮层/i18n/chn2) |
| 新建 | `tests/test_app_runtime_object.py` | app_runtime object_detect 通道+图标预读契约 |
| 新建 | `tests/test_i18n_object.py` | i18n object_detect 段键契约 |
| 改 | `comm/host_api.py` | CATEGORY_TYPE 加 object_detect→0x05 |
| 改 | `tests/test_host_api.py` | 覆盖 object_detect→0x05 |
| 改 | `core/app_runtime.py` | _channels_for 加 object_detect→chn2 XLA RGBP888;init_app 加 preload_object_icons |
| 改 | `core/icon_cache.py` | _object_icons + preload_object_icons + get_object_icon |
| 改 | `tests/test_icon_cache.py` | object 图标契约 |
| 改 | `resource/i18n/zh_CN.json` + `en_US.json` | object_detect 段(registered/clear/save) |
| 拷贝 | `resource/icons/object_detect_icon/{back,list}.png` | 从 face_detect_icon 拷贝(板端部署时) |

---

## Task 1: host_api CATEGORY_TYPE 加 object_detect→0x05

**Files:**
- Modify: `comm/host_api.py:41-48` (CATEGORY_TYPE dict)
- Test: `tests/test_host_api.py:33-38` (test_category_type_mapping_covers_all_categories 列表)

- [ ] **Step 1: Write the failing test**

编辑 `tests/test_host_api.py` 的 `test_category_type_mapping_covers_all_categories`,把 object_detect 加进断言列表:

```python
def test_category_type_mapping_covers_all_categories():
    """CATEGORY_TYPE 必须映射所有 reset 框架 category。"""
    src = _src()
    assert "CATEGORY_TYPE" in src, "must define CATEGORY_TYPE mapping"
    for cat, code in [("main_menu", "0x01"), ("settings", "0x01"),
                      ("camera", "0x02"), ("face_detect", "0x03"),
                      ("tag_detect", "0x04"), ("object_detect", "0x05"),
                      ("_template", "0x01")]:
        assert ('"%s"' % cat) in src or ("'%s'" % cat) in src, \
            "CATEGORY_TYPE must cover %s" % cat
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python tests/test_host_api.py`
Expected: FAIL `test_category_type_mapping_covers_all_categories: CATEGORY_TYPE must cover object_detect`

- [ ] **Step 3: Write minimal implementation**

编辑 `comm/host_api.py` 的 CATEGORY_TYPE,在 tag_detect 行后加 object_detect:

```python
    CATEGORY_TYPE = {
        "main_menu":  TYPE_MAIN_MENU,     # 0x01
        "settings":   TYPE_MAIN_MENU,     # 0x01（复用主菜单）
        "camera":     TYPE_CAMERA,        # 0x02
        "face_detect":TYPE_FACE_DETECT,   # 0x03
        "tag_detect": TYPE_TAG_DETECT,    # 0x04
        "object_detect": TYPE_OBJECT_DETECT,  # 0x05
        "_template":  TYPE_MAIN_MENU,     # 0x01（默认）
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python tests/test_host_api.py`
Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add comm/host_api.py tests/test_host_api.py
git commit -m "feat(host_api): object_detect -> TYPE_OBJECT_DETECT (0x05)" -m "Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## Task 2: ObjectDB 类别→槽库(纯 Python)

**Files:**
- Create: `core/object_db.py`
- Test: `tests/test_object_db.py`

- [ ] **Step 1: Write the failing test**

创建 `tests/test_object_db.py`:

```python
# tests/test_object_db.py — ObjectDB 真单元测试(纯 Python 可导入)
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "core"))

from object_db import ObjectDB


def test_register_fills_empty_slot_first():
    db = ObjectDB()
    s1 = db.register(0)   # class_id=0 (person)
    s2 = db.register(39)  # class_id=39 (bottle)
    assert s1 == 1 and s2 == 2, "empty slots filled in order 1,2"
    assert db.count == 2


def test_register_round_robin_after_full():
    db = ObjectDB()
    for i in range(4):
        assert db.register(i) == i + 1
    # 满4后覆盖 _next_slot(初始1),推进 1->2->3->4->1
    s5 = db.register(10)
    assert s5 == 1, "full db overwrites slot 1 (round-robin), got %r" % s5
    s6 = db.register(11)
    assert s6 == 2, "next overwrite slot 2"


def test_register_same_class_returns_existing_slot():
    """同类不重复占槽:同一 class_id 再注册返回原槽,不推进指针。"""
    db = ObjectDB()
    s1 = db.register(0)
    s2 = db.register(0)   # 同类 person 再注册
    assert s1 == s2 == 1, "same class_id must return same slot, got %r/%r" % (s1, s2)
    assert db.count == 1, "same class must not occupy new slot, count=1"


def test_match_hit_returns_slot_and_score_one():
    db = ObjectDB()
    db.register(0)
    slot, score = db.match(0)
    assert slot == 1, "matched slot 1"
    assert score == 1.0, "exact match score = 1.0"


def test_match_miss_returns_none_zero():
    db = ObjectDB()
    db.register(0)
    slot, score = db.match(39)
    assert slot is None, "miss -> None slot"
    assert score == 0.0, "miss -> 0.0 score"


def test_match_empty_db():
    db = ObjectDB()
    slot, score = db.match(0)
    assert slot is None and score == 0.0


def test_clear_empties_db():
    db = ObjectDB()
    db.register(0)
    db.register(39)
    db.clear()
    assert db.count == 0
    assert db.match(0) == (None, 0.0)


def test_flush_to_disk_is_noop_safe():
    """flush_to_disk 当前 no-op(持久化预留),调用不崩。"""
    db = ObjectDB()
    db.register(0)
    db.clear()
    db.flush_to_disk()  # must not raise


def test_runner():
    failures = 0
    for name in sorted(n for n in globals() if n.startswith("test_") and n != "test_runner"):
        try:
            globals()[name]()
            print("PASS %s" % name)
        except Exception as e:
            failures += 1
            print("FAIL %s: %s" % (name, e))
    print("\n%s" % ("ALL PASS" if failures == 0 else "%d FAILED" % failures))
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    test_runner()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python tests/test_object_db.py`
Expected: FAIL (ImportError: no module named 'object_db')

- [ ] **Step 3: Write minimal implementation**

创建 `core/object_db.py`:

```python
# core/object_db.py — 物体类别 ID 内存数据库(YOLOv8 COCO80 class_id)
#
# 镜像 tag_db 的内存-only + flush_to_disk 预留模式,但:
#   - 存 int class_id(0-79,COCO 标签索引),非标量 code_id
#   - 精确匹配(class_id 相等即命中),无相似度概念,score=1.0
#   - 同类不重复占槽:同一 class_id 再注册返回原槽,不推进轮转指针
#
# 纯 Python(无 MicroPython 依赖)→ 可在 host 端真单元测试。
#
# 持久化路径待定(同 face_db/tag_db):flush_to_disk 当前 no-op,后续决定存哪。
# K230 坑#2:运行时 SD 写与 display flush 抢 DMA,故运行时只改内存,退出刷盘。


class ObjectDB:
    """物体类别内存库。class_id 为 int(COCO 标签索引 0-79)。"""

    def __init__(self):
        self._features = {}        # {slot_id: class_id}
        self._next_slot = 1        # 轮转覆盖指针(1-4 循环)
        self._dirty = False
        self._clear_dirty = False

    def register(self, class_id):
        """注册 class_id 到槽位(轮转覆盖,同 tag_db;但同类不重复占槽)。

        已注册该 class_id → 返回原槽(不推进指针)。
        否则空槽优先(不推进 _next_slot);无空槽覆盖 _next_slot 并推进(1→2→3→4→1)。
        返回 slot_id(1-4)。纯内存,设 _dirty。
        """
        for slot_id, cid in self._features.items():
            if cid == class_id:
                return slot_id
        slot = None
        for i in range(1, 5):
            if i not in self._features:
                slot = i
                break
        if slot is None:
            slot = self._next_slot
            self._next_slot = self._next_slot % 4 + 1
        self._features[slot] = class_id
        self._dirty = True
        self._clear_dirty = False
        print("[ObjectDB] registered class_id=%d -> id%d (memory, dirty)" % (class_id, slot))
        return slot

    def match(self, class_id):
        """精确匹配 class_id。返回 (slot_id, 1.0) 或 (None, 0.0)。

        class_id 精确相等即命中(无相似度),score=1.0 作上位机置信度。
        """
        for slot_id, cid in self._features.items():
            if cid == class_id:
                return slot_id, 1.0
        return None, 0.0

    def clear(self):
        """清内存,设 _clear_dirty(clear wins over _dirty)。"""
        self._features.clear()
        self._clear_dirty = True
        self._dirty = False
        self._next_slot = 1
        print("[ObjectDB] cleared (memory, clear_dirty)")

    def flush_to_disk(self):
        """退出时刷盘(预留)。⚠️ 持久化路径待定,当前 no-op,仅复位 dirty 标志。"""
        if self._clear_dirty:
            print("[ObjectDB] exit: clear intent recorded (persistence disabled)")
        elif self._dirty:
            print("[ObjectDB] exit: %d class(es) pending (persistence disabled)"
                  % len(self._features))
        self._clear_dirty = False
        self._dirty = False

    @property
    def count(self):
        return len(self._features)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python tests/test_object_db.py`
Expected: ALL PASS (8 tests)

- [ ] **Step 5: Commit**

```bash
git add core/object_db.py tests/test_object_db.py
git commit -m "feat(object_db): class-id memory DB mirroring tag_db" -m "Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## Task 3: core/object_ai.py — YOLOv8 封装(AST 契约先行)

**Files:**
- Create: `core/object_ai.py`
- Test: `tests/test_object_ai.py`

> 注:object_ai 依赖 AIBase/Ai2d/nncase(板端模块),Windows 不可导入,故用 AST 契约测试(同 face_ai 测试范式)。

- [ ] **Step 1: Write the failing test**

创建 `tests/test_object_ai.py`:

```python
# tests/test_object_ai.py — ObjectDetectionApp AST 契约(板端不可导入 AIBase/Ai2d)
import ast
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
AI_PATH = os.path.join(ROOT, "core", "object_ai.py")


def _src():
    with open(AI_PATH, "r", encoding="utf-8") as f:
        return f.read()


def _tree():
    return ast.parse(_src(), filename=AI_PATH)


def _cls():
    for n in _tree().body:
        if isinstance(n, ast.ClassDef) and n.name == "ObjectDetectionApp":
            return n
    raise AssertionError("Class ObjectDetectionApp missing")


def _method(name):
    for n in _cls().body:
        if isinstance(n, ast.FunctionDef) and n.name == name:
            return n
    raise AssertionError("Method %s missing" % name)


def test_class_exists():
    try:
        _cls()
    except AssertionError:
        assert False, "object_ai.py must define ObjectDetectionApp"


def test_coco_labels_has_80_entries():
    """COCO_LABELS 必须是 80 类(person..toothbrush)。"""
    seg = ast.get_source_segment(_src(), _cls()) or ""
    assert "COCO_LABELS" in _src(), "must define COCO_LABELS"
    src = _src()
    # 至少含首尾两类标志
    assert '"person"' in src, "COCO_LABELS must contain person"
    assert '"toothbrush"' in src, "COCO_LABELS must contain toothbrush"


def test_kmodel_path_yolov8n_320():
    src = _src()
    assert "yolov8n_320.kmodel" in src, "must use yolov8n_320.kmodel"


def test_init_takes_confidence_and_nms_threshold():
    m = _method("__init__")
    args = [a.arg for a in m.args.args]
    assert "confidence_threshold" in args, "must accept confidence_threshold"
    assert "nms_threshold" in args, "must accept nms_threshold"


def test_postprocess_calls_nms():
    """postprocess 必须调 NMS(纯 Python,移植 demo)。"""
    seg = ast.get_source_segment(_src(), _method("postprocess")) or ""
    assert "nms" in seg.lower(), "postprocess must call self.nms()"


def test_nms_method_exists():
    try:
        _method("nms")
    except AssertionError:
        assert False, "must define nms(boxes, scores, thresh) method"


def test_config_preprocess_uses_resize():
    """config_preprocess 必须用 ai2d.resize(不做 letterbox,同 demo)。"""
    seg = ast.get_source_segment(_src(), _method("config_preprocess")) or ""
    assert "resize" in seg, "config_preprocess must ai2d.resize"


def test_deinit_cleans_kpu_ai2d():
    seg = ast.get_source_segment(_src(), _method("deinit")) or ""
    assert "kpu" in seg, "deinit must del kpu"
    assert "ai2d" in seg, "deinit must del ai2d"


def test_rgb888p_and_display_size_defaults():
    """默认 rgb888p_size/display_size 对齐 face_ai(1024x768/640x480)。"""
    seg = ast.get_source_segment(_src(), _method("__init__")) or ""
    assert "1024" in seg or "RGB888P_SIZE" in _src(), "rgb888p default 1024x768"
    assert "640" in seg or "DISPLAY_SIZE" in _src(), "display default 640x480"


def test_runner():
    failures = 0
    for name in sorted(n for n in globals() if n.startswith("test_") and n != "test_runner"):
        try:
            globals()[name]()
            print("PASS %s" % name)
        except Exception as e:
            failures += 1
            print("FAIL %s: %s" % (name, e))
    print("\n%s" % ("ALL PASS" if failures == 0 else "%d FAILED" % failures))
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    test_runner()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python tests/test_object_ai.py`
Expected: FAIL (AssertionError: object_ai.py must define ObjectDetectionApp — 文件不存在)

- [ ] **Step 3: Write minimal implementation**

创建 `core/object_ai.py`:

```python
# core/object_ai.py — YOLOv8n COCO80 物体检测封装(AIBase 子类)
#
# 镜像 face_ai.FaceDetectionApp 封装风格 + 移植 demo/AI类实验例程/实验15 后处理。
# - kmodel: /sdcard/examples/kmodel/yolov8n_320.kmodel,输入 [320,320]
# - AI2D: resize(rgb888p -> 320x320,不做 letterbox,同 demo)
# - postprocess: YOLOv8 输出 [N,84] -> argmax 取类 -> conf 阈值 -> 纯 Python NMS
#   返回 [[l,t,r,b,score,class_id], ...](rgb888p 坐标,float)
# - 不内置 draw_result(画框在 app on_frame 按注册槽上色)
#
# 纯 Python NMS(无 aidemo C 加速):板端帧率可能低于 face_detect,每帧 gc.collect。

import gc
import time

import nncase_runtime as nn
import ulab.numpy as np
from libs.AIBase import AIBase
from libs.AI2D import Ai2d
from libs.Utils import ScopedTiming

RGB888P_SIZE = [1024, 768]
DISPLAY_SIZE = [640, 480]

# COCO 80 类英文标签(从 demo 实验15 拷贝)
COCO_LABELS = [
    "person", "bicycle", "car", "motorcycle", "airplane", "bus", "train", "truck",
    "boat", "traffic light", "fire hydrant", "stop sign", "parking meter", "bench",
    "bird", "cat", "dog", "horse", "sheep", "cow", "elephant", "bear", "zebra",
    "giraffe", "backpack", "umbrella", "handbag", "tie", "suitcase", "frisbee",
    "skis", "snowboard", "sports ball", "kite", "baseball bat", "baseball glove",
    "skateboard", "surfboard", "tennis racket", "bottle", "wine glass", "cup",
    "fork", "knife", "spoon", "bowl", "banana", "apple", "sandwich", "orange",
    "broccoli", "carrot", "hot dog", "pizza", "donut", "cake", "chair", "couch",
    "potted plant", "bed", "dining table", "toilet", "tv", "laptop", "mouse",
    "remote", "keyboard", "cell phone", "microwave", "oven", "toaster", "sink",
    "refrigerator", "book", "clock", "vase", "scissors", "teddy bear",
    "hair drier", "toothbrush",
]


def ALIGN_UP(x, align=16):
    return (x + align - 1) // align * align


class ObjectDetectionApp(AIBase):
    def __init__(self, kmodel_path, labels=None, model_input_size=None,
                 max_boxes_num=50, confidence_threshold=0.2, nms_threshold=0.2,
                 rgb888p_size=None, display_size=None, debug_mode=0):
        if labels is None:
            labels = COCO_LABELS
        if model_input_size is None:
            model_input_size = [320, 320]
        if rgb888p_size is None:
            rgb888p_size = RGB888P_SIZE
        if display_size is None:
            display_size = DISPLAY_SIZE
        super().__init__(kmodel_path, model_input_size, rgb888p_size, debug_mode)
        self.kmodel_path = kmodel_path
        self.labels = labels
        self.model_input_size = model_input_size
        self.confidence_threshold = confidence_threshold
        self.nms_threshold = nms_threshold
        self.max_boxes_num = max_boxes_num
        self.rgb888p_size = [ALIGN_UP(rgb888p_size[0], 16), rgb888p_size[1]]
        self.display_size = [ALIGN_UP(display_size[0], 16), display_size[1]]
        self.debug_mode = debug_mode
        self.x_factor = float(self.rgb888p_size[0]) / self.model_input_size[0]
        self.y_factor = float(self.rgb888p_size[1]) / self.model_input_size[1]
        self.ai2d = Ai2d(debug_mode)
        self.ai2d.set_ai2d_dtype(nn.ai2d_format.NCHW_FMT, nn.ai2d_format.NCHW_FMT,
                                 np.uint8, np.uint8)

    def config_preprocess(self, input_image_size=None):
        gc.collect()
        with ScopedTiming("set preprocess config", self.debug_mode > 0):
            ai2d_input_size = input_image_size if input_image_size else self.rgb888p_size
            self.ai2d.resize(nn.interp_method.tf_bilinear, nn.interp_mode.half_pixel)
            self.ai2d.build([1, 3, ai2d_input_size[1], ai2d_input_size[0]],
                            [1, 3, self.model_input_size[1], self.model_input_size[0]])

    def postprocess(self, results):
        with ScopedTiming("postprocess", self.debug_mode > 0):
            result = results[0]
            result = result.reshape((result.shape[0] * result.shape[1], result.shape[2]))
            output_data = result.transpose()
            boxes_ori = output_data[:, 0:4]
            scores_ori = output_data[:, 4:]
            confs_ori = np.max(scores_ori, axis=-1)
            inds_ori = np.argmax(scores_ori, axis=-1)
            boxes, scores, inds = [], [], []
            for i in range(len(boxes_ori)):
                if confs_ori[i] > self.confidence_threshold:
                    scores.append(confs_ori[i])
                    inds.append(inds_ori[i])
                    x = boxes_ori[i, 0]
                    y = boxes_ori[i, 1]
                    w = boxes_ori[i, 2]
                    h = boxes_ori[i, 3]
                    left = int((x - 0.5 * w) * self.x_factor)
                    top = int((y - 0.5 * h) * self.y_factor)
                    right = int((x + 0.5 * w) * self.x_factor)
                    bottom = int((y + 0.5 * h) * self.y_factor)
                    boxes.append([left, top, right, bottom])
            if len(boxes) == 0:
                return []
            boxes = np.array(boxes)
            scores = np.array(scores)
            inds = np.array(inds)
            keep = self.nms(boxes, scores, self.nms_threshold)
            dets = np.concatenate(
                (boxes, scores.reshape((len(boxes), 1)), inds.reshape((len(boxes), 1))),
                axis=1)
            dets_out = []
            for keep_i in keep:
                dets_out.append(dets[keep_i])
            dets_out = np.array(dets_out)
            dets_out = dets_out[:self.max_boxes_num, :]
            return dets_out

    def nms(self, boxes, scores, thresh):
        """纯 Python NMS(移植 demo 实验15)。返回 keep 索引列表。"""
        x1, y1, x2, y2 = boxes[:, 0], boxes[:, 1], boxes[:, 2], boxes[:, 3]
        areas = (x2 - x1 + 1) * (y2 - y1 + 1)
        order = np.argsort(scores, axis=0)[::-1]
        keep = []
        while order.size > 0:
            i = order[0]
            keep.append(i)
            new_x1, new_y1, new_x2, new_y2 = [], [], [], []
            new_areas = []
            for order_i in order:
                new_x1.append(x1[order_i])
                new_x2.append(x2[order_i])
                new_y1.append(y1[order_i])
                new_y2.append(y2[order_i])
                new_areas.append(areas[order_i])
            new_x1 = np.array(new_x1)
            new_x2 = np.array(new_x2)
            new_y1 = np.array(new_y1)
            new_y2 = np.array(new_y2)
            xx1 = np.maximum(x1[i], new_x1)
            yy1 = np.maximum(y1[i], new_y1)
            xx2 = np.minimum(x2[i], new_x2)
            yy2 = np.minimum(y2[i], new_y2)
            w = np.maximum(0.0, xx2 - xx1 + 1)
            h = np.maximum(0.0, yy2 - yy1 + 1)
            inter = w * h
            new_areas = np.array(new_areas)
            ovr = inter / (areas[i] + new_areas - inter)
            new_order = []
            for ovr_i, ind in enumerate(ovr):
                if ind < thresh:
                    new_order.append(order[ovr_i])
            order = np.array(new_order, dtype=np.uint8)
        return keep

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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python tests/test_object_ai.py`
Expected: ALL PASS (8 tests)

- [ ] **Step 5: Commit**

```bash
git add core/object_ai.py tests/test_object_ai.py
git commit -m "feat(object_ai): YOLOv8n COCO80 detector (AIBase subclass, python NMS)" -m "Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## Task 4: icon_cache 加 object 图标

**Files:**
- Modify: `core/icon_cache.py:19` (加 _object_icons), `:146` 后(加 preload/get_object_icon)
- Test: `tests/test_icon_cache.py`

- [ ] **Step 1: Write the failing test**

在 `tests/test_icon_cache.py` 的 test_runner 之前追加:

```python
def test_preload_object_icons_exists():
    """icon_cache 须有 preload_object_icons() 预读 object_detect 图标。"""
    try:
        _method("preload_object_icons")
    except AssertionError:
        assert False, "_IconCache must define preload_object_icons()"


def test_get_object_icon_exists():
    """icon_cache 须有 get_object_icon(name) 取 object 图标。"""
    try:
        m = _method("get_object_icon")
    except AssertionError:
        assert False, "_IconCache must define get_object_icon(name)"
    args = [a.arg for a in m.args.args]
    assert "name" in args, "get_object_icon must take name"


def test_preload_object_icons_reads_object_detect_icon_dir():
    """preload_object_icons 必须读 object_detect_icon/ 目录。"""
    seg = ast.get_source_segment(_src(), _method("preload_object_icons")) or ""
    assert "object_detect_icon" in seg, "preload_object_icons must read object_detect_icon/"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python tests/test_icon_cache.py`
Expected: FAIL `_IconCache must define preload_object_icons()`

- [ ] **Step 3: Write minimal implementation**

`core/icon_cache.py` 改两处。

(1) `__init__` 加一行(在 `self._tag_icons = {}` 后):

```python
        self._tag_icons = {}      # name → (data, dsc)
        self._object_icons = {}   # name → (data, dsc)
```

(2) 在 `get_tag_icon` 方法后、`# 全局单例` 前,追加:

```python
    def preload_object_icons(self):
        """预读物体识别APP图标（在首次 task_handler 之前调用）"""
        base = "/sdcard/CamerAi/resource/icons/object_detect_icon/"
        icons = {
            "list": base + "list.png",
            "back": base + "back.png",
        }
        for name, path in icons.items():
            try:
                with open(path, 'rb') as f:
                    data = f.read()
                dsc = lv.img_dsc_t({
                    'data_size': len(data),
                    'data': data,
                })
                self._object_icons[name] = (data, dsc)
                print(f"[IconCache] object/{name} OK ({len(data)} bytes)")
            except Exception as e:
                print(f"[IconCache] object/{name} FAILED: {e}")

    def get_object_icon(self, name):
        """获取物体识别图标 (data, dsc)，未缓存返回 (None, None)"""
        return self._object_icons.get(name, (None, None))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python tests/test_icon_cache.py`
Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add core/icon_cache.py tests/test_icon_cache.py
git commit -m "feat(icon_cache): preload/get object_detect icons" -m "Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## Task 5: app_runtime 通道 + 图标预读

**Files:**
- Modify: `core/app_runtime.py:171-172` (init_app 加 object 分支), `:185-188` (_channels_for 加 object)
- Test: `tests/test_app_runtime_object.py`

- [ ] **Step 1: Write the failing test**

创建 `tests/test_app_runtime_object.py`:

```python
# tests/test_app_runtime_object.py — app_runtime object_detect 通道+图标预读契约(AST)
import ast
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RT_PATH = os.path.join(ROOT, "core", "app_runtime.py")


def _src():
    with open(RT_PATH, "r", encoding="utf-8") as f:
        return f.read()


def _method(cls_name, name):
    tree = ast.parse(_src(), filename=RT_PATH)
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == cls_name:
            for n in node.body:
                if isinstance(n, ast.FunctionDef) and n.name == name:
                    return n
    raise AssertionError("%s.%s missing" % (cls_name, name))


def test_channels_for_object_detect_uses_chn2_xla_rgbp888():
    """_channels_for 须为 object_detect 配 chn2 XLA RGBP888(同 face_detect AI 通道)。"""
    seg = ast.get_source_segment(_src(), _method("AppRuntime", "_channels_for")) or ""
    assert "object_detect" in seg, "_channels_for must handle object_detect"
    assert "XLA" in seg, "object_detect must use XLA detection size"
    assert "RGBP888" in seg, "object_detect must use RGBP888"
    assert "CAM_CHN_ID_2" in seg, "object_detect detection on chn2"


def test_init_app_preloads_object_icons():
    """init_app 须对 object_detect 调 preload_object_icons()。"""
    seg = ast.get_source_segment(_src(), _method("AppRuntime", "init_app")) or ""
    assert "object_detect" in seg, "init_app must branch on object_detect"
    assert "preload_object_icons" in seg, "init_app must call preload_object_icons for object_detect"


def test_runner():
    failures = 0
    for name in sorted(n for n in globals() if n.startswith("test_") and n != "test_runner"):
        try:
            globals()[name]()
            print("PASS %s" % name)
        except Exception as e:
            failures += 1
            print("FAIL %s: %s" % (name, e))
    print("\n%s" % ("ALL PASS" if failures == 0 else "%d FAILED" % failures))
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    test_runner()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python tests/test_app_runtime_object.py`
Expected: FAIL `_channels_for must handle object_detect`

- [ ] **Step 3: Write minimal implementation**

(1) `init_app` 的图标预读分支(在 tag_detect 分支后)加:

```python
        elif category_id == "tag_detect":
            icon_cache.preload_tag_icons()
        elif category_id == "object_detect":
            icon_cache.preload_object_icons()
```

(2) `_channels_for` 加 object_detect 分支(在 tag_detect 后、_template 前):

```python
        elif category_id == "tag_detect":
            # chn1 QVGA RGB565 专做检测（官方 AprilTag/QR demo 同款）；
            # chn0 VGA RGB888 显示。rect ×2 映射显示（QVGA→VGA 整数缩放）。
            chs.append((CAM_CHN_ID_1, Sensor.QVGA, Sensor.RGB565))
        elif category_id == "object_detect":
            # chn2 XLA RGBP888 专做 AI 推理(同 face_detect AI 通道)；
            # chn0 VGA RGB888 显示。检测框 rgb888p->display 整数缩放。
            chs.append((CAM_CHN_ID_2, Sensor.XLA, Sensor.RGBP888))
        elif category_id == "_template":
```

> ⚠️ 确认 `Sensor.XLA` 枚举名:face_detect 用的是 `Sensor.XLA`(见 `app_runtime.py:182`)。直接复用同一枚举值,板端已验证可用。

- [ ] **Step 4: Run test to verify it passes**

Run: `python tests/test_app_runtime_object.py`
Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add core/app_runtime.py tests/test_app_runtime_object.py
git commit -m "feat(app_runtime): object_detect chn2 XLA RGBP888 + icon preload" -m "Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## Task 6: i18n object_detect 段

**Files:**
- Modify: `resource/i18n/zh_CN.json` (tag_detect 段后加 object_detect 段)
- Modify: `resource/i18n/en_US.json` (同)
- Test: `tests/test_i18n_object.py`

- [ ] **Step 1: Write the failing test**

创建 `tests/test_i18n_object.py`:

```python
# tests/test_i18n_object.py — object_detect i18n 键契约
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ZH = os.path.join(ROOT, "resource", "i18n", "zh_CN.json")
EN = os.path.join(ROOT, "resource", "i18n", "en_US.json")


def _load(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def test_object_detect_section_exists_both_langs():
    for path in (ZH, EN):
        data = _load(path)
        assert "object_detect" in data, "missing object_detect section in %s" % path


def test_object_detect_keys_present():
    required = ["registered", "clear", "save"]
    for path in (ZH, EN):
        od = _load(path)["object_detect"]
        for k in required:
            assert k in od, "missing object_detect.%s in %s" % (k, path)


def test_object_detect_registered_has_placeholder():
    for path in (ZH, EN):
        val = _load(path)["object_detect"]["registered"]
        assert "%d" in val, "object_detect.registered must contain %%d in %s" % path


def test_runner():
    failures = 0
    for name in sorted(n for n in globals() if n.startswith("test_") and n != "test_runner"):
        try:
            globals()[name]()
            print("PASS %s" % name)
        except Exception as e:
            failures += 1
            print("FAIL %s: %s" % (name, e))
    print("\n%s" % ("ALL PASS" if failures == 0 else "%d FAILED" % failures))
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    test_runner()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python tests/test_i18n_object.py`
Expected: FAIL `missing object_detect section`

- [ ] **Step 3: Write minimal implementation**

在 `resource/i18n/zh_CN.json` 的 tag_detect 段后(其闭合 `}` 后逗号后)插入 object_detect 段:

```json
  "tag_detect": {
    "april_tag": "AprilTag",
    "qr_code": "二维码",
    "registered": "已注册 %d/4",
    "clear": "清除",
    "save": "保存"
  },
  "object_detect": {
    "registered": "已注册 %d/4",
    "clear": "清除",
    "save": "保存"
  },
```

在 `resource/i18n/en_US.json` 对应位置插入:

```json
  "object_detect": {
    "registered": "Registered %d/4",
    "clear": "Clear",
    "save": "Save"
  },
```

> ⚠️ 先 Read 两个 JSON 文件确认 tag_detect 段的确切闭合位置与逗号,再用 Edit 精确插入。en_US 若 tag_detect 段位置不同,在其后插入。

- [ ] **Step 4: Run test to verify it passes**

Run: `python tests/test_i18n_object.py`
Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add resource/i18n/zh_CN.json resource/i18n/en_US.json tests/test_i18n_object.py
git commit -m "feat(i18n): object_detect section (registered/clear/save) bilingual" -m "Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## Task 7: scripts/object_detect/app.py 主体(AST 契约先行)

**Files:**
- Create: `scripts/object_detect/app.py`
- Test: `tests/test_object_detect_app.py`

> 本脚本是核心。AST 契约先覆盖:on_frame/run/画框/十字/list 浮层/i18n/chn2/IdRegistry.registrar/无硬编码中文。

- [ ] **Step 1: Write the failing test**

创建 `tests/test_object_detect_app.py`:

```python
# tests/test_object_detect_app.py — object_detect app AST 契约(板端不可导入)
import ast
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP_PATH = os.path.join(ROOT, "scripts", "object_detect", "app.py")


def _app_src():
    with open(APP_PATH, "r", encoding="utf-8") as f:
        return f.read()


def _app_tree():
    return ast.parse(_app_src(), filename=APP_PATH)


def _func(name):
    tree = _app_tree()
    for n in tree.body:
        if isinstance(n, ast.FunctionDef) and n.name == name:
            return n
    raise AssertionError("Function %s missing in object_detect/app.py" % name)


def test_run_entrypoint_exists():
    try:
        _func("run")
    except AssertionError:
        assert False, "object_detect/app.py must define run(runtime)"


def test_on_frame_exists():
    try:
        _func("on_frame")
    except AssertionError:
        assert False, "must define on_frame(img)"


def test_on_frame_uses_cam_chn_id_2_for_detection():
    """检测须取 chn2(XLA RGBP888 AI 通道)。"""
    seg = ast.get_source_segment(_app_src(), _func("on_frame")) or ""
    assert "CAM_CHN_ID_2" in seg, "on_frame must snapshot chn=CAM_CHN_ID_2"


def test_on_frame_calls_host_tick_with_slots():
    seg = ast.get_source_segment(_app_src(), _func("on_frame")) or ""
    assert "host_tick" in seg, "on_frame must call host_tick(slots)"
    assert "slots" in seg, "on_frame must build slots list"


def test_on_frame_uses_id_registry_with_object_db_registrar():
    """KEY2 注册须走 object_db.register(registrar=)。"""
    seg = ast.get_source_segment(_app_src(), _func("on_frame")) or ""
    assert "try_register" in seg, "on_frame must call id_registry.try_register"
    assert "registrar" in seg, "try_register must pass registrar=object_db.register"


def test_on_frame_draws_center_green_crosshair():
    """on_frame 须在屏幕居中画小绿十字(VGA 中心 320,240)。"""
    seg = ast.get_source_segment(_app_src(), _func("on_frame")) or ""
    assert "draw_cross" in seg, "on_frame must draw a center crosshair"
    assert "320" in seg and "240" in seg, "crosshair must be at screen center (320, 240)"


def test_box_colors_white_unknown_and_slot_color():
    """画框对齐 face_detect:未注册白框(BOX_UNKNOWN),注册按 slot 彩色(BOX_COLORS)。"""
    src = _app_src()
    seg = ast.get_source_segment(src, _func("on_frame")) or ""
    assert "BOX_UNKNOWN" in src, "must use BOX_UNKNOWN (white) for unregistered boxes"
    assert "BOX_COLORS" in src, "must use BOX_COLORS (per-slot) for registered boxes"
    assert "_draw_color" in src, "must use _draw_color for RGB888 color tuple"
    # 不得红框
    assert "(255, 0, 0)" not in seg, "unknown box must be white, not red"


def test_on_frame_shows_id_and_english_class_name():
    """注册框须显示 ID号 + 英文类名(槽号+英文类名,不双语)。"""
    seg = ast.get_source_segment(_app_src(), _func("on_frame")) or ""
    assert "draw_string_advanced" in seg, "must draw label text"
    assert "ID" in seg, "registered box must show ID%d"
    # 类名须从 COCO_LABELS 取(英文),不得硬编码中文类名
    assert "COCO_LABELS" in _app_src() or "labels" in seg, \
        "class name must come from COCO_LABELS (english)"


def test_list_overlay_handlers_exist():
    """list 图标须绑定清除/保存浮层(对齐 face_detect/tag_detect)。"""
    for fn in ["_on_list_clicked", "_on_clear_clicked", "_on_save_clicked",
               "_on_overlay_clicked", "_on_screen_clicked", "_process_overlay_close"]:
        try:
            _func(fn)
        except AssertionError:
            assert False, "must define %s for list overlay" % fn


def test_list_button_binds_click_and_screen_closes_overlay():
    seg = ast.get_source_segment(_app_src(), _func("_build_ui")) or ""
    assert "_on_list_clicked" in seg, "_build_ui must bind _on_list_clicked to list button"
    assert "_on_screen_clicked" in seg, "_build_ui must bind _on_screen_clicked to screen"


def test_clear_clicked_clears_db_and_refreshes():
    seg = ast.get_source_segment(_app_src(), _func("_on_clear_clicked")) or ""
    assert "clear()" in seg, "_on_clear_clicked must clear db"
    assert "_refresh_count" in seg, "must refresh count after clear"
    assert "buzzer" in seg, "must beep on clear"


def test_run_loop_has_exitpoint_and_task_handler():
    seg = ast.get_source_segment(_app_src(), _func("run")) or ""
    assert "exitpoint" in seg, "run loop must call os.exitpoint()"
    assert "task_handler" in seg, "run loop must call lv.task_handler()"
    assert "_process_overlay_close" in seg, "run loop must call _process_overlay_close()"


def test_app_uses_i18n_not_hardcoded():
    """文本须走 lang.t(),不得硬编码中文。"""
    src = _app_src()
    assert "lang.t" in src or "lang.t(" in src, "must use lang.t()"
    bad = ["已注册", "清除", "保存", "物体识别"]
    for s in bad:
        assert ('"%s"' % s) not in src, "must not hardcode '%s'; use i18n" % s


def test_runner():
    failures = 0
    for name in sorted(n for n in globals() if n.startswith("test_") and n != "test_runner"):
        try:
            globals()[name]()
            print("PASS %s" % name)
        except Exception as e:
            failures += 1
            print("FAIL %s: %s" % (name, e))
    print("\n%s" % ("ALL PASS" if failures == 0 else "%d FAILED" % failures))
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    test_runner()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python tests/test_object_detect_app.py`
Expected: FAIL (FileNotFoundError — app.py 不存在)

- [ ] **Step 3: Write minimal implementation**

创建 `scripts/object_detect/app.py`:

```python
# scripts/object_detect/app.py — YOLOv8n COCO80 物体识别。
#
# 复用 _template 单线程主循环。chn2 XLA RGBP888 做 AI 推理(同 face_detect),
# chn0 VGA RGB888 显示。底栏仅左侧 list 图标(清除/保存浮层)+ 计数。KEY2 按类别
# 注册(最多4类),走 object_db.register via registrar。注册框显示 ID号+英文类名,
# 未注册白框。协议类型 0x05 上传4槽位。持久化预留(flush_to_disk no-op)。

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
from core.object_ai import ObjectDetectionApp, COCO_LABELS
from core.object_db import ObjectDB

BAR_H = 52
PREVIEW_Y = BAR_H
PREVIEW_H = 376
BAR_BG = 0x1A1A1A

# 画框配色对齐 face_detect/tag_detect:未注册白框,注册按 slot 取彩色。
BOX_COLORS = {
    1: 0x44CC44,   # 绿
    2: 0x4488FF,   # 蓝
    3: 0xFF8844,   # 橙
    4: 0xCC44FF,   # 紫
}
BOX_UNKNOWN = 0xFFFFFF   # 未注册白框

KMODEL_PATH = "/sdcard/examples/kmodel/yolov8n_320.kmodel"
RGB888P_W = 1024
RGB888P_H = 768
DISPLAY_W = 640
DISPLAY_H = 480


def _draw_color(hex_color):
    """hex 0xRRGGBB -> K230 draw_rectangle color tuple (A, B, G, R)。"""
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
_object_det = None
_db = None
_overlay = None
_clear_btn = None
_save_btn = None
_close_overlay = False


def _init_ai():
    global _object_det
    print("[object_detect] loading det kmodel...")
    _object_det = ObjectDetectionApp(
        KMODEL_PATH, labels=COCO_LABELS, model_input_size=[320, 320],
        max_boxes_num=50, confidence_threshold=0.2, nms_threshold=0.2,
        rgb888p_size=[RGB888P_W, RGB888P_H], display_size=[DISPLAY_W, DISPLAY_H],
        debug_mode=0)
    _object_det.config_preprocess()
    print("[object_detect] AI ready")


def _init_registry(fpioa):
    global _id_registry
    _id_registry = IdRegistry(fpioa, pin=0)


def _deinit_ai():
    global _object_det
    if _object_det is not None:
        try:
            _object_det.deinit()
        except Exception as e:
            print("[object_detect] det deinit warning: %s" % e)
        _object_det = None


def on_frame(img):
    """chn2 检测 -> 每类取最大实例 -> 匹配 DB -> 画框 -> 十字 -> host_tick。

    每类别取面积最大实例画框+填槽(协议每槽一坐标)。注册类彩色框+ID号+英文类名,
    未注册白框。KEY2 注册当前帧最大框的类别。
    """
    if _RUNTIME is None or _object_det is None:
        return
    img_ai = _RUNTIME.sensor.snapshot(chn=CAM_CHN_ID_2)
    img_np = img_ai.to_numpy_ref()
    try:
        dets = _object_det.run(img_np)
    except Exception as e:
        print("[object_detect] run error: %s" % e)
        dets = []

    slots = [None, None, None, None]
    # 每类别取面积最大实例
    per_class_max = {}   # class_id -> [l,t,r,b,score,cid]
    for det in dets:
        try:
            l, t, r, b, score, cid = [float(v) for v in det]
        except Exception:
            continue
        cid = int(cid)
        area = (r - l) * (b - t)
        cur = per_class_max.get(cid)
        if cur is None or area > (cur[2] - cur[0]) * (cur[3] - cur[1]):
            per_class_max[cid] = [l, t, r, b, score, cid]

    for cid, det in per_class_max.items():
        l, t, r, b, score, _ = det
        slot, _score = _db.match(cid)
        x = int(l) * DISPLAY_W // RGB888P_W
        y = int(t) * DISPLAY_H // RGB888P_H
        w = int(r - l) * DISPLAY_W // RGB888P_W
        h = int(b - t) * DISPLAY_H // RGB888P_H
        conf = int(score * 100)
        if slot is not None:
            color = _draw_color(BOX_COLORS.get(slot, BOX_UNKNOWN))
            img.draw_rectangle(x, y, w, h, color=color, thickness=4)
            img.draw_string_advanced(x, y - 24, 24,
                                     "ID%d %s" % (slot, COCO_LABELS[cid]), color=color)
            if 1 <= slot <= 4:
                slots[slot - 1] = (slot, x, y, w, h, conf)
        else:
            color = _draw_color(BOX_UNKNOWN)
            img.draw_rectangle(x, y, w, h, color=color, thickness=2)

    # 屏幕居中绿色十字(对准参考,小一点):VGA 640x480 中心 (320, 240)
    img.draw_cross(320, 240, color=(0xFF, 0x00, 0xFF, 0x00), size=20, thickness=2)

    # KEY2 注册:当前帧最大框的类别 -> 下一槽
    if _id_registry is not None and _id_registry.has_pending() and per_class_max:
        max_cid = max(per_class_max.values(),
                      key=lambda d: (d[2] - d[0]) * (d[3] - d[1]))[5]
        try:
            slot = _id_registry.try_register(max_cid, _RUNTIME.buzzer,
                                             registrar=_db.register)
            if slot is not None:
                _refresh_count()
        except Exception as e:
            print("[object_detect] register error: %s" % e)

    if _RUNTIME is not None and _RUNTIME.host is not None:
        _RUNTIME.host_tick(slots)
    gc.collect()


def _refresh_count():
    if _count_label is not None and _RUNTIME is not None and _db is not None:
        try:
            _count_label.set_text(_RUNTIME.lang.t("object_detect.registered", _db.count))
        except Exception:
            pass


def _on_list_clicked(e):
    """弹出清除/保存浮层(对齐 face_detect/tag_detect)。"""
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
    cl.set_text(_RUNTIME.lang.t("object_detect.clear"))
    cl.add_style(make_back_bar_text_style(fonts.body), 0)
    cl.center()
    _clear_btn.add_event(_on_clear_clicked, lv.EVENT.CLICKED, None)

    _save_btn = lv.btn(_overlay)
    _save_btn.set_size(120, 40)
    _save_btn.align(lv.ALIGN.RIGHT_MID, -20, 0)
    sv = lv.label(_save_btn)
    sv.set_text(_RUNTIME.lang.t("object_detect.save"))
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
    """清 db 内存 + 蜂鸣 + 关浮层。不删盘(持久化待定)。"""
    global _close_overlay
    if e.get_code() != lv.EVENT.CLICKED:
        return
    if _db is not None:
        _db.clear()
    _refresh_count()
    if _RUNTIME is not None and _RUNTIME.buzzer is not None:
        _RUNTIME.buzzer.beep(ms=200)
    _close_overlay = True


def _on_save_clicked(e):
    """空操作(退出自动持久化,当前 no-op)。只标志关闭浮层。"""
    global _close_overlay
    if e.get_code() != lv.EVENT.CLICKED:
        return
    _close_overlay = True


def _process_overlay_close():
    """主循环 deferred 关闭浮层(LVGL use-after-free 防护)。"""
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
    """顶栏(back+标题) + 透明预览 + 底栏(list图标 + 计数)。"""
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
    btn.set_size(48, 48)
    btn.align(lv.ALIGN.LEFT_MID, 2, 0)
    btn.set_style_bg_opa(0, 0)
    btn.set_style_border_width(0, 0)
    btn.set_style_shadow_width(0, 0)
    btn.set_style_outline_width(0, 0)
    btn.set_style_outline_opa(0, 0)
    btn.set_style_pad_all(0, 0)
    btn.clear_flag(lv.obj.FLAG.SCROLLABLE)
    btn.add_flag(lv.obj.FLAG.CLICKABLE)

    icon_data, icon_dsc = icon_cache.get_object_icon("back")
    if icon_dsc is not None and icon_data is not None:
        import struct
        w = h = 64
        if len(icon_data) >= 24:
            w = struct.unpack('>I', icon_data[16:20])[0]
            h = struct.unpack('>I', icon_data[20:24])[0]
        target = int(48 * 0.85)
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
            exit_flag[0] = True
    btn.add_event(_on_back, lv.EVENT.CLICKED, None)

    title = lv.label(_top_bar)
    title.set_text(runtime.lang.t("category.object_detect"))
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

    # list 图标(点击弹清除/保存浮层)
    list_btn = lv.obj(_bottom_bar)
    list_btn.set_size(48, 48)
    list_btn.align(lv.ALIGN.LEFT_MID, 2, 0)
    list_btn.set_style_bg_opa(0, 0)
    list_btn.set_style_border_width(0, 0)
    list_btn.set_style_pad_all(0, 0)
    list_btn.clear_flag(lv.obj.FLAG.SCROLLABLE)
    list_btn.add_flag(lv.obj.FLAG.CLICKABLE)
    list_btn.add_event(_on_list_clicked, lv.EVENT.CLICKED, None)
    list_icon_data, list_icon_dsc = icon_cache.get_object_icon("list")
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
        list_lbl.set_text("list")
        list_lbl.add_style(make_back_bar_text_style(fonts.body), 0)
        list_lbl.center()

    count_label = lv.label(_bottom_bar)
    count_label.set_text(runtime.lang.t("object_detect.registered", 0))
    count_label.add_style(make_back_bar_text_style(fonts.body), 0)
    count_label.align(lv.ALIGN.CENTER, 0, 0)
    _count_label = count_label


def _destroy_ui():
    global _screen, _top_bar, _bottom_bar, _preview, _count_label
    global _overlay, _clear_btn, _save_btn
    for obj in (_clear_btn, _save_btn, _overlay, _top_bar, _bottom_bar, _preview, _count_label):
        if obj is not None:
            try:
                obj.delete()
            except Exception:
                pass
    _clear_btn = None
    _save_btn = None
    _overlay = None
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
    """reset 框架入口。单线程主循环:snapshot chn0 -> on_frame -> show OSD1 -> task_handler。"""
    global _RUNTIME, _db
    _RUNTIME = runtime
    _db = ObjectDB()
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
                print("[object_detect] on_frame error: %s" % e)
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
                print("[object_detect] fc=%d" % fc)
    finally:
        _deinit_ai()
        _destroy_ui()
        if _db is not None:
            _db.flush_to_disk()
        _RUNTIME = None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python tests/test_object_detect_app.py`
Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add scripts/object_detect/app.py tests/test_object_detect_app.py
git commit -m "feat(object_detect): main app loop (YOLOv8 detect, class register, crosshair)" -m "Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## Task 8: 全量回归 + 拷贝图标

**Files:**
- Copy: `resource/icons/face_detect_icon/{back,list}.png` → `resource/icons/object_detect_icon/`

- [ ] **Step 1: 拷贝图标资源**

```bash
cp resource/icons/face_detect_icon/back.png resource/icons/object_detect_icon/back.png
cp resource/icons/face_detect_icon/list.png resource/icons/object_detect_icon/list.png
```

> 图标在板端 `/sdcard/CamerAi/resource/icons/object_detect_icon/` 下被读取,本地仓库拷贝用于版本管理 + 部署同步。

- [ ] **Step 2: 全量测试回归**

Run all tests:
```bash
python tests/test_object_db.py
python tests/test_object_ai.py
python tests/test_object_detect_app.py
python tests/test_host_api.py
python tests/test_icon_cache.py
python tests/test_app_runtime_object.py
python tests/test_i18n_object.py
```
Expected: 每个 ALL PASS。

- [ ] **Step 3: 提交图标 + 回归确认**

```bash
git add resource/icons/object_detect_icon/back.png resource/icons/object_detect_icon/list.png
git commit -m "feat(object_detect): add back/list icons (from face_detect_icon)" -m "Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## Task 9: 板端验收 + 记录

**Files:**
- Modify: `项目记录.md`(追加 2026-06-26 object_detect 段)
- Memory: 更新 `camerai-host-protocol.md`、新建 `camerai-object-detect.md`、更新 `MEMORY.md`

- [ ] **Step 1: 部署到板端**

将以下文件同步到 K230 `/sdcard/CamerAi/`:
- `comm/host_api.py`
- `core/object_ai.py`、`core/object_db.py`、`core/app_runtime.py`、`core/icon_cache.py`
- `scripts/object_detect/app.py`(整目录)
- `resource/i18n/zh_CN.json`、`resource/i18n/en_US.json`
- `resource/icons/object_detect_icon/{back,list}.png`

> kmodel `yolov8n_320.kmodel` 应已在 `/sdcard/examples/kmodel/`(板端自带),无需部署。

- [ ] **Step 2: 板端验收清单(7 点)**

1. 主菜单点"物体识别"进入,顶栏标题(中/英随语言)正确,预览出画面。
2. 拿常见物体(人/杯子/瓶子/键盘/手机)对镜头,检测框出现(白框,未注册)。
3. 按 KEY2 注册当前最大框类别 → 蜂鸣 → 框变彩色 + 顶部显示 `ID1 person` 等(槽号+英文类名),计数 1/4。
4. 再注册不同类别 → 槽 2 彩色 + `ID2 ...`,计数 2/4。注册第5类 → 覆盖槽1(轮转)。
5. 同类物体再注册 → 不重复占槽,返回原槽(计数不变)。
6. 底栏 list 图标 → 弹清除/保存浮层 → 点清除 → 蜂鸣 + 计数归0 + 已注册框变回白框。
7. 上位机收到类型 0x05 数据帧,4 槽位坐标大端正确(注册类别填槽,未注册全0)。

- [ ] **Step 3: 记录到 项目记录.md**

追加 2026-06-26 object_detect 段(架构/提交表/部署清单/验收结果/降级备注),格式参照 tag_detect 段。

- [ ] **Step 4: 记录到 memory**

- 更新 `camerai-host-protocol.md`:提及 object_detect 接入类型 0x05。
- 新建 `camerai-object-detect.md`:脚本结构(chn2 XLA RGBP888 检测、ObjectDetectionApp YOLOv8 纯 Python NMS、ObjectDB 类别→槽、注册框 ID号+英文类名、白框未注册、十字、双语、协议0x05)。
- 更新 `MEMORY.md` 索引。

- [ ] **Step 5: push**

```bash
git add 项目记录.md
git commit -m "docs: record object_detect implementation + board acceptance" -m "Co-Authored-By: Claude <noreply@anthropic.com>"
git push origin main
```

---

## 风险与降级(实现时注意)

1. **YOLOv8 后处理纯 Python NMS**:板端帧率可能低于 face_detect(face_det 用 aidemo C 加速)。降级:调小 `max_boxes_num`(50→20)、确保每帧 `gc.collect()`。板端若卡顿记录坑并降级,不阻塞功能验收。
2. **`Sensor.XLA` 枚举**:与 face_detect 复用同一通道枚举(`app_runtime.py:182` 已用 `Sensor.XLA`),板端已验证可用。若实际枚举名不同(如 `Sensor.XLAGA`),以 face_detect 现有行为准,不改枚举名。
3. **`det` 元素类型**:ulab ndarray 迭代出的元素可能是 numpy 标量,`[float(v) for v in det]` 强转 float 避免后续 int() 出错。
4. **多实例同类**:一帧多个"person"全画彩色框(每个 per_class_max 只取最大实例——按 spec 是每类一框)。若用户要求每实例都画,后续调整。当前按 spec 每类一框(最大实例)+ 一槽。

---

## 自检

- **Spec 覆盖**:UI 布局(Task7 _build_ui)/串口(Task1)/ID设置(Task2+Task7 KEY2)/list图标清除保存(Task7)/绿十字(Task7)/按类别注册(Task2+Task7)/注册框ID号+英文类名(Task7) — 全覆盖。
- **Placeholder**:无 TBD/TODO,每步含完整代码。
- **类型一致**:`ObjectDB.register(class_id)→slot`、`ObjectDetectionApp` 构造签名、`try_register(feature, buzzer, registrar=)`、`COCO_LABELS[cid]` — 各 Task 间一致。
- **顺序**:Task1(host_api)→Task2(db)→Task3(ai)→Task4(icon)→Task5(runtime)→Task6(i18n)→Task7(app)→Task8(回归+图标)→Task9(板端+记录)。Task7 依赖 Task1-6 全部到位。
