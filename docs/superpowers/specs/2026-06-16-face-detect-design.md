# 人脸识别APP 设计规格

> **状态**: 已确认 | **日期**: 2026-06-16 | **版本**: v1.0

## 1. 概述

**目标**: 在CamerAi上扩展人脸识别脚本APP，复用相机APP的顶栏/底栏通用UI布局，支持人脸注册（最多4个ID）、实时人脸检测与识别，并通过UART按自定义协议向主机发送4组识别数据。

**参考**: 正点原子K230D实验4 人脸识别实验（main1.py注册 + main2.py识别）

**技术栈**: K230 MicroPython + LVGL v8 + face_detection_320.kmodel + face_recognition.kmodel + aidemo + UART1

---

## 2. 架构概览

```
┌──────────────────────────────────────────────────────────┐
│                    ScriptRunner                           │
│  tick() → poll_handshake() → on_frame() → send_frame()  │
├──────────────────────────────────────────────────────────┤
│  FaceDetectApp (scripts/face_detect/app.py)              │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐               │
│  │ AI推理    │  │ UI渲染    │  │ 协议发送  │               │
│  │ FaceDet  │  │ LVGL框   │  │ send_face │               │
│  │ FaceReg  │  │ 十字架    │  │ _data()   │               │
│  │ DB搜索   │  │ 弹窗菜单  │  │           │               │
│  └──────────┘  └──────────┘  └──────────┘               │
├──────────────────────────────────────────────────────────┤
│  HostAPI (comm/host_api.py)                              │
│  UART1 TX=GPIO40 RX=GPIO41 @115200                       │
│  帧: 5A + 源 + 目标 + 长度 + 类型 + 数据 + 校验 + A5     │
├──────────────────────────────────────────────────────────┤
│  LCD (hw/lcd.py)                                         │
│  chn0=预览 chn1=拍照 chn2=AI推理(RGB888P 1024×768)      │
└──────────────────────────────────────────────────────────┘
```

**改动文件清单**:

| 文件 | 操作 | 说明 |
|------|------|------|
| `hw/lcd.py` | 修改 | 启动期声明chn2 AI推理通道 |
| `comm/host_api.py` | 重写 | UART初始化 + 协议组装 + 握手状态机 |
| `scripts/face_detect/app.py` | 新建 | 人脸识别APP主体（~700行） |
| `scripts/face_detect/manifest.json` | 新建 | 脚本元数据 |
| `scripts/face_detect/__init__.py` | 新建 | 空init |
| `core/icon_cache.py` | 修改 | 预读人脸识别图标（list.png等） |
| `core/script_runner.py` | 修改 | tick()中集成握手轮询 + K2按键轮询 |
| `config/categories.json` | 已存在 | face_detect条目已注册，无需改动 |

---

## 3. 硬件层 — AI推理通道声明

**文件**: `hw/lcd.py`

在`LCD.__init__`中，`MediaManager.init()`之前，紧接chn1声明之后追加chn2：

```python
# chn2 — AI推理输入：RGB888_PLANAR格式，1024×768（XGA）
# 所有AI类APP（人脸/物体/颜色识别等）共用此通道。
# 必须启动期声明——MediaManager.init()之后池不可重建（K230 pitfall #15）
self.sensor.set_framesize(Sensor.XGA, chn=CAM_CHN_ID_2)
self.sensor.set_pixformat(Sensor.RGB888_PLANAR, chn=CAM_CHN_ID_2)
self.ai_chn = CAM_CHN_ID_2
```

同时提供便捷方法：

```python
def get_ai_frame(self):
    """从chn2获取一帧AI推理图像，转为CHW numpy数组（对齐Demo格式）"""
    import ulab.numpy as np
    img = self.sensor.snapshot(chn=self.ai_chn)
    if img is None:
        return None
    rgb888 = img.to_rgb888()
    hwc = rgb888.to_numpy_ref()
    shape = hwc.shape
    tmp = hwc.reshape((shape[0] * shape[1], shape[2]))
    trans = tmp.transpose()
    chw = trans.copy()
    return chw.reshape((1, shape[2], shape[0], shape[1]))
```

---

## 4. 通信层 — HostAPI协议栈

**文件**: `comm/host_api.py`

### 4.1 硬件初始化

```python
from machine import UART, Pin

class HostAPI:
    FRAME_HEAD = 0x5A
    FRAME_TAIL = 0xA5  # 帧尾
    SRC_ADDR = 0xA7
    DST_ADDR = 0x97

    # 类型码
    TYPE_MAIN_MENU   = 0x01
    TYPE_CAMERA      = 0x02
    TYPE_FACE_DETECT = 0x03
    # ... 其余类型见协议

    def __init__(self):
        self._uart = UART(1, baudrate=115200, tx=Pin(40), rx=Pin(41))
        self._connected = False
        self._handlers = {}
```

### 4.2 协议帧格式

```
字节0:    0x5A          帧头
字节1:    0xA7          源地址（摄像头）
字节2:    0x97          目标地址（主机）
字节3:    长度          数据长度（类型字节 + 数据字节数）
字节4-N:  类型+数据     类型1字节 + 实际数据
倒数第2:  校验和        从字节0到数据最后一位累加，取低8位
倒数第1:  0xA5          帧尾
```

### 4.3 核心方法

```python
def _checksum(self, data):
    """计算校验和：所有字节累加取低8位"""
    s = 0
    for b in data:
        s = (s + b) & 0xFF
    return s

def send_frame(self, msg_type, payload=b''):
    """组装并发送完整帧"""
    data = bytearray([msg_type]) + bytes(payload)
    length = len(data)
    header = bytearray([self.FRAME_HEAD, self.SRC_ADDR, self.DST_ADDR, length])
    chk = self._checksum(header + data)
    frame = bytes(header) + bytes(data) + bytearray([chk, self.FRAME_TAIL])
    self._uart.write(frame)

def poll_handshake(self):
    """非阻塞检测握手帧：收到主机握手 → 自动应答"""
    if self._uart.any() >= 14:  # 握手帧长度14字节
        raw = self._uart.read(self._uart.any())
        # 查找握手帧特征: 5A 97 98 0B 09 ...
        # 匹配到则应答
        self._send_handshake_reply()

def _send_handshake_reply(self):
    """应答握手: 0x09 + 'Play Application'"""
    self.send_frame(0x09, b"Play Application")
    self._connected = True

def send_face_data(self, slots):
    """发送4组人脸识别数据（类型0x03）
    slots: list[tuple] 4组 (id, x, y, w, h, confidence)
    每组10字节: id(1B) + x(2B) + y(2B) + w(2B) + h(2B) + conf(1B)
    未使用的slot全填0
    """
    buf = bytearray(40)
    for i, slot in enumerate(slots):
        off = i * 10
        if slot is not None:
            fid, x, y, w, h, conf = slot
            buf[off]     = fid & 0xFF
            buf[off+1]   = x & 0xFF
            buf[off+2]   = (x >> 8) & 0xFF
            buf[off+3]   = y & 0xFF
            buf[off+4]   = (y >> 8) & 0xFF
            buf[off+5]   = w & 0xFF
            buf[off+6]   = (w >> 8) & 0xFF
            buf[off+7]   = h & 0xFF
            buf[off+8]   = (h >> 8) & 0xFF
            buf[off+9]   = conf & 0xFF
        # else: 保持0
    self.send_frame(self.TYPE_FACE_DETECT, bytes(buf))
```

### 4.4 握手状态管理

```python
@property
def connected(self):
    return self._connected

def reset_connection(self):
    """UART超时/异常时重置连接状态，等待主机重新握手"""
    self._connected = False
```

---

## 5. 人脸识别核心 — FaceDetectApp

**文件**: `scripts/face_detect/app.py`（新建）

### 5.1 类结构与生命周期

```python
class FaceDetectApp(BaseScript):
    SCRIPT_ID = "face_detect"
    SELF_MANAGED_TOP_BAR = True

    def __init__(self):
        super().__init__()
        # AI模型
        self._face_det = None
        self._face_reg = None
        self._anchors = None

        # 人脸数据库（内存 + 文件）
        self._db_features = {}   # {1: np_array(128), 2: ..., 3: ..., 4: ...}
        self._db_dir = "/data/fac_db/"

        # UI对象
        self._screen = None
        self._top_bar = None
        self._bottom_bar = None
        self._crosshair_lines = []   # 十字架4条LVGL线
        self._face_boxes = []        # 当前帧人脸框对象列表
        self._popup = None           # 弹出菜单

        # 发送周期
        self._last_send_ticks = 0
        self._send_interval = 10     # ms
```

**on_enter流程**:
```
1. _init_ai_models()    → 加载kmodel + anchors + 初始化FaceDet/FaceReg
2. _init_db()           → 从/data/fac_db/加载已有id1~id4.bin
3. _build_ui()          → 构建顶栏 + 预览区(十字架) + 底栏
4. 等待主机握手          → host.poll_handshake()在on_frame中持续检测
```

**on_frame流程**（每帧调用）:
```
1. ctx.host.poll_handshake()              # 握手检测
2. frame = ctx.lcd.get_ai_frame()         # chn2 snapshot → CHW numpy
3. det_boxes, landms = face_det.run(frame)
4. recg_results = []                      # [(box, matched_id, score)]
   对每张脸: feature = face_reg.run(landm)
            → 与db_features比对 → matched_id or None
5. _update_face_boxes(det_boxes, recg_results)  # LVGL框 + ID标签
6. if ctx.host.connected:
       if ticks_diff(now, _last_send) >= 10ms:
           _send_recognition_data()
7. gc.collect()
```

**on_exit流程**:
```
1. face_det.deinit() + face_reg.deinit()
2. _destroy_ui()   → 删除所有LVGL对象
3. super().on_exit()
```

### 5.2 AI模型初始化

```python
def _init_ai_models(self):
    import ulab.numpy as np

    # kmodel路径（设备上）
    det_kmodel = "/sdcard/examples/kmodel/face_detection_320.kmodel"
    reg_kmodel = "/sdcard/examples/kmodel/face_recognition.kmodel"
    anchors_path = "/sdcard/examples/utils/prior_data_320.bin"

    det_input = [320, 320]
    reg_input = [112, 112]
    rgb888p = [1024, 768]  # 对齐Demo

    # 加载anchors
    anchors = np.fromfile(anchors_path, dtype=np.float)
    self._anchors = anchors.reshape((4200, 4))

    # 复用Demo的FaceDetApp和FaceRegistrationApp类
    # （将Demo代码中的类定义迁移到本文件）
    self._face_det = FaceDetApp(
        det_kmodel, model_input_size=det_input,
        anchors=self._anchors,
        confidence_threshold=0.5, nms_threshold=0.2,
        rgb888p_size=rgb888p, debug_mode=0)
    self._face_det.config_preprocess()

    self._face_reg = FaceRegistrationApp(
        reg_kmodel, model_input_size=reg_input,
        rgb888p_size=rgb888p, debug_mode=0)
```

### 5.3 人脸数据库管理

```python
def _init_db(self):
    """启动时加载已注册的人脸特征到内存"""
    import os, ulab.numpy as np
    try:
        os.mkdir(self._db_dir)
    except:
        pass
    for i in range(1, 5):
        path = f"{self._db_dir}id{i}.bin"
        try:
            with open(path, 'rb') as f:
                data = f.read()
            self._db_features[i] = np.frombuffer(data, dtype=np.float)
        except:
            pass  # 该槽位未注册，跳过

def _save_db(self):
    """将内存中的特征写回文件"""
    import os
    try:
        os.mkdir(self._db_dir)
    except:
        pass
    for i, feature in self._db_features.items():
        path = f"{self._db_dir}id{i}.bin"
        with open(path, 'wb') as f:
            f.write(feature.tobytes())

def _clear_db(self):
    """删除全部人脸数据"""
    import os
    for i in range(1, 5):
        try:
            os.remove(f"{self._db_dir}id{i}.bin")
        except:
            pass
    self._db_features.clear()

def _register_face(self, feature, slot_id):
    """注册人脸到指定槽位"""
    self._db_features[slot_id] = feature
    # 立即写文件
    path = f"{self._db_dir}id{slot_id}.bin"
    with open(path, 'wb') as f:
        f.write(feature.tobytes())

def _search_face(self, feature):
    """在数据库中搜索匹配的人脸，返回 (matched_id, score) 或 (None, 0)"""
    import ulab.numpy as np
    feature = feature / np.linalg.norm(feature)
    best_id = None
    best_score = 0.0
    threshold = 0.75  # 人脸识别阈值
    for i, db_feat in self._db_features.items():
        db_feat = db_feat / np.linalg.norm(db_feat)
        score = np.dot(feature, db_feat) / 2 + 0.5
        if score > best_score and score >= threshold:
            best_score = score
            best_id = i
    return best_id, best_score
```

### 5.4 K2按键注册逻辑

```python
# K2按键由ScriptRunner统一轮询（GPIO0），检测到下降沿后调用：
def on_key(self, key):
    if key == 'K2':  # GPIO0
        self._register_current_face()

def _register_current_face(self):
    """将当前画面中最大人脸注册到下一个空槽位"""
    if not self._current_landmarks or not self._current_frame_data:
        return

    # 找最大人脸
    if not self._current_boxes:
        self.ctx.buzzer.beep(ms=30)  # 短促声：无人脸
        return
    largest_idx = max(range(len(self._current_boxes)),
                      key=lambda i: self._current_boxes[i][2] * self._current_boxes[i][3])
    landm = self._current_landmarks[largest_idx]

    # 推理特征
    self._face_reg.config_preprocess(landm)
    feature = self._face_reg.run(self._current_frame_data)

    # 找空槽位（1→2→3→4）
    for slot in range(1, 5):
        if slot not in self._db_features:
            self._register_face(feature, slot)
            self._update_status_text()  # 更新底栏"已注册: ID1 ID2"
            self.ctx.buzzer.beep(ms=80)  # 成功长声
            return

    self.ctx.buzzer.beep(ms=200)  # 超长声：4个槽位已满
```

### 5.5 10ms发送逻辑

```python
def _send_recognition_data(self):
    """组装4组识别数据并通过UART发送"""
    import time as _time
    now = _time.ticks_ms()
    if _time.ticks_diff(now, self._last_send_ticks) < self._send_interval:
        return
    self._last_send_ticks = now

    slots = [None, None, None, None]  # 4个槽位

    # 填充已识别的人脸数据
    for i, (box, matched_id, score) in enumerate(self._recognition_results):
        if i >= 4:
            break
        x, y, w, h = box[:4]
        conf = int(score * 100)
        fid = matched_id if matched_id is not None else 0
        slots[i] = (fid, int(x), int(y), int(w), int(h), conf)

    self.ctx.host.send_face_data(slots)
```

---

## 6. UI层设计

### 6.1 布局常量

```python
BAR_H = 52               # 顶栏/底栏高度
PREVIEW_Y = BAR_H        # 预览区起始Y
PREVIEW_H = 376          # 480 - 52*2
BAR_BG = 0x1A1A1A        # 栏背景色
CROSSHAIR_COLOR = 0x44CC44  # 绿色十字架
BOX_COLORS = {
    1: 0x44CC44,   # ID1 绿色
    2: 0x4488FF,   # ID2 蓝色
    3: 0xFF8844,   # ID3 橙色
    4: 0xCC44FF,   # ID4 紫色
}
```

### 6.2 顶栏

复用Camera APP的`_build_top_bar()`模式：
- 返回按钮(48×48透明点击区 + back.png图标) → 点击触发`ctx.request_exit()`
- 标题居中(`ctx.lang.t("category.face_detect")`)

### 6.3 预览区十字架

```python
def _build_crosshair(self, parent):
    """在预览区中间绘制绿色十字瞄准线"""
    cx, cy = 320, 240 + BAR_H // 2  # 预览区中心
    arm = 30  # 十字臂长
    gap = 8   # 中心缺口

    # 绘制4段线（上、下、左、右，中心留空）
    for (x1, y1, x2, y2) in [
        (cx, cy - arm, cx, cy - gap),      # 上
        (cx, cy + gap, cx, cy + arm),      # 下
        (cx - arm, cy, cx - gap, cy),      # 左
        (cx + gap, cy, cx + arm, cy),      # 右
    ]:
        line = lv.obj(parent)
        line.set_size(abs(x2-x1) if x2!=x1 else 2, abs(y2-y1) if y2!=y1 else 2)
        line.set_pos(x1, y1)
        line.set_style_bg_color(lv.color_hex(CROSSHAIR_COLOR), 0)
        line.set_style_bg_opa(180, 0)
        line.set_style_border_width(0, 0)
        line.clear_flag(lv.obj.FLAG.SCROLLABLE)
        line.clear_flag(lv.obj.FLAG.CLICKABLE)
        self._crosshair_lines.append(line)
```

### 6.4 人脸框 + ID标签

```python
def _update_face_boxes(self, boxes, recg_results):
    """每帧重建人脸框（删除旧框 → 创建新框）"""
    # 1. 删除上一帧的框
    for obj in self._face_boxes:
        obj.delete()
    self._face_boxes = []

    # 2. 为每个检测结果创建新框
    for i, (box, matched_id, _) in enumerate(recg_results):
        x, y, w, h = [int(v) for v in box[:4]]
        # 坐标从AI输入(1024×768)映射到屏幕预览区(640×376)
        sx = x * 640 // 1024
        sy = PREVIEW_Y + y * 376 // 768
        sw = w * 640 // 1024
        sh = h * 376 // 768

        # 边框
        rect = lv.obj(self._preview_bg)
        rect.set_size(sw, sh)
        rect.set_pos(sx, sy)
        rect.set_style_bg_opa(0, 0)
        rect.set_style_border_width(3, 0)
        color = BOX_COLORS.get(matched_id, 0xFFFFFF)
        rect.set_style_border_color(lv.color_hex(color), 0)
        rect.set_style_radius(0, 0)
        rect.clear_flag(lv.obj.FLAG.SCROLLABLE)
        rect.clear_flag(lv.obj.FLAG.CLICKABLE)
        self._face_boxes.append(rect)

        # ID标签（框内左上角）
        if matched_id is not None:
            label = lv.label(rect)
            label.set_text(f"ID{matched_id}")
            label.set_pos(2, 2)
            label.set_style_text_color(lv.color_hex(color), 0)
            label.set_style_bg_opa(160, 0)
            label.set_style_bg_color(lv.color_hex(0x000000), 0)
            self._face_boxes.append(label)
```

### 6.5 底栏

```python
def _build_bottom_bar(self):
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

    # ── list.png按钮（左侧）──
    list_btn = lv.obj(bar)
    list_btn.set_size(48, 48)
    list_btn.align(lv.ALIGN.LEFT_MID, 24, 0)
    # ... 透明按钮样式 ...
    list_btn.add_event(lambda e: self._on_list_click(), lv.EVENT.CLICKED)

    # list图标（使用icon_cache预读的PNG）
    icon_data, icon_dsc = icon_cache.get_face_icon("list")
    _make_icon(list_btn, icon_data, icon_dsc, 40, 4, 0)

    # ── 状态文字（中间）──
    self._status_label = lv.label(bar)
    self._status_label.align(lv.ALIGN.CENTER, 0, 0)
    self._update_status_text()
```

### 6.6 弹出菜单

```python
def _on_list_click(self):
    if self._popup is not None:
        self._dismiss_popup()
        return
    self._show_popup()

def _show_popup(self):
    """在底栏上方显示保存/清除菜单"""
    popup = lv.obj(self._screen)
    popup.set_size(160, 130)
    popup.align(lv.ALIGN.BOTTOM_MID, 0, -BAR_H - 8)
    popup.set_style_bg_color(lv.color_hex(0x333333), 0)
    popup.set_style_bg_opa(230, 0)
    popup.set_style_radius(12, 0)
    popup.set_style_border_width(0, 0)
    popup.clear_flag(lv.obj.FLAG.SCROLLABLE)
    self._popup = popup

    # 保存 / 清除 / 取消 三个按钮（纵向排列）
    for i, (text, action) in enumerate([
        ("保存", self._on_save),
        ("清除", self._on_clear),
        ("取消", self._dismiss_popup),
    ]):
        btn = lv.label(popup)
        btn.set_text(text)
        btn.set_pos(0, 6 + i * 40)
        btn.set_size(160, 36)
        # ... 样式 ...
```

---

## 7. ScriptRunner改动

### 7.1 K2按键轮询

```python
# ScriptRunner.__init__中：
from machine import Pin
self._k2_pin = Pin(0, Pin.IN, Pin.PULL_UP)  # K2=GPIO0，上拉
self._k2_last = 1  # 上次电平

# ScriptRunner.tick()中（仅当有脚本运行时）：
k2_cur = self._k2_pin.value()
if self._k2_last == 1 and k2_cur == 0:  # 下降沿检测
    if self._script and hasattr(self._script, 'on_key'):
        self._script.on_key('K2')
self._k2_last = k2_cur
```

### 7.2 握手轮询集成

```python
# ScriptRunner.tick()中（有脚本运行时）：
if self._ctx and self._ctx.host:
    self._ctx.host.poll_handshake()
```

---

## 8. 图标预读

**文件**: `core/icon_cache.py`

```python
def preload_face_icons(self):
    """预读人脸识别APP图标"""
    icons = {
        "list": "/sdcard/CamerAi/resource/icons/face_detect_icon/list.png",
    }
    for name, path in icons.items():
        try:
            with open(path, 'rb') as f:
                data = f.read()
            dsc = lv.img_dsc_t({'data_size': len(data), 'data': data})
            self._face_icons[name] = (data, dsc)
        except Exception as e:
            print(f"[IconCache] face/{name} FAILED: {e}")

def get_face_icon(self, name):
    return self._face_icons.get(name, (None, None))
```

在`main.py`中调用`icon_cache.preload_face_icons()`（在首次`task_handler`之前）。

---

## 9. 错误处理

| 场景 | 处理方式 |
|------|----------|
| kmodel加载失败 | on_enter中打印错误，提前返回（APP无法启动，返回主菜单） |
| anchors.bin缺失 | 打印错误路径，on_enter失败 |
| snapshot返回None | 静默跳过本帧（偶发空帧，不刷屏） |
| AI推理异常 | try/except捕获，打印异常，跳过本帧 |
| UART发送失败 | 标记`_connected=False`，等待主机重发握手 |
| 人脸DB文件损坏 | _init_db中跳过损坏的.bin，该槽位视为未注册 |
| 4个槽位已满按K2 | 长蜂鸣提示，不入库 |
| SD卡空间不足 | save/register失败时打印错误，蜂鸣短促提示 |

---

## 10. 测试策略

### 10.1 主机端AST测试（tests/test_face_detect.py）

- `test_face_detect_app_class_exists` — FaceDetectApp类存在，继承BaseScript
- `test_face_detect_script_id` — SCRIPT_ID = "face_detect"
- `test_face_detect_self_managed_top_bar` — SELF_MANAGED_TOP_BAR = True
- `test_host_api_has_send_face_data` — HostAPI.send_face_data()方法存在
- `test_host_api_frame_assembly` — send_frame()组装的帧格式正确（帧头/源/目标/类型/校验/帧尾）
- `test_lcd_declares_chn2` — hw/lcd.py声明了CAM_CHN_ID_2的framesize+pixformat
- `test_icon_cache_preloads_face_icons` — icon_cache预读了list.png

### 10.2 板端功能验证

- 人脸注册：进APP → 对准人脸 → 按K2 → 底栏显示"已注册: ID1" → /data/fac_db/id1.bin存在
- 人脸识别：对准已注册的人脸 → 绿色框 + "ID1"标签 → 串口收到4组ID数据
- 保存/清除：点list → 保存 → 重启后DB仍在 / 清除 → 确认 → DB清空
- 握手流程：主机发送握手帧 → 摄像头应答 → 开始推送数据
