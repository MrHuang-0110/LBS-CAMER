# 标签识别全屏扫描 + 动态排序上报 — 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 tag_detect 从"按键学习 ID"改为"每帧全屏扫描所有可识别码 → 按 x 从左到右排序 → 动态数量上报"。

**Architecture:** 新增纯 Python 模块 `core/tag_scan.py`（排序/截断/id 映射，host 可真单测）；`comm/host_api.py` 的 `send_id_data` 泛化为动态 N 槽载荷（N≤25，帧格式不变）；`scripts/tag_detect/app.py` 移除 KEY2/TagDB/浮层/计数链路，on_frame 走 tag_scan 后全白框绘制 + `host_tick`。

**Tech Stack:** K230 CanMV MicroPython；LVGL；media.sensor/display；host 侧 Python（无 pytest，逐文件 `python tests/test_xxx.py`）。

**Spec:** `docs/superpowers/specs/2026-08-03-tag-scan-sort-design.md`

---

## 关键接口约定（跨任务一致）

- `core/tag_scan.py`：`MAX_SLOTS = 25`；`build_slots(detected, qr_mode=False, scale=2)` → `list[(id_val, x*scale, y*scale, w*scale, h*scale, 100)]`。
  - `detected`: `list[(code_id, x, y, w, h)]`，x/y/w/h 为 QVGA 整数。
  - 排序 key `(x, y)` 升序（`sorted` 稳定）；截断前 `MAX_SLOTS` 个。
  - `qr_mode=False`（AprilTag）：`id_val = code_id if code_id <= 255 else 255`。
  - `qr_mode=True`（QR）：`id_val = 排序后序号 i+1`（code_id 不参与编码）。
- `comm/host_api.py`：`MAX_ID_SLOTS = 25`（与 tag_scan.MAX_SLOTS 对齐）；`send_id_data(msg_type, slots=None)` 编码 `min(len(slots), 25)` 组；`slots=None`/空 → 0 字节载荷；`send_frame(msg_type, payload, length=None)` 增加可选 length 参数（零分配保持）。
- 分组字节序不变：id(1B) + x/y/w/h(各 2B 大端) + conf(1B) = 10B/组。

---

### Task 1: `core/tag_scan.py` 纯逻辑模块（TDD）

**Files:**
- Create: `core/tag_scan.py`
- Test: `tests/test_tag_scan.py`

- [ ] **Step 1: 写失败测试**

新建 `tests/test_tag_scan.py`：

```python
# tests/test_tag_scan.py — tag_scan 纯逻辑真单元测试（无板端依赖）
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "core"))

from tag_scan import MAX_SLOTS, build_slots


def test_sort_by_x_then_y():
    """按 (x, y) 升序:最左边 x 最小排第一;同 x 按 y 升序。"""
    det = [(9, 200, 10, 20, 20), (5, 100, 30, 20, 20), (7, 100, 10, 20, 20)]
    slots = build_slots(det)
    assert [s[0] for s in slots] == [7, 5, 9], "ids must follow (x,y) ascending order"


def test_qr_mode_uses_sequence_numbers():
    """qr_mode=True: id 为排序后序号 1..N,与 code_id 无关。"""
    det = [("abc", 300, 10, 20, 20), ("xyz", 100, 10, 20, 20)]
    slots = build_slots(det, qr_mode=True)
    assert [s[0] for s in slots] == [1, 2], "QR ids must be sorted sequence numbers"


def test_april_id_above_255_clamped():
    """AprilTag id > 255 固定输出 255(协议 id 字段 1 字节)。"""
    det = [(300, 100, 10, 20, 20), (42, 200, 10, 20, 20)]
    slots = build_slots(det)
    assert slots[0][0] == 255, "id 300 must clamp to 255"
    assert slots[1][0] == 42, "id 42 stays as-is"


def test_truncates_to_max_slots():
    """超过 MAX_SLOTS(25) 个只取排序后前 25 个。"""
    det = [(i, i * 10, 0, 10, 10) for i in range(30)]
    slots = build_slots(det)
    assert len(slots) == MAX_SLOTS == 25


def test_coords_scaled_and_conf_100():
    """坐标 ×scale(默认2, QVGA→VGA),conf 固定 100。"""
    det = [(1, 100, 50, 20, 30)]
    slots = build_slots(det)
    x, y, w, h, conf = slots[0][1], slots[0][2], slots[0][3], slots[0][4], slots[0][5]
    assert (x, y, w, h) == (200, 100, 40, 60)
    assert conf == 100


def test_empty_detected_returns_empty():
    """无检测目标 → 空列表。"""
    assert build_slots([]) == []


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

- [ ] **Step 2: 运行确认失败**

Run: `python tests/test_tag_scan.py`
Expected: FAIL — `ImportError: cannot import name 'build_slots' from 'tag_scan'`（文件尚不存在）

- [ ] **Step 3: 实现 `core/tag_scan.py`**

新建 `core/tag_scan.py`：

```python
# core/tag_scan.py — 标签检测结果排序/截断/id 映射（纯 Python，无板端依赖）
#
# tag_detect 每帧全屏扫描后调用:排序(最左=目标1) → 截断 25 → id 编码 → 坐标缩放。
# 纯内置类型,host 可真单元测试。MAX_SLOTS 与 comm/host_api.MAX_ID_SLOTS 对齐。

MAX_SLOTS = 25


def build_slots(detected, qr_mode=False, scale=2):
    """把检测列表转为上传槽位列表。

    Args:
        detected: list[(code_id, x, y, w, h)] — code_id 为 int(AprilTag) 或
                  str(QR payload);坐标均为检测通道(QVGA)整数。
        qr_mode: True=QR 功能(id 取排序后序号 1..N);False=AprilTag(id 取码值)。
        scale: 坐标缩放系数(QVGA→VGA 用 2)。

    Returns:
        list[(id_val, x*scale, y*scale, w*scale, h*scale, 100)]:
        按 (x, y) 升序(最左边=目标1),截断前 MAX_SLOTS 个。
        AprilTag 码值 >255 固定输出 255(协议 id 字段 1 字节)。
    """
    ordered = sorted(detected, key=lambda t: (t[1], t[2]))
    ordered = ordered[:MAX_SLOTS]
    slots = []
    for i, item in enumerate(ordered):
        code_id = item[0]
        x, y, w, h = item[1], item[2], item[3], item[4]
        if qr_mode:
            id_val = i + 1
        else:
            id_val = code_id if code_id <= 255 else 255
        slots.append((id_val, x * scale, y * scale, w * scale, h * scale, 100))
    return slots
```

- [ ] **Step 4: 运行确认通过**

Run: `python tests/test_tag_scan.py`
Expected: 全部 `PASS` + `ALL PASS`

- [ ] **Step 5: 语法检查 + 提交**

Run: `python -m py_compile core/tag_scan.py`

```bash
git add core/tag_scan.py tests/test_tag_scan.py
git commit -m "feat(tag_scan): 标签排序/截断/id 映射纯逻辑模块
- 按 (x,y) 升序,最左=目标1,截断 MAX_SLOTS=25
- AprilTag id>255 截断为 255;QR 模式 id=排序序号
- 坐标 ×scale,conf=100;纯 Python host 可真单测"
```

---

### Task 2: `comm/host_api.py` — send_id_data 动态槽位

**Files:**
- Modify: `comm/host_api.py`（`send_id_data` 194-227 行、`__init__` 预分配缓冲 103-108 行、`send_frame` 154-178 行）
- Modify: `tests/test_host_api.py`（新增动态载荷 AST 断言）

- [ ] **Step 1: 写失败测试（新增 AST 断言）**

在 `tests/test_host_api.py` 末尾（`test_runner` 前）追加：

```python
def test_send_id_data_supports_dynamic_slot_count():
    """send_id_data 须按实际 slots 数量编码(N*10B),不得固定 4 槽/40B。

    N = min(len(slots), MAX_ID_SLOTS);slots=None/空 → 0 字节载荷。
    """
    src = _src()
    tree = ast.parse(src, filename=HOST_API_PATH)
    cls = _class_node(tree, "HostAPI")
    m = _method_node(cls, "send_id_data")
    seg = ast.get_source_segment(src, m) or ""
    assert "MAX_ID_SLOTS" in seg, "send_id_data must cap at MAX_ID_SLOTS"
    assert "min(" in seg, "send_id_data must use min(len(slots), MAX_ID_SLOTS)"
    assert "n * 10" in seg or "n*10" in seg, \
        "payload length must be n * 10 (dynamic), not fixed 40"
    # 不得再有固定 4 槽循环(range(4))或 40B 清零
    assert "range(4)" not in seg, "must not loop fixed range(4)"
    assert "for i in range(40)" not in seg, "must not zero-fill fixed 40 bytes"


def test_host_api_has_max_id_slots_constant():
    """HostAPI 必须定义 MAX_ID_SLOTS = 25(对齐 tag_scan.MAX_SLOTS)。"""
    src = _src()
    tree = ast.parse(src, filename=HOST_API_PATH)
    cls = _class_node(tree, "HostAPI")
    found = None
    for n in cls.body:
        if isinstance(n, ast.Assign):
            for t in n.targets:
                if isinstance(t, ast.Name) and t.id == "MAX_ID_SLOTS":
                    found = n.value
    assert found is not None, "MAX_ID_SLOTS constant missing in HostAPI"
    assert isinstance(found, ast.Constant) and found.value == 25, \
        "MAX_ID_SLOTS must be 25"
    # 模块级或类级对齐注释(防漂移)
    src2 = _src()
    assert "tag_scan" in src2, "MAX_ID_SLOTS must note alignment with tag_scan"


def test_id_payload_buffer_grown_to_250():
    """_id_payload 预分配须扩到 250B(25槽×10B),_tx 扩到 ≥257B。"""
    src = _src()
    tree = ast.parse(src, filename=HOST_API_PATH)
    cls = _class_node(tree, "HostAPI")
    init = _method_node(cls, "__init__")
    seg = ast.get_source_segment(src, init) or ""
    assert "bytearray(250)" in seg, "_id_payload must be bytearray(250)"
    assert "bytearray(257)" in seg, "_tx must be bytearray(257) (5+250+chk+TAIL)"


def test_send_frame_accepts_optional_length():
    """send_frame 须支持可选 length 参数(零分配动态载荷)。"""
    src = _src()
    tree = ast.parse(src, filename=HOST_API_PATH)
    cls = _class_node(tree, "HostAPI")
    m = _method_node(cls, "send_frame")
    args = [a.arg for a in m.args.args]
    assert "length" in args, "send_frame must accept optional length param"
    seg = ast.get_source_segment(src, m) or ""
    assert "len(payload)" in seg, "length must default to len(payload)"
```

- [ ] **Step 2: 运行确认失败**

Run: `python tests/test_host_api.py`
Expected: 新增 4 项 `FAIL`（`MAX_ID_SLOTS` 不存在、`bytearray(250)` 不存在、`length` 参数不存在、动态循环不存在），原有测试仍 PASS

- [ ] **Step 3: 实现 host_api 修改**

修改 `comm/host_api.py`：

**(a) 类常量区**（`TYPE_IMAGE_CLASSIFY = 0x13` 之后、`CATEGORY_TYPE` 之前）新增：

```python
    # 单帧 ID 数据最大槽位数(25×10B=250B ≤ length 字段 255 上限)。
    # 与 core/tag_scan.MAX_SLOTS 对齐;tag_detect 全屏扫描动态上报用。
    MAX_ID_SLOTS = 25
```

**(b) `__init__` 预分配缓冲**（第 103-108 行区域）改为：

```python
        # 预分配发送帧缓冲(复用,每帧零分配 → 防主菜单挂机 mem 线性泄漏)。
        # 帧 = HEAD+SRC+DST+length(4) + type(1) + payload(≤250) + chk(1) + TAIL(1)。
        # 数据帧 payload 上限 250(25槽×10B,tag_detect 动态) → 总 257;握手应答 payload 15 → 总 22。
        self._tx = bytearray(257)
        self._tx_len = 0
        # 预分配 id 数据载荷缓冲(250B),send_id_data 每帧复用,零分配。
        self._id_payload = bytearray(250)
```

**(c) `send_frame`** 签名与开头改为：

```python
    def send_frame(self, msg_type, payload=b'', length=None):
        """组装并发送完整协议帧(预分配 _tx 复用,每帧零临时分配)。

        Args:
            msg_type: 类型码 (int, 1字节)
            payload: 负载数据 (bytes/bytearray)
            length: 可选实际发送字节数(默认 len(payload);动态载荷用
                    前 N*10 字节切片,避免再分配)
        """
        if length is None:
            length = len(payload)  # 主机 dataAgreeAnalys: data[3]=length 只算 payload,type 在 data[4] 不计入
```

（`tx[3] = length` 与后续 `for i in range(length)` 等保持不变）

**(d) `send_id_data` 整体替换**（194-227 行）：

```python
    def send_id_data(self, msg_type, slots=None):
        """发送 N 组 ID 数据(动态数量,泛化 send_face_data,所有脚本共用)。

        Args:
            msg_type: 类型码 (int, 1字节)
            slots: list 或 None。每元素 (id,x,y,w,h,conf)。
                   N = min(len(slots), MAX_ID_SLOTS);None/空 → 0 字节载荷
                   (主菜单/相机"无目标"场景,主机按 length 解析)。
                   每组 10 字节: id(1B) + x(2B BE) + y(2B BE)
                                + w(2B BE) + h(2B BE) + conf(1B)
                   ⚠️ 大端(BE):对齐主机 _camer_cam_data 大端解析
                   (data[off+1]<<8)|data[off+2]。小端会致坐标值错乱。
        """
        payload = self._id_payload  # 预分配复用,每帧零分配
        n = 0
        if slots is not None:
            n = min(len(slots), self.MAX_ID_SLOTS)
        for i in range(n):
            off = i * 10
            fid, x, y, w, h, conf = slots[i]
            payload[off]     = fid & 0xFF
            payload[off + 1] = (x >> 8) & 0xFF  # 大端:高字节在前
            payload[off + 2] = x & 0xFF
            payload[off + 3] = (y >> 8) & 0xFF
            payload[off + 4] = y & 0xFF
            payload[off + 5] = (w >> 8) & 0xFF
            payload[off + 6] = w & 0xFF
            payload[off + 7] = (h >> 8) & 0xFF
            payload[off + 8] = h & 0xFF
            payload[off + 9] = conf & 0xFF
        # 只发前 n*10 字节:length 参数避免切片分配(零分配保持)
        self.send_frame(msg_type, payload, length=n * 10)
```

- [ ] **Step 4: 运行确认通过**

Run: `python tests/test_host_api.py`
Expected: 全部 `PASS` + `ALL PASS`（含原 16 项 + 新增 4 项）

- [ ] **Step 5: 回归其他依赖 send_id_data 的测试**

Run: `python tests/test_host_api_switch.py`、`python tests/test_host_tick_wiring.py`
Expected: 均 `ALL PASS`（tick/切换逻辑未变）

- [ ] **Step 6: 提交**

```bash
git add comm/host_api.py tests/test_host_api.py
git commit -m "feat(host_api): send_id_data 支持动态 N 槽载荷
- N=min(len(slots), MAX_ID_SLOTS=25),slots=None/空 → 0B
- send_frame 加可选 length 参数(零分配);_id_payload 250B,_tx 257B
- 帧格式不变,length 字段仍 1 字节;face/object 等 4 槽调用兼容"
```

---

### Task 3: `scripts/tag_detect/app.py` — 移除学习链路，全屏扫描

**Files:**
- Modify: `scripts/tag_detect/app.py`（整文件重写核心逻辑）
- Modify: `tests/test_tag_detect_app.py`（契约更新）
- Modify: `resource/i18n/zh_CN.json`、`resource/i18n/en_US.json`（可选清理，见 Step 5）

- [ ] **Step 1: 更新测试为失败状态（契约反转）**

重写 `tests/test_tag_detect_app.py` 中以下测试：

```python
def test_on_frame_uses_tag_scan_build_slots_not_registry():
    """on_frame 须走 tag_scan.build_slots;不得再有按键注册链路。"""
    seg = ast.get_source_segment(_app_src(), _func("on_frame")) or ""
    src = _app_src()
    assert "build_slots" in seg, "on_frame must call tag_scan.build_slots"
    assert "tag_scan" in src, "app must import core.tag_scan"
    # 注册链路已移除(注意:断言扫 AST 源码,注释中不得含这些词)
    assert "try_register" not in src
    assert "registrar" not in src
    assert "TagDB" not in src
    assert "poll_k2" not in src
    assert "has_pending" not in src


def test_on_frame_draws_all_white_boxes():
    """画框统一白色,无 BOX_COLORS 取色/循环。"""
    src = _app_src()
    seg = ast.get_source_segment(src, _func("on_frame")) or ""
    assert "BOX_COLORS" not in src, "must not keep per-slot BOX_COLORS"
    assert "(255, 255, 255" in seg or "0xFFFFFF" in seg or "WHITE" in seg, \
        "on_frame must draw boxes in uniform white"
    assert "draw_rectangle" in seg, "on_frame must draw rectangles"
    assert "draw_string_advanced" in seg, "on_frame must draw code-value labels"


def test_on_frame_no_fixed_four_slots():
    """on_frame 不得构造固定 4 槽;槽位数量由 build_slots 动态决定。"""
    seg = ast.get_source_segment(_app_src(), _func("on_frame")) or ""
    assert "slots" in seg, "on_frame must build slots from build_slots"
    assert "host_tick" in seg, "on_frame must call host_tick(slots)"


def test_run_removes_db_and_registry_init():
    """run() 不得再加载 DB/初始化注册器/轮询按键。"""
    src = _app_src()
    seg = ast.get_source_segment(src, _func("run")) or ""
    assert "load_from_disk" not in src
    assert "flush_to_disk" not in src
    assert "IdRegistry" not in src
    assert "_init_registry" not in src
    assert "poll_k2" not in seg
    assert "_process_overlay_close" not in seg


def test_db_path_constants_removed():
    """模块级 DB 路径常量须移除。"""
    src = _app_src()
    assert "_APRIL_DB_PATH" not in src
    assert "_QR_DB_PATH" not in src


def test_overlay_handlers_removed_and_list_is_placeholder():
    """浮层处理器移除;list 图标不绑定点击事件(纯占位)。"""
    for fn in ["_on_list_clicked", "_on_clear_clicked", "_on_save_clicked",
               "_on_overlay_clicked", "_process_overlay_close"]:
        tree = _app_tree()
        found = any(isinstance(n, ast.FunctionDef) and n.name == fn
                    for n in tree.body)
        assert not found, "must NOT define %s (removed with learning flow)" % fn
    seg = ast.get_source_segment(_app_src(), _func("_build_ui")) or ""
    assert "_on_list_clicked" not in seg, "list button must not bind _on_list_clicked"


def test_on_frame_keeps_crosshair_and_detection():
    """检测通道/十字线/双功能扫描保留。"""
    seg = ast.get_source_segment(_app_src(), _func("on_frame")) or ""
    assert "CAM_CHN_ID_1" in seg, "on_frame must snapshot chn=CAM_CHN_ID_1"
    assert "find_apriltags" in seg, "on_frame must call find_apriltags"
    assert "find_qrcodes" in seg, "on_frame must call find_qrcodes"
    assert "draw_cross" in seg, "on_frame must draw a center crosshair"
    assert "320" in seg and "240" in seg, "crosshair must be at (320, 240)"
```

同时删除原测试文件中这些测试（已被新契约取代）：
- `test_on_frame_uses_id_registry_with_tag_db_registrar`
- `test_tag_db_paths_are_module_level_for_on_frame_flush`
- `test_on_frame_white_unknown_and_slot_color_boxes`
- `test_list_overlay_handlers_exist`
- `test_list_button_binds_click_and_screen_closes_overlay`
- `test_clear_clicked_clears_active_db_and_refreshes`
- `test_run_loop_processes_overlay_close`

保留：`test_run_entrypoint_exists`、`test_on_frame_calls_find_apriltags_and_find_qrcodes`、`test_on_frame_uses_cam_chn_id_1_for_detection`、`test_on_frame_calls_host_tick_with_slots`、`test_run_loop_has_exitpoint_and_task_handler`、`test_app_uses_i18n_not_hardcoded`、`test_on_frame_draws_center_green_crosshair`。

- [ ] **Step 2: 运行确认失败**

Run: `python tests/test_tag_detect_app.py`
Expected: 新契约测试 `FAIL`（旧 app.py 仍含 try_register/TagDB/BOX_COLORS 等）

- [ ] **Step 3: 重写 `scripts/tag_detect/app.py`**

整文件替换为：

```python
# scripts/tag_detect/app.py — AprilTag + 二维码双功能标签识别。
#
# 复用 _template 单线程主循环。chn1 QVGA RGB565 做检测(官方 demo 同款),
# chn0 VGA RGB888 显示。两功能底栏切换(选中置绿)。每帧全屏扫描所有可识别
# 码 → core/tag_scan.build_slots 按 (x,y) 排序(最左=目标1) → 截断 25 →
# id 编码(AprilTag=实际码值>255截断为255 / QR=排序序号) → 动态数量上报
# (类型 0x04,载荷 N×10B)。画框统一白色。无按键学习链路。

import os
import sys
import time
import image
import lvgl as lv
from media.display import Display
from media.sensor import CAM_CHN_ID_0, CAM_CHN_ID_1
from core.icon_cache import icon_cache
from core.font_manager import fonts
from core import tag_scan

BAR_H = 52
PREVIEW_Y = BAR_H
PREVIEW_H = 376
BAR_BG = 0x1A1A1A
CARD_BG = 0x2A2A2A
CARD_ACTIVE = 0x2E7D32   # 选中卡片绿色
# chn1 QVGA(320x240) -> chn0 VGA(640x480):坐标 x2 整数缩放
DET_SCALE = 2
# 统一画框颜色:白色(用户明确要求,不按序号取色)
BOX_WHITE = (0xFF, 0xFF, 0xFF, 0xFF)

_RUNTIME = None
_screen = None
_top_bar = None
_bottom_bar = None
_preview = None
_active_fn = "april"      # "april" | "qr"
_april_card = None
_qr_card = None


def on_frame(img):
    """chn1 检测 → tag_scan 排序/截断/id 映射 → chn0 全白框 → host_tick 动态槽。"""
    if _RUNTIME is None:
        return
    img_det = _RUNTIME.sensor.snapshot(chn=CAM_CHN_ID_1)
    detected = []   # [(code_id, x, y, w, h), ...]

    if _active_fn == "april":
        try:
            tags = img_det.find_apriltags(families=image.TAG36H11)
        except Exception as e:
            print("[tag_detect] apriltag error: %s" % e)
            tags = []
        for tag in tags:
            rect = tag.rect()   # [x, y, w, h] in QVGA
            detected.append((tag.id(), rect[0], rect[1], rect[2], rect[3]))
    else:
        try:
            codes = img_det.find_qrcodes()
        except Exception as e:
            print("[tag_detect] qr error: %s" % e)
            codes = []
        for code in codes:
            rect = code.rect()
            detected.append((code.payload(), rect[0], rect[1], rect[2], rect[3]))

    slots = tag_scan.build_slots(detected, qr_mode=(_active_fn != "april"))

    # 全白框 + 码值标签(排序后 slots 与 detected 同序,取 detected[i][0] 显示)
    for i, (_id_val, x, y, w, h, _conf) in enumerate(slots):
        img.draw_rectangle(x, y, w, h, color=BOX_WHITE, thickness=2)
        img.draw_string_advanced(x, y - 24, 24, str(detected[i][0]),
                                 color=BOX_WHITE)

    # 屏幕居中绿色十字(对准参考,小一点):VGA 640x480 中心 (320, 240)
    img.draw_cross(320, 240, color=(0xFF, 0x00, 0xFF, 0x00), size=20, thickness=2)

    if _RUNTIME is not None and _RUNTIME.host is not None:
        _RUNTIME.host_tick(slots)


def _switch_fn(fn):
    """切换 AprilTag / QR 功能。"""
    global _active_fn
    if fn == _active_fn:
        return
    _active_fn = fn
    if _april_card is not None:
        _april_card.set_style_bg_color(
            lv.color_hex(CARD_ACTIVE if fn == "april" else CARD_BG), 0)
    if _qr_card is not None:
        _qr_card.set_style_bg_color(
            lv.color_hex(CARD_ACTIVE if fn == "qr" else CARD_BG), 0)


def _make_card(parent, label_key, fn, align_to):
    """建一个功能卡片(可点击切换)。返回 card obj。"""
    from ui.theme import make_back_bar_text_style
    card = lv.btn(parent)
    card.set_size(110, 40)
    card.align(lv.ALIGN.LEFT_MID, align_to, 0)
    card.set_style_bg_color(lv.color_hex(CARD_ACTIVE if _active_fn == fn else CARD_BG), 0)
    card.set_style_bg_opa(255, 0)
    card.set_style_radius(8, 0)
    card.set_style_border_width(0, 0)
    card.set_style_shadow_width(0, 0)
    lbl = lv.label(card)
    lbl.set_text(_RUNTIME.lang.t(label_key))
    lbl.add_style(make_back_bar_text_style(fonts.body), 0)
    lbl.center()

    def _on_click(e, _fn=fn):
        if e.get_code() == lv.EVENT.CLICKED:
            _switch_fn(_fn)
    card.add_event(_on_click, lv.EVENT.CLICKED, None)
    return card


def _build_ui(runtime, exit_flag):
    """顶栏(back+标题) + 透明预览 + 底栏(list占位图标 + AprilTag/QR卡片)。"""
    global _screen, _top_bar, _bottom_bar, _preview
    global _april_card, _qr_card
    screen = lv.scr_act()
    screen.set_style_bg_opa(0, 0)
    _screen = screen

    # 顶栏:返回钮 + 标题
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

    icon_data, icon_dsc = icon_cache.get_tag_icon("back")
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
    title.set_text(runtime.lang.t("category.tag_detect"))
    title.align(lv.ALIGN.CENTER, 0, 0)
    from ui.theme import make_back_bar_text_style
    title.add_style(make_back_bar_text_style(fonts.body), 0)

    # 透明预览区(透出 OSD1)
    _preview = lv.obj(screen)
    _preview.set_size(lv.pct(100), PREVIEW_H)
    _preview.set_pos(0, PREVIEW_Y)
    _preview.set_style_bg_opa(0, 0)
    _preview.set_style_border_width(0, 0)
    _preview.set_style_pad_all(0, 0)
    _preview.set_style_radius(0, 0)
    _preview.clear_flag(lv.obj.FLAG.SCROLLABLE)
    _preview.clear_flag(lv.obj.FLAG.CLICKABLE)

    # 底栏:list图标(纯占位,不绑定事件) + AprilTag卡片 + QR卡片
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
    # 不 add_flag(CLICKABLE)、不加事件:纯占位(用户要求)
    list_icon_data, list_icon_dsc = icon_cache.get_tag_icon("list")
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

    _april_card = _make_card(_bottom_bar, "tag_detect.april_tag", "april", 56)
    _qr_card = _make_card(_bottom_bar, "tag_detect.qr_code", "qr", 174)


def _destroy_ui():
    global _screen, _top_bar, _bottom_bar, _preview, _april_card, _qr_card
    for obj in (_april_card, _qr_card, _top_bar, _bottom_bar, _preview):
        if obj is not None:
            try:
                obj.delete()
            except Exception:
                pass
    _april_card = None
    _qr_card = None
    _top_bar = None
    _bottom_bar = None
    _preview = None
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
    global _RUNTIME
    _RUNTIME = runtime
    exit_flag = [False]
    _build_ui(runtime, exit_flag)
    fc = 0
    try:
        while not exit_flag[0]:
            os.exitpoint()
            img = runtime.sensor.snapshot(chn=CAM_CHN_ID_0)
            try:
                on_frame(img)
            except Exception as e:
                print("[tag_detect] on_frame error: %s" % e)
                try:
                    sys.print_exception(e)
                except Exception:
                    pass
            Display.show_image(img, 0, 0, Display.LAYER_OSD1)
            time.sleep_ms(lv.task_handler())
            fc += 1
            if fc % 30 == 0:
                print("[tag_detect] fc=%d" % fc)
    finally:
        _destroy_ui()
        _RUNTIME = None
```

⚠️ **注意**：上面源码注释里不要出现 `try_register` / `registrar` / `TagDB` / `poll_k2` / `has_pending` / `load_from_disk` / `flush_to_disk` / `IdRegistry` / `_init_registry` 这些词（AST 文本断言会扫到注释，CLAUDE.md 明确要求注释勿含被断言关键词）。

- [ ] **Step 4: 运行确认通过**

Run: `python tests/test_tag_detect_app.py`
Expected: 全部 `PASS` + `ALL PASS`

- [ ] **Step 5: i18n 清理（可选但推荐）**

`resource/i18n/zh_CN.json` 与 `resource/i18n/en_US.json` 的 `tag_detect` 段，删除不再使用的 `registered`/`clear`/`save` 键（保留 `april_tag`/`qr_code`）：

```json
  "tag_detect": {
    "april_tag": "AprilTag",
    "qr_code": "二维码"
  },
```

（en_US 同理，`qr_code` 为 "QR Code"。注意保留 `april_tag`/`qr_code` 两个键；若其他测试断言 `registered` 存在，则跳过本步——运行 `python tests/test_face_detect_i18n.py`、`python tests/test_i18n_tag.py` 确认无回归。）

- [ ] **Step 6: 语法检查 + 提交**

Run: `python -m py_compile scripts/tag_detect/app.py core/tag_scan.py comm/host_api.py`

```bash
git add scripts/tag_detect/app.py tests/test_tag_detect_app.py resource/i18n/zh_CN.json resource/i18n/en_US.json
git commit -m "feat(tag_detect): 全屏扫描+动态排序上报,移除按键学习链路
- on_frame 走 tag_scan.build_slots:(x,y)排序/截断25/id编码
- AprilTag id=实际码值(>255→255),QR id=排序序号,动态 N 槽上报
- 画框统一白色;list 图标纯占位;移除 KEY2/TagDB/浮层/计数"
```

---

### Task 4: 协议文档更新

**Files:**
- Modify: `通讯协议.txt`

- [ ] **Step 1: 更新 `通讯协议.txt`**

在"标签识别:0x04"相关处（文件末尾"例如在主菜单栏下的话…"段落之后）追加：

```text
  标签识别(0x04)动态数据说明:
    载荷 = N × 10 字节,N = 本帧检测到的目标数量(N ≤ 25),每帧动态,
    不固定、不补齐。每帧按屏幕从左到右排序:x 最小的为目标1,依此类推。
    每组 10 字节: id(1B) + x(2B BE) + y(2B BE) + w(2B BE) + h(2B BE) + conf(1B)
    id 语义由板端当前激活功能决定:
      - AprilTag 功能: id = 实际 AprilTag 码值(0~586;>255 固定输出 255)
      - 二维码功能:   id = 排序序号(第1个=1, 第2个=2, ...)
    主机须按帧 length 字段解析 N 组,不得假设固定 4 组/40 字节。
```

- [ ] **Step 2: 提交**

```bash
git add 通讯协议.txt
git commit -m "docs(protocol): 标签识别 0x04 动态 N×10B 载荷说明
- N≤25 动态上报,不固定不补齐;按 x 从左到右排序
- id 语义:AprilTag=实际码值(>255→255),QR=排序序号
- 主机按 length 解析,不假设固定 40B"
```

---

### Task 5: 全量回归 + 项目记录

**Files:**
- Modify: `项目记录.md`

- [ ] **Step 1: 全量回归**

Run:

```
python tests/test_tag_scan.py
python tests/test_tag_detect_app.py
python tests/test_host_api.py
python tests/test_host_api_switch.py
python tests/test_host_tick_wiring.py
python tests/test_framework.py
python tests/test_tag_db.py
python tests/test_tag_db_persist.py
python tests/test_id_registry.py
python tests/test_i18n_tag.py
```

Expected: 全部 `ALL PASS`

- [ ] **Step 2: 语法检查全量改动文件**

Run: `python -m py_compile core/tag_scan.py comm/host_api.py scripts/tag_detect/app.py`

- [ ] **Step 3: 追加 `项目记录.md`**

按日期分节（2026-08-03），记录：现象/根因/改动/验证/主机侧注意：

```markdown
## 2026-08-03 tag_detect 改全屏扫描+动态排序上报

- **需求**：取消按键学习 ID（KEY2 注册），改为每帧全屏扫描所有可识别 AprilTag/QR 码，
  按屏幕从左到右排序（x 最小 = 目标1），动态数量上报；id 直接用实际码值。
- **改动**：
  - 新增 `core/tag_scan.py`（纯 Python）：`build_slots(detected, qr_mode, scale=2)`
    排序（x,y 升序）→ 截断 25 → id 编码 → 坐标 ×2。host 可真单测。
  - `comm/host_api.py`：`send_id_data` 支持动态 N 槽（N≤25，N×10B 载荷，
    slots=None/空 → 0B）；`send_frame` 加可选 `length` 参数（零分配）；
    `_id_payload` 250B、`_tx` 257B。帧格式不变，face/object 等 4 槽调用兼容。
  - `scripts/tag_detect/app.py`：移除 KEY2/TagDB/持久化/清空保存浮层/注册计数；
    on_frame 走 `tag_scan.build_slots`，画框统一白色；list 图标纯占位。
  - id 语义：AprilTag = 实际码值（TAG36H11 0~586，>255 固定 255）；QR = 排序序号。
- **验证**：`tests/test_tag_scan.py`（真单测）、`test_tag_detect_app.py`（AST 契约反转）、
  `test_host_api.py`（动态载荷 AST）全 PASS；host_api_switch/host_tick_wiring/framework/tag_db/id_registry/i18n 回归全 PASS。
- **主机侧注意**：0x04 帧载荷由固定 40B 变为动态 N×10B（N≤25），主机须按 length 解析；
  id 语义由板端当前功能决定（AprilTag=码值 / QR=序号）。
- **坑**：AST 契约断言扫源码文本，注释中不得含 `try_register`/`TagDB` 等被断言关键词
  （重写 app.py 时注释已规避）。
```

- [ ] **Step 4: 提交**

```bash
git add 项目记录.md
git commit -m "docs: 记录 tag_detect 全屏扫描+动态排序上报改动(2026-08-03)"
```

- [ ] **Step 5: 推送**

```bash
git push
```

（若 SSH key 未配置导致 push 失败，告知用户手动推送，本地 commit 已完整。）

---

## Self-Review

**Spec 覆盖检查：**
- ✅ 需求1 保留双功能：Task 3 on_frame 保留 `find_apriltags`/`find_qrcodes` + `_switch_fn` + 双卡片
- ✅ 需求2 取消按键学习：Task 3 移除 IdRegistry/TagDB/浮层/计数，list 纯占位
- ✅ 需求3 排序：Task 1 `build_slots` 按 `(x, y)` 升序
- ✅ 需求4 id 字段：Task 1（AprilTag>255→255 / QR 序号）
- ✅ 需求5 动态数量：Task 2 `send_id_data` 动态 N 槽，N≤25，不补齐
- ✅ 需求6 全白框：Task 3 `BOX_WHITE` 统一
- ✅ 需求7 帧格式不变：Task 2 仅载荷长度动态，length 仍 1 字节
- ✅ 协议文档：Task 4
- ✅ 测试：Task 1/2/3 + Task 5 回归

**占位符扫描：** 无 TBD/TODO；每个代码步骤含完整代码。

**类型一致性：** `build_slots(detected, qr_mode, scale=2)` 签名在 Task 1 定义、Task 3 调用一致；`MAX_SLOTS`(tag_scan) 与 `MAX_ID_SLOTS`(host_api) 各自独立但值对齐（25）；`send_frame(msg_type, payload, length=None)` 在 Task 2 定义并被 send_id_data 调用。
