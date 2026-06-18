# scripts/camera/app.py — 相机 APP（拍照/录像 + 图库）
#
# 架构（参考官方 ai_lvgl.py 的 LVGL+相机共存模式）：
#   OSD1 层：相机帧（sensor.snapshot() → Display.show_image(LAYER_OSD1)）
#   OSD2 层：LVGL UI（flush 回调显式 show_image(LAYER_OSD2)）
#   LVGL 预览区 bg_opa=0 透明 → 透出下层 OSD1 相机画面
#
# 关键：不使用 Display.bind_layer() + VIDEO1 层。
# 实测 bind_layer 在 Display.init() 之后调用会触发 pool_id 错误。
# MediaManager 全程不重建。
#
# 状态机：PHOTO ←→ VIDEO → RECORDING，任意待机态 → GALLERY

import struct
import lvgl as lv
from media.display import Display
import image as _image_lib  # K230 JPEG 硬件解码（lv.task_handler 外安全）
# Sensor 不再在此构造 — 复用 LCD 启动期配置的常驻 sensor（见 hw/lcd.py）
# MediaManager 不再直接操作 — 参考 ai_lvgl.py，相机帧走 OSD1 层
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

# ── 图库常量 ──
GAL_THUMB_W = 120       # 缩略图最大宽度
GAL_THUMB_H = 90        # 缩略图最大高度
GAL_ROW_H = 100          # 照片行高度
GAL_ROW_BG = 0x1A1A1A    # 照片行卡片背景
GAL_DATE_H = 28          # 日期分组标题高度
GAL_DATE_BG = 0x111111   # 日期分组标题背景
GAL_DELETE_SIZE = 36     # 删除按钮尺寸
GAL_ROW_GAP = 6          # 行间距
GAL_ROW_RADIUS = 8       # 照片行圆角
GAL_THUMB_RADIUS = 6     # 缩略图圆角

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
        self._record_path = ""

        # 图库相关
        self._gallery_list = None
        self._gallery_objects = []

        # 模式图标（录像模式绿色指示）— K230 LVGL v8 img_recolor 可能不可用
        self._mode_green_dot = None

        # 拍照白闪反馈（on_frame 轮询删除，K230 无 lv.timer）
        self._flash_obj = None
        self._flash_start = 0

        # 图库 — 缩略图数据（像素字节常驻内存供 LVGL 重绘）
        self._gallery_thumbs = []   # list[dict]: path/fname/mtime/img_dsc/pixel_data
        self._gallery_groups = []   # list[dict]: date_key/label/photos

    # ── 生命周期 ──────────────────────────────────

    def on_enter(self, ctx):
        super().on_enter(ctx)
        print("[Camera] on_enter: begin _init_camera")
        self._init_camera()
        print("[Camera] on_enter: begin _build_ui")
        self._build_ui()
        print("[Camera] on_enter: done")

    def on_frame(self):
        import os
        os.exitpoint()

        # 推送相机帧到 OSD1 层（LVGL 在 OSD2，透明区域透出 OSD1 画面）
        # 参考官方 ai_lvgl.py 的 LVGL+相机共存模式
        if self._sensor is not None and self._state != STATE_GALLERY:
            try:
                img = self._sensor.snapshot()
                if img is not None:
                    Display.show_image(img, 0, 0, Display.LAYER_OSD1)
            except Exception:
                pass  # 偶发 snapshot 失败不刷屏

        # 录像计时器更新
        if self._state == STATE_RECORDING:
            self._update_timer()

        # 拍照白闪：120ms 后删除
        if self._flash_obj is not None:
            import time as _time
            if _time.ticks_diff(_time.ticks_ms(), self._flash_start) >= 120:
                try:
                    self._flash_obj.delete()
                except Exception:
                    pass
                self._flash_obj = None

    def on_exit(self):
        self._stop_camera()
        self._destroy_ui()
        super().on_exit()

    # ── 相机控制 ──────────────────────────────────

    def _init_camera(self):
        """开始相机预览 — 复用 LCD 启动期已配置的常驻 Sensor

        全机只有一个 sensor 实例，缓冲在启动期随 MediaManager.init() 分配
        （VGA/RGB888）。取流由 LCD.ensure_sensor_running() 幂等启动——全机
        生命周期只 run() 一次，之后常驻。

        K230 坑：sensor.stop() 后再 run() 须先 reset()，而 reset() 在
        MediaManager.init() 之后会触碰缓冲池（不可重建）→ 第二次进相机
        报 `should call reset() first`。因此本 APP 退出**不 stop sensor**，
        只停止向 OSD1 推帧（_stop_camera 解引用）。

        相机帧通过 sensor.snapshot() 捕获后由 on_frame() 推送到 OSD1 层，
        LVGL UI 渲染到 OSD2 层，OSD2 透明区域透出 OSD1 相机画面。
        """
        # 取启动期常驻 sensor（已 reset + framesize + pixformat）
        self._sensor = self.ctx.lcd.get_sensor()
        print("[Camera] DIAG 1: got shared sensor (preconfigured)")

        # 清 LVGL 双缓冲为透明像素（bg_opa=0 不写像素，需确保无残留）
        if self.ctx.lcd is not None:
            self.ctx.lcd.clear_framebuffers()
            print("[Camera] LVGL framebuffers cleared")

        # 幂等启动取流——全机只 run() 一次，第二次起直接返回（不再报 reset 错）
        self.ctx.lcd.ensure_sensor_running()
        print("[Camera] DIAG 2: sensor running — preview started")

    def _stop_camera(self):
        """停止推帧 — 不 stop sensor（K230 stop 后须 reset，会触碰缓冲池）

        sensor 由 LCD 常驻管理，全机持续取流。本方法只解引用 self._sensor，
        使 on_frame() 不再向 OSD1 推帧；OSD1 残留帧被主菜单的不透明背景盖住。
        下次进相机再 get_sensor() 取回同一实例。
        """
        print("[Camera] stopping preview push (sensor stays resident)...")
        self._sensor = None

        # 清 LVGL 双缓冲，为菜单渲染准备干净画布
        if self.ctx.lcd is not None:
            self.ctx.lcd.clear_framebuffers()
            print("[Camera] LVGL buffers cleared for menu")

    # ── UI 构建 ──────────────────────────────────

    def _build_ui(self):
        lang = self.ctx.lang
        screen = lv.scr_act()
        # 屏幕背景透明 —— OSD2 透明处透出下层 OSD1 相机画面。
        # 顶栏/底栏自带不透明背景，仅中间预览区透明。
        screen.set_style_bg_opa(0, 0)
        self._screen = screen

        self._build_top_bar()
        self._build_preview_area()
        self._build_bottom_bar()

    # ── 顶栏 ──────────────────────────────────────

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

    # ── 预览区 ────────────────────────────────────

    def _build_preview_area(self):
        """预览区：全透明 LVGL 对象，让底层 OSD1 相机画面透出

        bg_opa=0 + flush 回调清零非活跃缓冲 → 预览区帧缓冲像素保持透明
        → OSD2 透明像素透出下层 OSD1 相机画面。
        """
        preview = lv.obj(self._screen)
        preview.set_size(lv.pct(100), PREVIEW_H)
        preview.set_pos(0, PREVIEW_Y)
        preview.set_style_bg_opa(0, 0)  # 透明！透出下层 OSD1
        preview.set_style_border_width(0, 0)
        preview.set_style_pad_all(0, 0)
        preview.set_style_radius(0, 0)
        preview.clear_flag(lv.obj.FLAG.SCROLLABLE)
        preview.clear_flag(lv.obj.FLAG.CLICKABLE)
        self._preview_bg = preview

    # ── 底栏 ──────────────────────────────────────

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
            img, _ = _make_icon(gallery_btn, icon_data, icon_dsc,
                                ICON_TARGET, 4, 0)
            self._gallery_icon = img

        gallery_btn.add_event(
            lambda e: self._on_gallery(e) if e.get_code() == lv.EVENT.CLICKED else None,
            lv.EVENT.CLICKED, None)

        # ── 快门按钮（中）──
        shutter_btn = lv.obj(bar)
        shutter_btn.set_size(SHUTTER_OUTER, SHUTTER_OUTER)
        shutter_btn.align(lv.ALIGN.CENTER, 0, 0)
        shutter_btn.set_style_bg_opa(0, 0)
        shutter_btn.set_style_border_width(3, 0)
        shutter_btn.set_style_border_color(lv.color_hex(WHITE), 0)
        shutter_btn.set_style_border_opa(255, 0)
        shutter_btn.set_style_radius(lv.pct(50), 0)
        shutter_btn.set_style_pad_all(0, 0)
        shutter_btn.clear_flag(lv.obj.FLAG.SCROLLABLE)
        shutter_btn.add_flag(lv.obj.FLAG.CLICKABLE)
        shutter_btn.add_event(
            lambda e: self._on_shutter(e) if e.get_code() == lv.EVENT.CLICKED else None,
            lv.EVENT.CLICKED, None)
        self._shutter_btn = shutter_btn

        # ── 录制计时器（快门右侧，初始隐藏）──
        timer = lv.label(bar)
        timer.set_text("")
        timer.align_to(shutter_btn, lv.ALIGN.OUT_RIGHT_MID, 16, 0)
        timer_style = make_back_bar_text_style(fonts.body)
        timer.add_style(timer_style, 0)
        timer.set_style_text_color(lv.color_hex(RED), 0)
        timer.set_style_text_opa(255, 0)
        # 清除默认主题的描边/阴影（避免红字带黑边）
        timer.set_style_bg_opa(0, 0)
        timer.set_style_border_width(0, 0)
        timer.set_style_pad_all(0, 0)
        try:
            timer.set_style_shadow_width(0, 0)
            timer.set_style_shadow_opa(0, 0)
        except Exception:
            pass
        try:
            timer.set_style_text_outline_width(0, 0)
            timer.set_style_text_outline_opa(0, 0)
        except Exception:
            pass
        timer.add_flag(lv.obj.FLAG.HIDDEN)
        self._timer_label = timer

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
            img, _ = _make_icon(mode_btn, icon_data, icon_dsc,
                                ICON_TARGET, 4, 0)
            self._mode_icon = img

        # 模式指示绿点（叠在图标下方表示录像模式，K230 LVGL v8 img_recolor 替代方案）
        dot = lv.obj(bar)
        dot.set_size(8, 8)
        dot.align(lv.ALIGN.RIGHT_MID, -18, 16)  # 图标右下
        dot.set_style_bg_color(lv.color_hex(GREEN), 0)
        dot.set_style_bg_opa(0, 0)  # 初始隐藏
        dot.set_style_border_width(0, 0)
        dot.set_style_radius(lv.pct(50), 0)
        dot.clear_flag(lv.obj.FLAG.SCROLLABLE)
        dot.clear_flag(lv.obj.FLAG.CLICKABLE)
        self._mode_green_dot = dot

        mode_btn.add_event(
            lambda e: self._on_mode_toggle(e) if e.get_code() == lv.EVENT.CLICKED else None,
            lv.EVENT.CLICKED, None)

    # ── 模式切换 ──────────────────────────────────

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

    def _refresh_mode_icon(self):
        """根据状态更新模式图标指示（绿点）"""
        if self._mode_green_dot is None:
            return

        is_video = (self._state in (STATE_VIDEO, STATE_RECORDING))
        self._mode_green_dot.set_style_bg_opa(255 if is_video else 0, 0)

    # ── 快门 ──────────────────────────────────────

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

    # ── 拍照 ──────────────────────────────────────

    def _capture_photo(self):
        """拍照并保存到 /data/photo/"""
        import os
        import time as _time

        photo_dir = "/data/photo/"
        try:
            os.mkdir(photo_dir)
        except Exception:
            pass

        t = _time.localtime()
        fname = f"IMG_{t[0]:04d}{t[1]:02d}{t[2]:02d}_{t[3]:02d}{t[4]:02d}{t[5]:02d}.jpg"
        path = photo_dir + fname

        try:
            from media.sensor import CAM_CHN_ID_1
            chn = getattr(self.ctx.lcd, "capture_chn", CAM_CHN_ID_1)
            # chn1 = VGA/RGB565（支持 jpg save）。首帧偶发未就绪，
            # 短重试几次；持久失败才上报（配置正确时一般首次即成功）。
            # 保存为 JPG：文件体积小。图库通过 image.Image() 硬件解码
            # 为 raw RGB888 像素再交给 LVGL 渲染（LVGL K230 未编译 LV_USE_JPEG）。
            img = None
            last_err = None
            for _attempt in range(5):
                try:
                    img = self._sensor.snapshot(chn=chn)
                    break
                except Exception as se:
                    last_err = se
                    _time.sleep_ms(30)
            if img is None:
                raise last_err if last_err else Exception("snapshot returned None")
            img.save(path)
            print(f"[Camera] photo saved: {path}")
            self._flash_feedback()
        except Exception as e:
            print(f"[Camera] capture failed: {e}")

    def _flash_feedback(self):
        """拍照白闪反馈 — 创建半透明白层，由 on_frame() 轮询在 ~120ms 后删除
        （K230 MicroPython 无 lv.timer，统一走 on_frame 帧驱动）。"""
        if self._preview_bg is None:
            return
        import time as _time
        flash = lv.obj(self._preview_bg)
        flash.set_size(lv.pct(100), lv.pct(100))
        flash.set_pos(0, 0)
        flash.set_style_bg_color(lv.color_hex(WHITE), 0)
        flash.set_style_bg_opa(160, 0)
        flash.set_style_border_width(0, 0)
        flash.set_style_radius(0, 0)
        flash.clear_flag(lv.obj.FLAG.SCROLLABLE)
        flash.clear_flag(lv.obj.FLAG.CLICKABLE)
        self._flash_obj = flash
        self._flash_start = _time.ticks_ms()

    # ── 图库 — 缩略图加载 ──────────────────────────────

    @staticmethod
    def _fit_thumb_size(img_w, img_h, max_w=GAL_THUMB_W, max_h=GAL_THUMB_H):
        """计算适配缩略图容器的等比缩放尺寸"""
        if img_w <= 0 or img_h <= 0:
            return max_w, max_h
        scale = min(max_w / img_w, max_h / img_h)
        if scale > 1.0:
            scale = 1.0  # 不放大
        return int(img_w * scale), int(img_h * scale)

    @staticmethod
    def _bmp_dimensions(data):
        """Parse BMP header to get image width/height (little-endian)."""
        if not data or len(data) < 26:
            return 0, 0
        # BMP file header: 14 bytes. DIB header at offset 14.
        # width at byte 18, height at byte 22 (4 bytes LE each)
        w = (data[18] | (data[19] << 8) | (data[20] << 16)
             | (data[21] << 24))
        h = (data[22] | (data[23] << 8) | (data[24] << 16)
             | (data[25] << 24))
        return w, h

    def _load_thumbnail(self, path):
        """加载照片缩略图，构造 LVGL img_dsc_t。

        BMP：直接读取原始字节 → LVGL 内置 BMP 解码器（板端验证可行）。
        JPG：K230 image.Image() **不能读 JPG**（只支持 BMP，否则报
        `Image is not BMP!`）。改读拍照时生成的 sidecar 缩略图：
        `<photo>.jpg.thumb.bmp`（160×120，~38KB，由 _capture_photo
        通过 mean_pooled(8,8) 生成）。
        旧 JPG 无 sidecar 时返回 None，UI 自动降级为占位灰块。

        Returns: dict {pixel_data, src_w, src_h, w, h, img_dsc} 或 None
        """
        import os as _os

        try:
            _os.exitpoint()
            low = path.lower()

            if not (low.endswith('.jpg') or low.endswith('.jpeg')
                    or low.endswith('.bmp')):
                return None

            if low.endswith('.bmp'):
                # BMP：直接读取原文件字节，LVGL 内置解码器处理
                with open(path, 'rb') as f:
                    data = f.read()
                if not data:
                    print(f"[Gallery] empty BMP: {path}")
                    return None
                src_w, src_h = CameraApp._bmp_dimensions(data)
            else:
                # JPG：读 sidecar 缩略图 BMP（拍照时同步生成）
                thumb_path = path + ".thumb.bmp"
                try:
                    with open(thumb_path, 'rb') as f:
                        data = f.read()
                except Exception:
                    # 旧 JPG 无 sidecar，UI 显示占位灰块
                    return None
                if not data:
                    print(f"[Gallery] empty thumb: {thumb_path}")
                    return None
                src_w, src_h = CameraApp._bmp_dimensions(data)
                if src_w <= 0 or src_h <= 0:
                    print(f"[Gallery] bad thumb header: {thumb_path}")
                    return None

            _os.exitpoint()
            # LVGL 内置 BMP 解码器从字节头自动识别尺寸/格式
            dsc = lv.img_dsc_t({
                'data_size': len(data),
                'data': data,
            })

            # 缩略图显示目标尺寸
            tw, th = CameraApp._fit_thumb_size(src_w, src_h)
            return {
                'pixel_data': data,     # GC 保活：bytes 被 img_dsc.data 引用
                'src_w': src_w,         # 源图尺寸（供 set_zoom 计算缩放比）
                'src_h': src_h,
                'w': tw,                # 显示目标尺寸
                'h': th,
                'img_dsc': dsc,
            }

        except Exception as e:
            print(f"[Gallery] load thumbnail failed for {path}: {e}")
            import sys
            try:
                sys.print_exception(e)
            except Exception:
                pass
            return None

    # ── 图库 — 日期分组 ──────────────────────────────

    def _group_photos_by_date(self, photo_dir):
        """扫描照片目录，按日期分组后批量加载缩略图。

        Returns: list[dict] 每组 {date_key, label, photos: [{path,fname,mtime,thumb}]}
                 空列表表示没有照片
        """
        import os as _os
        import time as _time
        import gc as _gc

        # ── 扫描文件 ──
        files = []
        try:
            for f in _os.listdir(photo_dir):
                low = f.lower()
                # 排除 sidecar 缩略图（IMG_xxx.jpg.thumb.bmp）——它们以
                # .bmp 结尾会通过下方过滤，但不是独立照片。
                if low.endswith('.thumb.bmp'):
                    continue
                # .avi 暂不处理（录像为空壳）
                if low.endswith('.jpg') or low.endswith('.bmp'):
                    full_path = photo_dir + f
                    try:
                        st = _os.stat(full_path)
                        files.append((f, full_path, st[8]))  # (name, path, mtime)
                    except Exception:
                        files.append((f, full_path, 0))
        except Exception as e:
            print(f"[Gallery] listdir failed: {e}")
            return []

        if not files:
            return []

        # ── 按 mtime 倒序 → 按日期分组 ──
        files.sort(key=lambda x: x[2], reverse=True)

        groups_dict = {}
        for fname, fpath, mtime in files:
            if mtime > 0:
                t = _time.localtime(mtime)
                date_key = f"{t[0]:04d}-{t[1]:02d}-{t[2]:02d}"
                date_label = f"{t[0]}年{t[1]}月{t[2]}日"
            else:
                date_key = "unknown"
                date_label = "未知日期"

            if date_key not in groups_dict:
                groups_dict[date_key] = {
                    'date_key': date_key,
                    'label': date_label,
                    'photos': [],
                }
            groups_dict[date_key]['photos'].append({
                'fname': fname,
                'path': fpath,
                'mtime': mtime,
                'thumb': None,  # 待加载
            })

        # ── 保持日期倒序 ──
        groups = list(groups_dict.values())
        groups.sort(key=lambda g: g['date_key'], reverse=True)

        # 图库不显示缩略图（K230 image.Image 不能读 JPG），照片行
        # 只显示文件名 + 日期 + 删除按钮。photo['thumb'] 保持 None。

        return groups

    # ── 录像 ──────────────────────────────────────

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

    def _stop_recording(self):
        """停止录像"""
        self._state = STATE_VIDEO
        self._refresh_shutter()
        self._show_timer(False)
        print(f"[Camera] recording stopped: {self._record_path}")

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
        elapsed = _time.ticks_diff(_time.ticks_ms(),
                                    self._record_start_ticks) // 1000
        h = elapsed // 3600
        m = (elapsed % 3600) // 60
        s = elapsed % 60

        self._timer_blink = (elapsed % 2 == 0)
        dot = "●" if self._timer_blink else "○"

        self._timer_label.set_text(f"{dot} {h:02d}:{m:02d}:{s:02d}")

    # ── 图库 ──────────────────────────────────────

    def _on_gallery(self, e):
        """图库按钮（仅待机状态可用）"""
        if self._state in (STATE_RECORDING, STATE_GALLERY):
            return
        self._enter_gallery()

    def _enter_gallery(self):
        """进入图库页面 — 扫描 + 分组 + 加载缩略图 + 构建 UI"""
        import os as _os
        import gc as _gc

        self._state = STATE_GALLERY

        # 隐藏相机 UI
        if self._bottom_bar is not None:
            self._bottom_bar.add_flag(lv.obj.FLAG.HIDDEN)
        if self._preview_bg is not None:
            self._preview_bg.add_flag(lv.obj.FLAG.HIDDEN)
        if self._timer_label is not None:
            self._timer_label.add_flag(lv.obj.FLAG.HIDDEN)

        # 图库页需要不透明屏幕背景（相机预览时 bg_opa=0 透出 OSD1）
        if self._screen is not None:
            self._screen.set_style_bg_color(lv.color_hex(Colors.BG), 0)
            self._screen.set_style_bg_opa(255, 0)

        # 更新标题
        if self._title_label is not None:
            self._title_label.set_text(self.ctx.lang.t("camera.gallery"))

        # ── 扫描 + 分组 + 加载缩略图（所有 I/O 在构建 LVGL 之前完成）──
        photo_dir = "/data/photo/"
        self._gallery_thumbs = []
        self._gallery_groups = []
        self._gallery_objects = []

        try:
            _os.mkdir(photo_dir)
        except Exception:
            pass

        groups = self._group_photos_by_date(photo_dir)
        self._gallery_groups = groups

        # 保活所有缩略图引用（LVGL 重绘时解引用 pixel_data）
        for group in groups:
            for photo in group['photos']:
                if photo['thumb'] is not None:
                    self._gallery_thumbs.append(photo['thumb'])

        # ── 构建 LVGL 列表 UI（此时零 I/O）──
        self._build_gallery_ui(groups)

        # 安全回收
        _gc.collect()
        print(f"[Gallery] enter done: {len(groups)} groups, {len(self._gallery_thumbs)} thumbs")

    # ── 图库 UI 组件 ──────────────────────────────────

    def _make_date_header(self, parent, y, text):
        """创建日期分组标题 bar — 深色背景 + 居中灰色文字"""
        bar = lv.obj(parent)
        bar.set_size(lv.pct(100), GAL_DATE_H)
        bar.set_pos(0, y)
        bar.set_style_bg_color(lv.color_hex(GAL_DATE_BG), 0)
        bar.set_style_bg_opa(255, 0)
        bar.set_style_border_width(0, 0)
        bar.set_style_pad_all(0, 0)
        bar.set_style_radius(0, 0)
        bar.clear_flag(lv.obj.FLAG.SCROLLABLE)
        bar.clear_flag(lv.obj.FLAG.CLICKABLE)
        self._gallery_objects.append(bar)

        label = lv.label(bar)
        label.set_text(text)
        label.align(lv.ALIGN.CENTER, 0, 0)
        label.add_style(make_back_bar_text_style(fonts.body), 0)
        label.set_style_text_color(lv.color_hex(Colors.TEXT_DIM), 0)
        self._gallery_objects.append(label)

        return bar

    def _make_photo_row(self, parent, y, photo):
        """创建照片行 — 文件名/日期 + 删除按钮（不显示缩略图）

        K230 image.Image() 不能读 JPG（只支持 BMP），曾尝试 sidecar
        BMP 缩略图但加复杂度且需重新拍摄已有照片。当前回退到只显示
        文件名+日期+删除按钮的简洁列表。

        Args:
            parent: 滚动列表容器
            y: 行 Y 坐标
            photo: dict {fname, path, mtime, thumb (unused)}

        Returns: row obj（用于后续删除定位）
        """
        import time as _time

        row = lv.obj(parent)
        row.set_size(lv.pct(100), GAL_ROW_H)
        row.set_pos(0, y)
        row.set_style_bg_color(lv.color_hex(GAL_ROW_BG), 0)
        row.set_style_bg_opa(255, 0)
        row.set_style_border_width(0, 0)
        row.set_style_radius(GAL_ROW_RADIUS, 0)
        row.set_style_pad_all(4, 0)
        row.clear_flag(lv.obj.FLAG.SCROLLABLE)
        self._gallery_objects.append(row)

        # ── 文件名（左侧）──
        name_lbl = lv.label(row)
        fname = photo['fname']
        if len(fname) > 30:
            fname = fname[:28] + ".."
        name_lbl.set_text(fname)
        name_x = 16
        name_lbl.align(lv.ALIGN.LEFT_MID, name_x, -12)
        name_lbl.set_style_text_color(lv.color_hex(WHITE), 0)
        name_lbl.add_style(make_back_bar_text_style(fonts.body), 0)
        self._gallery_objects.append(name_lbl)

        # ── 日期时间（文件名下方）──
        date_lbl = lv.label(row)
        mtime = photo['mtime']
        if mtime > 0:
            t = _time.localtime(mtime)
            date_str = f"{t[0]}-{t[1]:02d}-{t[2]:02d} {t[3]:02d}:{t[4]:02d}"
        else:
            date_str = "?"
        date_lbl.set_text(date_str)
        date_lbl.align(lv.ALIGN.LEFT_MID, name_x, 12)
        date_lbl.set_style_text_color(lv.color_hex(Colors.TEXT_DIM), 0)
        date_lbl.add_style(make_back_bar_text_style(fonts.body), 0)
        self._gallery_objects.append(date_lbl)

        # ── 删除按钮（右侧）──
        del_btn = lv.obj(row)
        del_btn.set_size(GAL_DELETE_SIZE, GAL_DELETE_SIZE)
        del_btn.align(lv.ALIGN.RIGHT_MID, -12, 0)
        del_btn.set_style_bg_opa(0, 0)
        del_btn.set_style_border_width(0, 0)
        del_btn.set_style_shadow_width(0, 0)
        del_btn.set_style_outline_width(0, 0)
        del_btn.set_style_outline_opa(0, 0)
        del_btn.set_style_pad_all(0, 0)
        del_btn.clear_flag(lv.obj.FLAG.SCROLLABLE)
        del_btn.add_flag(lv.obj.FLAG.CLICKABLE)

        # 删除 ✕ 标签
        x_lbl = lv.label(del_btn)
        x_lbl.set_text("×")  # × 乘号（字体子集内）
        x_lbl.center()
        x_lbl.set_style_text_color(lv.color_hex(0xCC4444), 0)
        x_lbl.add_style(make_back_bar_text_style(fonts.body), 0)
        self._gallery_objects.extend([del_btn, x_lbl])

        # 闭包捕获 photo 和 row（lambda 默认参数方式）
        del_btn.add_event(
            lambda e, p=photo, r=row: (
                self._on_delete_photo(p, r) if e.get_code() == lv.EVENT.CLICKED else None
            ),
            lv.EVENT.CLICKED, None)

        return row

    def _build_gallery_ui(self, groups):
        """构建图库纵向滚动列表 — 日期分组标题 + 照片行"""
        screen = self._screen
        list_h = screen.get_height() - BAR_H

        lst = lv.obj(screen)
        lst.set_size(lv.pct(100), list_h)
        lst.set_pos(0, BAR_H)
        lst.set_style_bg_color(lv.color_hex(Colors.BG), 0)
        lst.set_style_bg_opa(255, 0)
        lst.set_style_border_width(0, 0)
        lst.set_style_pad_all(8, 0)
        lst.set_style_radius(0, 0)
        lst.set_scroll_dir(lv.DIR.VER)
        self._gallery_list = lst

        if not groups:
            lang = self.ctx.lang
            empty = lv.label(lst)
            empty.set_text(lang.t("camera.no_photos"))
            empty.align(lv.ALIGN.CENTER, 0, 0)
            empty.add_style(make_back_bar_text_style(fonts.body), 0)
            empty.set_style_text_color(lv.color_hex(Colors.TEXT_DIM), 0)
            self._gallery_objects.append(empty)
            return

        y = 4
        for group in groups:
            # 日期分组标题
            self._make_date_header(lst, y, group['label'])
            y += GAL_DATE_H + 4

            # 照片行
            for photo in group['photos']:
                row = self._make_photo_row(lst, y, photo)
                photo['_row_obj'] = row
                y += GAL_ROW_H + GAL_ROW_GAP

            y += 8  # 组间额外间距

        # content_height 不小于列表可见高度，确保照片少时列表仍填满屏幕
        content_h = y + 4
        if content_h < list_h:
            content_h = list_h
        lst.set_content_height(content_h)

    @staticmethod
    def _remove_photo_from_groups(groups, photo):
        """Remove photo from grouped gallery data and drop empty date groups."""
        for group in list(groups):
            photos = group.get('photos', [])
            if photo in photos:
                photos.remove(photo)
                if not photos:
                    groups.remove(group)
                return True
        return False

    def _rebuild_gallery_ui(self):
        """Rebuild gallery list so remaining rows are laid out from current data."""
        old_objects = self._gallery_objects
        self._gallery_objects = []

        for obj in old_objects:
            try:
                obj.delete()
            except Exception:
                pass

        if self._gallery_list is not None:
            try:
                self._gallery_list.delete()
            except Exception:
                pass
            self._gallery_list = None

        self._build_gallery_ui(self._gallery_groups)

    # ── 图库 — 删除 ──────────────────────────────────

    def _on_delete_photo(self, photo, row_obj):
        """删除照片文件 + 移除 UI 行"""
        import os as _os

        path = photo['path']
        print(f"[Gallery] delete: {path}")

        # 1. 删除文件
        try:
            _os.remove(path)
        except Exception as e:
            print(f"[Gallery] remove failed: {e}")
            return  # 删除失败，保留 UI

        # 1b. 同步删除 sidecar 缩略图（JPG 才有）
        low = path.lower()
        if low.endswith('.jpg') or low.endswith('.jpeg'):
            try:
                _os.remove(path + ".thumb.bmp")
            except Exception:
                pass

        # 2. 从保活列表移除缩略图数据
        thumb = photo.get('thumb')
        if thumb is not None and thumb in self._gallery_thumbs:
            self._gallery_thumbs.remove(thumb)

        # 3. 从分组数据移除，然后重建列表让下方照片重新排版上移
        self._remove_photo_from_groups(self._gallery_groups, photo)
        self._rebuild_gallery_ui()

        # 4. 蜂鸣反馈
        self.ctx.buzzer.beep(ms=20)

    def _leave_gallery(self):
        """离开图库，清理缩略图数据 + LVGL 对象 + 恢复相机 UI"""
        import gc as _gc

        # ── 清理 LVGL 对象 ──
        for obj in self._gallery_objects:
            try:
                obj.delete()
            except Exception:
                pass
        self._gallery_objects = []

        if self._gallery_list is not None:
            try:
                self._gallery_list.delete()
            except Exception:
                pass
            self._gallery_list = None

        # ── 释放缩略图像素内存 ──
        self._gallery_thumbs = []
        self._gallery_groups = []

        # ── 恢复相机 UI ──
        # 先恢复屏幕透明（相机预览需要 bg_opa=0 透出 OSD1）
        if self._screen is not None:
            self._screen.set_style_bg_opa(0, 0)
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

        # 此时 LVGL 对象已全部删除，安全 gc
        _gc.collect()
        print("[Gallery] leave done")

    # ── 返回 ──────────────────────────────────────

    def _on_back(self, e):
        if self._state == STATE_GALLERY:
            self._leave_gallery()
        else:
            self.ctx.request_exit()

    # ── 销毁 ──────────────────────────────────────

    def _destroy_ui(self):
        # 释放图库缩略图内存（非 LVGL 对象，先于 LVGL 清理）
        self._gallery_thumbs = []
        self._gallery_groups = []

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
        self._mode_green_dot = None

        if self._flash_obj is not None:
            try:
                self._flash_obj.delete()
            except Exception:
                pass
            self._flash_obj = None

        # 恢复屏幕背景不透明（相机页设过透明，主菜单需不透明背景）
        try:
            scr = lv.scr_act()
            scr.set_style_bg_color(lv.color_hex(Colors.BG), 0)
            scr.set_style_bg_opa(255, 0)
        except Exception:
            pass
        self._screen = None
