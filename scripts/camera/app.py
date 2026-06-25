# scripts/camera/app.py — 相机 APP(拍照/录像 + 图库)run(runtime) 范式
#
# 架构(reset 框架,对齐 settings/_template):
#   OSD1 层:相机帧(runtime.sensor.snapshot(chn0) → Display.show_image(LAYER_OSD1))
#   OSD2 层:LVGL UI(flush 回调显式 show_image(LAYER_OSD2))
#   LVGL 预览区 bg_opa=0 透明 → 透出下层 OSD1 相机画面
#
# 单线程主循环(snapshot→状态业务→task_handler 串行,一个写者),从结构上
# 消除双线程双写者 display DMA 竞争。
#
# 传感器:每进程独立 runtime.sensor(init_app 已配 chn0 VGA/RGB888 预览 +
# chn1 SXGAM/RGB565 拍照,并由 init_app 启动取流)。退出由 main.py runtime.cleanup()
# 统一 stop + deinit,不再用旧架构的共享常驻 lcd sensor。
#
# 状态机:PHOTO ←→ VIDEO → RECORDING,任意待机态 → GALLERY

import os
import time
import struct
import lvgl as lv
from media.display import Display
from media.sensor import CAM_CHN_ID_0, CAM_CHN_ID_1
from core.icon_cache import icon_cache
from core.font_manager import fonts
from ui.theme import Colors, make_back_bar_text_style


# ── 布局常量 ──────────────────────────────────────
BAR_H = 52              # 顶栏/底栏高度
PREVIEW_Y = BAR_H       # 预览区起始 Y
PREVIEW_H = 376         # 480 - BAR_H * 2
BTN_SIZE = 48           # 栏上按钮点击区
ICON_TARGET = 40        # 栏上图标目标尺寸
SHUTTER_OUTER = 44      # 快门外径(含圆环)
BAR_BG = 0x1A1A1A       # 栏背景色

# ── 图库常量 ──
GAL_ROW_H = 100          # 照片行高度
GAL_ROW_BG = 0x1A1A1A    # 照片行卡片背景
GAL_DATE_H = 28          # 日期分组标题高度
GAL_DATE_BG = 0x111111   # 日期分组标题背景
GAL_DELETE_SIZE = 36     # 删除按钮尺寸
GAL_ROW_GAP = 6          # 行间距
GAL_ROW_RADIUS = 8       # 照片行圆角

# 颜色
RED = 0xCC4444
GREEN = 0x44CC44
WHITE = 0xFFFFFF

# 状态
STATE_PHOTO = 0
STATE_VIDEO = 1
STATE_RECORDING = 2
STATE_GALLERY = 3

# ── 模块级状态(替代旧类 self._xxx)──
_RUNTIME = None
_state = STATE_PHOTO
_screen = None
_top_bar = None
_bottom_bar = None
_preview_bg = None
_timer_label = None
_shutter_btn = None
_mode_green_dot = None
_title_label = None

# 录像相关
_record_start_ticks = 0
_timer_blink = True
_record_path = ""

# 拍照白闪反馈(主循环轮询删除,K230 无 lv.timer)
_flash_obj = None
_flash_start = 0

# 图库相关
_gallery_list = None
_gallery_objects = []
_gallery_groups = []

# 待删除照片队列 — 删除按钮 CLICKED 回调只入队 + 蜂鸣,
# 实际 os.remove + _rebuild_gallery_ui 由主循环 _process_pending_deletes 执行
# (K230 无 lv.timer,对齐白闪 deferred 模式)。根因:删除按钮是 _gallery_list
# 的子孙,在回调内删 list(事件派发控件祖先)= LVGL use-after-free → 板端
# 死机重启(C 级故障,不可被 try/except 捕获)。
_pending_deletes = []


def _ctx_runtime():
    """返回当前 run() 的 runtime(入口缓存到模块级)。

    LVGL 回调(_on_shutter/_on_mode_toggle 等)拿不到 run() 的 runtime 参数,
    通过本函数取模块级缓存的 _RUNTIME。
    """
    return _RUNTIME


def _png_zoom(png_data, target):
    """从 PNG 头解析真实尺寸,计算缩放因子。"""
    if not png_data or len(png_data) < 24:
        return 256
    w = struct.unpack('>I', png_data[16:20])[0]
    h = struct.unpack('>I', png_data[20:24])[0]
    if w <= 0 or h <= 0:
        return 256
    zoom = int(min(target / w, target / h) * 256)
    return max(8, min(zoom, 256))


def _make_icon(parent, icon_data, icon_dsc, target_size, x):
    """在 parent 上创建图标,返回 img_obj。"""
    if icon_dsc is None or icon_data is None:
        return None

    img = lv.img(parent)
    img.set_src(icon_dsc)
    zoom = _png_zoom(icon_data, target_size)
    img.set_zoom(zoom)

    src_w = struct.unpack('>I', icon_data[16:20])[0]
    rendered_w = src_w * zoom // 256
    actual_x = x - (src_w - rendered_w) // 2
    img.align(lv.ALIGN.LEFT_MID, actual_x, 0)
    return img


# ── 主入口 ──────────────────────────────────────

def run(runtime):
    """camera 主入口(reset 框架调 mod.run(runtime))。

    单线程主循环:chn0 预览帧 → OSD1 + 状态业务(录像计时/白闪) + task_handler。
    触摸返回钮设 exit_flag → 循环退出 → _destroy_ui → main.py cleanup+reset 回菜单。
    """
    global _RUNTIME, _state
    _RUNTIME = runtime
    _state = STATE_PHOTO
    exit_flag = [False]
    _build_ui(runtime, exit_flag)
    while not exit_flag[0]:
        os.exitpoint()
        # 推送相机帧到 OSD1 层(图库态不推)
        if _state != STATE_GALLERY:
            try:
                img = runtime.sensor.snapshot(chn=CAM_CHN_ID_0)
                Display.show_image(img, 0, 0, Display.LAYER_OSD1)
            except Exception:
                pass  # 偶发 snapshot 失败不刷屏
        # 录像计时器更新
        if _state == STATE_RECORDING:
            _update_timer()
        # 拍照白闪清理(120ms 后删)
        _update_flash()
        # 处理 deferred 删除(从图库删除按钮回调入队;回调内直接删 list 会
        # use-after-free 死机,故 deferred 到主循环执行)
        _process_pending_deletes()
        runtime.host_tick()
        time.sleep_ms(lv.task_handler())
    _destroy_ui()


# ── UI 构建 ──────────────────────────────────

def _build_ui(runtime, exit_flag):
    """构建顶栏(返回钮+标题) + 透明预览区 + 底栏(图库/快门/模式)。"""
    global _screen
    screen = lv.scr_act()
    # 屏幕背景透明:OSD2 透明处透出下层 OSD1 相机画面
    screen.set_style_bg_opa(0, 0)
    _screen = screen

    _build_top_bar(runtime, exit_flag)
    _build_preview_area()
    _build_bottom_bar(runtime)


def _build_top_bar(runtime, exit_flag):
    """顶栏:返回钮(左) + 标题(居中)。"""
    global _top_bar, _title_label
    lang = runtime.lang

    bar = lv.obj(_screen)
    bar.set_size(lv.pct(100), BAR_H)
    bar.set_pos(0, 0)
    bar.set_style_bg_color(lv.color_hex(BAR_BG), 0)
    bar.set_style_bg_opa(255, 0)
    bar.set_style_border_width(0, 0)
    bar.set_style_pad_all(0, 0)
    bar.set_style_radius(0, 0)
    bar.clear_flag(lv.obj.FLAG.SCROLLABLE)
    _top_bar = bar

    # 返回钮(48×48 透明点击区 + back 图标)
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
    if icon_dsc is not None and icon_data is not None:
        _make_icon(btn, icon_data, icon_dsc, ICON_TARGET, 4)
    else:
        lbl = lv.label(btn)
        lbl.set_text("<")
        lbl.center()

    def _on_back(e):
        if e.get_code() == lv.EVENT.CLICKED:
            if _state == STATE_GALLERY:
                _leave_gallery()
            else:
                exit_flag[0] = True
    btn.add_event(_on_back, lv.EVENT.CLICKED, None)

    # 标题居中
    title = lv.label(bar)
    title.set_text(lang.t("category.camera"))
    title.align(lv.ALIGN.CENTER, 0, 0)
    title.add_style(make_back_bar_text_style(fonts.body), 0)
    _title_label = title


def _build_preview_area():
    """预览区:全透明 LVGL 对象,让底层 OSD1 相机画面透出。"""
    global _preview_bg
    preview = lv.obj(_screen)
    preview.set_size(lv.pct(100), PREVIEW_H)
    preview.set_pos(0, PREVIEW_Y)
    preview.set_style_bg_opa(0, 0)  # 透明!透出下层 OSD1
    preview.set_style_border_width(0, 0)
    preview.set_style_pad_all(0, 0)
    preview.set_style_radius(0, 0)
    preview.clear_flag(lv.obj.FLAG.SCROLLABLE)
    preview.clear_flag(lv.obj.FLAG.CLICKABLE)
    _preview_bg = preview


def _build_bottom_bar(runtime):
    """底栏:图库(左) + 快门(中) + 模式(右)。"""
    global _bottom_bar, _shutter_btn, _timer_label, _mode_green_dot

    bar = lv.obj(_screen)
    bar.set_size(lv.pct(100), BAR_H)
    bar.set_pos(0, PREVIEW_Y + PREVIEW_H)
    bar.set_style_bg_color(lv.color_hex(BAR_BG), 0)
    bar.set_style_bg_opa(255, 0)
    bar.set_style_border_width(0, 0)
    bar.set_style_pad_all(0, 0)
    bar.set_style_radius(0, 0)
    bar.clear_flag(lv.obj.FLAG.SCROLLABLE)
    _bottom_bar = bar

    # ── 图库按钮(左)──
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
    if icon_dsc is not None and icon_data is not None:
        _make_icon(gallery_btn, icon_data, icon_dsc, ICON_TARGET, 4)

    gallery_btn.add_event(_on_gallery, lv.EVENT.CLICKED, None)

    # ── 快门按钮(中)──
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
    shutter_btn.add_event(_on_shutter, lv.EVENT.CLICKED, None)
    _shutter_btn = shutter_btn

    # ── 录制计时器(快门右侧,初始隐藏)──
    timer = lv.label(bar)
    timer.set_text("")
    timer.align_to(shutter_btn, lv.ALIGN.OUT_RIGHT_MID, 16, 0)
    timer.add_style(make_back_bar_text_style(fonts.body), 0)
    timer.set_style_text_color(lv.color_hex(RED), 0)
    timer.set_style_text_opa(255, 0)
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
    _timer_label = timer

    # ── 模式按钮(右)──
    mode_btn = lv.obj(bar)
    mode_btn.set_size(BTN_SIZE, BTN_SIZE)
    mode_btn.align(lv.ALIGN.RIGHT_MID, -24, 0)
    mode_btn.set_style_bg_opa(0, 0)
    mode_btn.set_style_border_width(0,0)
    mode_btn.set_style_shadow_width(0, 0)
    mode_btn.set_style_outline_width(0, 0)
    mode_btn.set_style_outline_opa(0, 0)
    mode_btn.set_style_pad_all(0, 0)
    mode_btn.clear_flag(lv.obj.FLAG.SCROLLABLE)
    mode_btn.add_flag(lv.obj.FLAG.CLICKABLE)

    icon_data, icon_dsc = icon_cache.get_camera_icon("mode")
    if icon_dsc is not None and icon_dsc is not None:
        _make_icon(mode_btn, icon_data, icon_dsc, ICON_TARGET, 4)

    # 模式指示绿点(录像模式,K230 LVGL v8 img_recolor 替代方案)
    dot = lv.obj(bar)
    dot.set_size(8, 8)
    dot.align(lv.ALIGN.RIGHT_MID, -18, 16)
    dot.set_style_bg_color(lv.color_hex(GREEN), 0)
    dot.set_style_bg_opa(0, 0)  # 初始隐藏
    dot.set_style_border_width(0, 0)
    dot.set_style_radius(lv.pct(50), 0)
    dot.clear_flag(lv.obj.FLAG.SCROLLABLE)
    dot.clear_flag(lv.obj.FLAG.CLICKABLE)
    _mode_green_dot = dot

    mode_btn.add_event(_on_mode_toggle, lv.EVENT.CLICKED, None)

    _refresh_shutter()
    _refresh_mode_icon()


# ── 模式切换 ──────────────────────────────────

def _on_mode_toggle(e):
    """切换拍照 ↔ 录像(仅待机状态)。"""
    global _state
    if e.get_code() != lv.EVENT.CLICKED:
        return
    if _state == STATE_PHOTO:
        _state = STATE_VIDEO
    elif _state == STATE_VIDEO:
        _state = STATE_PHOTO
    else:
        return  # 录像中或图库中不响应
    _refresh_shutter()
    _refresh_mode_icon()


def _refresh_shutter():
    """根据当前状态更新快门外观。"""
    if _shutter_btn is None:
        return
    btn = _shutter_btn
    if _state == STATE_PHOTO:
        btn.set_style_bg_opa(0, 0)
        btn.set_style_border_color(lv.color_hex(WHITE), 0)
        btn.set_style_border_width(3, 0)
        btn.set_style_radius(lv.pct(50), 0)
    elif _state == STATE_VIDEO:
        btn.set_style_bg_color(lv.color_hex(RED), 0)
        btn.set_style_bg_opa(255, 0)
        btn.set_style_border_color(lv.color_hex(WHITE), 0)
        btn.set_style_border_width(3, 0)
        btn.set_style_radius(lv.pct(50), 0)
    elif _state == STATE_RECORDING:
        btn.set_style_bg_color(lv.color_hex(RED), 0)
        btn.set_style_bg_opa(255, 0)
        btn.set_style_border_width(0, 0)
        btn.set_style_radius(4, 0)


def _refresh_mode_icon():
    """根据状态更新模式图标指示(绿点)。"""
    if _mode_green_dot is None:
        return
    is_video = (_state in (STATE_VIDEO, STATE_RECORDING))
    _mode_green_dot.set_style_bg_opa(255 if is_video else 0, 0)


# ── 快门 ──────────────────────────────────────

def _on_shutter(e):
    """快门按钮:拍照 / 开始录像 / 停止录像。"""
    if e.get_code() != lv.EVENT.CLICKED:
        return
    runtime = _ctx_runtime()
    if _state == STATE_PHOTO:
        _capture_photo()
        runtime.buzzer.beep(ms=30)
    elif _state == STATE_VIDEO:
        _start_recording()
        runtime.buzzer.beep(ms=50)
    elif _state == STATE_RECORDING:
        _stop_recording()
        runtime.buzzer.beep(ms=80)


# ── 拍照 ──────────────────────────────────────

def _capture_photo():
    """拍照并保存到 /data/photo/(JPG,chn1 通道)。"""
    global _flash_obj, _flash_start
    runtime = _ctx_runtime()

    photo_dir = "/data/photo/"
    try:
        os.mkdir(photo_dir)
    except Exception:
        pass

    t = time.localtime()
    fname = "IMG_%04d%02d%02d_%02d%02d%02d.jpg" % (
        t[0], t[1], t[2], t[3], t[4], t[5])
    path = photo_dir + fname

    try:
        # chn1 = SXGAM/RGB565(支持 jpg save)。首帧偶发未就绪,短重试几次。
        img = None
        last_err = None
        for _attempt in range(5):
            try:
                img = runtime.sensor.snapshot(chn=CAM_CHN_ID_1)
                break
            except Exception as se:
                last_err = se
                time.sleep_ms(30)
        if img is None:
            raise last_err if last_err else Exception("snapshot returned None")
        img.save(path)
        print("[Camera] photo saved: %s" % path)
        _flash_feedback()
    except Exception as e:
        print("[Camera] capture failed: %s" % e)


def _flash_feedback():
    """拍照白闪反馈 — 创建半透明白层,由主循环 _update_flash 在 ~120ms 后删除。"""
    global _flash_obj, _flash_start
    if _preview_bg is None:
        return
    flash = lv.obj(_preview_bg)
    flash.set_size(lv.pct(100), lv.pct(100))
    flash.set_pos(0, 0)
    flash.set_style_bg_color(lv.color_hex(WHITE), 0)
    flash.set_style_bg_opa(160, 0)
    flash.set_style_border_width(0, 0)
    flash.set_style_radius(0, 0)
    flash.clear_flag(lv.obj.FLAG.SCROLLABLE)
    flash.clear_flag(lv.obj.FLAG.CLICKABLE)
    _flash_obj = flash
    _flash_start = time.ticks_ms()


def _update_flash():
    """主循环每帧调:白闪 120ms 后删除。"""
    global _flash_obj
    if _flash_obj is None:
        return
    if time.ticks_diff(time.ticks_ms(), _flash_start) >= 120:
        try:
            _flash_obj.delete()
        except Exception:
            pass
        _flash_obj = None


# ── 录像(空壳:状态 + 计时器,无实际编码)──

def _start_recording():
    """开始录像(空壳:仅状态 + 计时器)。"""
    global _state, _record_start_ticks, _record_path
    photo_dir = "/data/photo/"
    try:
        os.mkdir(photo_dir)
    except Exception:
        pass

    t = time.localtime()
    fname = "VID_%04d%02d%02d_%02d%02d%02d.avi" % (
        t[0], t[1], t[2], t[3], t[4], t[5])
    _record_path = photo_dir + fname
    _record_start_ticks = time.ticks_ms()

    _state = STATE_RECORDING
    _refresh_shutter()
    _show_timer(True)
    print("[Camera] recording started: %s" % _record_path)


def _stop_recording():
    """停止录像(空壳)。"""
    global _state
    _state = STATE_VIDEO
    _refresh_shutter()
    _show_timer(False)
    print("[Camera] recording stopped: %s" % _record_path)


def _show_timer(visible):
    """显示/隐藏录制计时器。"""
    if _timer_label is None:
        return
    if visible:
        _timer_label.clear_flag(lv.obj.FLAG.HIDDEN)
        _timer_label.set_text("● 00:00:00")
    else:
        _timer_label.add_flag(lv.obj.FLAG.HIDDEN)
        _timer_label.set_text("")


def _update_timer():
    """每帧调用:更新录制时间 + 红点闪烁。"""
    global _timer_blink
    if _timer_label is None or _state != STATE_RECORDING:
        return

    elapsed = time.ticks_diff(time.ticks_ms(), _record_start_ticks) // 1000
    h = elapsed // 3600
    m = (elapsed % 3600) // 60
    s = elapsed % 60

    _timer_blink = (elapsed % 2 == 0)
    dot = "●" if _timer_blink else "○"
    _timer_label.set_text("%s %02d:%02d:%02d" % (dot, h, m, s))


# ── 图库 ──────────────────────────────────────

def _on_gallery(e):
    """图库按钮(仅待机状态可用)。"""
    if e.get_code() != lv.EVENT.CLICKED:
        return
    if _state in (STATE_RECORDING, STATE_GALLERY):
        return
    _enter_gallery()


def _group_photos_by_date(photo_dir):
    """扫描照片目录,按日期分组。无缩略图(只显示文件名+日期+删除)。

    Returns: list[dict] 每组 {date_key, label, photos: [{path,fname,mtime}]}
    """
    files = []
    try:
        for f in os.listdir(photo_dir):
            low = f.lower()
            if low.endswith('.thumb.bmp'):
                continue
            if low.endswith('.avi'):
                continue  # 录像为空壳,不展示
            if low.endswith('.jpg') or low.endswith('.bmp'):
                full_path = photo_dir + f
                try:
                    st = os.stat(full_path)
                    files.append((f, full_path, st[8]))  # (name, path, mtime)
                except Exception:
                    files.append((f, full_path, 0))
    except Exception as e:
        print("[Gallery] listdir failed: %s" % e)
        return []

    if not files:
        return []

    # 按 mtime 倒序 → 按日期分组
    files.sort(key=lambda x: x[2], reverse=True)

    groups_dict = {}
    for fname, fpath, mtime in files:
        if mtime > 0:
            t = time.localtime(mtime)
            date_key = "%04d-%02d-%02d" % (t[0], t[1], t[2])
            date_label = "%d年%d月%d日" % (t[0], t[1], t[2])
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
        })

    groups = list(groups_dict.values())
    groups.sort(key=lambda g: g['date_key'], reverse=True)
    return groups


def _enter_gallery():
    """进入图库页面 — 扫描 + 分组 + 构建 UI(无缩略图)。"""
    global _state, _gallery_objects, _gallery_groups

    _state = STATE_GALLERY

    # 隐藏相机 UI
    if _bottom_bar is not None:
        _bottom_bar.add_flag(lv.obj.FLAG.HIDDEN)
    if _preview_bg is not None:
        _preview_bg.add_flag(lv.obj.FLAG.HIDDEN)
    if _timer_label is not None:
        _timer_label.add_flag(lv.obj.FLAG.HIDDEN)

    # 图库页需要不透明屏幕背景(相机预览时 bg_opa=0 透出 OSD1)
    if _screen is not None:
        _screen.set_style_bg_color(lv.color_hex(Colors.BG), 0)
        _screen.set_style_bg_opa(255, 0)

    # 更新标题
    if _title_label is not None:
        _title_label.set_text(_ctx_runtime().lang.t("camera.gallery"))

    # 扫描 + 分组(无缩略图 I/O)
    _gallery_objects = []
    _gallery_groups = []

    photo_dir = "/data/photo/"
    try:
        os.mkdir(photo_dir)
    except Exception:
        pass

    groups = _group_photos_by_date(photo_dir)
    _gallery_groups = groups

    _build_gallery_ui(groups)
    print("[Gallery] enter done: %d groups" % len(groups))


def _make_date_header(parent, y, text):
    """创建日期分组标题 bar — 深色背景 + 居中灰色文字。"""
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
    _gallery_objects.append(bar)

    label = lv.label(bar)
    label.set_text(text)
    label.align(lv.ALIGN.CENTER, 0, 0)
    label.add_style(make_back_bar_text_style(fonts.body), 0)
    label.set_style_text_color(lv.color_hex(Colors.TEXT_DIM), 0)
    _gallery_objects.append(label)
    return bar


def _make_photo_row(parent, y, photo):
    """创建照片行 — 文件名/日期 + 删除按钮(不显示缩略图)。"""
    row = lv.obj(parent)
    row.set_size(lv.pct(100), GAL_ROW_H)
    row.set_pos(0, y)
    row.set_style_bg_color(lv.color_hex(GAL_ROW_BG), 0)
    row.set_style_bg_opa(255, 0)
    row.set_style_border_width(0, 0)
    row.set_style_radius(GAL_ROW_RADIUS, 0)
    row.set_style_pad_all(4, 0)
    row.clear_flag(lv.obj.FLAG.SCROLLABLE)
    _gallery_objects.append(row)

    # ── 文件名(左侧)──
    name_lbl = lv.label(row)
    fname = photo['fname']
    if len(fname) > 30:
        fname = fname[:28] + ".."
    name_lbl.set_text(fname)
    name_x = 16
    name_lbl.align(lv.ALIGN.LEFT_MID, name_x, -12)
    name_lbl.set_style_text_color(lv.color_hex(WHITE), 0)
    name_lbl.add_style(make_back_bar_text_style(fonts.body), 0)
    _gallery_objects.append(name_lbl)

    # ── 日期时间(文件名下方)──
    date_lbl = lv.label(row)
    mtime = photo['mtime']
    if mtime > 0:
        t = time.localtime(mtime)
        date_str = "%04d-%02d-%02d %02d:%02d" % (t[0], t[1], t[2], t[3], t[4])
    else:
        date_str = "?"
    date_lbl.set_text(date_str)
    date_lbl.align(lv.ALIGN.LEFT_MID, name_x, 12)
    date_lbl.set_style_text_color(lv.color_hex(Colors.TEXT_DIM), 0)
    date_lbl.add_style(make_back_bar_text_style(fonts.body), 0)
    _gallery_objects.append(date_lbl)

    # ── 删除按钮(右侧)──
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

    x_lbl = lv.label(del_btn)
    x_lbl.set_text("×")
    x_lbl.center()
    x_lbl.set_style_text_color(lv.color_hex(0xCC4444), 0)
    x_lbl.add_style(make_back_bar_text_style(fonts.body), 0)
    _gallery_objects.extend([del_btn, x_lbl])

    # 闭包捕获 photo 和 row
    del_btn.add_event(
        lambda e, p=photo, r=row: (
            _on_delete_photo(p, r) if e.get_code() == lv.EVENT.CLICKED else None
        ),
        lv.EVENT.CLICKED, None)

    return row


def _build_gallery_ui(groups):
    """构建图库纵向滚动列表 — 日期分组标题 + 照片行。"""
    global _gallery_list
    screen = _screen
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
    _gallery_list = lst

    if not groups:
        lang = _ctx_runtime().lang
        empty = lv.label(lst)
        empty.set_text(lang.t("camera.no_photos"))
        empty.align(lv.ALIGN.CENTER, 0, 0)
        empty.add_style(make_back_bar_text_style(fonts.body), 0)
        empty.set_style_text_color(lv.color_hex(Colors.TEXT_DIM), 0)
        _gallery_objects.append(empty)
        return

    y = 4
    for group in groups:
        _make_date_header(lst, y, group['label'])
        y += GAL_DATE_H + 4
        for photo in group['photos']:
            _make_photo_row(lst, y, photo)
            y += GAL_ROW_H + GAL_ROW_GAP
        y += 8  # 组间额外间距

    content_h = y + 4
    if content_h < list_h:
        content_h = list_h
    lst.set_content_height(content_h)


def _remove_photo_from_groups(groups, photo):
    """从分组数据移除照片,空组删除。"""
    for group in list(groups):
        photos = group.get('photos', [])
        if photo in photos:
            photos.remove(photo)
            if not photos:
                groups.remove(group)
            return True
    return False


def _rebuild_gallery_ui():
    """删除旧列表对象,按当前 _gallery_groups 重建。"""
    global _gallery_objects, _gallery_list
    old_objects = _gallery_objects
    _gallery_objects = []

    for obj in old_objects:
        try:
            obj.delete()
        except Exception:
            pass

    if _gallery_list is not None:
        try:
            _gallery_list.delete()
        except Exception:
            pass
        _gallery_list = None

    _build_gallery_ui(_gallery_groups)


def _on_delete_photo(photo, row_obj):
    """删除按钮 CLICKED 回调 — 只入队 + 蜂鸣,不删文件/不重建 UI。

    实际删除由主循环 _process_pending_deletes 执行。根因:删除按钮(del_btn)
    是 _gallery_list 的子孙,在回调内调 _rebuild_gallery_ui() 会删除 _gallery_list
    (事件派发控件的祖先)→ LVGL use-after-free → 板端死机重启(C 级故障)。
    对齐白闪 deferred 模式(K230 无 lv.timer,统一走主循环帧驱动)。
    """
    _pending_deletes.append(photo)
    _ctx_runtime().buzzer.beep(ms=20)


def _process_pending_deletes():
    """主循环每帧调:处理从删除按钮回调 deferred 的照片删除。

    此处已离开 CLICKED 事件回调,删除 _gallery_list(被删按钮的祖先)安全,
    不会 use-after-free。
    """
    if not _pending_deletes:
        return
    while _pending_deletes:
        photo = _pending_deletes.pop(0)
        path = photo['path']
        print("[Gallery] delete: %s" % path)
        # 1. 删除文件
        try:
            os.remove(path)
        except Exception as e:
            print("[Gallery] remove failed: %s" % e)
            continue  # 删除失败,跳过重建该照片
        # 2. 从分组数据移除,重建列表让下方照片上移
        _remove_photo_from_groups(_gallery_groups, photo)
    # 批量删完后重建一次 UI(避免每删一张重建一次)
    _rebuild_gallery_ui()


def _leave_gallery():
    """离开图库,清理 LVGL 对象 + 恢复相机 UI。"""
    global _state, _gallery_list, _gallery_groups

    for obj in _gallery_objects:
        try:
            obj.delete()
        except Exception:
            pass
    _gallery_objects[:] = []

    if _gallery_list is not None:
        try:
            _gallery_list.delete()
        except Exception:
            pass
        _gallery_list = None

    _gallery_groups = []
    _pending_deletes.clear()  # 离开图库,丢弃未处理的删除请求(对象即将销毁)

    # 恢复相机 UI:屏幕透明(相机预览需 bg_opa=0 透出 OSD1)
    if _screen is not None:
        _screen.set_style_bg_opa(0, 0)
    if _bottom_bar is not None:
        _bottom_bar.clear_flag(lv.obj.FLAG.HIDDEN)
    if _preview_bg is not None:
        _preview_bg.clear_flag(lv.obj.FLAG.HIDDEN)

    # 恢复标题 + 状态
    if _title_label is not None:
        _title_label.set_text(_ctx_runtime().lang.t("category.camera"))
    _state = STATE_PHOTO
    _refresh_shutter()
    _refresh_mode_icon()
    print("[Gallery] leave done")


# ── 销毁 ──────────────────────────────────────

def _destroy_ui():
    """删全部 LVGL 对象 + 恢复屏幕不透明。
    不碰 runtime 硬件(由 main.py runtime.cleanup() 统一 deinit)。
    """
    global _screen, _top_bar, _bottom_bar, _preview_bg, _timer_label
    global _gallery_list, _gallery_objects, _gallery_groups
    global _shutter_btn, _mode_green_dot, _title_label, _flash_obj

    # 释放图库对象
    _gallery_objects[:] = []
    _gallery_groups = []
    _pending_deletes.clear()  # 退出 APP,丢弃未处理的删除请求

    for obj in (_top_bar, _bottom_bar, _preview_bg, _timer_label, _gallery_list):
        if obj is not None:
            try:
                obj.delete()
            except Exception:
                pass
    _top_bar = None
    _bottom_bar = None
    _preview_bg = None
    _timer_label = None
    _gallery_list = None
    _shutter_btn = None
    _mode_green_dot = None
    _title_label = None

    if _flash_obj is not None:
        try:
            _flash_obj.delete()
        except Exception:
            pass
        _flash_obj = None

    # 恢复屏幕背景不透明(相机页设过透明,主菜单需不透明背景)
    try:
        scr = lv.scr_act()
        scr.set_style_bg_color(lv.color_hex(Colors.BG), 0)
        scr.set_style_bg_opa(255, 0)
    except Exception:
        pass
    _screen = None
