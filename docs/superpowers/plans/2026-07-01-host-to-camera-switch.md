# 主机→摄像头脚本切换指令(K230 侧接收) 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** K230 通过 HostAPI 接收主机协议帧命令(切换脚本模式),解析 mode→category 后回调 main.py 写 `.next_script` + `machine.reset()` 切到目标脚本(或回主菜单)。

**Architecture:** 方案 A — HostAPI 只管协议(解析 8 字节命令帧 `5A 97 A7 01 FF <mode> <chk> A5`、校验和、mode→category 反向映射、回调通知),main.py 注册真实回调执行写文件+reset(复用 `_write_next_script`/`_clear_next_script`/`machine.reset`,与菜单点击路径一致)。职责清晰,HostAPI 可纯单测(喂字节、验回调),无循环 import。

**Tech Stack:** MicroPython + K230 UART1。测试用纯 Python `ast` 静态契约 + 字节级解析单测(板端模块不可导入,host_api.py 是纯协议逻辑可独立导入或 AST 测);用简易 `test_runner` 自跑(项目无 pytest,见 项目记录.md)。

**Spec:** `docs/superpowers/specs/2026-07-01-host-to-camera-switch-design.md`

---

## File Structure

- **Modify** `comm/host_api.py` — 增命令帧常量、`MODE_TO_CATEGORY` 反向映射、`register_switch_handler`、纯函数 `_parse_switch_frame`、`poll_handshake` 扫描循环扩展识别命令帧并调回调+消费。
- **Modify** `main.py` — `run_menu()` 与 `run_script()` 启动时 `runtime.host.register_switch_handler(_on_remote_switch)`;新增模块级 `_on_remote_switch(category)`。
- **Create** `tests/test_host_api_switch.py` — 纯 Python 测映射/解析/校验和/尾/前缀/回调分发/半帧/未知mode + AST 契约 + 自跑 runner。
- **Create** `tests/test_main_remote_switch_ast.py` — AST 守护 main.py 注册点 + `_on_remote_switch` 两分支。

**职责边界:** `host_api.py` = 协议解析+映射+回调通知(纯逻辑,无副作用);`main.py` = 真实切换动作(文件 I/O + reset)。契约守护在 `tests/`。两者解耦,HostAPI 不 import main。

---

## Task 1: 写失败测试 — MODE_TO_CATEGORY 映射 + _parse_switch_frame 解析

**Files:**
- Create: `tests/test_host_api_switch.py`

- [ ] **Step 1: 写失败测试文件(映射 + 解析)**

写入 `tests/test_host_api_switch.py`:

```python
# tests/test_host_api_switch.py -- 主机→摄像头切换命令帧解析(host 侧 AST/字节契约)
import ast, os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOST_API_PATH = os.path.join(ROOT, "comm", "host_api.py")


def _src():
    with open(HOST_API_PATH, "r", encoding="utf-8") as f:
        return f.read()


def _make_switch_frame(mode):
    """构造主机切换命令帧 5A 97 A7 01 FF <mode> <chk> A5。"""
    head = [0x5A, 0x97, 0xA7, 0x01, 0xFF, mode]
    chk = sum(head) & 0xFF
    return bytearray(head + [chk, 0xA5])


def test_mode_to_category_map_values():
    """MODE_TO_CATEGORY 反向映射:0x01→None(回菜单), 各脚本 mode→category。"""
    src = _src()
    assert "MODE_TO_CATEGORY" in src, "must define MODE_TO_CATEGORY"
    tree = ast.parse(src, filename=HOST_API_PATH)
    mtc = None
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name) and t.id == "MODE_TO_CATEGORY":
                    mtc = node.value
    assert isinstance(mtc, ast.Dict), "MODE_TO_CATEGORY must be a dict literal"
    mapping = {}
    for k, v in zip(mtc.keys, mtc.values):
        assert isinstance(k, ast.Constant) and isinstance(k.value, int), \
            "MODE_TO_CATEGORY keys must be int literals"
        if isinstance(v, ast.Constant):
            mapping[k.value] = v.value  # None 或 str
        elif isinstance(v, ast.Name) and v.id == "None":
            mapping[k.value] = None
    assert mapping.get(0x01) is None, "0x01 must map to None (main menu)"
    assert mapping.get(0x02) == "camera"
    assert mapping.get(0x03) == "face_detect"
    assert mapping.get(0x04) == "tag_detect"
    assert mapping.get(0x05) == "object_detect"
    assert mapping.get(0x06) == "color_detect"
    assert mapping.get(0x07) == "road_detect"
    assert mapping.get(0x10) == "gesture_detect"
    assert mapping.get(0x11) == "body_detect"
    assert mapping.get(0x12) == "object_classify"
    assert mapping.get(0x13) == "image_classify"


def test_type_mode_switch_constant():
    """TYPE_MODE_SWITCH 必须 == 0xFF(命令帧 index 字段)。"""
    src = _src()
    assert "TYPE_MODE_SWITCH" in src
    assert "TYPE_MODE_SWITCH = 0xFF" in src or \
           "TYPE_MODE_SWITCH=0xFF" in src


def test_parse_switch_frame_valid():
    """_parse_switch_frame 对正确帧返回 mode。"""
    ns = {}
    exec(compile(ast.parse(_src()), HOST_API_PATH, "exec"), ns)
    # _parse_switch_frame 是 HostAPI 的方法;取类里的 staticmethod 或实例方法
    HostAPI = ns["HostAPI"]
    # 若是实例方法/staticmethod,通过类访问
    parse = HostAPI._parse_switch_frame
    frame = _make_switch_frame(0x13)
    result = parse(frame, 0)
    assert result is not None, "valid frame must parse"
    mode, nxt = result
    assert mode == 0x13
    assert nxt == 8


def test_parse_switch_frame_bad_checksum():
    """校验和错 → None。"""
    ns = {}
    exec(compile(ast.parse(_src()), HOST_API_PATH, "exec"), ns)
    parse = ns["HostAPI"]._parse_switch_frame
    frame = bytearray([0x5A, 0x97, 0xA7, 0x01, 0xFF, 0x13, 0x00, 0xA5])  # chk 故意错
    assert parse(frame, 0) is None


def test_parse_switch_frame_bad_tail():
    """尾非 A5 → None。"""
    ns = {}
    exec(compile(ast.parse(_src()), HOST_API_PATH, "exec"), ns)
    parse = ns["HostAPI"]._parse_switch_frame
    head = [0x5A, 0x97, 0xA7, 0x01, 0xFF, 0x13]
    chk = sum(head) & 0xFF
    frame = bytearray(head + [chk, 0x00])  # 尾错
    assert parse(frame, 0) is None


def test_parse_switch_frame_bad_prefix():
    """前缀错 → None。"""
    ns = {}
    exec(compile(ast.parse(_src()), HOST_API_PATH, "exec"), ns)
    parse = ns["HostAPI"]._parse_switch_frame
    head = [0x5A, 0x97, 0xA6, 0x01, 0xFF, 0x13]  # dst 错(0xA6)
    chk = sum(head) & 0xFF
    frame = bytearray(head + [chk, 0xA5])
    assert parse(frame, 0) is None


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

- [ ] **Step 2: 运行测试,验证失败(RED)**

Run: `python tests/test_host_api_switch.py`
Expected: FAIL — `MODE_TO_CATEGORY` 未定义、`_parse_switch_frame` 不存在,多个测试失败。

- [ ] **Step 3: Commit(RED 状态留证)**

```bash
git add tests/test_host_api_switch.py
git commit -m "test(host_api_switch): RED — assert MODE_TO_CATEGORY + _parse_switch_frame"
```

---

## Task 2: 实现 — host_api 常量 + 映射 + _parse_switch_frame(GREEN)

**Files:**
- Modify: `comm/host_api.py:28-39`(常量区)与 `comm/host_api.py`(新增方法)

- [ ] **Step 1: 在常量区增 TYPE_MODE_SWITCH + 地址常量**

在 `comm/host_api.py` 的 `TYPE_IMAGE_CLASSIFY = 0x13` 行之后、`# category_id → msg_type 映射` 注释之前,插入命令帧常量。用 Edit,`old_string`:

```python
    TYPE_IMAGE_CLASSIFY  = 0x13

    # category_id → msg_type 映射（reset 框架 category 与协议类型码对接）
```

`new_string`:

```python
    TYPE_IMAGE_CLASSIFY  = 0x13

    # ── 主机→摄像头 脚本切换命令帧 ──
    # 主机 _camer_changer_camer_mode 发:5A 97 A7 01 FF <mode> <chk> A5(8 字节)
    #   [0]=HEAD [1]=src97 [2]=dstA7(=DEV_ID_CAMER) [3]=len=1
    #   [4]=index=FF(命令) [5]=mode [6]=chk [7]=TAIL
    TYPE_MODE_SWITCH       = 0xFF   # 命令帧 index 字段
    MODE_SWITCH_SRC        = 0x97
    MODE_SWITCH_DST        = 0xA7
    MODE_SWITCH_FRAME_LEN  = 8

    # category_id → msg_type 映射（reset 框架 category 与协议类型码对接）
```

- [ ] **Step 2: 在 CATEGORY_TYPE dict 之后增 MODE_TO_CATEGORY 反向映射**

在 `comm/host_api.py` 的 `CATEGORY_TYPE` dict 闭合 `}` 之后(即 `"_template": ...` 行之后),新增 `MODE_TO_CATEGORY`。先读确认闭合位置,用 Edit,`old_string`:

```python
        "image_classify":  TYPE_IMAGE_CLASSIFY,    # 0x13
        "_template":  TYPE_MAIN_MENU,     # 0x01（默认）
    }
```

`new_string`:

```python
        "image_classify":  TYPE_IMAGE_CLASSIFY,    # 0x13
        "_template":  TYPE_MAIN_MENU,     # 0x01（默认）
    }

    # mode → category 反向映射(主机切换命令帧的 mode 字段 → 脚本 category)。
    # 0x01 = 回主菜单(category=None → 清 .next_script + reset)。
    MODE_TO_CATEGORY = {
        0x01: None,               # 主菜单
        0x02: "camera",
        0x03: "face_detect",
        0x04: "tag_detect",
        0x05: "object_detect",
        0x06: "color_detect",
        0x07: "road_detect",
        0x10: "gesture_detect",
        0x11: "body_detect",
        0x12: "object_classify",
        0x13: "image_classify",
    }
```

- [ ] **Step 3: 增 _parse_switch_frame 纯函数(作为 staticmethod)**

在 `comm/host_api.py` 的 `# ── 帧组装与发送 ──` 区的 `_checksum` staticmethod 之后,新增 `_parse_switch_frame`。用 Edit,`old_string`:

```python
    @staticmethod
    def _checksum(data):
        """计算校验和：所有字节累加取低8位"""
        s = 0
        for b in data:
            s = (s + b) & 0xFF
        return s
```

`new_string`:

```python
    @staticmethod
    def _checksum(data):
        """计算校验和：所有字节累加取低8位"""
        s = 0
        for b in data:
            s = (s + b) & 0xFF
        return s

    @staticmethod
    def _parse_switch_frame(buf, offset):
        """尝试在 buf[offset] 解析主机切换命令帧 8 字节。

        帧: 5A 97 A7 01 FF <mode> <chk> A5
        chk = (0x5A+0x97+0xA7+0x01+0xFF+mode) & 0xFF

        Returns: (mode, next_offset) 或 None(校验失败/长度不足)。
        """
        if offset + 8 > len(buf):
            return None
        if buf[offset]     != 0x5A: return None
        if buf[offset + 1] != 0x97: return None
        if buf[offset + 2] != 0xA7: return None
        if buf[offset + 3] != 0x01: return None
        if buf[offset + 4] != 0xFF: return None
        mode = buf[offset + 5]
        chk = (0x5A + 0x97 + 0xA7 + 0x01 + 0xFF + mode) & 0xFF
        if buf[offset + 6] != chk: return None
        if buf[offset + 7] != 0xA5: return None
        return (mode, offset + 8)
```

- [ ] **Step 4: 运行测试,验证通过(GREEN)**

Run: `python tests/test_host_api_switch.py`
Expected: PASS — 全部 6 个测试通过(映射值/常量/valid/bad_checksum/bad_tail/bad_prefix)。

- [ ] **Step 5: 回归 host_api 既有测试**

Run: `python tests/test_host_api.py`
Expected: ALL PASS(既有契约未破坏;新增常量/映射/方法不影响握手与数据帧逻辑)。

- [ ] **Step 6: Commit**

```bash
git add comm/host_api.py
git commit -m "feat(host_api): parse host switch command frame + mode→category map"
```

---

## Task 3: 写失败测试 — register_switch_handler + poll_handshake 分发

**Files:**
- Modify: `tests/test_host_api_switch.py`(追加测试)

- [ ] **Step 1: 追加回调注册与分发测试**

在 `tests/test_host_api_switch.py` 的 `test_parse_switch_frame_bad_prefix` 之后、`test_runner` 之前,追加:

```python
def test_register_switch_handler_exists():
    """HostAPI 必须有 register_switch_handler(cb)。"""
    src = _src()
    assert "def register_switch_handler" in src, \
        "must define register_switch_handler(cb)"


def test_dispatch_calls_handler_with_category():
    """喂完整命令帧到 _rx_buf,调 poll_handshake → 回调以正确 category 调用。

    用替身 HostAPI(避开 _uart/_connected 依赖):直接置 _rx_buf,
    调 _dispatch_switch_only(若 poll_handshake 难隔离)或 monkey patch _uart。
    本测试用 monkey patch _uart.any/_uart.read。
    """
    ns = {}
    exec(compile(ast.parse(_src()), HOST_API_PATH, "exec"), ns)
    HostAPI = ns["HostAPI"]

    class _FakeUART:
        def __init__(self, data):
            self._data = bytearray(data)
        def any(self):
            return len(self._data)
        def read(self, n):
            d = self._data[:n]
            self._data = self._data[n:]
            return d

    api = HostAPI.__new__(HostAPI)  # 跳过 __init__(避免开真 UART)
    api._uart = _FakeUART(_make_switch_frame(0x13))
    api._connected = False
    api._last_handshake_ms = 0
    api._rx_buf = bytearray()
    api._switch_handler = None

    received = []
    api.register_switch_handler(lambda cat: received.append(cat))
    api.poll_handshake()
    assert received == ["image_classify"], \
        "handler must be called with 'image_classify', got %r" % received


def test_dispatch_half_frame_no_call():
    """半帧(不足 8 字节)不触发回调,保留尾部等拼接。"""
    ns = {}
    exec(compile(ast.parse(_src()), HOST_API_PATH, "exec"), ns)
    HostAPI = ns["HostAPI"]

    class _FakeUART:
        def __init__(self, data):
            self._data = bytearray(data)
        def any(self):
            return len(self._data)
        def read(self, n):
            d = self._data[:n]
            self._data = self._data[n:]
            return d

    api = HostAPI.__new__(HostAPI)
    api._uart = _FakeUART(b"\x5A\x97\xA7\x01\xFF")  # 只 5 字节(半帧)
    api._connected = False
    api._last_handshake_ms = 0
    api._rx_buf = bytearray()
    api._switch_handler = None

    received = []
    api.register_switch_handler(lambda cat: received.append(cat))
    api.poll_handshake()
    assert received == [], "half frame must not dispatch"
    # 半帧尾部应保留在 _rx_buf(以 0x5A 起的尾部)
    assert api._rx_buf == bytearray(b"\x5A\x97\xA7\x01\xFF"), \
        "half frame tail must be retained for next poll"


def test_dispatch_unknown_mode_no_call():
    """未知 mode(不在 MODE_TO_CATEGORY,如 0x99)不触发回调。"""
    ns = {}
    exec(compile(ast.parse(_src()), HOST_API_PATH, "exec"), ns)
    HostAPI = ns["HostAPI"]

    class _FakeUART:
        def __init__(self, data):
            self._data = bytearray(data)
        def any(self):
            return len(self._data)
        def read(self, n):
            d = self._data[:n]
            self._data = self._data[n:]
            return d

    api = HostAPI.__new__(HostAPI)
    api._uart = _FakeUART(_make_switch_frame(0x99))
    api._connected = False
    api._last_handshake_ms = 0
    api._rx_buf = bytearray()
    api._switch_handler = None

    received = []
    api.register_switch_handler(lambda cat: received.append(cat))
    api.poll_handshake()
    assert received == [], "unknown mode must not dispatch"


def test_dispatch_mode_0x01_calls_with_none():
    """mode=0x01 → 回调以 None 调用(回主菜单)。"""
    ns = {}
    exec(compile(ast.parse(_src()), HOST_API_PATH, "exec"), ns)
    HostAPI = ns["HostAPI"]

    class _FakeUART:
        def __init__(self, data):
            self._data = bytearray(data)
        def any(self):
            return len(self._data)
        def read(self, n):
            d = self._data[:n]
            self._data = self._data[n:]
            return d

    api = HostAPI.__new__(HostAPI)
    api._uart = _FakeUART(_make_switch_frame(0x01))
    api._connected = False
    api._last_handshake_ms = 0
    api._rx_buf = bytearray()
    api._switch_handler = None

    received = []
    api.register_switch_handler(lambda cat: received.append(cat))
    api.poll_handshake()
    assert received == [None], "mode 0x01 must dispatch None (main menu)"
```

- [ ] **Step 2: 运行测试,验证新测试失败(RED)**

Run: `python tests/test_host_api_switch.py`
Expected: FAIL — `register_switch_handler` 不存在、`poll_handshake` 不识别命令帧,4 个新测试失败;前 6 个测试仍 PASS。

- [ ] **Step 3: Commit(RED 状态留证)**

```bash
git add tests/test_host_api_switch.py
git commit -m "test(host_api_switch): RED — assert register_switch_handler + dispatch"
```

---

## Task 4: 实现 — register_switch_handler + poll_handshake 扩展(GREEN)

**Files:**
- Modify: `comm/host_api.py`

- [ ] **Step 1: 在 __init__ 增 _switch_handler 字段**

在 `comm/host_api.py` 的 `__init__` 末尾(诊断字段之后),用 Edit,`old_string`:

```python
        # 板端诊断:低频打印实际 category→msg_type 映射,用于排查主机收到类型不对。
        self._diag_tick_count = 0
        self._diag_last_category = None
        self._diag_last_msg_type = None
```

`new_string`:

```python
        # 板端诊断:低频打印实际 category→msg_type 映射,用于排查主机收到类型不对。
        self._diag_tick_count = 0
        self._diag_last_category = None
        self._diag_last_msg_type = None
        # 主机→摄像头切换命令回调:cb(category),category=str 或 None(回菜单)。
        self._switch_handler = None
```

- [ ] **Step 2: 增 register_switch_handler 方法**

在 `comm/host_api.py` 的 `register_handler` 方法之前(或之后,同"命令注册"区),用 Edit,`old_string`:

```python
    # ── 命令注册（预留）──

    def register_handler(self, cmd, callback):
```

`new_string`:

```python
    # ── 命令注册（预留）──

    def register_switch_handler(self, callback):
        """注册主机→摄像头切换命令回调。

        Args:
            callback: func(category) — category 为 str(脚本 category_id)
                      或 None(回主菜单)。由 main.py 执行写 .next_script + reset。
        """
        self._switch_handler = callback

    def register_handler(self, cmd, callback):
```

- [ ] **Step 3: 重写 poll_handshake 扫描循环,识别命令帧**

在 `comm/host_api.py` 的 `poll_handshake` 中,把扫描循环(从 `frame = self.HANDSHAKE_REQUEST_FRAME` 到方法末尾 `self._rx_buf = bytearray(buf[matched_at + flen:])`)替换为支持两种帧的版本。用 Edit,`old_string`:

```python
        frame = self.HANDSHAKE_REQUEST_FRAME
        flen = len(frame)
        buf = self._rx_buf
        nbuf = len(buf)
        i = 0
        matched_at = -1
        while i <= nbuf - flen:
            if buf[i] == self.FRAME_HEAD and buf[i:i + flen] == frame:
                matched_at = i
                break
            i += 1

        if matched_at < 0:
            # 未匹配:保留从最后一个 0x5A 开始的尾部(可能是半帧,等下次拼)
            # MicroPython bytearray.rfind 不接受 int,须传 bytes
            last_head = buf.rfind(bytes([self.FRAME_HEAD]))
            if last_head > 0:
                self._rx_buf = bytearray(buf[last_head:])
            else:
                self._rx_buf = bytearray()
            return

        # 匹配:应答 + 记时间戳 + 消费掉匹配帧及之前的字节
        self._send_handshake_reply()
        self._rx_buf = bytearray(buf[matched_at + flen:])
```

`new_string`:

```python
        frame = self.HANDSHAKE_REQUEST_FRAME
        flen = len(frame)
        buf = self._rx_buf
        nbuf = len(buf)
        i = 0
        # 扫描:在每个 0x5A 位置先试握手常量(18B),再试切换命令帧(8B)。
        # 命令帧短(8B)且 index=FF,与握手帧(index=09)不冲突。
        while i < nbuf:
            if buf[i] != self.FRAME_HEAD:
                i += 1
                continue
            # 1) 握手常量匹配(需完整 18B)
            if i + flen <= nbuf and buf[i:i + flen] == frame:
                self._send_handshake_reply()
                buf = bytearray(buf[i + flen:])
                i = 0
                nbuf = len(buf)
                continue
            # 2) 切换命令帧匹配(8B)
            parsed = self._parse_switch_frame(buf, i)
            if parsed is not None:
                mode, _nxt = parsed
                category = self.MODE_TO_CATEGORY.get(mode)
                if category is not None or mode == 0x01:
                    print("[HostAPI] switch frame mode=0x%02X category=%s" %
                          (mode, category))
                    if self._switch_handler is not None:
                        try:
                            self._switch_handler(category)
                        except Exception as e:
                            print("[HostAPI] switch handler error: %s" % e)
                else:
                    print("[HostAPI] switch frame unknown mode=0x%02X" % mode)
                buf = bytearray(buf[i + 8:])
                i = 0
                nbuf = len(buf)
                continue
            # 当前 0x5A 处两种帧都不完整匹配:可能是半帧起点,保留尾部退出
            # (不足 8B → _parse_switch_frame 返回 None,但可能是半帧,需保留)
            if i + 8 > nbuf:
                break
            i += 1

        # 保留从最后一个 0x5A 开始的尾部(半帧,等下次拼)
        last_head = buf.rfind(bytes([self.FRAME_HEAD]))
        if last_head >= 0:
            self._rx_buf = bytearray(buf[last_head:])
        else:
            self._rx_buf = bytearray()
```

- [ ] **Step 4: 运行测试,验证全部通过(GREEN)**

Run: `python tests/test_host_api_switch.py`
Expected: PASS — 全部 10 个测试通过(前 6 + register_exists + dispatch_category + half_frame + unknown_mode + mode_0x01_none)。

- [ ] **Step 5: 回归 host_api 既有测试(握手仍正常)**

Run: `python tests/test_host_api.py`
Expected: ALL PASS。特别确认 `test_poll_handshake_matches_full_request_frame_not_substring`、`test_poll_handshake_does_not_short_circuit_on_connected`、`test_poll_handshake_rfind_does_not_pass_int` 仍通过。

- [ ] **Step 6: Commit**

```bash
git add comm/host_api.py
git commit -m "feat(host_api): register_switch_handler + poll_handshake dispatches switch frames"
```

---

## Task 5: 写失败测试 — main.py 注册 + _on_remote_switch 两分支

**Files:**
- Create: `tests/test_main_remote_switch_ast.py`

- [ ] **Step 1: 写失败 AST 契约测试**

写入 `tests/test_main_remote_switch_ast.py`:

```python
# tests/test_main_remote_switch_ast.py -- main.py 远程切换注册点 AST 守护
import ast, os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MAIN_PATH = os.path.join(ROOT, "main.py")


def _src():
    with open(MAIN_PATH, "r", encoding="utf-8") as f:
        return f.read()


def test_main_registers_switch_handler():
    """main.py 必须调 register_switch_handler(_on_remote_switch)。"""
    src = _src()
    assert "register_switch_handler" in src, \
        "main.py must register switch handler"
    assert "_on_remote_switch" in src, \
        "main.py must reference _on_remote_switch"


def test_main_has_on_remote_switch_def():
    """main.py 必须定义 def _on_remote_switch(category)。"""
    src = _src()
    assert "def _on_remote_switch" in src, \
        "main.py must define _on_remote_switch(category)"


def test_on_remote_switch_uses_write_and_reset():
    """_on_remote_switch 体:有 category 分支调 _write_next_script + machine.reset。

    AST 提取 _on_remote_switch 函数体,验证含 _write_next_script 与 machine.reset。
    (清空分支 _clear_next_script + reset 由下一测试守护。)
    """
    tree = ast.parse(_src(), filename=MAIN_PATH)
    fn = None
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_on_remote_switch":
            fn = node
            break
    assert fn is not None, "_on_remote_switch must be defined"
    body_src = ast.get_source_segment(_src(), fn) or ""
    assert "_write_next_script" in body_src, \
        "_on_remote_switch must call _write_next_script for script switch"
    assert "machine.reset" in body_src, \
        "_on_remote_switch must call machine.reset"


def test_on_remote_switch_handles_none_branch():
    """_on_remote_switch 体:None 分支(回菜单)调 _clear_next_script + reset。"""
    tree = ast.parse(_src(), filename=MAIN_PATH)
    fn = None
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_on_remote_switch":
            fn = node
            break
    assert fn is not None
    body_src = ast.get_source_segment(_src(), fn) or ""
    assert "_clear_next_script" in body_src, \
        "_on_remote_switch must call _clear_next_script for None (main menu) branch"
    assert "machine.reset" in body_src


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

- [ ] **Step 2: 运行测试,验证失败(RED)**

Run: `python tests/test_main_remote_switch_ast.py`
Expected: FAIL — main.py 无 `register_switch_handler` 调用、无 `_on_remote_switch` 定义,4 个测试失败。

- [ ] **Step 3: Commit(RED 状态留证)**

```bash
git add tests/test_main_remote_switch_ast.py
git commit -m "test(main_remote_switch): RED — assert register + _on_remote_switch branches"
```

---

## Task 6: 实现 — main.py 注册 + _on_remote_switch(GREEN)

**Files:**
- Modify: `main.py`

- [ ] **Step 1: 增 _on_remote_switch 模块级函数**

在 `main.py` 的 `_clear_next_script` 函数之后(约 `main.py:63`),新增 `_on_remote_switch`。用 Edit,`old_string`:

```python
def _clear_next_script():
    try:
        os.remove(NEXT_SCRIPT_PATH)
    except Exception:
        pass
```

`new_string`:

```python
def _clear_next_script():
    try:
        os.remove(NEXT_SCRIPT_PATH)
    except Exception:
        pass


def _on_remote_switch(category):
    """主机远程切换脚本回调(HostAPI 解析命令帧后调用)。

    category=None → 回主菜单(清 .next_script + reset)。
    category=str  → 进对应脚本(写 .next_script + reset)。
    复用菜单点击路径(_write_next_script + machine.reset),与本地点击一致。
    """
    print("[CamerAi] remote switch -> %s" % ("main_menu" if category is None else category))
    if category is None:
        _clear_next_script()
    else:
        _write_next_script(category)
    machine.reset()
```

- [ ] **Step 2: 在 run_menu() 注册回调**

在 `main.py` 的 `run_menu()` 中,`runtime.init_menu(fpioa)` 之后、屏幕背景设置之前,注册回调。用 Edit,`old_string`:

```python
    fpioa = FPIOA()
    runtime = AppRuntime()
    runtime.init_menu(fpioa)

    # 显式设默认屏幕纯黑背景 + radius0 + border0：消除 LVGL 默认主题四角白点
```

`new_string`:

```python
    fpioa = FPIOA()
    runtime = AppRuntime()
    runtime.init_menu(fpioa)

    # 注册主机远程切换脚本回调(主菜单态也允许被远程切走)
    if runtime.host is not None:
        runtime.host.register_switch_handler(_on_remote_switch)

    # 显式设默认屏幕纯黑背景 + radius0 + border0：消除 LVGL 默认主题四角白点
```

- [ ] **Step 3: 在 run_script() 注册回调**

在 `main.py` 的 `run_script()` 中,`runtime.init_app(category_id, fpioa)` 之后、`_load_script` 之前,注册回调。用 Edit,`old_string`:

```python
    fpioa = FPIOA()
    runtime = AppRuntime()
    runtime.init_app(category_id, fpioa)
    print("[CamerAi] loading script module...")
```

`new_string`:

```python
    fpioa = FPIOA()
    runtime = AppRuntime()
    runtime.init_app(category_id, fpioa)
    # 注册主机远程切换脚本回调(脚本态允许被远程切走/回菜单)
    if runtime.host is not None:
        runtime.host.register_switch_handler(_on_remote_switch)
    print("[CamerAi] loading script module...")
```

- [ ] **Step 4: 运行测试,验证通过(GREEN)**

Run: `python tests/test_main_remote_switch_ast.py`
Expected: PASS — 全部 4 个测试通过。

- [ ] **Step 5: 编译检查 main.py**

Run: `python -m py_compile main.py`
Expected: 无输出(编译通过)。

- [ ] **Step 6: Commit**

```bash
git add main.py
git commit -m "feat(main): register remote switch handler + _on_remote_switch (reset switch)"
```

---

## Task 7: 全量回归 + 部署清单

**Files:** 无新改动,只验证。

- [ ] **Step 1: 跑 switch 测试套件**

Run: `python tests/test_host_api_switch.py && python tests/test_main_remote_switch_ast.py`
Expected: 全部 PASS(10 + 4 = 14 项)。

- [ ] **Step 2: 回归 host_api 与既有契约测试**

Run: `python tests/test_host_api.py && python tests/test_image_classify_ast.py`
Expected: ALL PASS。

- [ ] **Step 3: 编译检查所有改动文件**

Run: `python -m py_compile comm/host_api.py main.py tests/test_host_api_switch.py tests/test_main_remote_switch_ast.py`
Expected: 无输出(全部编译通过)。

- [ ] **Step 4: 更新 项目记录.md(追加实施完成条目)**

在 `项目记录.md` 顶部 `## 2026-07-01 主机→摄像头脚本切换指令 — 设计确认` 条目下,追加实施完成小结:

- 部署文件:`comm/host_api.py`(命令帧常量 + MODE_TO_CATEGORY + _parse_switch_frame + register_switch_handler + poll_handshake 扩展)、`main.py`(_on_remote_switch + run_menu/run_script 注册)。
- 测试:`tests/test_host_api_switch.py` 10 项 + `tests/test_main_remote_switch_ast.py` 4 项全绿。
- 待板端验收:主机 PikaScript `changer_camer_mode(4, 0x10)` → 摄像头 reset 进手势;`0x01` → 回菜单;坏帧不切不崩。

- [ ] **Step 5: Commit**

```bash
git add 项目记录.md
git commit -m "docs(项目记录): host-to-camera switch implemented"
```

---

## 验收标准(板端)

1. 摄像头跑任意脚本(如 image_classify 预览)。
2. 主机 PikaScript 调 `changer_camer_mode(4, 0x10)` → 摄像头 reset 后进手势识别。
3. 主机调 `changer_camer_mode(4, 0x01)` → 摄像头 reset 回主菜单。
4. 各 mode(0x02~0x13)切换均进对应脚本,无卡死/无残留。
5. 乱发坏帧(校验和错/尾错)→ 摄像头不切换、不崩、继续正常运行。
6. 切换后主机仍能收到新脚本的对应协议数据帧(mode 字段正确)。
