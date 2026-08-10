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
from core import tag_mode
from core.geometry import clamp_rect

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
_active_fn = "april"      # "april" | "qr";run() 启动时按 .tag_fn 记忆覆盖
_april_card = None
_qr_card = None
_tag_fn_dirty = False   # 切换功能后置位:主循环 task_handler 前落盘 .tag_fn(坑#2)


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

    slots, codes = tag_scan.build_slots(
        detected, qr_mode=(_active_fn != "april"), return_codes=True)

    # 全白框 + 码值标签(codes 与 slots 同序:码值与框一一对应,防 ID 错位)
    for i, (_id_val, x, y, w, h, _conf) in enumerate(slots):
        x, y, w, h = clamp_rect(x, y, w, h, img.width(), img.height())
        img.draw_rectangle(x, y, w, h, color=BOX_WHITE, thickness=2)
        img.draw_string_advanced(x, y - 24, 24, "ID:" + str(codes[i]),
                                 color=BOX_WHITE)

    # 名称帧(类型 0x0E):id + 识别内容(QR=payload 原文 / AprilTag=码值字符串)
    names = [(slots[i][0], str(codes[i])) for i in range(len(slots))]

    # 屏幕居中绿色十字(对准参考,小一点):VGA 640x480 中心 (320, 240)
    img.draw_cross(320, 240, color=(0xFF, 0x00, 0xFF, 0x00), size=20, thickness=2)

    if _RUNTIME is not None and _RUNTIME.host is not None:
        _RUNTIME.host_tick(slots, names)


def _switch_fn(fn):
    """切换 AprilTag / QR 功能。只改内存 + 置 dirty,写 .tag_fn 由主循环
    task_handler 前安全窗口执行(LVGL 事件回调在 task_handler 内,坑#2)。"""
    global _active_fn, _tag_fn_dirty
    if fn == _active_fn:
        return
    _active_fn = fn
    _tag_fn_dirty = True
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
    global _RUNTIME, _active_fn, _tag_fn_dirty
    _RUNTIME = runtime
    # 记忆启动:按 .tag_fn 上次选择决定初始功能(首次 task_handler 前安全窗口读取)
    _active_fn = tag_mode.read_tag_fn()
    _tag_fn_dirty = False
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
            # 切换功能后落盘 .tag_fn(task_handler 前安全窗口,坑#2)
            if _tag_fn_dirty:
                tag_mode.write_tag_fn(_active_fn)
                _tag_fn_dirty = False
            Display.show_image(img, 0, 0, Display.LAYER_OSD1)
            time.sleep_ms(lv.task_handler())
            fc += 1
            if fc % 30 == 0:
                print("[tag_detect] fc=%d" % fc)
    finally:
        if _tag_fn_dirty:
            tag_mode.write_tag_fn(_active_fn)  # 退出兜底(异常/正常退出均落盘)
        _destroy_ui()
        _RUNTIME = None
