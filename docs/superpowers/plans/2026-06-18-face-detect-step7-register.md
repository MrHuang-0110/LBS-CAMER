# Step 7: K2 注册人脸 + 可复用 ID 管理模块 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Follow TDD workflow — each task: write failing test → implement → verify pass → commit. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 按 K2(GPIO0)物理按键注册当前帧最大人脸到 face_db(当场 flush),注册后内存即生效当场识别彩色框+ID;抽出可复用注册管理模块(core/face_db.register + core/id_registry);收尾 face_detect 临时定位(走完整 init_app + buzzer 接回)。

**Architecture:** 三模块解耦——core/face_db.py(增 register 轮转覆盖+指针持久化+clear)、core/id_registry.py(新建,K2 软件边沿+注册协作)、scripts/face_detect/app.py(接入,复用 Step 5 特征零额外 NPU)。main.py/app_runtime.py 收尾临时定位(删跳过 init_app + 恢复 Buzzer)。

**Tech Stack:** MicroPython(K230 CanMV) / ulab.numpy / AST host tests / Python 3.x(test runner)

**Spec:** `docs/superpowers/specs/2026-06-18-face-detect-step7-register-design.md`

---

### 模块职责

| 文件 | 操作 | 职责 |
|------|------|------|
| `core/face_db.py` | 修改 | 增 `register(feature)`(轮转覆盖B+当场flush)、`clear()`(删.bin+.next_slot+指针回1)、`_load_next_slot()`/`_save_next_slot()`(指针持久化)、`_next_slot`属性 |
| `core/id_registry.py` | **新建** | IdRegistry 类:K2 软件边沿(松开→按下置 pending)+2秒超时+try_register 调 face_db.register+蜂鸣 |
| `scripts/face_detect/app.py` | 修改 | run()加 IdRegistry 初始化+poll_k2;face_det_thread 加 try_register(复用 Step5 feature);import 加 id_registry |
| `main.py` | 修改 | 删"face_detect 跳过 init_app"分支(所有脚本统一走 init_app) |
| `core/app_runtime.py` | 修改 | 删"face_detect 跳过 touch/fonts/services"分支;恢复 Buzzer 创建 |
| `tests/test_face_register.py` | **新建** | TDD host AST+stub 测试(照搬 test_face_detect.py runner 模式) |

### 执行顺序

Task 1(测试) → Task 2(face_db) → Task 3(id_registry) → Task 4(face_detect 接入) → Task 5(main.py 收尾) → Task 6(app_runtime 收尾+Buzzer) → Task 7(全量测试绿+commit)

**任务 1-6 之间互相独立的部分可并行,但 face_db/id_registry 实现必须在接入测试之前。**

---

### Task 1: 新建测试文件 tests/test_face_register.py(全部 RED)

**Files:**
- Create: `tests/test_face_register.py`

**状态:所有测试应 FAIL。**

- [ ] **Step 1: 写 face_db.register 填空 slot 测试(stub face_db)**

```python
# tests/test_face_register.py — host-side AST + stub tests for Step 7 register.
import ast
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP_PATH = os.path.join(ROOT, "scripts", "face_detect", "app.py")
FACE_DB_PATH = os.path.join(ROOT, "core", "face_db.py")
MAIN_PATH = os.path.join(ROOT, "main.py")
APP_RUNTIME_PATH = os.path.join(ROOT, "core", "app_runtime.py")
ID_REGISTRY_PATH = os.path.join(ROOT, "core", "id_registry.py")


def _parse(path):
    with open(path, "r", encoding="utf-8") as f:
        return ast.parse(f.read(), filename=path)


def _function_node(tree, name):
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError("Function %s missing" % name)


def _class_node(tree, name):
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == name:
            return node
    raise AssertionError("Class %s missing" % name)


# ── face_db.register 测试 ──

def test_face_db_register_fills_empty_slot_first():
    """register() fills first empty slot without rotating _next_slot."""
    src = open(FACE_DB_PATH, encoding="utf-8").read()
    assert "def register(" in src, "face_db must have register method"

    # Simulate: stub _FaceDB with register logic
    class StubFaceDB:
        def __init__(self):
            self._features = {}
            self._next_slot = 1

        def register(self, feature):
            slot = None
            for i in range(1, 5):
                if i not in self._features:
                    slot = i
                    break
            if slot is None:
                slot = self._next_slot
                self._next_slot = self._next_slot % 4 + 1
            self._features[slot] = feature
            return slot

    db = StubFaceDB()
    # Register 4 faces → should fill id1, id2, id3, id4
    slots = []
    for i in range(4):
        s = db.register("feature_%d" % i)
        slots.append(s)
    assert slots == [1, 2, 3, 4], "first 4 registers should fill id1-4, got %s" % slots
    assert db._next_slot == 1, "pointer should stay at 1 (empty slots filled, no rotation)"


def test_face_db_register_rotates_when_full():
    """When all 4 slots full, register() rotates _next_slot 1→2→3→4→1."""
    src = open(FACE_DB_PATH, encoding="utf-8").read()
    assert "_next_slot" in src, "face_db must track _next_slot pointer"

    class StubFaceDB:
        def __init__(self):
            self._features = {}
            self._next_slot = 1

        def register(self, feature):
            slot = None
            for i in range(1, 5):
                if i not in self._features:
                    slot = i
                    break
            if slot is None:
                slot = self._next_slot
                self._next_slot = self._next_slot % 4 + 1
            self._features[slot] = feature
            return slot

    db = StubFaceDB()
    # Fill all 4
    for i in range(4):
        db.register("f%d" % i)
    assert list(db._features.keys()) == [1, 2, 3, 4]
    assert db._next_slot == 1
    # 5th → overwrite id1, pointer → 2
    s5 = db.register("f5")
    assert s5 == 1, "5th register should rotate to id1, got %d" % s5
    assert db._next_slot == 2, "pointer should advance to 2"
    # 6th → overwrite id2, pointer → 3
    s6 = db.register("f6")
    assert s6 == 2, "6th register should rotate to id2, got %d" % s6
    assert db._next_slot == 3, "pointer should advance to 3"
    # 7th → overwrite id3, pointer → 4
    s7 = db.register("f7")
    assert s7 == 3
    assert db._next_slot == 4
    # 8th → overwrite id4, pointer → 1
    s8 = db.register("f8")
    assert s8 == 4
    assert db._next_slot == 1, "pointer should wrap 4→1"


def test_face_db_persists_next_slot():
    """register() calls _save_next_slot; _load_next_slot reads back correctly."""
    src = open(FACE_DB_PATH, encoding="utf-8").read()
    assert "_save_next_slot" in src, "must save _next_slot to disk"
    assert "_load_next_slot" in src, "must load _next_slot from disk"
    assert "init_features" in src  # _load_next_slot called inside init_features
    # Check _save_next_slot writes to .next_slot file
    assert ".next_slot" in src, "must use .next_slot file for pointer persistence"


def test_face_db_clear_deletes_next_slot_and_resets_pointer():
    """clear() removes .next_slot file and resets _next_slot=1."""
    src = open(FACE_DB_PATH, encoding="utf-8").read()
    # clear() must: reset _next_slot to 1, remove .next_slot
    assert "def clear(" in src, "face_db must have clear method"
    # clear should reference _next_slot and .next_slot
    clear_start = src.find("def clear(")
    clear_body = src[clear_start:]
    assert "_next_slot" in clear_body, "clear must reset _next_slot pointer"
    assert ".next_slot" in clear_body, "clear must delete .next_slot file"


# ── id_registry 测试 ──

def test_id_registry_class_exists():
    """core/id_registry.py must exist with IdRegistry class."""
    tree = _parse(ID_REGISTRY_PATH)
    _class_node(tree, "IdRegistry")


def test_id_registry_has_poll_k2_and_try_register():
    """IdRegistry must have poll_k2() for main thread and try_register() for AI thread."""
    tree = _parse(ID_REGISTRY_PATH)
    cls = _class_node(tree, "IdRegistry")
    method_names = {n.name for n in cls.body if isinstance(n, ast.FunctionDef)}
    assert "poll_k2" in method_names, "IdRegistry missing poll_k2"
    assert "try_register" in method_names, "IdRegistry missing try_register"


def test_id_registry_poll_k2_edge_detect():
    """poll_k2 must track _prev_pressed for edge detection (release→press triggers once)."""
    src = open(ID_REGISTRY_PATH, encoding="utf-8").read()
    assert "_prev_pressed" in src, "must track previous state for edge detection"
    assert "self._pending = True" in src, "must set _pending on press edge"


def test_id_registry_pending_timeout():
    """try_register must discard pending after 2 seconds."""
    src = open(ID_REGISTRY_PATH, encoding="utf-8").read()
    assert "2000" in src or "2" in src, "must have timeout (2000ms) for pending"
    assert "ticks_ms" in src or "time" in src, "must use ticks_ms for timeout tracking"


def test_id_registry_try_register_calls_face_db():
    """try_register must import and call face_db.register when pending."""
    src = open(ID_REGISTRY_PATH, encoding="utf-8").read()
    assert "face_db.register" in src, "try_register must call face_db.register"
    assert "from core.face_db import" in src or "from core.face_db" in src, \
        "must import face_db"


def test_id_registry_try_register_clears_pending():
    """try_register must clear _pending after attempt (success or fail)."""
    src = open(ID_REGISTRY_PATH, encoding="utf-8").read()
    # After calling register, _pending must be False
    assert "self._pending = False" in src, "must clear _pending after try_register"


def test_id_registry_try_register_buzzer_feedback():
    """try_register must call buzzer.beep(ms=80) on success, shorter on no-face/no-action."""
    src = open(ID_REGISTRY_PATH, encoding="utf-8").read()
    assert "buzzer" in src, "try_register must accept buzzer parameter"
    assert "beep" in src, "must call buzzer.beep for feedback"


# ── face_detect 接入测试 ──

def test_face_detect_run_inits_id_registry():
    """run() must import IdRegistry and call poll_k2 in main loop."""
    src = open(APP_PATH, encoding="utf-8").read()
    assert "IdRegistry" in src, "run() must import IdRegistry"
    assert "id_registry" in src, "run() must create id_registry instance"
    assert "poll_k2" in src, "main loop must call id_registry.poll_k2()"


def test_face_detect_ai_thread_calls_try_register():
    """face_det_thread must call id_registry.try_register(feature, runtime.buzzer) after recognition."""
    src = open(APP_PATH, encoding="utf-8").read()
    assert "try_register" in src, "AI thread must call try_register"


# ── 收尾临时定位测试 ──

def test_main_no_face_detect_init_app_skip():
    """main.py must NOT have the face_detect init_app skip branch anymore."""
    src = open(MAIN_PATH, encoding="utf-8").read()
    # Old: if category_id != "face_detect": runtime.init_app(...)  ← must be gone
    # New: runtime.init_app(category_id, fpioa)  ← unconditional
    assert 'category_id != "face_detect"' not in src, \
        "main.py must not have face_detect init_app exclusion"
    assert "runtime.init_app(category_id, fpioa)" in src, \
        "main.py must call runtime.init_app for all categories"


def test_app_runtime_init_app_no_face_detect_skip():
    """app_runtime init_app must NOT skip touch/fonts/services for face_detect."""
    src = open(APP_RUNTIME_PATH, encoding="utf-8").read()
    # Old: if category_id != "face_detect": ... _init_touch(); fonts; _init_services()
    # New: unconditional _init_touch + fonts + _init_services for all categories
    assert 'category_id != "face_detect"' not in src, \
        "app_runtime init_app must not skip any category"
    assert "_init_touch" in src, "init_app must call _init_touch"
    assert "fonts.load_all" in src, "init_app must load fonts"
    assert "_init_services" in src, "init_app must call _init_services"


def test_app_runtime_buzzer_created():
    """_init_services must create Buzzer (not commented out)."""
    src = open(APP_RUNTIME_PATH, encoding="utf-8").read()
    # Buzzer should be imported and created (not commented)
    init_services_start = src.find("def _init_services(")
    init_services_end = src.find("def cleanup(") if "def cleanup(" in src else len(src)
    init_services_body = src[init_services_start:init_services_end]
    assert "Buzzer(" in init_services_body, "_init_services must create Buzzer"
    assert "from hw.buzzer import Buzzer" in init_services_body, \
        "must import Buzzer"
    assert "self.buzzer = Buzzer(" in init_services_body, \
        "must assign Buzzer to self.buzzer"


# ── test runner ──

def test_runner():
    failures = 0
    tests = [(name, fn) for name, fn in sorted(globals().items())
             if name.startswith("test_") and callable(fn) and name != "test_runner"]
    for name, fn in tests:
        try:
            fn()
            print("PASS %s" % name)
        except Exception as e:
            failures += 1
            print("FAIL %s: %s" % (name, e))
    if failures:
        print("\n%d FAILED" % failures)
        sys.exit(1)
    print("\nALL PASS")


if __name__ == "__main__":
    test_runner()
```

- [ ] **Step 2: 跑 RED——全部 FAIL(因实现文件还没改)**

```bash
python tests/test_face_register.py
```

预期:全部(或几乎全部)FAIL,因为 `face_db.py` 还没 `register`/`clear`/`_save_next_slot`/`_load_next_slot`, `id_registry.py` 还不存在, `main.py` 还有跳过分支。

- [ ] **Step 3: 确认旧测试仍通过(基线不破)**

```bash
python tests/test_face_detect.py && python tests/test_framework.py
```

预期:ALL PASS(保持现有测试绿)。

---

### Task 2: 实现 face_db.register / clear / 指针持久化

**Files:**
- Modify: `core/face_db.py`

- [ ] **Step 1: 在 _FaceDB.__init__ 加 _next_slot 属性 + _NEXT_SLOT_PATH 常量**

修改 `core/face_db.py`。在 `_DB_DIR` 下加常量:

```python
_DB_DIR = "/data/fac_db"
_NEXT_SLOT_PATH = "/data/fac_db/.next_slot"
```

在 `__init__` 加一行:

```python
    def __init__(self):
        self._features = {}
        self._loaded = False
        self._next_slot = 1   # Step 7: 轮转覆盖指针(1-4 循环),clear()/init_features() 读写
```

- [ ] **Step 2: 在 init_features 末尾调 _load_next_slot**

在 `init_features` 方法末尾,return 之前加一行:

```python
        self._load_next_slot()   # Step 7: 读回上次覆盖指针(与读.bin同安全窗口)
        self._loaded = True
        print(f"[FaceDB] init_features done: {len(self._features)} face(s)")
        return self._features
```

- [ ] **Step 3: 新增 _load_next_slot / _save_next_slot 私有方法**

在 `init_features` 方法之后、`get_features` 之前插入:

```python
    def _load_next_slot(self):
        """读 _next_slot 指针文件(init_features 内,与读.bin同安全窗口)。
        文件不存在/损坏 → 默认 1。"""
        try:
            with open(_NEXT_SLOT_PATH, 'r') as f:
                v = int(f.read().strip())
            self._next_slot = v if 1 <= v <= 4 else 1
        except Exception:
            self._next_slot = 1

    def _save_next_slot(self):
        """写 _next_slot 指针文件(register 内,与 flush_to_disk 同批写盘)。"""
        try:
            with open(_NEXT_SLOT_PATH, 'w') as f:
                f.write(str(self._next_slot))
        except Exception as e:
            print("[FaceDB] save next_slot failed: %s" % e)
```

- [ ] **Step 4: 新增 register(feature) 方法**

在 `get_features` 之后、`flush_to_disk` 之前插入:

```python
    def register(self, feature):
        """注册特征到 slot(轮转覆盖 B)+ 当场写盘。返回 slot_id(1-4)。

        - 有空 slot:填第一个空 slot(不动 _next_slot 指针)
        - 无空 slot:覆盖 _next_slot 指向的 slot,指针 +1(1→2→3→4→1)
        - 写内存后立刻 flush_to_disk() + _save_next_slot()(试法1)
        """
        slot = None
        for i in range(1, 5):
            if i not in self._features:
                slot = i
                break
        if slot is None:
            slot = self._next_slot
            self._next_slot = self._next_slot % 4 + 1
        self._features[slot] = feature
        self.flush_to_disk()
        self._save_next_slot()
        print("[FaceDB] registered → id%d (flushed)" % slot)
        return slot
```

- [ ] **Step 5: 新增 clear() 方法(删.bin + 删.next_slot + 指针回1)**

在 `clear_disk` 之后、`face_db = _FaceDB()` 之前插入:

```python
    def clear(self):
        """清内存 + 删 .bin + 删 .next_slot + _next_slot 回 1。"""
        import os
        self._features.clear()
        for i in range(1, 5):
            try:
                os.remove(f"{_DB_DIR}/id{i}.bin")
            except Exception:
                pass
        try:
            os.remove(_NEXT_SLOT_PATH)
        except Exception:
            pass
        self._next_slot = 1
        print("[FaceDB] cleared (memory + disk + pointer)")
```

- [ ] **Step 6: 跑 register 相关测试**

```bash
python tests/test_face_register.py
```

预期:face_db 相关的 5 个测试 PASS(test_face_db_register_fills_empty_slot_first / test_face_db_register_rotates_when_full / test_face_db_persists_next_slot / test_face_db_clear_deletes_next_slot_and_resets_pointer / test_runner)。id_registry 相关测试仍 FAIL(文件不存在)。

- [ ] **Step 7: 确认旧测试仍通过**

```bash
python tests/test_face_detect.py && python tests/test_framework.py
```

- [ ] **Step 8: commit**

```bash
git add core/face_db.py tests/test_face_register.py
git commit -m "feat(face_db): add register(rotate+flush) + clear + _next_slot persistence

Step 7 sub-task: face_db.register fills empty slots first, rotates when full
(method B), writes memory + immediately flush_to_disk (trial 1). _next_slot
persisted to /data/fac_db/.next_slot for continuity across resets.

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 3: 新建 core/id_registry.py

**Files:**
- Create: `core/id_registry.py`

- [ ] **Step 1: 创建 core/id_registry.py**

```python
# core/id_registry.py — 可复用注册控制器
#
# K2 = GPIO0 输入(fpioa.set_function(0, FPIOA.GPIO0))。
# 主线程软件边沿检测:按下瞬间置 pending=True(只一次,防按住连触发)。
# AI 线程每帧调 try_register(feature):pending(2秒内) → face_db.register
# + 蜂鸣 + 清 pending。
#
# 不依赖具体 AI 模型:脚本自己提 feature 传入。后续手势/物体脚本复用。

import time
from machine import Pin, FPIOA


class IdRegistry:
    """可复用注册控制器:K2 按键 + face_db.register 协作。

    主线程: poll_k2() 边沿检测,按下置 pending(2秒超时)。
    AI 线程: try_register(feature, buzzer) 消费 pending,调 face_db.register。

    不绑定任何 AI 模型——调用方自己提特征传入。
    后续脚本(手势/物体)复用:只需自己提特征 → 调 id_registry.try_register。
    """

    def __init__(self, fpioa, pin=0, valid_level=0):
        """valid_level:按下时电平(默认 0=低电平有效,K230D BOX K2 上拉+按下接地)。"""
        fpioa.set_function(pin, FPIOA.GPIO0 + pin)
        self._k2 = Pin(pin, Pin.IN, Pin.PULL_UP)
        self._valid_level = valid_level
        self._prev_pressed = False
        self._pending = False
        self._pending_time = 0
        self._last_slot = None

    def poll_k2(self):
        """主线程 task_handler 间隙调。软件边沿:松开→按下 瞬间置 pending。
        只触发一次,按住不松不重复置 pending。"""
        pressed = (self._k2.value() == self._valid_level)
        if pressed and not self._prev_pressed:
            self._pending = True
            self._pending_time = time.ticks_ms()
        self._prev_pressed = pressed

    def try_register(self, feature, buzzer=None):
        """AI 线程每帧调。pending(2秒内)→ face_db.register + 蜂鸣 + 清 pending。
        返回 slot_id(1-4)或 None(没按/超时/失败)。

        feature: 512维 ndarray(由脚本 face_reg.run 提取,不重复 NPU 推理)。
        buzzer: Buzzer 实例或 None(无 buzzer 时静默,守卫安全)。
        """
        if not self._pending:
            return None
        # 2 秒超时:防"按了→走开→别人来→误注册"
        if time.ticks_diff(time.ticks_ms(), self._pending_time) > 2000:
            self._pending = False
            print("[IdRegistry] pending timeout, discarded")
            return None
        self._pending = False
        try:
            from core.face_db import face_db
            slot = face_db.register(feature)
            self._last_slot = slot
            if buzzer is not None:
                buzzer.beep(ms=80)
            return slot
        except Exception as e:
            print("[IdRegistry] register failed: %s" % e)
            if buzzer is not None:
                buzzer.beep(ms=200)
            return None

    @property
    def last_slot(self):
        """上次注册分配的 slot_id(1-4),供 UI 反馈。None=从未注册。"""
        return self._last_slot
```

- [ ] **Step 2: 跑 id_registry 相关测试**

```bash
python tests/test_face_register.py
```

预期:id_registry 相关 5 个测试 PASS。face_detect 接入+main+app_runtime 测试仍 FAIL(还没改)。

- [ ] **Step 3: 确认旧测试仍通过**

```bash
python tests/test_face_detect.py && python tests/test_framework.py
```

- [ ] **Step 4: commit**

```bash
git add core/id_registry.py
git commit -m "feat(id_registry): add reusable K2 registration controller

IdRegistry: software edge-detect on GPIO0, 2s pending timeout,
try_register delegates to face_db.register + buzzer feedback.
Model-agnostic: scripts provide feature, id_registry handles slot+flush.

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 4: face_detect/app.py 接入 id_registry

**Files:**
- Modify: `scripts/face_detect/app.py`

- [ ] **Step 1: 确认当前 app.py 的关键位置(与 backup 的差异)**

当前 app.py 的 `run()` 主循环只有 `time.sleep_ms(lv.task_handler())`,没有 K2 轮询。当前 `face_det_thread` 的 Step 5 识别段在 `database_search` 后面,没有 `try_register`。

- [ ] **Step 2: run() 加 id_registry 初始化 + 主循环 poll_k2**

在 `run(runtime)` 函数内,`running = True` 之前(face_reg 加载之后、AI 线程启动前)加:

```python
    # Step 7: K2 注册控制器(复用 face_db.register + 轮转覆盖)
    from core.id_registry import IdRegistry
    id_registry = IdRegistry(runtime.fpioa, pin=0)
```

然后在主循环的 `while True:` 内、`time.sleep_ms(...)` 前加 `id_registry.poll_k2()`:

```python
    while True:
        os.exitpoint()
        id_registry.poll_k2()              # Step 7: 主线程 K2 边沿检测
        time.sleep_ms(lv.task_handler())
```

具体改法(edit `run()` 末尾 while 块):

```python
    running = True
    time.sleep_ms(100)
    _thread.start_new_thread(face_det_thread, ())
    try:
        while True:
            os.exitpoint()
            id_registry.poll_k2()
            time.sleep_ms(lv.task_handler())
    except BaseException as e:
        sys.print_exception(e)
    running = False
```

- [ ] **Step 3: face_det_thread 识别后加 try_register**

在 `face_det_thread` 内,Step 5 识别段 `matched_id = database_search(...)` 之后、`recognition_results.append(...)` 之前,加一行:

```python
                    matched_id = database_search(feature, db_features)
                    # Step 7: K2 注册(复用刚提的特征,不重复 NPU)
                    id_registry.try_register(feature, runtime.buzzer)
                    recognition_results.append((max_i, matched_id))
```

- [ ] **Step 4: 顶部加 runtime 参数访问声明——确认 face_det_thread 能访问 runtime**

`face_det_thread` 是模块级函数(非方法),当前通过 `global` 访问 `sensor`/`face_det` 等。`runtime` 由 `run(runtime)` 局部传不进去。需要把 `runtime` 也设为全局:

在 `run()` 内,加:

```python
    global running, db_features, id_registry, runtime as _rt
```

但 Python 不允许 `global ... as`。改为:在 `run()` 顶部声明全局,再在模块级新增一个 `_runtime = None`:

在模块顶部(接近 `# 全局变量` 区域,line ~34 附近),加:

```python
_runtime = None  # Step 7: AI 线程访问 runtime.buzzer
```

在 `run()` 内:

```python
def run(runtime):
    global running, db_features, _runtime
    _runtime = runtime
```

在 `face_det_thread` 内:

```python
    try_register_call = id_registry.try_register(feature, _runtime.buzzer)
```

但实际上简单点——把 `runtime` 直接设全局。当前模块没有显式全局变量区,有个 `running` 和 `db_features` 的全局声明。找到 `run()` 的 `global` 行并加 `_rt`:

```python
def run(runtime):
    global running, db_features
    # ...
```

改成:

```python
def run(runtime):
    global running, db_features, _rt
    _rt = runtime
```

在 `face_det_thread` 顶部全局声明加 `_rt`:

```python
def face_det_thread():
    global sensor, face_det, running, fc
    # ...
```

改成:

```python
def face_det_thread():
    global sensor, face_det, running, fc, _rt
```

然后识别段用 `_rt.buzzer`:

```python
                    id_registry.try_register(feature, _rt.buzzer)
```

- [ ] **Step 5: 跑 face_detect 接入测试**

```bash
python tests/test_face_register.py
```

预期:`test_face_detect_run_inits_id_registry` 和 `test_face_detect_ai_thread_calls_try_register` 现在 PASS。main/app_runtime 收尾测试仍 FAIL。

- [ ] **Step 6: 确认旧测试仍通过(改 face_detect 可能影响基线测试)**

```bash
python tests/test_face_detect.py
```

预期:ALL PASS。若 `test_face_detect_baseline_has_no_camerai_ui_dependencies` 因 `id_registry` import 失败,需在 forbidden 列表排除。当前测试只禁 `core.icon_cache` 等,`core.id_registry` 不在禁单 → 应自动通过。

- [ ] **Step 7: commit**

```bash
git add scripts/face_detect/app.py
git commit -m "feat(face_detect): integrate id_registry — K2 poll + try_register

run() main loop calls id_registry.poll_k2() for edge-detect.
face_det_thread reuses Step 5 feature for id_registry.try_register
(zero extra NPU cost). _rt global exposes runtime.buzzer to AI thread.

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 5: main.py 收尾——删 face_detect 跳过 init_app

**Files:**
- Modify: `main.py`

- [ ] **Step 1: 删临时定位分支**

当前 `run_script` 内(line ~106-110):

```python
    # ⚠️ 临时定位：face_detect 跳过 runtime.init_app，由 face_detect.run() 自己
    # 全套 init（对齐裸跑 test_face_baseline_camerai_sensor.py）。验证 reset 框架
    # init_app 是否元凶。验证后恢复。
    if category_id != "face_detect":
        runtime.init_app(category_id, fpioa)
```

改为所有 category 统一走 `init_app`:

```python
    runtime.init_app(category_id, fpioa)
```

同时删 app_runtime import 不再需要的条件注释(看上下文保留 import)。

- [ ] **Step 2: 跑 main.py 收尾测试**

```bash
python tests/test_face_register.py
```

预期:`test_main_no_face_detect_init_app_skip` 现在 PASS。

- [ ] **Step 3: 确认框架测试仍通过**

```bash
python tests/test_framework.py
```

- [ ] **Step 4: commit**

```bash
git add main.py
git commit -m "fix(main): remove face_detect init_app skip — all scripts use init_app

Verified: stable config (no deadlock) confirmed on board. Remove
temporary isolation code that bypassed runtime.init_app for face_detect.

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 6: app_runtime.py 收尾——删 face_detect 跳过 + 恢复 Buzzer

**Files:**
- Modify: `core/app_runtime.py`

- [ ] **Step 1: 删 init_app 中 face_detect 跳过 touch/fonts/services 分支**

当前 `init_app` 内:

```python
        # ⚠️ 临时严格基线对齐：face_detect 定位时跳过 touch/fonts/services，
        # 让 runtime.init_app 尽量等同裸跑 test_face_baseline_camerai_sensor.py。
        # 若稳定，再逐项加回 touch/fonts/host/UI 定位污染源。
        if category_id != "face_detect":
            self._init_touch()
            from core.font_manager import fonts
            try:
                fonts.load_all()
            except Exception as e:
                print("[Runtime] font load warning: %s" % e)
            self._init_services(fpioa)
        else:
            self.host = None
            self.lang = None
            self.config = None
            self.buzzer = None
```

改为所有 category 统一走完整 init:

```python
        self._init_touch()
        from core.font_manager import fonts
        try:
            fonts.load_all()
        except Exception as e:
            print("[Runtime] font load warning: %s" % e)
        self._init_services(fpioa)
```

- [ ] **Step 2: _init_services 恢复 Buzzer 创建**

当前 `_init_services` 内:

```python
        # ⚠️ 临时定位：不创建 Buzzer（PWM0 硬件），验证 PWM 是否与 NPU/DMA/sensor
        # 冲突致卡死。验证后恢复。
        # from hw.buzzer import Buzzer
        # self.buzzer = Buzzer(fpioa, pinx=60, pwm_ch=0, valid=0)
        # self.buzzer.set_enabled(self.config.get('buzzer_enabled', True))
        self.buzzer = None
```

恢复为:

```python
        from hw.buzzer import Buzzer
        self.buzzer = Buzzer(fpioa, pinx=60, pwm_ch=0, valid=0)
        self.buzzer.set_enabled(self.config.get('buzzer_enabled', True))
```

- [ ] **Step 3: 跑 app_runtime 收尾测试**

```bash
python tests/test_face_register.py
```

预期:ALL PASS。

- [ ] **Step 4: 确认所有测试绿**

```bash
python tests/test_face_register.py && python tests/test_face_detect.py && python tests/test_framework.py
```

预期:ALL PASS。

- [ ] **Step 5: commit**

```bash
git add core/app_runtime.py
git commit -m "fix(app_runtime): restore full init_app for face_detect + Buzzer

Remove temporary isolation: touch/fonts/services now init for all
categories including face_detect. Restore Buzzer creation in
_init_services (PWM0 conflict hypothesis to be verified on board).

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 7: 全量测试验证 + 提交

- [ ] **Step 1: 跑所有测试**

```bash
python tests/test_face_register.py && python tests/test_face_detect.py && python tests/test_framework.py
```

预期:ALL PASS。

- [ ] **Step 2: AST 语法检查**

```bash
python -c "import ast; ast.parse(open('core/face_db.py',encoding='utf-8').read()); print('face_db OK')"
python -c "import ast; ast.parse(open('core/id_registry.py',encoding='utf-8').read()); print('id_registry OK')"
python -c "import ast; ast.parse(open('scripts/face_detect/app.py',encoding='utf-8').read()); print('face_detect OK')"
python -c "import ast; ast.parse(open('main.py',encoding='utf-8').read()); print('main OK')"
python -c "import ast; ast.parse(open('core/app_runtime.py',encoding='utf-8').read()); print('app_runtime OK')"
```

- [ ] **Step 3: commit**

```bash
git commit --allow-empty -m "test(step7): all tests green — register tests + existing suites

face_db: register/clear/pointer persist
id_registry: K2 edge-detect + try_register
face_detect: id_registry integration
main/app_runtime: full init_app restored + Buzzer

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### 板端验收

1. 部署全部 5 个文件到 `/sdcard/CamerAi/`:
   - `core/face_db.py`
   - `core/id_registry.py`
   - `scripts/face_detect/app.py`
   - `main.py`
   - `core/app_runtime.py`
2. 上电 → 主菜单 → 点 face_detect → 进 face_detect 不卡(验证 init_app 收尾+buzzer 接回不引入卡死)
3. 对脸按 K2:蜂鸣 80ms + 当场出现彩色框+ID(注册成功,内存即生效)
4. 注册 4 张脸后第 5 次按 K2 覆盖 id1(轮转 B),第 6 次覆盖 id2
5. 退出 face_detect → reset 回菜单 → 再进 face_detect → 已注册人脸仍识别(持久化生效)
6. **核心验证**:注册时当场 flush 不卡(试法1)
7. buzzer 接回后不卡 → 排除 PWM0 为卡死元凶
