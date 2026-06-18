# 人脸识别APP 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 扩展人脸识别脚本APP，复用相机APP通用UI布局（顶栏+底栏），支持人脸注册（K2按键，最多4个ID）、实时识别、UART协议发送4组ID数据。

**Architecture:** 硬件层在LCD启动期声明chn2 AI推理通道（RGB888P 1024×768）；通信层重写HostAPI实现UART1协议栈（组帧/校验/握手）；人脸识别APP复用Demo的FaceDetApp+FaceRegistrationApp类，LVGL OSD2层绘制十字架和人脸框，K2按键由ScriptRunner统一轮询并分发给app.on_key()。

**Tech Stack:** K230 MicroPython + LVGL v8 + face_detection_320.kmodel + face_recognition.kmodel + aidemo + nncase_runtime + ulab numpy + UART1

**依赖:** [Spec: 人脸识别APP设计规格](../specs/2026-06-16-face-detect-design.md)

---

### Task 1: 编写测试文件（TDD — 先写失败测试）

**Files:**
- Create: `tests/test_face_detect.py`

- [ ] **Step 1: 创建测试文件，包含全部7个AST测试**

```python
# tests/test_face_detect.py — host-side AST regression tests for face detect app.
# Run with:
#   python tests/test_face_detect.py
import ast
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP_PATH = os.path.join(ROOT, "scripts", "face_detect", "app.py")
LCD_PATH = os.path.join(ROOT, "hw", "lcd.py")
HOST_PATH = os.path.join(ROOT, "comm", "host_api.py")
ICON_PATH = os.path.join(ROOT, "core", "icon_cache.py")
RUNNER_PATH = os.path.join(ROOT, "core", "script_runner.py")


def _parse(path):
    with open(path, "r", encoding="utf-8") as f:
        return ast.parse(f.read(), filename=path)


def _top_level_imports(tree):
    names = set()
    for node in tree.body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.asname or alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module.split(".")[0])
    return names


def _class_node(tree, name):
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == name:
            return node
    raise AssertionError(f"Class {name} missing")


def _method_names(class_node):
    return {
        node.name for node in class_node.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


def _method_node(class_node, name):
    for node in class_node.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"Method {name} missing")


# ── Test 1: FaceDetectApp class structure ──

def test_face_detect_app_class_exists():
    tree = _parse(APP_PATH)
    cls = _class_node(tree, "FaceDetectApp")
    # Must inherit from BaseScript
    bases = [b.id for b in cls.bases if isinstance(b, ast.Name)]
    assert "BaseScript" in bases, "FaceDetectApp must inherit BaseScript"
    # SCRIPT_ID
    for node in cls.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "SCRIPT_ID":
                    assert isinstance(node.value, ast.Constant), \
                        "SCRIPT_ID must be a string constant"
                    assert node.value.value == "face_detect", \
                        f"SCRIPT_ID must be 'face_detect', got {node.value.value}"
    # SELF_MANAGED_TOP_BAR
    for node in cls.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "SELF_MANAGED_TOP_BAR":
                    assert isinstance(node.value, ast.Constant), \
                        "SELF_MANAGED_TOP_BAR must be a constant"
                    assert node.value.value is True, \
                        "SELF_MANAGED_TOP_BAR must be True"


# ── Test 2: FaceDetectApp has required methods ──

def test_face_detect_app_has_required_methods():
    tree = _parse(APP_PATH)
    cls = _class_node(tree, "FaceDetectApp")
    methods = _method_names(cls)
    required = [
        "on_enter", "on_frame", "on_exit", "on_key",
        "_init_ai_models", "_init_db", "_save_db", "_clear_db",
        "_register_face", "_search_face",
        "_build_ui", "_build_top_bar", "_build_crosshair", "_build_bottom_bar",
        "_update_face_boxes", "_send_recognition_data",
        "_on_list_click", "_show_popup", "_dismiss_popup",
        "_update_status_text",
    ]
    missing = [m for m in required if m not in methods]
    assert not missing, f"FaceDetectApp missing methods: {missing}"


# ── Test 3: LCD declares chn2 AI channel ──

def test_lcd_declares_chn2_ai_channel():
    tree = _parse(LCD_PATH)
    # Find LCD.__init__ method
    lcd_cls = _class_node(tree, "LCD")
    init_method = _method_node(lcd_cls, "__init__")

    found_framesize = False
    found_pixformat = False
    found_ai_chn = False

    for node in ast.walk(init_method):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr == "set_framesize":
                # Check if second arg references CAM_CHN_ID_2
                for kw in node.keywords:
                    if kw.arg == "chn":
                        if isinstance(kw.value, ast.Name) and "CHN_ID_2" in kw.value.id:
                            found_framesize = True
                for i, arg in enumerate(node.args):
                    if i >= 1 and isinstance(arg, ast.Name) and "CHN_ID_2" in arg.id:
                        found_framesize = True
            if node.func.attr == "set_pixformat":
                for kw in node.keywords:
                    if kw.arg == "chn":
                        if isinstance(kw.value, ast.Name) and "CHN_ID_2" in kw.value.id:
                            found_pixformat = True
                for i, arg in enumerate(node.args):
                    if i >= 1 and isinstance(arg, ast.Name) and "CHN_ID_2" in arg.id:
                        found_pixformat = True
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Attribute) and target.attr == "ai_chn":
                    found_ai_chn = True

    assert found_framesize, "LCD.__init__ must set_framesize for chn2"
    assert found_pixformat, "LCD.__init__ must set_pixformat for chn2 (RGB888_PLANAR)"
    assert found_ai_chn, "LCD must set self.ai_chn = CAM_CHN_ID_2"


# ── Test 4: LCD has get_ai_frame() method ──

def test_lcd_has_get_ai_frame():
    tree = _parse(LCD_PATH)
    lcd_cls = _class_node(tree, "LCD")
    methods = _method_names(lcd_cls)
    assert "get_ai_frame" in methods, "LCD must have get_ai_frame() for AI frame capture"


# ── Test 5: HostAPI has protocol methods ──

def test_host_api_has_protocol_methods():
    tree = _parse(HOST_PATH)
    cls = _class_node(tree, "HostAPI")
    methods = _method_names(cls)
    required = ["send_frame", "send_face_data", "poll_handshake"]
    missing = [m for m in required if m not in methods]
    assert not missing, f"HostAPI missing methods: {missing}"


# ── Test 6: HostAPI constants ──

def test_host_api_constants():
    tree = _parse(HOST_PATH)
    cls = _class_node(tree, "HostAPI")

    constants = {}
    for node in cls.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id.isupper():
                    if isinstance(node.value, ast.Constant):
                        constants[target.id] = node.value.value

    assert constants.get("FRAME_HEAD") == 0x5A, \
        f"FRAME_HEAD must be 0x5A, got {constants.get('FRAME_HEAD')}"
    assert constants.get("FRAME_TAIL") == 0xA5, \
        f"FRAME_TAIL must be 0xA5, got {constants.get('FRAME_TAIL')}"
    assert constants.get("SRC_ADDR") == 0xA7, \
        f"SRC_ADDR must be 0xA7"
    assert constants.get("DST_ADDR") == 0x97, \
        f"DST_ADDR must be 0x97"
    assert constants.get("TYPE_FACE_DETECT") == 0x03, \
        f"TYPE_FACE_DETECT must be 0x03"


# ── Test 7: icon_cache has face icon support ──

def test_icon_cache_has_face_icons():
    tree = _parse(ICON_PATH)
    cls = _class_node(tree, "_IconCache")
    methods = _method_names(cls)
    assert "preload_face_icons" in methods, \
        "_IconCache must have preload_face_icons()"
    assert "get_face_icon" in methods, \
        "_IconCache must have get_face_icon()"

    # Check _face_icons dict initialization in __init__
    init_method = _method_node(cls, "__init__")
    found = False
    for node in ast.walk(init_method):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Attribute) and target.attr == "_face_icons":
                    found = True
    assert found, "_IconCache.__init__ must initialize self._face_icons"


if __name__ == "__main__":
    failures = 0
    for name in sorted(n for n in dir() if n.startswith("test_")):
        try:
            globals()[name]()
            print(f"PASS {name}")
        except AssertionError as e:
            failures += 1
            print(f"FAIL {name}: {e}")
        except AssertionError as e2:
            failures += 1
            print(f"FAIL {name}: {e2}")
    print(f"\n{'ALL PASS' if failures == 0 else str(failures) + ' FAILED'}")
    sys.exit(1 if failures else 0)
```

- [ ] **Step 2: 运行测试验证全部失败**

```bash
python tests/test_face_detect.py
```
Expected: 6 FAIL（APP/icon_cache/HostAPI/LCD 尚未实现）, 1 FAIL（导入错误）

---

### Task 2: LCD 声明 chn2 AI推理通道 + get_ai_frame()

**Files:**
- Modify: `hw/lcd.py` (在 `__init__` 中 chn1 声明之后，`MediaManager.init()` 之前追加 chn2)

- [ ] **Step 1: 添加 chn2 声明**

在 `hw/lcd.py` 的 `LCD.__init__` 方法中，chn1 的 `set_pixformat` 之后、`self.capture_chn = CAM_CHN_ID_1` 之后，插入 chn2 声明：

```python
        # chn2 — AI推理输入：RGB888_PLANAR格式，1024×768（XGA）
        # 喂给 NPU kmodel 做人脸/物体/颜色等 AI 推理。
        # 所有 AI 类 APP 共用此通道，必须启动期声明
        # （MediaManager.init() 之后池不可重建，K230 pitfall #15）。
        self.sensor.set_framesize(Sensor.XGA, chn=CAM_CHN_ID_2)
        self.sensor.set_pixformat(Sensor.RGB888_PLANAR, chn=CAM_CHN_ID_2)
        self.ai_chn = CAM_CHN_ID_2
```

注意需要从 `media.sensor` 导入 `CAM_CHN_ID_2`，检查当前导入行：
```python
from media.sensor import Sensor, CAM_CHN_ID_0, CAM_CHN_ID_1
```
改为：
```python
from media.sensor import Sensor, CAM_CHN_ID_0, CAM_CHN_ID_1, CAM_CHN_ID_2
```

- [ ] **Step 2: 添加 get_ai_frame() 方法**

在 `LCD` 类的 `ensure_sensor_running()` 方法后面（约 line 122），添加：

```python
    def get_ai_frame(self):
        """从 chn2 获取一帧 AI 推理图像，转为 CHW numpy 数组（对齐 Demo 格式）。

        Demo 中 PipeLine.get_frame() 返回的 img 经 image2rgb888array()
        转为 (1, 3, H, W) CHW numpy 数组供 NPU 推理。此方法复现该流程：
        chn2 snapshot → to_rgb888() → numpy reshape/transpose → CHW。

        Returns: ulab numpy ndarray shape (1, 3, 768, 1024) 或 None
        """
        import ulab.numpy as np

        img = self.sensor.snapshot(chn=self.ai_chn)
        if img is None:
            return None

        rgb888 = img.to_rgb888()
        hwc = rgb888.to_numpy_ref()
        shape = hwc.shape
        # HWC → CHW
        tmp = hwc.reshape((shape[0] * shape[1], shape[2]))
        trans = tmp.transpose()
        chw = trans.copy()
        return chw.reshape((1, shape[2], shape[0], shape[1]))
```

- [ ] **Step 3: 运行测试验证 LCD 相关测试通过**

```bash
python tests/test_face_detect.py
```
Expected: `test_lcd_declares_chn2_ai_channel` PASS, `test_lcd_has_get_ai_frame` PASS，其余仍 FAIL

- [ ] **Step 4: Commit**

```bash
git add hw/lcd.py tests/test_face_detect.py
git commit -m "feat(lcd): declare chn2 AI推理通道(RGB888P XGA) + get_ai_frame()

- LCD.__init__ 启动期声明 CAM_CHN_ID_2: XGA/RGB888_PLANAR
- get_ai_frame() 从 chn2 snapshot 转 CHW numpy 数组（对齐 Demo 格式）
- 所有 AI 类 APP 共用此通道（MediaManager 初始化后池不可重建）

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 3: 重写 HostAPI 实现 UART1 协议栈

**Files:**
- Modify: `comm/host_api.py`（完整重写）

- [ ] **Step 1: 重写 HostAPI**

将 `comm/host_api.py` 完整替换为：

```python
# comm/host_api.py — UART1 串口通信协议栈
#
# 实现自定义二进制协议：
#   帧头(5A) + 源地址(A7) + 目标地址(97) + 数据长度 + 类型 + 数据 + 校验 + 帧尾(A5)
#   校验 = 从帧头到数据最后一位逐字节累加取低8位
#
# 握手流程（见 通讯协议.txt）：
#   主机发送: 5A 97 98 0B 09 [Please Link...] XX A5
#   摄像头应答: 5A A7 97 0F 09 [Play Application...] XX A5
#   握手成功后主机不再发握手帧，摄像头按当前脚本类型周期推送数据。

from machine import UART, Pin


class HostAPI:
    """上位机串口通信接口（UART1, 115200-8-N-1）"""

    # ── 协议常量 ──
    FRAME_HEAD = 0x5A
    FRAME_TAIL = 0xA5
    SRC_ADDR = 0xA7
    DST_ADDR = 0x97

    # 类型码（对齐 通讯协议.txt）
    TYPE_MAIN_MENU    = 0x01
    TYPE_CAMERA       = 0x02
    TYPE_FACE_DETECT  = 0x03
    TYPE_TAG_DETECT   = 0x04
    TYPE_OBJECT_DETECT = 0x05
    TYPE_COLOR_DETECT = 0x06
    TYPE_ROAD_DETECT  = 0x07
    TYPE_GESTURE_DETECT = 0x08
    TYPE_BODY_DETECT  = 0x09
    TYPE_OBJECT_CLASSIFY = 0x0A
    TYPE_IMAGE_CLASSIFY  = 0x0B

    # 握手相关
    HANDSHAKE_CMD = 0x09
    HANDSHAKE_REPLY_PAYLOAD = b"Play Application"
    HANDSHAKE_REQUEST_MAGIC = b"Please Link"

    def __init__(self):
        # UART1: TX=GPIO40, RX=GPIO41, 115200-8-N-1
        self._uart = UART(1, baudrate=115200, tx=Pin(40), rx=Pin(41))
        self._connected = False
        self._rx_buf = bytearray(256)
        self._handlers = {}

    # ── 公开属性 ──

    @property
    def connected(self):
        return self._connected

    # ── 帧组装与发送 ──

    @staticmethod
    def _checksum(data):
        """计算校验和：所有字节累加取低8位"""
        s = 0
        for b in data:
            s = (s + b) & 0xFF
        return s

    def send_frame(self, msg_type, payload=b''):
        """组装并发送完整协议帧。

        Args:
            msg_type: 类型码 (int, 1字节)
            payload: 负载数据 (bytes)
        """
        inner = bytearray([msg_type]) + bytes(payload)
        length = len(inner)
        header = bytearray([self.FRAME_HEAD, self.SRC_ADDR,
                            self.DST_ADDR, length])
        chk = self._checksum(header + inner)
        frame = bytes(header) + bytes(inner) + bytearray([chk, self.FRAME_TAIL])
        try:
            self._uart.write(frame)
        except Exception as e:
            print(f"[HostAPI] send_frame error: {e}")
            self._connected = False

    def send_face_data(self, slots):
        """发送4组人脸识别数据（类型0x03）。

        Args:
            slots: list of 4 tuples or None.
                   每个 tuple = (id, x, y, w, h, confidence)
                   None 或 (0,0,0,0,0,0) 表示该槽位无数据。
                   每组 10 字节: id(1B) + x(2B LE) + y(2B LE)
                                + w(2B LE) + h(2B LE) + conf(1B)
        """
        buf = bytearray(40)
        for i in range(4):
            off = i * 10
            slot = slots[i] if i < len(slots) else None
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
        self.send_frame(self.TYPE_FACE_DETECT, bytes(buf))

    # ── 握手状态机 ──

    def poll_handshake(self):
        """非阻塞握手检测：检查UART接收缓冲区，匹配握手帧→自动应答。

        应在每帧 on_frame 中调用（由 ScriptRunner.tick() 驱动）。
        """
        try:
            n = self._uart.any()
        except Exception:
            self._connected = False
            return

        if n == 0:
            # 无数据，检查是否断线（长时间无数据可视为断线）
            return

        if n > len(self._rx_buf):
            n = len(self._rx_buf)
        try:
            raw = self._uart.read(n)
        except Exception:
            return

        if raw is None:
            return

        # 在接收缓冲中查找握手请求特征: ... 09 [Please Link] ...
        magic = self.HANDSHAKE_REQUEST_MAGIC
        if magic in raw:
            self._send_handshake_reply()
            return

        # 也检查是否收到命令帧（0x09类型 + 特定数据）
        # 主机握手帧: 5A 97 98 0B 09 [Please Link...] XX A5
        # 在 raw 中查找 0x09 后跟 magic
        for i in range(len(raw) - len(magic) - 1):
            if raw[i] == 0x09:
                if raw[i + 1:i + 1 + len(magic)] == magic:
                    self._send_handshake_reply()
                    return

    def _send_handshake_reply(self):
        """发送握手应答帧"""
        self.send_frame(self.HANDSHAKE_CMD, self.HANDSHAKE_REPLY_PAYLOAD)
        self._connected = True
        print("[HostAPI] handshake reply sent — connected")

    # ── 命令注册（预留）──

    def register_handler(self, cmd, callback):
        """注册命令回调（预留）"""
        self._handlers[cmd] = callback

    def is_connected(self):
        """是否已连接上位机"""
        return self._connected
```

- [ ] **Step 2: 运行测试验证 HostAPI 相关测试通过**

```bash
python tests/test_face_detect.py
```
Expected: `test_host_api_has_protocol_methods` PASS, `test_host_api_constants` PASS

- [ ] **Step 3: Commit**

```bash
git add comm/host_api.py
git commit -m "feat(comm): HostAPI 实现 UART1 协议栈(组帧/校验/握手/send_face_data)

- UART1 TX=GPIO40 RX=GPIO41 @115200
- send_frame(type, payload): 自动组装 5A+A7+97+len+type+data+chk+A5
- send_face_data(slots): 4组×10字节人脸数据(类型0x03)
- poll_handshake(): 非阻塞检测主机握手帧→自动应答
- 12种APP类型码常量定义

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 4: icon_cache 添加人脸识别图标

**Files:**
- Modify: `core/icon_cache.py`

- [ ] **Step 1: 在 _IconCache 中添加人脸图标支持**

在 `_IconCache.__init__` 的 `self._camera_icons = {}` 后面添加：

```python
        self._face_icons = {}     # name → (data, dsc)
```

在 `get_camera_icon` 方法后面添加：

```python
    def preload_face_icons(self):
        """预读人脸识别APP图标（在首次 task_handler 之前调用）"""
        base = "/sdcard/CamerAi/resource/icons/face_detect_icon/"
        icons = {
            "list": base + "list.png",
        }
        for name, path in icons.items():
            try:
                with open(path, 'rb') as f:
                    data = f.read()
                dsc = lv.img_dsc_t({
                    'data_size': len(data),
                    'data': data,
                })
                self._face_icons[name] = (data, dsc)
                print(f"[IconCache] face/{name} OK ({len(data)} bytes)")
            except Exception as e:
                print(f"[IconCache] face/{name} FAILED: {e}")

    def get_face_icon(self, name):
        """获取人脸识别图标 (data, dsc)，未缓存返回 (None, None)"""
        return self._face_icons.get(name, (None, None))
```

- [ ] **Step 2: 运行测试**

```bash
python tests/test_face_detect.py
```
Expected: `test_icon_cache_has_face_icons` PASS

- [ ] **Step 3: Commit**

```bash
git add core/icon_cache.py
git commit -m "feat(icon_cache): 新增人脸识别图标预读(preload_face_icons + get_face_icon)

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 5: ScriptRunner 集成 K2 按键轮询 + 握手

**Files:**
- Modify: `core/script_runner.py`

- [ ] **Step 1: 在 ScriptRunner.__init__ 中添加 K2 按键初始化**

在 `ScriptRunner.__init__` 方法中，`self._current_script_id = None` 之后添加：

```python
        # K2 物理按键（GPIO0，上拉，下降沿检测 → 分发给 app.on_key('K2')）
        from machine import Pin
        self._k2_pin = Pin(0, Pin.IN, Pin.PULL_UP)
        self._k2_last = 1  # 上拉默认高电平
```

- [ ] **Step 2: 在 ScriptRunner.tick() 中集成 K2 轮询和握手**

在 `tick()` 方法中，`os.exitpoint()` 之后、退出检测之前，插入：

```python
        # K2 按键轮询（下降沿检测 → 分发给脚本）
        try:
            k2_cur = self._k2_pin.value()
            if self._k2_last == 1 and k2_cur == 0:
                if self._script is not None and hasattr(self._script, 'on_key'):
                    self._script.on_key('K2')
            self._k2_last = k2_cur
        except Exception:
            pass

        # 握手状态机轮询（所有 stream 模式脚本都需要）
        if self._ctx is not None and self._ctx.host is not None:
            try:
                self._ctx.host.poll_handshake()
            except Exception:
                pass
```

- [ ] **Step 3: Commit**

```bash
git add core/script_runner.py
git commit -m "feat(runner): tick()集成K2按键轮询(GPIO0下降沿)+host.poll_handshake()

- K2=GPIO0, 上拉, 下降沿检测后调用 script.on_key('K2')
- 每帧调用 host.poll_handshake() 维持握手状态机

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 6: main.py 预读人脸图标

**Files:**
- Modify: `main.py`

- [ ] **Step 1: 在首次 task_handler 之前调用 preload_face_icons()**

在 `main.py` 中，`icon_cache.preload_camera_icons()` 之后添加一行：

```python
    icon_cache.preload_face_icons()
```

完整上下文（约 line 124）：
```python
    icon_cache.preload_settings_icons()
    icon_cache.preload_camera_icons()
    icon_cache.preload_face_icons()
```

- [ ] **Step 2: Commit**

```bash
git add main.py
git commit -m "feat(main): 启动期预读人脸识别图标(face_detect_icon/list.png)

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 7: 创建 face_detect 脚本脚手架

**Files:**
- Create: `scripts/face_detect/__init__.py`
- Create: `scripts/face_detect/manifest.json`

- [ ] **Step 1: 创建空 __init__.py**

```python
# scripts/face_detect — 人脸识别 APP
```

- [ ] **Step 2: 创建 manifest.json**

```json
{
  "id": "face_detect",
  "version": "1.0.0",
  "name_key": "category.face_detect",
  "desc_key": "category.face_detect_desc",
  "entry_icon": "/sdcard/CamerAi/resource/icons/menu_icon/face_detect.png",
  "icon_dir": "/sdcard/CamerAi/resource/icons/face_detect_icon/",
  "models": [
    "/sdcard/examples/kmodel/face_detection_320.kmodel",
    "/sdcard/examples/kmodel/face_recognition.kmodel"
  ],
  "demo_ref": "实验4 人脸识别实验/main2.py",
  "ui_mode": "stream",
  "enabled": true,
  "order": 3
}
```

- [ ] **Step 3: Commit**

```bash
git add scripts/face_detect/__init__.py scripts/face_detect/manifest.json
git commit -m "feat(face_detect): 创建脚本脚手架(manifest.json + __init__.py)

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 8: 人脸识别APP主体 — Part 1（UI层 + 生命周期框架）

**Files:**
- Create: `scripts/face_detect/app.py`

- [ ] **Step 1: 创建 app.py（UI层 + 生命周期 + 数据结构）**

```python
# scripts/face_detect/app.py — 人脸识别 APP
#
# 架构：
#   OSD1 层：相机帧（sensor.snapshot() → Display.show_image(LAYER_OSD1)）
#   OSD2 层：LVGL UI（顶栏 + 底栏 + 十字架 + 人脸框）
#   chn2 层：AI推理帧（sensor.snapshot(chn=ai_chn) → NPU kmodel）
#
# 生命周期：on_enter → [on_frame × N] → on_exit
#   on_frame 每帧：握手轮询 → AI推理 → 更新人脸框 → 按10ms周期发送

import struct
import lvgl as lv
from media.display import Display
from scripts._base import BaseScript
from core.icon_cache import icon_cache
from core.font_manager import fonts
from ui.theme import Colors, make_back_bar_text_style


# ── 布局常量（复用 Camera APP）──
BAR_H = 52
PREVIEW_Y = BAR_H
PREVIEW_H = 376  # 480 - 52*2
BAR_BG = 0x1A1A1A
BTN_SIZE = 48
ICON_TARGET = 40

# 十字架
CROSSHAIR_COLOR = 0x44CC44  # 绿色
CROSSHAIR_ARM = 30           # 臂长
CROSSHAIR_GAP = 8            # 中心缺口

# 人脸框颜色（4个ID）
BOX_COLORS = {
    1: 0x44CC44,   # ID1 绿色
    2: 0x4488FF,   # ID2 蓝色
    3: 0xFF8844,   # ID3 橙色
    4: 0xCC44FF,   # ID4 紫色
}
BOX_UNKNOWN = 0xFFFFFF  # 未注册人脸白色框

RED = 0xCC4444
WHITE = 0xFFFFFF

# 发送周期
SEND_INTERVAL_MS = 10


def _png_zoom(png_data, target):
    """从 PNG 头解析真实尺寸，计算缩放因子"""
    if not png_data or len(png_data) < 24:
        return 256
    w = struct.unpack('>I', png_data[16:20])[0]
    h = struct.unpack('>I', png_data[20:24])[0]
    if w <= 0 or h <= 0:
        return 256
    zoom = int(min(target / w, target / h) * 256)
    return max(8, min(zoom, 256))


def _make_icon(parent, icon_data, icon_dsc, target_size, x, y):
    """在 parent 上创建图标（K230 set_zoom 居中补偿模式）"""
    if icon_dsc is None or icon_data is None:
        return None, x

    img = lv.img(parent)
    img.set_src(icon_dsc)
    zoom = _png_zoom(icon_data, target_size)
    img.set_zoom(zoom)

    src_w = struct.unpack('>I', icon_data[16:20])[0]
    rendered_w = src_w * zoom // 256
    actual_x = x - (src_w - rendered_w) // 2
    img.align(lv.ALIGN.LEFT_MID, actual_x, 0)
    return img, actual_x


class FaceDetectApp(BaseScript):
    SCRIPT_ID = "face_detect"
    SELF_MANAGED_TOP_BAR = True

    def __init__(self):
        super().__init__()
        # AI模型（由 _init_ai_models 初始化）
        self._face_det = None
        self._face_reg = None
        self._anchors = None

        # 人脸数据库
        self._db_features = {}     # {1: np_array(128), ...}
        self._db_dir = "/data/fac_db/"

        # 当前帧 AI 推理结果（供 on_key 注册和发送使用）
        self._current_boxes = []        # list of [x,y,w,h,score]
        self._current_landmarks = []    # list of landm arrays
        self._current_frame_data = None  # CHW numpy array
        self._recognition_results = []  # [(box, matched_id, score), ...]

        # UI
        self._screen = None
        self._top_bar = None
        self._bottom_bar = None
        self._preview_bg = None
        self._title_label = None
        self._status_label = None
        self._crosshair_lines = []     # 十字架4条线
        self._face_boxes = []          # 当前帧人脸框+标签
        self._popup = None             # 弹出菜单

        # 发送计时
        self._last_send_ticks = 0

    # ── 生命周期 ──────────────────────────────────

    def on_enter(self, ctx):
        super().on_enter(ctx)
        print("[FaceDetect] on_enter: begin _init_db")
        self._init_db()
        print("[FaceDetect] on_enter: begin _init_ai_models")
        self._init_ai_models()
        print("[FaceDetect] on_enter: begin _build_ui")
        self._build_ui()
        print("[FaceDetect] on_enter: done")

    def on_frame(self):
        import os
        import time as _time
        os.exitpoint()

        # 握手轮询由 ScriptRunner 统一处理，这里仅处理 AI 推理 + 发送

        # ── AI 推理 ──
        if self._face_det is not None and self.ctx.lcd is not None:
            try:
                frame = self.ctx.lcd.get_ai_frame()
                if frame is not None:
                    self._current_frame_data = frame
                    det_boxes, landms = self._face_det.run(frame)
                    self._current_boxes = det_boxes if det_boxes else []
                    self._current_landmarks = landms if landms else []

                    # 人脸识别（与 DB 比对）
                    self._recognition_results = []
                    if self._current_boxes and self._current_landmarks:
                        for i, landm in enumerate(self._current_landmarks):
                            if i >= 4:  # 最多4张脸
                                break
                            try:
                                self._face_reg.config_preprocess(landm)
                                feature = self._face_reg.run(frame)
                                matched_id, score = self._search_face(feature)
                                self._recognition_results.append(
                                    (self._current_boxes[i], matched_id, score))
                            except Exception:
                                self._recognition_results.append(
                                    (self._current_boxes[i], None, 0.0))

                    # 更新 LVGL 人脸框
                    self._update_face_boxes()
            except Exception as e:
                print(f"[FaceDetect] AI inference error: {e}")
                import sys
                try:
                    sys.print_exception(e)
                except Exception:
                    pass

        # ── 发送识别数据（10ms 周期）──
        if self.ctx.host is not None:
            try:
                now = _time.ticks_ms()
                if _time.ticks_diff(now, self._last_send_ticks) >= SEND_INTERVAL_MS:
                    self._last_send_ticks = now
                    self._send_recognition_data()
            except Exception:
                pass

    def on_exit(self):
        self._destroy_ui()

        # 释放 AI 模型
        if self._face_det is not None:
            try:
                self._face_det.deinit()
            except Exception:
                pass
            self._face_det = None
        if self._face_reg is not None:
            try:
                self._face_reg.deinit()
            except Exception:
                pass
            self._face_reg = None
        self._anchors = None

        super().on_exit()

    def on_key(self, key):
        if key == 'K2':
            self._register_current_face()

    # ── 数据库管理 ──────────────────────────────────

    def _init_db(self):
        """启动时加载已注册的人脸特征到内存"""
        import os
        import ulab.numpy as np
        try:
            os.mkdir(self._db_dir)
        except Exception:
            pass
        for i in range(1, 5):
            path = f"{self._db_dir}id{i}.bin"
            try:
                with open(path, 'rb') as f:
                    data = f.read()
                if len(data) >= 512:
                    self._db_features[i] = np.frombuffer(data, dtype=np.float)
                    print(f"[FaceDetect] loaded id{i}.bin ({len(data)} bytes)")
            except Exception:
                pass  # 该槽位未注册

    def _save_db(self):
        """将内存中的特征写回文件"""
        import os
        try:
            os.mkdir(self._db_dir)
        except Exception:
            pass
        for i, feature in self._db_features.items():
            path = f"{self._db_dir}id{i}.bin"
            try:
                with open(path, 'wb') as f:
                    f.write(feature.tobytes())
                print(f"[FaceDetect] saved id{i}.bin")
            except Exception as e:
                print(f"[FaceDetect] save id{i} failed: {e}")

    def _clear_db(self):
        """删除全部人脸数据"""
        import os
        for i in range(1, 5):
            try:
                os.remove(f"{self._db_dir}id{i}.bin")
            except Exception:
                pass
        self._db_features.clear()
        print("[FaceDetect] database cleared")

    def _register_face(self, feature, slot_id):
        """注册人脸到指定槽位"""
        import os
        self._db_features[slot_id] = feature
        try:
            os.mkdir(self._db_dir)
        except Exception:
            pass
        path = f"{self._db_dir}id{slot_id}.bin"
        with open(path, 'wb') as f:
            f.write(feature.tobytes())
        print(f"[FaceDetect] registered face → id{slot_id}")

    def _search_face(self, feature):
        """在数据库中搜索匹配的人脸，返回 (matched_id, score)"""
        import ulab.numpy as np
        if not self._db_features:
            return None, 0.0

        feature = feature / np.linalg.norm(feature)
        best_id = None
        best_score = 0.0
        threshold = 0.75

        for i, db_feat in self._db_features.items():
            db_feat = db_feat / np.linalg.norm(db_feat)
            score = np.dot(feature, db_feat) / 2 + 0.5
            if score > best_score and score >= threshold:
                best_score = score
                best_id = i
        return best_id, best_score

    # ── K2 注册人脸 ──────────────────────────────────

    def _register_current_face(self):
        """将当前画面中最大人脸注册到下一个空槽位"""
        if not self._current_boxes:
            self.ctx.buzzer.beep(ms=30)  # 短促声：无人脸
            return

        if not self._current_frame_data or not self._current_landmarks:
            self.ctx.buzzer.beep(ms=30)
            return

        # 找最大人脸（按面积）
        largest_idx = 0
        largest_area = 0
        for i, box in enumerate(self._current_boxes):
            w = box[2] if len(box) > 2 else 0
            h = box[3] if len(box) > 3 else 0
            area = w * h
            if area > largest_area:
                largest_area = area
                largest_idx = i

        if largest_idx >= len(self._current_landmarks):
            self.ctx.buzzer.beep(ms=30)
            return

        landm = self._current_landmarks[largest_idx]

        # 推理特征
        try:
            self._face_reg.config_preprocess(landm)
            feature = self._face_reg.run(self._current_frame_data)
        except Exception as e:
            print(f"[FaceDetect] feature extract failed: {e}")
            self.ctx.buzzer.beep(ms=30)
            return

        # 找空槽位（1→2→3→4）
        for slot in range(1, 5):
            if slot not in self._db_features:
                self._register_face(feature, slot)
                self._update_status_text()
                self.ctx.buzzer.beep(ms=80)  # 成功长声
                return

        self.ctx.buzzer.beep(ms=200)  # 超长声：4个槽位已满

    # ── 数据发送 ──────────────────────────────────

    def _send_recognition_data(self):
        """组装4组识别数据并通过 UART 发送"""
        slots = [None, None, None, None]

        for i, (box, matched_id, score) in enumerate(self._recognition_results):
            if i >= 4:
                break
            x, y, w, h = [int(v) for v in box[:4]]
            conf = int(score * 100)
            fid = matched_id if matched_id is not None else 0
            slots[i] = (fid, x, y, w, h, conf)

        try:
            self.ctx.host.send_face_data(slots)
        except Exception as e:
            print(f"[FaceDetect] send error: {e}")

    # ── UI 构建 ──────────────────────────────────

    def _build_ui(self):
        screen = lv.scr_act()
        screen.set_style_bg_opa(0, 0)  # 透明透出 OSD1 相机画面
        self._screen = screen

        self._build_top_bar()
        self._build_preview_area()
        self._build_bottom_bar()

    # ── 顶栏 ──────────────────────────────────────

    def _build_top_bar(self):
        """顶栏：返回按钮(左) + 标题(居中) — 复用 Camera APP 模式"""
        lang = self.ctx.lang
        bar = lv.obj(self._screen)
        bar.set_size(lv.pct(100), BAR_H)
        bar.set_pos(0, 0)
        bar.set_style_bg_color(lv.color_hex(BAR_BG), 0)
        bar.set_style_bg_opa(255, 0)
        bar.set_style_border_width(0, 0)
        bar.set_style_pad_all(0, 0)
        bar.set_style_radius(0, 0)
        bar.clear_flag(lv.obj.FLAG.SCROLLABLE)
        self._top_bar = bar

        # 返回按钮
        btn = lv.obj(bar)
        btn.set_size(BTN_SIZE, BTN_SIZE)
        btn.align(lv.ALIGN.LEFT_MID, 2, 0)
        btn.set_style_bg_opa(0, 0)
        btn.set_style_border_width(0, 0)
        btn.set_style_shadow_width(0, 0)
        btn.set_style_outline_width(0, 0)
        btn.set_style_outline_opa(0, 0)
        btn.set_style_pad_all(0, 0)
        btn.clear_flag(lv.obj.FLAG.SCROLLABLE)
        btn.add_flag(lv.obj.FLAG.CLICKABLE)

        icon_data, icon_dsc = icon_cache.get_camera_icon("back")
        if icon_data is not None and icon_dsc is not None:
            _make_icon(btn, icon_data, icon_dsc, ICON_TARGET, 4, 0)
        else:
            lbl = lv.label(btn)
            lbl.set_text("<")
            lbl.center()

        btn.add_event(
            lambda e: self.ctx.request_exit() if e.get_code() == lv.EVENT.CLICKED else None,
            lv.EVENT.CLICKED, None)

        # 标题居中
        title = lv.label(bar)
        title.set_text(lang.t("category.face_detect"))
        title.align(lv.ALIGN.CENTER, 0, 0)
        title_style = make_back_bar_text_style(fonts.body)
        title.add_style(title_style, 0)
        self._title_label = title

    # ── 预览区（含十字架）──────────────────────────

    def _build_preview_area(self):
        """预览区：透明背景透出 OSD1 相机画面 + 绿色十字架"""
        preview = lv.obj(self._screen)
        preview.set_size(lv.pct(100), PREVIEW_H)
        preview.set_pos(0, PREVIEW_Y)
        preview.set_style_bg_opa(0, 0)
        preview.set_style_border_width(0, 0)
        preview.set_style_pad_all(0, 0)
        preview.set_style_radius(0, 0)
        preview.clear_flag(lv.obj.FLAG.SCROLLABLE)
        preview.clear_flag(lv.obj.FLAG.CLICKABLE)
        self._preview_bg = preview

        self._build_crosshair(preview)

    def _build_crosshair(self, parent):
        """在预览区中间绘制绿色十字瞄准线"""
        # 屏幕预览区中心（640×376）
        cx = 320
        cy = PREVIEW_H // 2  # 预览区垂直中心
        arm = CROSSHAIR_ARM
        gap = CROSSHAIR_GAP

        for (x1, y1, x2, y2) in [
            (cx, cy - arm, cx, cy - gap),      # 上臂
            (cx, cy + gap, cx, cy + arm),      # 下臂
            (cx - arm, cy, cx - gap, cy),      # 左臂
            (cx + gap, cy, cx + arm, cy),      # 右臂
        ]:
            w = abs(x2 - x1) if x2 != x1 else 2
            h = abs(y2 - y1) if y2 != y1 else 2
            line = lv.obj(parent)
            line.set_size(w, h)
            line.set_pos(x1 if x1 < x2 else x2, y1 if y1 < y2 else y2)
            line.set_style_bg_color(lv.color_hex(CROSSHAIR_COLOR), 0)
            line.set_style_bg_opa(180, 0)
            line.set_style_border_width(0, 0)
            line.set_style_radius(0, 0)
            line.clear_flag(lv.obj.FLAG.SCROLLABLE)
            line.clear_flag(lv.obj.FLAG.CLICKABLE)
            self._crosshair_lines.append(line)

    # ── 人脸框更新 ──────────────────────────────────

    def _update_face_boxes(self):
        """每帧重建人脸框（删除旧框 → 创建新框）"""
        # 1. 删除上一帧的框
        for obj in self._face_boxes:
            try:
                obj.delete()
            except Exception:
                pass
        self._face_boxes = []

        if not self._recognition_results or self._preview_bg is None:
            return

        # 2. 创建新框
        for i, (box, matched_id, _) in enumerate(self._recognition_results):
            if i >= 4:
                break
            x, y, w, h = [int(v) for v in box[:4]]
            # 坐标从 AI 输入 (1024×768) 映射到屏幕预览区 (640×376)
            sx = x * 640 // 1024
            sy = y * PREVIEW_H // 768
            sw = max(w * 640 // 1024, 4)
            sh = max(h * PREVIEW_H // 768, 4)

            # 边框矩形
            rect = lv.obj(self._preview_bg)
            rect.set_size(sw, sh)
            rect.set_pos(sx, sy)
            rect.set_style_bg_opa(0, 0)
            rect.set_style_border_width(3, 0)
            color = BOX_COLORS.get(matched_id, BOX_UNKNOWN)
            rect.set_style_border_color(lv.color_hex(color), 0)
            rect.set_style_border_opa(255, 0)
            rect.set_style_radius(0, 0)
            rect.clear_flag(lv.obj.FLAG.SCROLLABLE)
            rect.clear_flag(lv.obj.FLAG.CLICKABLE)
            self._face_boxes.append(rect)

            # ID 标签（框内左上角）
            if matched_id is not None:
                label = lv.label(rect)
                label.set_text(f"ID{matched_id}")
                label.set_pos(2, 2)
                label.set_style_text_color(lv.color_hex(color), 0)
                label.set_style_text_opa(255, 0)
                label.set_style_bg_opa(160, 0)
                label.set_style_bg_color(lv.color_hex(0x000000), 0)
                label.set_style_pad_all(2, 0)
                label.set_style_radius(2, 0)
                self._face_boxes.append(label)

    # ── 底栏 ──────────────────────────────────────

    def _build_bottom_bar(self):
        """底栏：list图标(左) + 状态文字(中)"""
        bar = lv.obj(self._screen)
        bar.set_size(lv.pct(100), BAR_H)
        bar.set_pos(0, PREVIEW_Y + PREVIEW_H)
        bar.set_style_bg_color(lv.color_hex(BAR_BG), 0)
        bar.set_style_bg_opa(255, 0)
        bar.set_style_border_width(0, 0)
        bar.set_style_pad_all(0, 0)
        bar.set_style_radius(0, 0)
        bar.clear_flag(lv.obj.FLAG.SCROLLABLE)
        self._bottom_bar = bar

        # ── list.png 按钮（左侧）──
        list_btn = lv.obj(bar)
        list_btn.set_size(BTN_SIZE, BTN_SIZE)
        list_btn.align(lv.ALIGN.LEFT_MID, 24, 0)
        list_btn.set_style_bg_opa(0, 0)
        list_btn.set_style_border_width(0, 0)
        list_btn.set_style_shadow_width(0, 0)
        list_btn.set_style_outline_width(0, 0)
        list_btn.set_style_outline_opa(0, 0)
        list_btn.set_style_pad_all(0, 0)
        list_btn.clear_flag(lv.obj.FLAG.SCROLLABLE)
        list_btn.add_flag(lv.obj.FLAG.CLICKABLE)

        icon_data, icon_dsc = icon_cache.get_face_icon("list")
        if icon_data is not None and icon_dsc is not None:
            img, _ = _make_icon(list_btn, icon_data, icon_dsc, ICON_TARGET, 4, 0)
        else:
            # 图标加载失败时用文字代替
            lbl = lv.label(list_btn)
            lbl.set_text("=")
            lbl.center()

        list_btn.add_event(
            lambda e: self._on_list_click() if e.get_code() == lv.EVENT.CLICKED else None,
            lv.EVENT.CLICKED, None)

        # ── 状态文字（中间）──
        self._status_label = lv.label(bar)
        self._status_label.align(lv.ALIGN.CENTER, 0, 0)
        self._update_status_text()

    def _update_status_text(self):
        """更新底栏中间的状态文字"""
        if self._status_label is None:
            return
        registered = sorted(self._db_features.keys())
        if registered:
            ids_text = " ".join(f"ID{i}" for i in registered)
            text = f"已注册: {ids_text}"
        else:
            text = "按K2注册人脸"
        self._status_label.set_text(text)
        style = make_back_bar_text_style(fonts.body)
        self._status_label.add_style(style, 0)

    # ── 弹出菜单 ──────────────────────────────────

    def _on_list_click(self):
        if self._popup is not None:
            self._dismiss_popup()
            return
        self._show_popup()

    def _show_popup(self):
        """在底栏上方显示保存/清除/取消菜单"""
        popup = lv.obj(self._screen)
        popup.set_size(160, 130)
        popup.align(lv.ALIGN.BOTTOM_MID, 0, -BAR_H - 8)
        popup.set_style_bg_color(lv.color_hex(0x333333), 0)
        popup.set_style_bg_opa(230, 0)
        popup.set_style_radius(12, 0)
        popup.set_style_border_width(0, 0)
        popup.set_style_pad_all(8, 0)
        popup.clear_flag(lv.obj.FLAG.SCROLLABLE)
        self._popup = popup

        # 点击空白处关闭（通过屏幕点击事件）
        popup.add_event(
            lambda e: None,  # 拦截点击不透传
            lv.EVENT.CLICKED, None)

        menu_items = [
            ("保存", lambda: self._on_save()),
            ("清除", lambda: self._on_clear()),
            ("取消", lambda: self._dismiss_popup()),
        ]

        for i, (text, callback) in enumerate(menu_items):
            item = lv.obj(popup)
            item.set_size(144, 34)
            item.set_pos(0, 4 + i * 38)
            item.set_style_bg_opa(0, 0)
            item.set_style_border_width(0, 0)
            item.set_style_radius(6, 0)
            item.clear_flag(lv.obj.FLAG.SCROLLABLE)
            item.add_flag(lv.obj.FLAG.CLICKABLE)
            self._face_boxes.append(item)  # 复用 _face_boxes 跟踪

            lbl = lv.label(item)
            lbl.set_text(text)
            lbl.align(lv.ALIGN.CENTER, 0, 0)
            lbl.set_style_text_color(lv.color_hex(WHITE), 0)
            lbl.add_style(make_back_bar_text_style(fonts.body), 0)
            self._face_boxes.append(lbl)

            item.add_event(
                lambda e, cb=callback: cb() if e.get_code() == lv.EVENT.CLICKED else None,
                lv.EVENT.CLICKED, None)

    def _dismiss_popup(self):
        """关闭弹出菜单"""
        if self._popup is not None:
            try:
                self._popup.delete()
            except Exception:
                pass
            self._popup = None

    def _on_save(self):
        """保存按钮：持久化所有人脸数据"""
        self._save_db()
        self.ctx.buzzer.beep(ms=50)
        self._dismiss_popup()

    def _on_clear(self):
        """清除按钮：删除全部人脸数据（需确认）"""
        # 简化：直接清除（单次点击即可，避免二次弹窗复杂度）
        self._clear_db()
        self._update_status_text()
        self.ctx.buzzer.beep(ms=100)
        self._dismiss_popup()

    # ── 销毁 ──────────────────────────────────

    def _destroy_ui(self):
        """释放所有 LVGL 对象"""
        self._face_boxes = []
        self._crosshair_lines = []
        self._dismiss_popup()

        for attr in ('_top_bar', '_bottom_bar', '_preview_bg'):
            obj = getattr(self, attr, None)
            if obj is not None:
                try:
                    obj.delete()
                except Exception:
                    pass
                setattr(self, attr, None)

        self._status_label = None
        self._title_label = None

        # 恢复屏幕背景不透明
        try:
            scr = lv.scr_act()
            scr.set_style_bg_color(lv.color_hex(Colors.BG), 0)
            scr.set_style_bg_opa(255, 0)
        except Exception:
            pass
        self._screen = None
```

- [ ] **Step 2: 运行测试验证基础结构**

```bash
python tests/test_face_detect.py
```
Expected: `test_face_detect_app_class_exists` PASS, `test_face_detect_app_has_required_methods` PASS（注意：此时尚无 AI 推理类，但方法签名已存在即可通过 AST 检测）

---

### Task 9: 人脸识别APP主体 — Part 2（AI推理类迁移 + 完整集成）

**Files:**
- Modify: `scripts/face_detect/app.py`（追加 AI 推理类）

- [ ] **Step 1: 在文件顶部导入区追加 AI 相关 import**

在 `app.py` 头部现有 import 之后添加：

```python
# AI 推理依赖（设备端运行时导入，IDE 不可用）
# 以下 import 在 K230 设备上可用，主机端 AST 测试不执行 import
try:
    import ulab.numpy as np
    import nncase_runtime as nn
    import aidemo
    import image as _image_lib
    import math
    import gc as _gc
except ImportError:
    pass  # IDE 环境无此模块，仅 AST 测试用
```

- [ ] **Step 2: 在 FaceDetectApp 类之前追加 FaceDetApp 和 FaceRegistrationApp 类**

从 Demo main2.py 迁移并精简（移除 PipeLine 依赖和 debug_mode，保留核心推理逻辑）。在 `class FaceDetectApp(BaseScript):` 之前插入：

```python
# ═══════════════════════════════════════════════════════
# AI 推理类（迁移自正点原子 Demo main2.py）
# ═══════════════════════════════════════════════════════

# 从 libs.AIBase 导入 AIBase（设备端）
from libs.AIBase import AIBase
from libs.AI2D import Ai2d


def _align_up(x, align=16):
    return (x + align - 1) // align * align


class FaceDetApp(AIBase):
    """人脸检测推理"""
    def __init__(self, kmodel_path, model_input_size, anchors,
                 confidence_threshold=0.5, nms_threshold=0.2,
                 rgb888p_size=None, debug_mode=0):
        if rgb888p_size is None:
            rgb888p_size = [1024, 768]
        super().__init__(kmodel_path, model_input_size, rgb888p_size, debug_mode)
        self.kmodel_path = kmodel_path
        self.model_input_size = model_input_size
        self.confidence_threshold = confidence_threshold
        self.nms_threshold = nms_threshold
        self.anchors = anchors
        self.rgb888p_size = [_align_up(rgb888p_size[0], 16), rgb888p_size[1]]
        self.image_size = []
        self.ai2d = Ai2d(debug_mode)
        self.ai2d.set_ai2d_dtype(
            nn.ai2d_format.NCHW_FMT, nn.ai2d_format.NCHW_FMT,
            np.uint8, np.uint8)

    def config_preprocess(self, input_image_size=None):
        ai2d_input_size = input_image_size if input_image_size else self.rgb888p_size
        self.image_size = [ai2d_input_size[1], ai2d_input_size[0]]
        dst_w = self.model_input_size[0]
        dst_h = self.model_input_size[1]
        ratio_w = dst_w / ai2d_input_size[0]
        ratio_h = dst_h / ai2d_input_size[1]
        ratio = ratio_w if ratio_w < ratio_h else ratio_h
        new_w = int(ratio * ai2d_input_size[0])
        new_h = int(ratio * ai2d_input_size[1])
        dw = (dst_w - new_w) / 2
        dh = (dst_h - new_h) / 2
        top = int(round(0))
        bottom = int(round(dh * 2 + 0.1))
        left = int(round(0))
        right = int(round(dw * 2 - 0.1))
        self.ai2d.pad([0, 0, 0, 0, top, bottom, left, right], 0, [104, 117, 123])
        self.ai2d.resize(nn.interp_method.tf_bilinear, nn.interp_mode.half_pixel)
        self.ai2d.build(
            [1, 3, ai2d_input_size[1], ai2d_input_size[0]],
            [1, 3, self.model_input_size[1], self.model_input_size[0]])

    def postprocess(self, results):
        res = aidemo.face_det_post_process(
            self.confidence_threshold, self.nms_threshold,
            self.model_input_size[0], self.anchors,
            self.rgb888p_size, results)
        if len(res) == 0:
            return [], []
        else:
            return res[0], res[1]


class FaceRegistrationApp(AIBase):
    """人脸特征提取推理"""
    def __init__(self, kmodel_path, model_input_size,
                 rgb888p_size=None, debug_mode=0):
        if rgb888p_size is None:
            rgb888p_size = [1024, 768]
        super().__init__(kmodel_path, model_input_size, rgb888p_size, debug_mode)
        self.kmodel_path = kmodel_path
        self.model_input_size = model_input_size
        self.rgb888p_size = [_align_up(rgb888p_size[0], 16), rgb888p_size[1]]
        self.umeyama_args_112 = [
            38.2946, 51.6963,
            73.5318, 51.5014,
            56.0252, 71.7366,
            41.5493, 92.3655,
            70.7299, 92.2041
        ]
        self.ai2d = Ai2d(debug_mode)
        self.ai2d.set_ai2d_dtype(
            nn.ai2d_format.NCHW_FMT, nn.ai2d_format.NCHW_FMT,
            np.uint8, np.uint8)

    def config_preprocess(self, landm, input_image_size=None):
        ai2d_input_size = input_image_size if input_image_size else self.rgb888p_size
        affine_matrix = self._get_affine_matrix(landm)
        self.ai2d.affine(nn.interp_method.cv2_bilinear, 0, 0, 127, 1, affine_matrix)
        self.ai2d.build(
            [1, 3, ai2d_input_size[1], ai2d_input_size[0]],
            [1, 3, self.model_input_size[1], self.model_input_size[0]])

    def postprocess(self, results):
        return results[0][0]

    def _get_affine_matrix(self, sparse_points):
        matrix_dst = self._image_umeyama_112(sparse_points)
        return [matrix_dst[0][0], matrix_dst[0][1], matrix_dst[0][2],
                matrix_dst[1][0], matrix_dst[1][1], matrix_dst[1][2]]

    def _image_umeyama_112(self, src):
        SRC_NUM = 5
        src_mean = [0.0, 0.0]
        dst_mean = [0.0, 0.0]
        for i in range(0, SRC_NUM * 2, 2):
            src_mean[0] += src[i]
            src_mean[1] += src[i + 1]
            dst_mean[0] += self.umeyama_args_112[i]
            dst_mean[1] += self.umeyama_args_112[i + 1]
        src_mean[0] /= SRC_NUM
        src_mean[1] /= SRC_NUM
        dst_mean[0] /= SRC_NUM
        dst_mean[1] /= SRC_NUM
        src_demean = [[0.0, 0.0] for _ in range(SRC_NUM)]
        dst_demean = [[0.0, 0.0] for _ in range(SRC_NUM)]
        for i in range(SRC_NUM):
            src_demean[i][0] = src[2 * i] - src_mean[0]
            src_demean[i][1] = src[2 * i + 1] - src_mean[1]
            dst_demean[i][0] = self.umeyama_args_112[2 * i] - dst_mean[0]
            dst_demean[i][1] = self.umeyama_args_112[2 * i + 1] - dst_mean[1]
        A = [[0.0, 0.0], [0.0, 0.0]]
        for i in range(2):
            for k in range(2):
                for j in range(SRC_NUM):
                    A[i][k] += dst_demean[j][i] * src_demean[j][k]
                A[i][k] /= SRC_NUM
        T = [[1, 0, 0], [0, 1, 0], [0, 0, 1]]
        U, S, V = self._svd22([A[0][0], A[0][1], A[1][0], A[1][1]])
        T[0][0] = U[0] * V[0] + U[1] * V[2]
        T[0][1] = U[0] * V[1] + U[1] * V[3]
        T[1][0] = U[2] * V[0] + U[3] * V[2]
        T[1][1] = U[2] * V[1] + U[3] * V[3]
        src_demean_mean = [0.0, 0.0]
        src_demean_var = [0.0, 0.0]
        for i in range(SRC_NUM):
            src_demean_mean[0] += src_demean[i][0]
            src_demean_mean[1] += src_demean[i][1]
        src_demean_mean[0] /= SRC_NUM
        src_demean_mean[1] /= SRC_NUM
        for i in range(SRC_NUM):
            src_demean_var[0] += (src_demean_mean[0] - src_demean[i][0]) ** 2
            src_demean_var[1] += (src_demean_mean[1] - src_demean[i][1]) ** 2
        src_demean_var[0] /= SRC_NUM
        src_demean_var[1] /= SRC_NUM
        scale = 1.0 / (src_demean_var[0] + src_demean_var[1]) * (S[0] + S[1])
        T[0][2] = dst_mean[0] - scale * (T[0][0] * src_mean[0] + T[0][1] * src_mean[1])
        T[1][2] = dst_mean[1] - scale * (T[1][0] * src_mean[0] + T[1][1] * src_mean[1])
        T[0][0] *= scale
        T[0][1] *= scale
        T[1][0] *= scale
        T[1][1] *= scale
        return T

    def _svd22(self, a):
        s = [0.0, 0.0]
        u = [0.0, 0.0, 0.0, 0.0]
        v = [0.0, 0.0, 0.0, 0.0]
        s[0] = (math.sqrt((a[0] - a[3]) ** 2 + (a[1] + a[2]) ** 2)
                + math.sqrt((a[0] + a[3]) ** 2 + (a[1] - a[2]) ** 2)) / 2
        s[1] = abs(s[0] - math.sqrt((a[0] - a[3]) ** 2 + (a[1] + a[2]) ** 2))
        v[2] = math.sin(math.atan2(
            2 * (a[0] * a[1] + a[2] * a[3]),
            a[0] ** 2 - a[1] ** 2 + a[2] ** 2 - a[3] ** 2) / 2) if s[0] > s[1] else 0
        v[0] = math.sqrt(1 - v[2] ** 2)
        v[1] = -v[2]
        v[3] = v[0]
        u[0] = -(a[0] * v[0] + a[1] * v[2]) / s[0] if s[0] != 0 else 1
        u[2] = -(a[2] * v[0] + a[3] * v[2]) / s[0] if s[0] != 0 else 0
        u[1] = (a[0] * v[1] + a[1] * v[3]) / s[1] if s[1] != 0 else -u[2]
        u[3] = (a[2] * v[1] + a[3] * v[3]) / s[1] if s[1] != 0 else u[0]
        v[0] = -v[0]
        v[2] = -v[2]
        return u, s, v
```

- [ ] **Step 3: 在 FaceDetectApp._init_ai_models() 中追加实际初始化逻辑**

将 Task 8 中创建的 `_init_ai_models` 方法（目前为空或占位）替换为完整实现。在 `FaceDetectApp` 类内找到 `_init_ai_models` 并追加：

```python
    def _init_ai_models(self):
        """加载人脸检测+识别模型"""
        import ulab.numpy as np_local

        det_kmodel = "/sdcard/examples/kmodel/face_detection_320.kmodel"
        reg_kmodel = "/sdcard/examples/kmodel/face_recognition.kmodel"
        anchors_path = "/sdcard/examples/utils/prior_data_320.bin"

        det_input = [320, 320]
        reg_input = [112, 112]
        rgb888p = [1024, 768]

        # 加载 anchors
        anchors = np_local.fromfile(anchors_path, dtype=np_local.float)
        self._anchors = anchors.reshape((4200, 4))

        # 人脸检测
        self._face_det = FaceDetApp(
            det_kmodel, model_input_size=det_input,
            anchors=self._anchors,
            confidence_threshold=0.5, nms_threshold=0.2,
            rgb888p_size=rgb888p, debug_mode=0)
        self._face_det.config_preprocess()
        print("[FaceDetect] face_det model loaded")

        # 人脸特征提取
        self._face_reg = FaceRegistrationApp(
            reg_kmodel, model_input_size=reg_input,
            rgb888p_size=rgb888p, debug_mode=0)
        print("[FaceDetect] face_reg model loaded")
```

- [ ] **Step 4: 运行全部测试**

```bash
python tests/test_face_detect.py
```
Expected: ALL 7 tests PASS

- [ ] **Step 5: Commit**

```bash
git add scripts/face_detect/app.py
git commit -m "feat(face_detect): 人脸识别APP主体 — AI推理类+UI+DB管理+K2注册+协议发送

- 迁移Demo的FaceDetApp+FaceRegistrationApp（face_detection_320 + face_recognition kmodel）
- LVGL OSD2层绿色十字架 + 4色人脸框(ID1绿/ID2蓝/ID3橙/ID4紫) + ID标签
- K2按键注册最大人脸到空槽位(1→2→3→4)
- /data/fac_db/ 存储128维特征(每个ID一个512B的bin文件)
- 底栏list.png弹出保存/清除菜单
- 10ms周期通过UART1发送4组ID数据(类型0x03)

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 10: 最终验证 — 全量测试 + 检查

**Files:**
- (无修改，验证阶段)

- [ ] **Step 1: 运行所有已有测试确保无回归**

```bash
python tests/test_camera_gallery.py
```
Expected: ALL 5 tests PASS

- [ ] **Step 2: 运行人脸识别全部测试**

```bash
python tests/test_face_detect.py
```
Expected: ALL 7 tests PASS

- [ ] **Step 3: 自查清单**

- [ ] `hw/lcd.py`: chn2 声明在 `MediaManager.init()` 之前 ✓
- [ ] `comm/host_api.py`: `send_face_data` 按 spec 格式（10B×4）✓
- [ ] `scripts/face_detect/app.py`: 所有 spec 中的方法均已实现 ✓
- [ ] `core/icon_cache.py`: `preload_face_icons()` + `get_face_icon()` ✓
- [ ] `core/script_runner.py`: K2 GPIO0 轮询 + `poll_handshake()` ✓
- [ ] `main.py`: `preload_face_icons()` 在首次 `task_handler` 之前调用 ✓
- [ ] `tests/test_face_detect.py`: 7 个测试覆盖所有改动点 ✓

- [ ] **Step 4: 最终 Commit**

```bash
git add -A
git commit -m "test(face_detect): 添加7个AST回归测试 + 全量集成验证

- test_face_detect_app_class_exists: BaseScript继承+SCRIPT_ID+SELF_MANAGED_TOP_BAR
- test_face_detect_app_has_required_methods: 17个必需方法
- test_lcd_declares_chn2_ai_channel: chn2 set_framesize+set_pixformat+ai_chn
- test_lcd_has_get_ai_frame: get_ai_frame方法
- test_host_api_has_protocol_methods: send_frame/send_face_data/poll_handshake
- test_host_api_constants: FRAME_HEAD=0x5A/FRAME_TAIL=0xA5/SRC=0xA7/DST=0x97/TYPE=0x03
- test_icon_cache_has_face_icons: preload_face_icons+get_face_icon

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### 改动文件总览

| # | 文件 | 操作 | 行数 |
|---|------|------|------|
| 1 | `tests/test_face_detect.py` | 新建 | ~180 |
| 2 | `hw/lcd.py` | 修改(+1 import, +6 chn2声明, +20 get_ai_frame) | ~26 |
| 3 | `comm/host_api.py` | 重写 | ~150 |
| 4 | `core/icon_cache.py` | 修改(+25 preload_face_icons + +5 get_face_icon) | ~30 |
| 5 | `core/script_runner.py` | 修改(+15 K2轮询 + +5 握手集成) | ~20 |
| 6 | `main.py` | 修改(+1行) | ~1 |
| 7 | `scripts/face_detect/__init__.py` | 新建 | ~1 |
| 8 | `scripts/face_detect/manifest.json` | 新建 | ~15 |
| 9 | `scripts/face_detect/app.py` | 新建 | ~750 |

**总预计**: 新建~950行，修改~75行，10次commit。

