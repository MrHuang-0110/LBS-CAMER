# 相机 APP 布局重设计 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将相机从纯全屏预览重设计为「拍照/录像 + 图库」完整相机应用，LVGL 与 Sensor 双层显示共存。

**Architecture:** Sensor → VIDEO1 层(背景)，LVGL UI 层(前景)，预览区透明透出相机画面。ScriptRunner 不再对 stream 模式关闭 LVGL。CameraApp 自管顶栏（居中标题），通过内部 4 状态机切换拍照/录像待机/录像中/图库。

**Tech Stack:** MicroPython + LVGL v8 + K230 Sensor/Display/MediaManager API

---

## 文件改动映射

| 文件 | 操作 | 职责 |
|------|------|------|
| `resource/i18n/zh_CN.json` | 改 | 新增 camera 相关 i18n key |
| `resource/i18n/en_US.json` | 改 | 同上 |
| `core/icon_cache.py` | 改 | 新增相机图标预读（back/shutter/mode/gallery） |
| `core/script_runner.py` | 改 | stream 模式保留 LVGL；支持脚本自管顶栏 |
| `scripts/camera/app.py` | **重写** | CameraApp 全量代码 |

---

### Task 1: 国际化新增 key

**Files:**
- Modify: `resource/i18n/zh_CN.json`
- Modify: `resource/i18n/en_US.json`

- [ ] **Step 1: 在 zh_CN.json 末尾新增 camera 段**

在 `zh_CN.json` 的 `"common"` 段之前插入（当前文件最后一个 key 是 `"app_name"`）：

```json
"camera": {
    "gallery": "图库",
    "photo_mode": "拍照",
    "video_mode": "录像",
    "recording": "录制中",
    "no_photos": "暂无照片",
    "save_failed": "保存失败"
},
```

- [ ] **Step 2: 在 en_US.json 末尾新增 camera 段**

```json
"camera": {
    "gallery": "Gallery",
    "photo_mode": "Photo",
    "video_mode": "Video",
    "recording": "Recording",
    "no_photos": "No photos",
    "save_failed": "Save failed"
},
```

保持 `"common": { "back": "返回", "app_name": "CamerAi" }` 不变，`camera` 段插入在 `common` 之前。

- [ ] **Step 3: Commit**

```bash
git add resource/i18n/zh_CN.json resource/i18n/en_US.json
git commit -m "feat(i18n): add camera app i18n keys (gallery/photo/video/recording)"
```

---

### Task 2: icon_cache 新增相机图标预读

**Files:**
- Modify: `core/icon_cache.py`

- [ ] **Step 1: 新增相机图标方法到 _IconCache**

在 `_IconCache` 类中新增 `preload_camera_icons()` 方法和对应的 getter：

```python
# 在 __init__ 中添加：
self._camera_icons = {}   # name → (data, dsc)

# 新增方法 preload_camera_icons：
def preload_camera_icons(self):
    """预读相机 APP 图标（在首次 task_handler 之前调用）"""
    base = "/sdcard/CamerAi/resource/icons/camera_icon/"
    icons = {
        "back":    base + "back.png",
        "shutter": base + "photo.png",
        "gallery": base + "camera.png",
        "mode":    base + "countdown.png",
    }
    for name, path in icons.items():
        try:
            with open(path, 'rb') as f:
                data = f.read()
            dsc = lv.img_dsc_t({
                'data_size': len(data),
                'data': data,
            })
            self._camera_icons[name] = (data, dsc)
            print(f"[IconCache] camera/{name} OK ({len(data)} bytes)")
        except Exception as e:
            print(f"[IconCache] camera/{name} FAILED: {e}")

# 新增 getter：
def get_camera_icon(self, name):
    """获取相机图标 (data, dsc)，未缓存返回 (None, None)"""
    return self._camera_icons.get(name, (None, None))
```

- [ ] **Step 2: Commit**

```bash
git add core/icon_cache.py
git commit -m "feat(icon_cache): add camera icon preloading (back/shutter/gallery/mode)"
```

---

### Task 3: ScriptRunner — stream 模式保留 LVGL + 脚本自管顶栏

**Files:**
- Modify: `core/script_runner.py`

核心改动 3 处：`_switch_to_camera_mode` 不再关 LVGL；`_switch_to_lvgl_mode` 不再重建 LVGL；`launch` 支持脚本自管顶栏。

- [ ] **Step 1: 在 BaseScript 添加自管顶栏标志**

修改 `scripts/_base.py`：

```python
class BaseScript:
    """脚本基类 — ScriptRunner 通过此类调度所有脚本"""

    SCRIPT_ID = ""
    SELF_MANAGED_TOP_BAR = False  # True = 脚本自管顶栏，Runner 不挂载 BackBar
```

- [ ] **Step 2: 修改 `_switch_to_camera_mode` — 不关 LVGL**

将 `core/script_runner.py:224-245` 的 `_switch_to_camera_mode` 替换为：

```python
def _switch_to_camera_mode(self):
    """从 LVGL 切换到相机直出模式

    新架构：LVGL 保持运行，Sensor 绑定到 VIDEO1 层（在 LVGL 下层）。
    LVGL 前景层渲染顶栏/底栏，透明预览区透出 VIDEO1 相机画面。
    """
    if self._current_mode == MODE_CAMERA:
        return

    # LVGL 保持运行，不调 lv.deinit()。
    # 仅释放 LVGL 独占的 display 绑定，让 Sensor 脚本重绑 VIDEO1。
    self.lcd.lvgl_deinit()
    self.touch.lvgl_deinit()

    os.exitpoint(os.EXITPOINT_ENABLE_SLEEP)
    time.sleep_ms(50)
    self.lcd.display.deinit()
    MediaManager.deinit()

    self._current_mode = MODE_CAMERA
```

关键变化：去掉了 `lv.deinit()` 调用。LVGL 对象存活，但显示绑定释放，相机脚本 on_enter 重新初始化 Display + Sensor。

- [ ] **Step 3: 修改 `_switch_to_lvgl_mode` — 不重建 LVGL**

将 `core/script_runner.py:247-274` 的 `_switch_to_lvgl_mode` 替换为：

```python
def _switch_to_lvgl_mode(self):
    """从相机模式恢复到 LVGL 模式"""
    if self._current_mode == MODE_LVGL:
        return

    os.exitpoint(os.EXITPOINT_ENABLE_SLEEP)
    time.sleep_ms(100)

    # 释放 MediaManager（若脚本未释放）
    try:
        MediaManager.deinit()
    except Exception:
        pass

    # 重新初始化 Display（LVGL 从未销毁，恢复显示绑定即可）
    self.lcd.display.init(Display.ST7701, self.lcd.width,
                          self.lcd.height, to_ide=self.lcd.to_ide,
                          quality=100)
    MediaManager.init()

    # LVGL 未销毁，仅恢复显示驱动和触摸
    self.lcd.lvgl_init()
    self.touch.lvgl_init()

    self._current_mode = MODE_LVGL
```

关键变化：去掉了 `lv.init()` 调用（LVGL 从未销毁）。

- [ ] **Step 4: 修改 `launch` — 脚本自管顶栏时跳过 BackBar**

修改 `core/script_runner.py:91-144` 的 `launch` 方法中第 4-5 步（显示模式切换和返回栏挂载）：

```python
# 4. 切换显示模式
ui_mode = self.config.get_category(script_id).get('ui_mode', 'stream')
self._ui_mode = ui_mode
self._switch_mode_for(ui_mode)

# 5. 挂载统一返回栏（脚本可声明自管顶栏跳过）
self_managed = getattr(script, 'SELF_MANAGED_TOP_BAR', False)
if not self_managed:
    self._back_bar = BackBar(
        self.lang.t(self.config.get_category(script_id).get(
            'name_key', script_id)),
        on_back=lambda: self._ctx.request_exit(),
    )
    self._back_bar.show()
else:
    self._back_bar = None
```

- [ ] **Step 5: 修改 `exit` — 自管顶栏脚本跳过 BackBar 清理**

修改 `core/script_runner.py:170-207` 的 `exit` 方法中第 2 步：

```python
# 2. 统一返回栏移除（仅当 Runner 管理时）
if self._back_bar is not None:
    try:
        self._back_bar.hide()
        del self._back_bar
        self._back_bar = None
    except Exception as e:
        print(f"[Runner] back_bar cleanup error: {e}")
```

- [ ] **Step 6: Commit**

```bash
git add core/script_runner.py scripts/_base.py
git commit -m "feat(script_runner): stream mode keeps LVGL alive; support self-managed top bar"
```

---

### Task 4: CameraApp — 骨架 + 常量 + 状态机

**Files:**
- Rewrite: `scripts/camera/app.py`

- [ ] **Step 1: 写入 CameraApp 完整骨架**

```python
# scripts/camera/app.py — 相机 APP（拍照/录像 + 图库）
#
# 架构：Sensor → VIDEO1 层(背景) + LVGL UI 层(前景)
# LVGL 全程运行，预览区透明透出相机画面。
#
# 状态机：PHOTO ←→ VIDEO → RECORDING，任意待机态 → GALLERY
#
# ⚠️ K230 约束：on_enter() 内 LVGL 已初始化（Runner 不关），
# 可直接构建 UI。文件 I/O 走 /data/photo/（内部 flash）。

import struct
import lvgl as lv
from media.sensor import Sensor
from media.display import Display
from media.media import MediaManager
from scripts._base import BaseScript
from core.icon_cache import icon_cache
from core.font_manager import fonts
from ui.theme import Colors, make_back_bar_text_style


# ── 布局常量 ──────────────────────────────────────
BAR_H = 52              # 顶栏/底栏高度
PREVIEW_Y = BAR_H       # 预览区起始 Y
PREVIEW_H = 376         # 480 - BAR_H * 2
BTN_SIZE = 48           # 栏上按钮点击区
ICON_TARGET = 40        # 栏上图标目标尺寸
SHUTTER_OUTER = 44      # 快门外径（含圆环）
SHUTTER_INNER = 36      # 快门内圆/方
BAR_BG = 0x1A1A1A       # 栏背景色
TIMER_H = 28            # 录制计时器高度

# 颜色
RED = 0xCC4444
GREEN = 0x44CC44
WHITE = 0xFFFFFF

# 状态
STATE_PHOTO = 0
STATE_VIDEO = 1
STATE_RECORDING = 2
STATE_GALLERY = 3


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
    """在 parent 上创建图标，返回 (img_obj, actual_x)
    
    K230 set_zoom 居中补偿：缩放后图标在源尺寸 img 对象内居中，
    需负偏移贴边。
    """
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


class CameraApp(BaseScript):
    SCRIPT_ID = "camera"
    SELF_MANAGED_TOP_BAR = True

    def __init__(self):
        super().__init__()
        self._state = STATE_PHOTO
        self._sensor = None
        self._screen = None
        self._top_bar = None
        self._bottom_bar = None
        self._preview_bg = None
        self._timer_label = None
        self._shutter_btn = None
        self._mode_icon = None
        self._gallery_icon = None
        self._title_label = None

        # 录像相关
        self._record_start_ticks = 0
        self._timer_blink = True

        # 图库相关
        self._gallery_list = None
        self._gallery_objects = []

    # ── 生命周期 ──────────────────────────────────

    def on_enter(self, ctx):
        super().on_enter(ctx)
        self._init_camera()
        self._build_ui()

    def on_frame(self):
        import os
        os.exitpoint()

        # 录像计时器更新
        if self._state == STATE_RECORDING:
            self._update_timer()

    def on_exit(self):
        self._stop_camera()
        self._destroy_ui()
        super().on_exit()

    # ── 相机控制 ──────────────────────────────────

    def _init_camera(self):
        """初始化 Sensor + Display（LVGL 已在 Runner 保持运行）"""
        self._sensor = Sensor(width=1280, height=960)
        self._sensor.reset()
        self._sensor.set_framesize(Sensor.VGA)
        self._sensor.set_pixformat(Sensor.YUV420SP)

        bind_info = self._sensor.bind_info()
        Display.bind_layer(**bind_info, layer=Display.LAYER_VIDEO1)

        Display.init(Display.ST7701, 640, 480, fps=90, to_ide=True)
        MediaManager.init()

        self._sensor.run()
        print("[Camera] preview started (LVGL overlay mode)")

    def _stop_camera(self):
        print("[Camera] stopping...")
        if self._sensor is not None:
            try:
                self._sensor.stop()
            except Exception as e:
                print(f"[Camera] sensor.stop error: {e}")
            self._sensor = None

        try:
            Display.deinit()
        except Exception as e:
            print(f"[Camera] Display.deinit error: {e}")

        try:
            MediaManager.deinit()
        except Exception as e:
            print(f"[Camera] MediaManager.deinit error: {e}")

    # ── UI 构建（占位，后续 Task 补充实现）─────────

    def _build_ui(self):
        pass  # Task 5-7 实现

    def _build_top_bar(self):
        pass  # Task 5 实现

    def _build_bottom_bar(self):
        pass  # Task 6 实现

    def _build_preview_area(self):
        pass  # Task 7 实现

    def _refresh_shutter(self):
        pass  # Task 6 实现

    def _refresh_mode_icon(self):
        pass  # Task 6 实现

    # ── 拍照/录像 ──────────────────────────────────

    def _on_shutter(self, e):
        pass  # Task 8-9 实现

    def _on_mode_toggle(self, e):
        pass  # Task 6 实现

    def _capture_photo(self):
        pass  # Task 8 实现

    def _start_recording(self):
        pass  # Task 9 实现

    def _stop_recording(self):
        pass  # Task 9 实现

    def _update_timer(self):
        pass  # Task 9 实现

    # ── 图库 ──────────────────────────────────────

    def _on_gallery(self, e):
        pass  # Task 10 实现

    def _enter_gallery(self):
        pass  # Task 10 实现

    def _leave_gallery(self):
        pass  # Task 10 实现

    # ── 返回 ──────────────────────────────────────

    def _on_back(self, e):
        pass  # Task 5 实现

    def _destroy_ui(self):
        pass  # Task 5 实现
```

- [ ] **Step 2: Commit**

```bash
git add scripts/camera/app.py
git commit -m "feat(camera): skeleton with constants, state machine, lifecycle stubs"
```

---

### Task 5: CameraApp — 顶栏 + 返回逻辑

**Files:**
- Modify: `scripts/camera/app.py`

- [ ] **Step 1: 实现 `_build_top_bar`**

替换 `_build_top_bar` 方法：

```python
def _build_top_bar(self):
    """顶栏：返回按钮(左) + 标题(居中)"""
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

    # 返回按钮（48×48 透明点击区 + 图标）
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
        lambda e: self._on_back(e) if e.get_code() == lv.EVENT.CLICKED else None,
        lv.EVENT.CLICKED, None)

    # 标题居中
    title = lv.label(bar)
    title.set_text(lang.t("category.camera"))
    title.align(lv.ALIGN.CENTER, 0, 0)
    title_style = make_back_bar_text_style(fonts.body)
    title.add_style(title_style, 0)
    self._title_label = title
```

- [ ] **Step 2: 实现 `_on_back`**

```python
def _on_back(self, e):
    if self._state == STATE_GALLERY:
        self._leave_gallery()
    else:
        self.ctx.request_exit()
```

- [ ] **Step 3: 在 `_build_ui` 中调用顶栏构建**

```python
def _build_ui(self):
    lang = self.ctx.lang
    screen = lv.scr_act()
    screen.set_style_bg_color(lv.color_hex(Colors.BG), 0)
    screen.set_style_bg_opa(255, 0)
    self._screen = screen

    self._build_top_bar()
    self._build_preview_area()
    self._build_bottom_bar()
```

- [ ] **Step 4: 实现 `_destroy_ui`**

```python
def _destroy_ui(self):
    for attr in ('_top_bar', '_bottom_bar', '_preview_bg',
                 '_timer_label', '_gallery_list'):
        obj = getattr(self, attr, None)
        if obj is not None:
            try:
                obj.delete()
            except Exception:
                pass
            setattr(self, attr, None)
    self._gallery_objects = []
    self._shutter_btn = None
    self._mode_icon = None
    self._gallery_icon = None
    self._title_label = None
    self._screen = None
```

- [ ] **Step 5: 去掉 `_build_ui` 中的 `pass` 占位**

现在 `_build_ui` 和 `_destroy_ui` 已实现，删除此前 Task 4 骨架中的对应 `pass` 占位。

- [ ] **Step 6: Commit**

```bash
git add scripts/camera/app.py
git commit -m "feat(camera): top bar with back button + centered title"
```

---

### Task 6: CameraApp — 底栏 + 模式切换 + 快门 UI 变化

**Files:**
- Modify: `scripts/camera/app.py`

- [ ] **Step 1: 实现 `_build_bottom_bar`**

替换 `_build_bottom_bar` 方法：

```python
def _build_bottom_bar(self):
    """底栏：图库(左) + 快门(中) + 模式(右)"""
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

    # ── 图库按钮（左）──
    gallery_btn = lv.obj(bar)
    gallery_btn.set_size(BTN_SIZE, BTN_SIZE)
    gallery_btn.align(lv.ALIGN.LEFT_MID, 24, 0)
    gallery_btn.set_style_bg_opa(0, 0)
    gallery_btn.set_style_border_width(0, 0)
    gallery_btn.set_style_shadow_width(0, 0)
    gallery_btn.set_style_outline_width(0, 0)
    gallery_btn.set_style_outline_opa(0, 0)
    gallery_btn.set_style_pad_all(0, 0)
    gallery_btn.clear_flag(lv.obj.FLAG.SCROLLABLE)
    gallery_btn.add_flag(lv.obj.FLAG.CLICKABLE)

    icon_data, icon_dsc = icon_cache.get_camera_icon("gallery")
    if icon_data is not None and icon_dsc is not None:
        img, _ = _make_icon(gallery_btn, icon_data, icon_dsc, ICON_TARGET, 4, 0)
        self._gallery_icon = img

    gallery_btn.add_event(
        lambda e: self._on_gallery(e) if e.get_code() == lv.EVENT.CLICKED else None,
        lv.EVENT.CLICKED, None)

    # ── 快门按钮（中）──
    shutter_btn = lv.obj(bar)
    shutter_btn.set_size(SHUTTER_OUTER, SHUTTER_OUTER)
    shutter_btn.align(lv.ALIGN.CENTER, 0, 0)
    shutter_btn.set_style_bg_opa(0, 0)  # 默认透明
    shutter_btn.set_style_border_width(3, 0)
    shutter_btn.set_style_border_color(lv.color_hex(WHITE), 0)
    shutter_btn.set_style_border_opa(255, 0)
    shutter_btn.set_style_radius(lv.pct(50), 0)  # 圆形
    shutter_btn.set_style_pad_all(0, 0)
    shutter_btn.clear_flag(lv.obj.FLAG.SCROLLABLE)
    shutter_btn.add_flag(lv.obj.FLAG.CLICKABLE)
    shutter_btn.add_event(
        lambda e: self._on_shutter(e) if e.get_code() == lv.EVENT.CLICKED else None,
        lv.EVENT.CLICKED, None)
    self._shutter_btn = shutter_btn

    # ── 模式按钮（右）──
    mode_btn = lv.obj(bar)
    mode_btn.set_size(BTN_SIZE, BTN_SIZE)
    mode_btn.align(lv.ALIGN.RIGHT_MID, -24, 0)
    mode_btn.set_style_bg_opa(0, 0)
    mode_btn.set_style_border_width(0, 0)
    mode_btn.set_style_shadow_width(0, 0)
    mode_btn.set_style_outline_width(0, 0)
    mode_btn.set_style_outline_opa(0, 0)
    mode_btn.set_style_pad_all(0, 0)
    mode_btn.clear_flag(lv.obj.FLAG.SCROLLABLE)
    mode_btn.add_flag(lv.obj.FLAG.CLICKABLE)

    icon_data, icon_dsc = icon_cache.get_camera_icon("mode")
    if icon_data is not None and icon_dsc is not None:
        img, _ = _make_icon(mode_btn, icon_data, icon_dsc, ICON_TARGET, 4, 0)
        self._mode_icon = img

    mode_btn.add_event(
        lambda e: self._on_mode_toggle(e) if e.get_code() == lv.EVENT.CLICKED else None,
        lv.EVENT.CLICKED, None)
```

- [ ] **Step 2: 实现 `_on_mode_toggle`**

```python
def _on_mode_toggle(self, e):
    """切换拍照 ↔ 录像（仅待机状态）"""
    if self._state == STATE_PHOTO:
        self._state = STATE_VIDEO
    elif self._state == STATE_VIDEO:
        self._state = STATE_PHOTO
    else:
        return  # 录像中或图库中不响应
    self._refresh_shutter()
    self._refresh_mode_icon()
```

- [ ] **Step 3: 实现 `_refresh_shutter`**

```python
def _refresh_shutter(self):
    """根据当前状态更新快门外观"""
    if self._shutter_btn is None:
        return

    btn = self._shutter_btn
    if self._state == STATE_PHOTO:
        # 白圈空心圆
        btn.set_style_bg_opa(0, 0)
        btn.set_style_border_color(lv.color_hex(WHITE), 0)
        btn.set_style_border_width(3, 0)
        btn.set_style_radius(lv.pct(50), 0)
    elif self._state == STATE_VIDEO:
        # 红色实心小圆 + 白边
        btn.set_style_bg_color(lv.color_hex(RED), 0)
        btn.set_style_bg_opa(255, 0)
        btn.set_style_border_color(lv.color_hex(WHITE), 0)
        btn.set_style_border_width(3, 0)
        btn.set_style_radius(lv.pct(50), 0)
    elif self._state == STATE_RECORDING:
        # 红色正方形
        btn.set_style_bg_color(lv.color_hex(RED), 0)
        btn.set_style_bg_opa(255, 0)
        btn.set_style_border_width(0, 0)
        btn.set_style_radius(4, 0)
```

- [ ] **Step 4: 实现 `_refresh_mode_icon`**

```python
def _refresh_mode_icon(self):
    """根据状态更新模式图标颜色"""
    if self._mode_icon is None:
        return

    if self._state in (STATE_VIDEO, STATE_RECORDING):
        self._mode_icon.set_style_img_recolor(lv.color_hex(GREEN), 0)
        self._mode_icon.set_style_img_recolor_opa(255, 0)
    else:
        self._mode_icon.set_style_img_recolor_opa(0, 0)
```

> **⚠️ 板端验证注意**：`img_recolor` 在 K230 LVGL v8 可能不可用。如不支持，回退方案：在 _on_mode_toggle 中删除重建图标（lv.img.delete() + 重新 lv.img 设置不同颜色——但这需要两张不同颜色的 PNG）。另一个替代：在模式图标下方叠加一个绿色小圆点（lv.obj 6×6, bg=GREEN, radius=50%）表示录像模式。

- [ ] **Step 5: 在 `_build_ui` 中去掉旧 `_build_bottom_bar` 的 `pass` 占位**

- [ ] **Step 6: Commit**

```bash
git add scripts/camera/app.py
git commit -m "feat(camera): bottom bar with gallery/shutter/mode buttons + state UI refresh"
```

---

### Task 7: CameraApp — 预览区（透明透出 VIDEO1）

**Files:**
- Modify: `scripts/camera/app.py`

- [ ] **Step 1: 实现 `_build_preview_area`**

替换 `_build_preview_area`：

```python
def _build_preview_area(self):
    """预览区：全透明 LVGL 对象，让底层 VIDEO1 相机画面透出"""
    preview = lv.obj(self._screen)
    preview.set_size(lv.pct(100), PREVIEW_H)
    preview.set_pos(0, PREVIEW_Y)
    # 完全透明 — 这是关键：bg_opa=0 让 VIDEO1 层可见
    preview.set_style_bg_opa(0, 0)
    preview.set_style_border_width(0, 0)
    preview.set_style_pad_all(0, 0)
    preview.set_style_radius(0, 0)
    preview.clear_flag(lv.obj.FLAG.SCROLLABLE)
    preview.clear_flag(lv.obj.FLAG.CLICKABLE)
    self._preview_bg = preview

    # 录制计时器（初始隐藏）
    timer = lv.label(preview)
    timer.set_text("")
    timer.align(lv.ALIGN.TOP_MID, 0, 8)
    timer_style = make_back_bar_text_style(fonts.body)
    timer.add_style(timer_style, 0)
    timer.set_style_text_color(lv.color_hex(RED), 0)
    timer.add_flag(lv.obj.FLAG.HIDDEN)
    self._timer_label = timer
```

- [ ] **Step 2: 去掉 `_build_preview_area` 的 `pass` 占位**

- [ ] **Step 3: Commit**

```bash
git add scripts/camera/app.py
git commit -m "feat(camera): transparent preview area for VIDEO1 passthrough + timer label"
```

---

### Task 8: CameraApp — 拍照逻辑

**Files:**
- Modify: `scripts/camera/app.py`

- [ ] **Step 1: 实现 `_on_shutter` 分派**

替换 `_on_shutter`：

```python
def _on_shutter(self, e):
    """快门按钮：拍照 / 开始录像 / 停止录像"""
    ctx = self.ctx
    if self._state == STATE_PHOTO:
        self._capture_photo()
        ctx.buzzer.beep(ms=30)
    elif self._state == STATE_VIDEO:
        self._start_recording()
        ctx.buzzer.beep(ms=50)
    elif self._state == STATE_RECORDING:
        self._stop_recording()
        ctx.buzzer.beep(ms=80)
```

- [ ] **Step 2: 实现 `_capture_photo`**

```python
def _capture_photo(self):
    """拍照并保存到 /data/photo/"""
    import os
    import time as _time

    # 确保目录存在
    photo_dir = "/data/photo/"
    try:
        os.mkdir(photo_dir)
    except Exception:
        pass  # 已存在

    # 生成文件名
    t = _time.localtime()
    fname = f"IMG_{t[0]:04d}{t[1]:02d}{t[2]:02d}_{t[3]:02d}{t[4]:02d}{t[5]:02d}.jpg"
    path = photo_dir + fname

    # 捕获 Sensor 当前帧
    try:
        # K230 MicroPython: sensor.snapshot() 返回 image 对象
        img = self._sensor.snapshot()
        # 保存为 JPEG（quality=95）
        img.save(path, quality=95)
        print(f"[Camera] photo saved: {path}")

        # 屏幕闪烁反馈
        self._flash_feedback()
    except Exception as e:
        print(f"[Camera] capture failed: {e}")
```

> **⚠️ 板端验证注意**：K230 MicroPython 的 Sensor.snapshot() API 需确认。如 `sensor.snapshot()` 不存在，可能需要用 `sensor.capture()` 或通过 `image.Image` 构造。板端测试时根据实际固件 API 调整。

- [ ] **Step 3: 实现屏幕闪烁反馈**

```python
def _flash_feedback(self):
    """拍照/录像时的短暂视觉反馈"""
    if self._preview_bg is None:
        return

    # 在预览区叠加一个白色半透明闪烁层
    flash = lv.obj(self._preview_bg)
    flash.set_size(lv.pct(100), lv.pct(100))
    flash.set_pos(0, 0)
    flash.set_style_bg_color(lv.color_hex(WHITE), 0)
    flash.set_style_bg_opa(128, 0)
    flash.set_style_border_width(0, 0)
    flash.set_style_radius(0, 0)
    flash.clear_flag(lv.obj.FLAG.SCROLLABLE)
    flash.clear_flag(lv.obj.FLAG.CLICKABLE)

    # 用 lv.anim 做闪烁动画（200ms 渐隐）
    import lvgl as lv
    anim = lv.anim_t()
    anim.init()
    anim.set_var(flash)
    anim.set_time(200)
    anim.set_values(128, 0)

    def _anim_cb(obj, val):
        obj.set_style_bg_opa(int(val), 0)

    def _done_cb(anim_obj):
        try:
            flash.delete()
        except Exception:
            pass

    anim.set_custom_exec_cb(lambda a, v: _anim_cb(flash, v))
    anim.set_ready_cb(lambda a: _done_cb(a))
    anim.start()
```

> **⚠️ 板端验证注意**：K230 LVGL v8 的 lv.anim_t API 签名需确认。如 set_custom_exec_cb/set_ready_cb 不可用，退化方案：只用 `flash.delete()` 不做动画（200ms 后由 on_frame 中手动计数删除）。

- [ ] **Step 4: Commit**

```bash
git add scripts/camera/app.py
git commit -m "feat(camera): photo capture + save to /data/photo/ + flash feedback"
```

---

### Task 9: CameraApp — 录像逻辑 + 计时器

**Files:**
- Modify: `scripts/camera/app.py`

- [ ] **Step 1: 实现 `_start_recording`**

```python
def _start_recording(self):
    """开始录像"""
    import os
    import time as _time

    photo_dir = "/data/photo/"
    try:
        os.mkdir(photo_dir)
    except Exception:
        pass

    t = _time.localtime()
    fname = f"VID_{t[0]:04d}{t[1]:02d}{t[2]:02d}_{t[3]:02d}{t[4]:02d}{t[5]:02d}.avi"
    self._record_path = photo_dir + fname
    self._record_start_ticks = _time.ticks_ms()

    self._state = STATE_RECORDING
    self._refresh_shutter()
    self._show_timer(True)
    print(f"[Camera] recording started: {self._record_path}")
```

- [ ] **Step 2: 实现 `_stop_recording`**

```python
def _stop_recording(self):
    """停止录像"""
    self._state = STATE_VIDEO
    self._refresh_shutter()
    self._show_timer(False)
    print(f"[Camera] recording stopped: {getattr(self, '_record_path', '')}")
```

- [ ] **Step 3: 实现计时器显示/更新**

```python
def _show_timer(self, visible):
    """显示/隐藏录制计时器"""
    if self._timer_label is None:
        return
    if visible:
        self._timer_label.clear_flag(lv.obj.FLAG.HIDDEN)
        self._timer_label.set_text("● 00:00:00")
    else:
        self._timer_label.add_flag(lv.obj.FLAG.HIDDEN)
        self._timer_label.set_text("")

def _update_timer(self):
    """每帧调用：更新录制时间 + 红点闪烁"""
    if self._timer_label is None or self._state != STATE_RECORDING:
        return

    import time as _time
    elapsed = _time.ticks_diff(_time.ticks_ms(), self._record_start_ticks) // 1000
    h = elapsed // 3600
    m = (elapsed % 3600) // 60
    s = elapsed % 60

    # 红点闪烁（500ms 周期）
    self._timer_blink = (elapsed % 2 == 0)
    dot = "●" if self._timer_blink else "○"

    self._timer_label.set_text(f"{dot} {h:02d}:{m:02d}:{s:02d}")
```

- [ ] **Step 4: 在 `on_frame` 中保留计时器更新调用**

Task 4 骨架中已预留 `self._update_timer()` 调用，确认仍存在。

- [ ] **Step 5: Commit**

```bash
git add scripts/camera/app.py
git commit -m "feat(camera): video recording with blinking timer overlay"
```

---

### Task 10: CameraApp — 图库子页面

**Files:**
- Modify: `scripts/camera/app.py`

- [ ] **Step 1: 实现图库进入/离开**

```python
def _on_gallery(self, e):
    """图库按钮（仅待机状态可用）"""
    if self._state in (STATE_RECORDING, STATE_GALLERY):
        return
    self._enter_gallery()

def _enter_gallery(self):
    """进入图库页面"""
    import os

    self._state = STATE_GALLERY

    # 隐藏相机 UI
    if self._bottom_bar is not None:
        self._bottom_bar.add_flag(lv.obj.FLAG.HIDDEN)
    if self._preview_bg is not None:
        self._preview_bg.add_flag(lv.obj.FLAG.HIDDEN)
    if self._timer_label is not None:
        self._timer_label.add_flag(lv.obj.FLAG.HIDDEN)

    # 更新标题
    if self._title_label is not None:
        self._title_label.set_text(self.ctx.lang.t("camera.gallery"))

    # 扫描 /data/photo/
    photo_dir = "/data/photo/"
    files = []
    try:
        for f in os.listdir(photo_dir):
            low = f.lower()
            if low.endswith(('.jpg', '.bmp', '.avi')):
                try:
                    st = os.stat(photo_dir + f)
                    files.append((f, st[8]))  # (name, mtime)
                except Exception:
                    files.append((f, 0))
    except Exception as e:
        print(f"[Camera] gallery listdir failed: {e}")

    # 按 mtime 倒序
    files.sort(key=lambda x: x[1], reverse=True)

    # 构建列表
    self._build_gallery_list(files)

def _leave_gallery(self):
    """离开图库，回到相机预览"""
    # 销毁图库列表
    if self._gallery_list is not None:
        try:
            self._gallery_list.delete()
        except Exception:
            pass
        self._gallery_list = None
    self._gallery_objects = []

    # 恢复相机 UI
    if self._bottom_bar is not None:
        self._bottom_bar.clear_flag(lv.obj.FLAG.HIDDEN)
    if self._preview_bg is not None:
        self._preview_bg.clear_flag(lv.obj.FLAG.HIDDEN)

    # 恢复标题 + 状态
    if self._title_label is not None:
        self._title_label.set_text(self.ctx.lang.t("category.camera"))
    self._state = STATE_PHOTO
    self._refresh_shutter()
    self._refresh_mode_icon()
```

- [ ] **Step 2: 实现图库列表构建**

```python
def _build_gallery_list(self, files):
    """构建图库垂直滚动列表"""
    lang = self.ctx.lang
    screen = self._screen

    # 列表容器（在顶栏下方）
    list_h = screen.get_height() - BAR_H
    lst = lv.obj(screen)
    lst.set_size(lv.pct(100), list_h)
    lst.set_pos(0, BAR_H)
    lst.set_style_bg_color(lv.color_hex(Colors.BG), 0)
    lst.set_style_bg_opa(255, 0)
    lst.set_style_border_width(0, 0)
    lst.set_style_pad_all(8, 0)
    lst.set_style_radius(0, 0)
    # 允许滚动
    lst.set_scroll_dir(lv.DIR.VER)
    self._gallery_list = lst

    if not files:
        # 空状态提示
        empty = lv.label(lst)
        empty.set_text(lang.t("camera.no_photos"))
        empty.align(lv.ALIGN.CENTER, 0, 0)
        empty_style = make_back_bar_text_style(fonts.body)
        empty.add_style(empty_style, 0)
        empty.set_style_text_color(lv.color_hex(Colors.TEXT_DIM), 0)
        self._gallery_objects.append(empty)
        return

    row_h = 64
    y = 4
    for fname, mtime in files:
        row = lv.obj(lst)
        row.set_size(lv.pct(100), row_h)
        row.set_pos(0, y)
        row.set_style_bg_color(lv.color_hex(0x1A1A1A), 0)
        row.set_style_bg_opa(255, 0)
        row.set_style_border_width(0, 0)
        row.set_style_radius(8, 0)
        row.set_style_pad_all(6, 0)
        row.clear_flag(lv.obj.FLAG.SCROLLABLE)
        self._gallery_objects.append(row)

        # 文件类型标记
        is_video = fname.lower().endswith('.avi')
        type_lbl = lv.label(row)
        type_lbl.set_text("🎬" if is_video else "📷")
        type_lbl.align(lv.ALIGN.LEFT_MID, 4, 0)

        # 文件名 + 日期
        import time as _time
        if mtime > 0:
            t = _time.localtime(mtime)
            date_str = f"{t[0]}-{t[1]:02d}-{t[2]:02d} {t[3]:02d}:{t[4]:02d}"
        else:
            date_str = "?"

        info = lv.obj(row)
        info.set_size(lv.pct(70), row_h - 4)
        info.align(lv.ALIGN.LEFT_MID, 48, 0)
        info.set_style_bg_opa(0, 0)
        info.set_style_border_width(0, 0)
        info.set_style_pad_all(0, 0)
        info.clear_flag(lv.obj.FLAG.SCROLLABLE)

        name_lbl = lv.label(info)
        name_lbl.set_text(fname[:30])
        name_lbl.set_style_text_color(lv.color_hex(WHITE), 0)
        name_lbl.add_style(make_back_bar_text_style(fonts.body), 0)

        date_lbl = lv.label(info)
        date_lbl.set_text(date_str)
        date_lbl.align(lv.ALIGN.BOTTOM_LEFT, 0, 0)
        date_lbl.set_style_text_color(lv.color_hex(Colors.TEXT_DIM), 0)

        y += row_h + 4

    # 更新内容高度以支持滚动
    lst.set_content_height(y + 4)
```

- [ ] **Step 3: Commit**

```bash
git add scripts/camera/app.py
git commit -m "feat(camera): gallery sub-page with photo list sorted by date"
```

---

### Task 11: 板端验证 + 问题修复

**说明：** MicroPython + K230 环境无法运行 pytest。板端验证步骤：

- [ ] **Step 1: 部署到 SD 卡**

```bash
# 将改动的文件复制到 SD 卡 CamerAi 目录
# scripts/camera/app.py
# core/script_runner.py
# core/icon_cache.py
# resource/i18n/zh_CN.json
# resource/i18n/en_US.json
```

- [ ] **Step 2: 验证主菜单进入相机**

点击主菜单「相机」卡片 → 应看到：顶栏 52px（左返回图标 + 居中「相机」） + 中间相机预览画面 + 底栏 52px（左图库 + 中白圈快门 + 右模式）

- [ ] **Step 3: 验证模式切换**

点击模式图标 → 快门变红色实心圆 + 模式图标变绿
再次点击 → 恢复白圈 + 白图标

- [ ] **Step 4: 验证拍照**

拍照模式下点击快门 → 蜂鸣一声 + 屏幕闪白 → 检查 `/data/photo/IMG_*.jpg` 是否生成

- [ ] **Step 5: 验证录像**

切换到录像模式 → 点击红圆快门 → 红圆变红方 + 顶部出现红色计时器（● 00:00:XX 闪烁） → 点击红方 → 计时器消失，恢复红圆

- [ ] **Step 6: 验证图库**

点击图库图标 → 进入图库页面（顶栏标题变「图库」） → 看到照片列表（按日期倒序） → 点击返回 → 回到相机预览

- [ ] **Step 7: 记录问题并修复**

板端出现的任何异常（崩溃、渲染不对、API 不兼容）都记录，逐个修复并提交。

- [ ] **Step 8: 最终提交**

```bash
git add -A
git commit -m "fix(camera): board verification fixes"
```

---

## 风险点 & 回退计划

| 风险 | 概率 | 回退 |
|------|------|------|
| VIDEO1 层与 LVGL 透明区不穿透(显示全黑或全白) | 中 | 退回方案 B（LVGL/Camera 互斥），放弃录像计时器 |
| `sensor.snapshot()` API 不存在 | 中 | 查 K230 MicroPython 固件文档，换用正确的 capture API |
| `lv.anim_t` API 与当前固件不兼容 | 低 | 删掉闪烁动画，仅蜂鸣反馈 |
| `img_recolor` 不支持(K230 LVGL v8) | 中 | 模式切换时重建图标，用不同颜色的 PNG 或叠加绿色圆点 |
| /data/photo/ 目录写不进去 | 低 | 换 /sdcard/CamerAi/photo/，但需注意 SD I/O 死锁风险 |
| 录像持续写入导致 DMA 冲突 | 低 | 内存缓冲 → 退出时一次性写入（限制录像时长 30s） |

---

## 板端验证记录 (2026-06-12)

### 进展一：显示栈死锁绕开（已解决）
- **现象**:进相机黑屏无响应,DIAG 3(bind_layer VIDEO1)后串口无输出。
- **根因**:`display.init()` 在 deinit→reinit 循环后死锁。LVGL 双缓冲 draw_buf 是 MediaManager 显存池的 image.Image,`del` 只去引用、GC 未必释放 VB 块 → MediaManager.deinit() 时池脏 → 下次 display.init() 死锁。
- **修复**:显示栈开机一次性初始化、全程永不拆。`_switch_to_camera_mode/lvgl_mode` 仅翻模式标志;`_init_camera` 只 `bind_layer(VIDEO1)`+`sensor.run()`;`_stop_camera` 只 `sensor.stop()`。Commit 5c0cb83a。

### 进展二：主菜单未隐藏挡死 VIDEO1（已解决）
- **现象**:DIAG 1-4 全打印,顶/底栏正常渲染,但中间预览区透出的是主菜单画面而非相机。
- **根因**:`MainMenu` 是 `lv.scr_act()` 上的**全屏不透明容器**;`MainMenu.hide()` 方法存在但**全项目从未被调用**。点卡片 → `runner.launch()` 时主菜单容器仍挂在屏上。相机把 `scr_act().bg_opa=0` 设透明只改屏幕自身背景,盖不住上层主菜单容器。预览区透明 → 透出的是没被隐藏的主菜单(不透明),它挡死了下层 VIDEO1。page 模式脚本(如设置)建不透明全屏 UI 盖住主菜单,所以没暴露此 bug;相机预览区透明才暴露。
- **修复**(对称于已有 `runner_exited → menu.show()`):`launch()` 加载脚本成功后 `event_bus.emit('runner_launched')`;main.py 订阅 `runner_launched → menu.hide()`。不碰显示栈。
