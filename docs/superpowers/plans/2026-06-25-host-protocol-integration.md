# 上位机通讯协议接入 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 `comm/host_api.py` 已实现的二进制协议（握手 + 4组ID推送）接入 reset 框架主循环，覆盖主菜单 + 全部脚本（_template/camera/settings/face_detect + 未来扩展），face_detect 识全部脸按 DB 槽位填值。

**Architecture:** 协议逻辑集中框架级——`app_runtime.host_tick(slots)` 每帧调 `host.tick(category_id, slots)`（poll_handshake + 按 category 选类型码 + 组帧发送）。脚本只报 category + slots。face_detect on_frame 改识全部脸：每检测框跑 reg + database_search，匹配的 DB slot 填对应组。

**Tech Stack:** MicroPython / K230 CanMV / lvgl / nncase_runtime / ulab.numpy；host 侧 AST 契约测试（板端模块 Windows 不可导入）。

**设计文档:** `docs/superpowers/specs/2026-06-25-host-protocol-integration-design.md`

---

## 文件结构

| 文件 | 责任 | 改动 |
|---|---|---|
| `comm/host_api.py` | 协议帧组装/发送/握手 | 加 `CATEGORY_TYPE`/`send_id_data`/`tick`；`send_face_data` 改薄封装 |
| `core/app_runtime.py` | 每进程运行时 | `init_menu`/`init_app` 存 `category_id`；加 `host_tick` |
| `main.py` | 启动器 | `run_menu` 循环加 `runtime.host_tick()` |
| `scripts/_template/app.py` | 脚本基类 | `run` 循环加 `runtime.host_tick()` |
| `scripts/camera/app.py` | 相机 | `run` 循环加 `runtime.host_tick()` |
| `scripts/settings/app.py` | 设置 | `run` 循环加 `runtime.host_tick()` |
| `scripts/face_detect/app.py` | 人脸识别 | `on_frame` 改识全部脸 + 构建4槽位 + `host_tick(slots)` |
| `tests/test_host_api.py` | host_api 契约 | 新建 |
| `tests/test_framework.py` | 框架契约 | 扩展 app_runtime host_tick/category_id |
| `tests/test_face_detect_template.py` | face_detect 契约 | 扩展 on_frame 多脸+host_tick；各脚本 host_tick |

⚠️ 板端模块（host_api/app_runtime/脚本 import lvgl 等）在 Windows 不可导入，全部用 AST/源码字符串断言。

---

## Task 1: host_api 加 CATEGORY_TYPE / send_id_data / tick

**Files:**
- Modify: `comm/host_api.py`
- Test: `tests/test_host_api.py`（新建）

- [ ] **Step 1: 写失败的测试（新建 test_host_api.py）**

```python
# tests/test_host_api.py — host_api 协议接入契约（AST，板端模块不可导入）
import ast
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOST_API_PATH = os.path.join(ROOT, "comm", "host_api.py")


def _src():
    with open(HOST_API_PATH, "r", encoding="utf-8") as f:
        return f.read()


def _class_node(tree, name):
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == name:
            return node
    raise AssertionError("Class %s missing" % name)


def _method_node(cls_node, name):
    for n in cls_node.body:
        if isinstance(n, ast.FunctionDef) and n.name == name:
            return n
    raise AssertionError("Method %s missing" % name)


def test_category_type_mapping_covers_all_categories():
    """CATEGORY_TYPE 必须映射所有 reset 框架 category。"""
    src = _src()
    assert "CATEGORY_TYPE" in src, "must define CATEGORY_TYPE mapping"
    for cat, code in [("main_menu", "0x01"), ("settings", "0x01"),
                      ("camera", "0x02"), ("face_detect", "0x03"),
                      ("_template", "0x01")]:
        assert ('"%s"' % cat) in src or ("'%s'" % cat) in src, \
            "CATEGORY_TYPE must cover %s" % cat


def test_send_id_data_exists_with_slots_param():
    """send_id_data(msg_type, slots=None) — 泛化4组发送。"""
    tree = ast.parse(_src(), filename=HOST_API_PATH)
    cls = _class_node(tree, "HostAPI")
    m = _method_node(cls, "send_id_data")
    args = [a.arg for a in m.args.args]
    assert "msg_type" in args, "send_id_data must take msg_type"
    assert "slots" in args, "send_id_data must take slots"


def test_tick_exists_and_calls_poll_and_send():
    """tick(category_id, slots=None): poll_handshake + send_id_data。"""
    src = _src()
    tree = ast.parse(src, filename=HOST_API_PATH)
    cls = _class_node(tree, "HostAPI")
    m = _method_node(cls, "tick")
    args = [a.arg for a in m.args.args]
    assert "category_id" in args, "tick must take category_id"
    seg = ast.get_source_segment(src, m) or ""
    assert "poll_handshake" in seg, "tick must call poll_handshake"
    assert "send_id_data" in seg, "tick must call send_id_data"
    assert "CATEGORY_TYPE" in seg, "tick must look up CATEGORY_TYPE"


def test_send_face_data_delegates_to_send_id_data():
    """send_face_data 保留为薄封装（旧调试备份引用），委托 send_id_data。"""
    src = _src()
    tree = ast.parse(src, filename=HOST_API_PATH)
    cls = _class_node(tree, "HostAPI")
    m = _method_node(cls, "send_face_data")
    seg = ast.get_source_segment(src, m) or ""
    assert "send_id_data" in seg, "send_face_data must delegate to send_id_data"


def test_runner():
    failures = 0
    for name in sorted(n for n in dir() if n.startswith("test_") and n != "test_runner"):
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

- [ ] **Step 2: 运行测试确认失败**

Run: `python tests/test_host_api.py`
Expected: FAIL（CATEGORY_TYPE / send_id_data / tick 不存在）

- [ ] **Step 3: 实现 host_api 扩展**

在 `comm/host_api.py` 的 `HostAPI` 类内，`TYPE_*` 常量之后、`__init__` 之前加：

```python
    # category_id → msg_type 映射（reset 框架 category 与协议类型码对接）
    CATEGORY_TYPE = {
        "main_menu":  TYPE_MAIN_MENU,     # 0x01
        "settings":   TYPE_MAIN_MENU,     # 0x01（复用主菜单）
        "camera":     TYPE_CAMERA,        # 0x02
        "face_detect":TYPE_FACE_DETECT,   # 0x03
        "_template":  TYPE_MAIN_MENU,     # 0x01（默认）
    }
```

注意：`CATEGORY_TYPE` 引用 `TYPE_*` 类属性，必须定义在 `TYPE_*` 之后。

把 `send_face_data` 改为薄封装（替换原方法体）：

```python
    def send_face_data(self, slots):
        """发送4组人脸识别数据（类型0x03）。薄封装 → send_id_data。

        Args:
            slots: list of 4 tuples or None. 详见 send_id_data。
        """
        self.send_id_data(self.TYPE_FACE_DETECT, slots)
```

新增 `send_id_data` 和 `tick`（放在 `send_face_data` 之后、`poll_handshake` 之前）：

```python
    def send_id_data(self, msg_type, slots=None):
        """发送4组ID数据（泛化 send_face_data，所有脚本共用）。

        Args:
            msg_type: 类型码 (int, 1字节)
            slots: list[4]，每元素 None 或 (id,x,y,w,h,conf)。
                   None / 越界 → 该组全0。
                   每组 10 字节: id(1B) + x(2B LE) + y(2B LE)
                                + w(2B LE) + h(2B LE) + conf(1B)
                   总计 40 字节数据载荷。
        """
        buf = bytearray(40)
        for i in range(4):
            off = i * 10
            slot = slots[i] if (slots is not None and i < len(slots)) else None
            if slot is not None:
                fid, x, y, w, h, conf = slot
                buf[off]     = fid & 0xFF
                buf[off + 1] = x & 0xFF
                buf[off + 2] = (x >> 8) & 0xFF
                buf[off + 3] = y & 0xFF
                buf[off + 4] = (y >> 8) & 0xFF
                buf[off + 5] = w & 0xFF
                buf[off + 6] = (w >> 8) & 0xFF
                buf[off + 7] = h & 0xFF
                buf[off + 8] = (h >> 8) & 0xFF
                buf[off + 9] = conf & 0xFF
            # else: 保持 0（未使用槽位全0）
        self.send_frame(msg_type, bytes(buf))

    def tick(self, category_id, slots=None):
        """每帧调：握手轮询 + 按 category 推送4组数据。

        Args:
            category_id: reset 框架 category（"main_menu"/"camera"/...）
            slots: list[4] 或 None。None → 4组全0（主菜单/相机/settings）。
        """
        self.poll_handshake()
        msg_type = self.CATEGORY_TYPE.get(category_id, self.TYPE_MAIN_MENU)
        self.send_id_data(msg_type, slots)
```

- [ ] **Step 4: 运行测试确认通过**

Run: `python tests/test_host_api.py`
Expected: ALL PASS

- [ ] **Step 5: 提交**

```bash
git add comm/host_api.py tests/test_host_api.py
git commit -m "feat(host_api): add CATEGORY_TYPE/send_id_data/tick for framework-level protocol"
```

---

## Task 2: app_runtime 存 category_id + host_tick

**Files:**
- Modify: `core/app_runtime.py`
- Test: `tests/test_framework.py`

- [ ] **Step 1: 写失败的测试（追加到 test_framework.py）**

在 `test_core_init_has_no_legacy_side_effect_imports` 之后、`if __name__` 之前追加：

```python
def test_app_runtime_stores_category_id_in_init_menu():
    """init_menu 必须存 self.category_id='main_menu'（host_tick 用）。"""
    src = open(RUNTIME_PATH, encoding="utf-8").read()
    start = src.find("def init_menu(")
    assert start != -1, "init_menu missing"
    body = src[start:src.find("def ", start + 1)]
    assert "category_id" in body and "main_menu" in body, \
        "init_menu must set self.category_id = 'main_menu'"


def test_app_runtime_stores_category_id_in_init_app():
    """init_app 必须存 self.category_id=category_id（host_tick 用）。"""
    src = open(RUNTIME_PATH, encoding="utf-8").read()
    start = src.find("def init_app(")
    assert start != -1, "init_app missing"
    body = src[start:src.find("def ", start + 1)]
    assert "self.category_id" in body, \
        "init_app must store self.category_id = category_id"


def test_app_runtime_has_host_tick_method():
    """AppRuntime 必须有 host_tick(slots=None) 方法（每帧握手+推送）。"""
    src = open(RUNTIME_PATH, encoding="utf-8").read()
    tree = _parse(RUNTIME_PATH)
    cls = _class_node(tree, "AppRuntime")
    methods = _method_names(cls)
    assert "host_tick" in methods, "AppRuntime must have host_tick method"
    start = src.find("def host_tick(")
    seg = src[start:src.find("def ", start + 1)]
    assert "self.host" in seg and "tick" in seg, \
        "host_tick must call self.host.tick(...)"
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python tests/test_framework.py`
Expected: 3 个新测试 FAIL（category_id/host_tick 不存在）

- [ ] **Step 3: 实现 app_runtime 扩展**

在 `AppRuntime.__init__` 的 `self._sensor_running = False` 之后加：

```python
        self.category_id = None
```

在 `init_menu` 方法体首行（`self.fpioa = fpioa` 之后）加：

```python
        self.category_id = "main_menu"
```

在 `init_app` 方法体首行（`self.fpioa = fpioa` 之后）加：

```python
        self.category_id = category_id
```

在 `cleanup` 方法之前（`_init_services` 之后）加：

```python
    def host_tick(self, slots=None):
        """每帧调：握手轮询 + 按当前 category 推送4组数据。

        slots=None → 4组全0（主菜单/相机/settings）。face_detect 传匹配槽位。
        """
        if self.host is not None:
            self.host.tick(self.category_id, slots)
```

- [ ] **Step 4: 运行测试确认通过**

Run: `python tests/test_framework.py`
Expected: ALL PASS

- [ ] **Step 5: 提交**

```bash
git add core/app_runtime.py tests/test_framework.py
git commit -m "feat(app_runtime): store category_id + add host_tick for per-frame protocol"
```

---

## Task 3: main.py run_menu + _template/camera/settings 接 host_tick

**Files:**
- Modify: `main.py`
- Modify: `scripts/_template/app.py`
- Modify: `scripts/camera/app.py`
- Modify: `scripts/settings/app.py`
- Test: `tests/test_face_detect_template.py`

- [ ] **Step 1: 写失败的测试（追加到 test_face_detect_template.py）**

注意：此测试文件已用于多脚本契约，但 APP_PATH 只指向 face_detect。本任务的"各脚本/主菜单 host_tick"断言改放新文件 `tests/test_host_tick_wiring.py` 更清晰。新建：

```python
# tests/test_host_tick_wiring.py — 主菜单+各脚本主循环必须调 host_tick
import ast
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _src(rel):
    with open(os.path.join(ROOT, rel), "r", encoding="utf-8") as f:
        return f.read()


def _run_fn_body(src, fn_name="run"):
    """返回 run() 函数源码段（从 def run 到下一个顶层 def 之前）。"""
    start = src.find("def %s(" % fn_name)
    assert start != -1, "%s missing" % fn_name
    nxt = src.find("\ndef ", start + 1)
    return src[start:nxt if nxt != -1 else len(src)]


def test_main_run_menu_calls_host_tick():
    src = _src("main.py")
    start = src.find("def run_menu(")
    assert start != -1, "run_menu missing"
    body = src[start:src.find("\ndef ", start + 1)]
    assert "host_tick" in body, "run_menu loop must call runtime.host_tick()"


def test_template_run_calls_host_tick():
    body = _run_fn_body(_src("scripts/_template/app.py"))
    assert "host_tick" in body, "_template run loop must call runtime.host_tick()"


def test_camera_run_calls_host_tick():
    body = _run_fn_body(_src("scripts/camera/app.py"))
    assert "host_tick" in body, "camera run loop must call runtime.host_tick()"


def test_settings_run_calls_host_tick():
    body = _run_fn_body(_src("scripts/settings/app.py"))
    assert "host_tick" in body, "settings run loop must call runtime.host_tick()"


def test_runner():
    failures = 0
    for name in sorted(n for n in dir() if n.startswith("test_") and n != "test_runner"):
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

- [ ] **Step 2: 运行测试确认失败**

Run: `python tests/test_host_tick_wiring.py`
Expected: 4 FAIL（main/_template/camera/settings 未调 host_tick）

- [ ] **Step 3: main.py run_menu 加 host_tick**

在 `main.py` 的 `run_menu` 函数，`while True:` 循环内，`_th = lv.task_handler()` 之后加：

```python
        runtime.host_tick()
```

修改后循环体：
```python
    while True:
        os.exitpoint()
        _th = lv.task_handler()
        runtime.host_tick()
        time.sleep_ms(_th if _th > 0 else 5)
```

- [ ] **Step 4: _template run 加 host_tick**

在 `scripts/_template/app.py` 的 `run` 函数循环内，`Display.show_image(...)` 之后、`time.sleep_ms(lv.task_handler())` 之前加：

```python
        runtime.host_tick()
```

- [ ] **Step 5: camera run 加 host_tick**

在 `scripts/camera/app.py` 的 `run` 函数循环内，`_process_pending_deletes()` 之后、`time.sleep_ms(lv.task_handler())` 之前加：

```python
        runtime.host_tick()
```

- [ ] **Step 6: settings run 加 host_tick**

在 `scripts/settings/app.py` 的 `run` 函数循环内，`time.sleep_ms(lv.task_handler())` 之前加：

```python
        runtime.host_tick()
```

注意 settings 的 `run` 用 `_RUNTIME` 模块变量，但 `run(runtime)` 参数即为 runtime，直接用 `runtime.host_tick()`。

- [ ] **Step 7: 运行测试确认通过**

Run: `python tests/test_host_tick_wiring.py`
Expected: ALL PASS

- [ ] **Step 8: 提交**

```bash
git add main.py scripts/_template/app.py scripts/camera/app.py scripts/settings/app.py tests/test_host_tick_wiring.py
git commit -m "feat(scripts): wire host_tick into menu + _template/camera/settings main loops"
```

---

## Task 4: face_detect on_frame 改识全部脸 + 槽位构建 + host_tick

**Files:**
- Modify: `scripts/face_detect/app.py`
- Test: `tests/test_face_detect_template.py`

- [ ] **Step 1: 写失败的测试（追加到 test_face_detect_template.py）**

在文件末尾 `if __name__` 之前追加：

```python
def test_face_detect_on_frame_recognizes_all_faces():
    """on_frame 必须对每个检测框跑 reg（识全部脸），不再只取 max_i。

    断言 on_frame 源码含遍历 det_boxes 的 reg 循环 + database_search。
    """
    tree = _parse()
    fn = _function_node(tree, "on_frame")
    src = ast.get_source_segment(_src(), fn) or ""
    # 识全部脸：遍历检测框跑 reg（不再只 max_i 单次）
    assert "for i in range(len(det_boxes))" in src or \
           "for i, " in src, \
        "on_frame must loop over all det_boxes to run reg per face"
    assert "database_search" in src, "on_frame must call database_search per face"
    assert "config_preprocess(landms" in src, \
        "on_frame must config_preprocess per-face landmarks"


def test_face_detect_on_frame_builds_four_slots():
    """on_frame 必须构建4槽位 list（slots[mid-1]=...）并调 host_tick(slots)。"""
    tree = _parse()
    fn = _function_node(tree, "on_frame")
    src = ast.get_source_segment(_src(), fn) or ""
    assert "slots = [None, None, None, None]" in src, \
        "on_frame must init 4-slot list"
    assert "slots[mid - 1]" in src or "slots[mid-1]" in src, \
        "on_frame must fill slot by matched id (slots[mid-1])"
    assert "host_tick(slots)" in src, \
        "on_frame must call host_tick(slots)"


def test_face_detect_on_frame_still_supports_k2_register():
    """on_frame 仍保留 K2 注册逻辑（最大脸注册），has_pending + try_register。"""
    tree = _parse()
    fn = _function_node(tree, "on_frame")
    src = ast.get_source_segment(_src(), fn) or ""
    assert "has_pending" in src, "on_frame must keep K2 has_pending check"
    assert "try_register" in src, "on_frame must keep try_register"
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python tests/test_face_detect_template.py`
Expected: 3 个新测试 FAIL（on_frame 还是只识最大脸，无 slots/host_tick）

- [ ] **Step 3: 重写 face_detect on_frame**

替换 `scripts/face_detect/app.py` 中整个 `on_frame` 函数为：

```python
def on_frame(img):
    """Detect on chn2, recognize ALL faces, draw onto chn0 preview, push 4 slots.

    识全部脸：对每个检测框跑 reg + database_search，匹配的 DB slot 填对应组。
    K2 注册仍取最大脸（注册语义不变）。每帧推送4槽位给上位机。
    ⚠️ 多脸 reg 为板端首次验证（坑#16 NPU 累积风险，见 spec 降级方案）。
    """
    if _RUNTIME is None or _face_det is None:
        return
    img_ai = _RUNTIME.sensor.snapshot(chn=CAM_CHN_ID_2)
    img_np = img_ai.to_numpy_ref()
    det_boxes, landms = _face_det.run(img_np)

    recognition_results = []
    slots = [None, None, None, None]
    if det_boxes and landms and _face_reg is not None:
        # 识全部脸：每个检测框跑 reg + 匹配，填对应 DB 槽位
        for i in range(len(det_boxes)):
            try:
                _face_reg.config_preprocess(landms[i])
                feature = _face_reg.run(img_np)
                mid = database_search(feature, _db_features)
                if mid is not None:
                    recognition_results.append((i, mid))
                    det = det_boxes[i]
                    x, y, w, h = det[:4]
                    x = int(x * _face_det.display_size[0] // _face_det.rgb888p_size[0])
                    y = int(y * _face_det.display_size[1] // _face_det.rgb888p_size[1])
                    w = int(w * _face_det.display_size[0] // _face_det.rgb888p_size[0])
                    h = int(h * _face_det.display_size[1] // _face_det.rgb888p_size[1])
                    conf = int(det[4] * 100) if len(det) > 4 else 0
                    if 1 <= mid <= 4:
                        slots[mid - 1] = (mid, x, y, w, h, conf)
            except Exception as e:
                print("[face_detect] recog error: %s" % e)
        # K2 注册：注册当前帧最大脸（注册语义不变）
        if _id_registry is not None and _id_registry.has_pending():
            max_i = max(range(len(det_boxes)),
                        key=lambda j: det_boxes[j][2] * det_boxes[j][3])
            try:
                _face_reg.config_preprocess(landms[max_i])
                feature = _face_reg.run(img_np)
                slot = _id_registry.try_register(feature, _RUNTIME.buzzer)
                if slot is not None:
                    _db_features[slot] = feature
                    recognition_results.append((max_i, slot))
                    if 1 <= slot <= 4:
                        slots[slot - 1] = (slot, 0, 0, 0, 0, 0)
                    _refresh_count()
            except Exception as e:
                print("[face_detect] register error: %s" % e)

    _face_det.draw_result(img, det_boxes, recognition_results)
    if _RUNTIME is not None and _RUNTIME.host is not None:
        _RUNTIME.host_tick(slots)
    gc.collect()
```

- [ ] **Step 4: 运行测试确认通过**

Run: `python tests/test_face_detect_template.py`
Expected: ALL PASS

- [ ] **Step 5: 提交**

```bash
git add scripts/face_detect/app.py tests/test_face_detect_template.py
git commit -m "feat(face_detect): recognize all faces + build 4 slots + host_tick push"
```

---

## Task 5: 全量回归 + 板端验证准备

**Files:**
- 无修改，只跑测试 + 部署清单

- [ ] **Step 1: 跑全部 host 侧测试**

Run:
```bash
python tests/test_host_api.py
python tests/test_framework.py
python tests/test_face_ai.py
python tests/test_face_detect_template.py
python tests/test_host_tick_wiring.py
python tests/test_face_db.py
```
Expected: 全部 ALL PASS

- [ ] **Step 2: 确认改动文件清单**

应改动且已提交的文件：
- `comm/host_api.py`、`core/app_runtime.py`、`main.py`
- `scripts/_template/app.py`、`scripts/camera/app.py`、`scripts/settings/app.py`、`scripts/face_detect/app.py`
- `tests/test_host_api.py`（新）、`tests/test_host_tick_wiring.py`（新）、`tests/test_framework.py`、`tests/test_face_detect_template.py`

Run: `git status` + `git log --oneline -6`
Expected: 工作区干净（除本任务前已有的未提交改动：app_runtime.py/face_db.py/face_detect app.py 的旧浮层改动——这些与本任务合并提交，见 Step 3 说明）

- [ ] **Step 3: 板端部署清单**

部署到 `/sdcard/CamerAi/`（覆盖）：
- `comm/host_api.py`
- `core/app_runtime.py`
- `main.py`
- `scripts/_template/app.py`
- `scripts/camera/app.py`
- `scripts/settings/app.py`
- `scripts/face_detect/app.py`

硬断电后验证矩阵：
1. **主菜单**：进主菜单 → 上位机收到类型0x01 + 4组全0（握手应答 "Play Application"）
2. **settings**：进设置 → 上位机收到类型0x01 + 4组全0
3. **camera**：进相机 → 上位机收到类型0x02 + 4组全0
4. **face_detect 基础**：进人脸识别（无注册）→ 上位机收到类型0x03 + 4组全0
5. **face_detect 注册+识多人**：K2 注册2人（张三ID1、李四ID2）→ 两人同画面 → 上位机收到 slot1+slot2 同时填值，slot3/4 全0
6. **NPU 稳定性**：face_detect 持续运行，观察是否丢帧/卡死（多脸 reg 首次验证，坑#16 风险点）

- [ ] **Step 4: 板端验证后更新记录**

板端通过后：
- 更新 `项目记录.md`（协议接入 + 多脸识别验收）
- 更新 memory（`camerai-script-template.md` 或新建协议条目）
- 提交记录

如板端 face_detect 多脸卡死：不要盲目改，走 systematic-debugging 定位根因（可能降频降级，另开任务，不在本计划）。
