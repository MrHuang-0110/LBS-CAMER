# scripts/tag_detect/app.py — AprilTag + 二维码双功能标签识别。
#
# 复用 _template 单线程主循环。chn1 QVGA RGB565 做检测(官方 demo 同款),
# chn0 VGA RGB888 显示。两功能底栏切换(选中置绿),各自独立 ID 设置(最多4),
# KEY2 注册(走 tag_db.register via registrar),协议类型 0x04 上传4槽位。
# 持久化预留(flush_to_disk no-op)。

import os
import sys
import time
import image
import lvgl as lv
from media.display import Display
from media.sensor import CAM_CHN_ID_0, CAM_CHN_ID_1
from core.icon_cache import icon_cache
from core.font_manager import fonts
from core.id_registry import IdRegistry
from core.tag_db import TagDB

BAR_H = 52
PREVIEW_Y = BAR_H
PREVIEW_H = 376
BAR_BG = 0x1A1A1A
CARD_BG = 0x2A2A2A
CARD_ACTIVE = 0x2E7D32   # 选中卡片绿色
# chn1 QVGA(320x240) -> chn0 VGA(640x480):坐标 x2 整数缩放
DET_SCALE = 2

_RUNTIME = None
_screen = None
_top_bar = None
_bottom_bar = None
_preview = None
_count_label = None
_id_registry = None
_april_db = None
_qr_db = None
_active_fn = "april"      # "april" | "qr"
_april_card = None
_qr_card = None


def _active_db():
    return _april_db if _active_fn == "april" else _qr_db


def _init_registry(fpioa):
    global _id_registry
    _id_registry = IdRegistry(fpioa, pin=0)


def on_frame(img):
    """chn1 检测 -> 匹配 DB -> 命中填4槽位 -> chn0 画框 -> host_tick。"""
    if _RUNTIME is None:
        return
    img_det = _RUNTIME.sensor.snapshot(chn=CAM_CHN_ID_1)
    slots = [None, None, None, None]
    db = _active_db()
    detected = []   # [(code_id, rect), ...]

    if _active_fn == "april":
        try:
            tags = img_det.find_apriltags(families=image.TAG36H11)
        except Exception as e:
            print("[tag_detect] apriltag error: %s" % e)
            tags = []
        for tag in tags:
            code_id = tag.id()
            rect = tag.rect()   # [x, y, w, h] in QVGA
            detected.append((code_id, rect))
    else:
        try:
            codes = img_det.find_qrcodes()
        except Exception as e:
            print("[tag_detect] qr error: %s" % e)
            codes = []
        for code in codes:
            code_id = code.payload()
            rect = code.rect()
            detected.append((code_id, rect))

    # 匹配 DB,命中填槽位 + chn0 画框
    for code_id, rect in detected:
        x, y, w, h = [int(v) for v in rect]
        slot, score = db.match(code_id)
        if slot is not None:
            slots[slot - 1] = (slot, x * DET_SCALE, y * DET_SCALE,
                               w * DET_SCALE, h * DET_SCALE, 100)
            img.draw_rectangle([x * DET_SCALE, y * DET_SCALE,
                                w * DET_SCALE, h * DET_SCALE],
                               color=(0, 255, 0), thickness=4)
            img.draw_string_advanced(x * DET_SCALE, y * DET_SCALE - 24, 24,
                                     "id%d" % slot)
        else:
            img.draw_rectangle([x * DET_SCALE, y * DET_SCALE,
                                w * DET_SCALE, h * DET_SCALE],
                               color=(255, 0, 0), thickness=2)

    # KEY2 注册:pending 且当前帧有检测到码 -> 存入下一槽
    if _id_registry is not None and _id_registry.has_pending() and detected:
        code_id, _rect = detected[0]
        slot = _id_registry.try_register(code_id, _RUNTIME.buzzer,
                                         registrar=db.register)
        if slot is not None:
            _refresh_count()

    if _RUNTIME is not None and _RUNTIME.host is not None:
        _RUNTIME.host_tick(slots)


def _refresh_count():
    if _count_label is not None and _RUNTIME is not None:
        try:
            _count_label.set_text(
                _RUNTIME.lang.t("tag_detect.registered", _active_db().count))
        except Exception:
            pass


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
    _refresh_count()


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
    """顶栏(back+标题) + 透明预览 + 底栏(list图标 + AprilTag/QR卡片 + 计数)。"""
    global _screen, _top_bar, _bottom_bar, _preview, _count_label
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
    btn.set_size(48, 48)
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
        target = int(48 * 0.85)
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

    # 底栏:list图标(纯显示) + AprilTag卡片 + QR卡片 + 计数
    _bottom_bar = lv.obj(screen)
    _bottom_bar.set_size(lv.pct(100), BAR_H)
    _bottom_bar.set_pos(0, PREVIEW_Y + PREVIEW_H)
    _bottom_bar.set_style_bg_color(lv.color_hex(BAR_BG), 0)
    _bottom_bar.set_style_bg_opa(255, 0)
    _bottom_bar.set_style_border_width(0, 0)
    _bottom_bar.set_style_pad_all(0, 0)
    _bottom_bar.set_style_radius(0, 0)
    _bottom_bar.clear_flag(lv.obj.FLAG.SCROLLABLE)

    # list 图标(只显示不绑功能)
    list_btn = lv.obj(_bottom_bar)
    list_btn.set_size(48, 48)
    list_btn.align(lv.ALIGN.LEFT_MID, 2, 0)
    list_btn.set_style_bg_opa(0, 0)
    list_btn.set_style_border_width(0, 0)
    list_btn.set_style_pad_all(0, 0)
    list_btn.clear_flag(lv.obj.FLAG.SCROLLABLE)
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

    count_label = lv.label(_bottom_bar)
    count_label.set_text(runtime.lang.t("tag_detect.registered", 0))
    count_label.add_style(make_back_bar_text_style(fonts.body), 0)
    count_label.align(lv.ALIGN.RIGHT_MID, -12, 0)
    _count_label = count_label


def _destroy_ui():
    global _screen, _top_bar, _bottom_bar, _preview, _count_label, _april_card, _qr_card
    for obj in (_april_card, _qr_card, _top_bar, _bottom_bar, _preview, _count_label):
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
    _count_label = None
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
    global _RUNTIME, _april_db, _qr_db
    _RUNTIME = runtime
    _april_db = TagDB()
    _qr_db = TagDB()
    exit_flag = [False]
    _init_registry(runtime.fpioa)
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
            if _id_registry is not None:
                _id_registry.poll_k2()
            Display.show_image(img, 0, 0, Display.LAYER_OSD1)
            time.sleep_ms(lv.task_handler())
            fc += 1
            if fc % 30 == 0:
                print("[tag_detect] fc=%d" % fc)
    finally:
        _destroy_ui()
        if _april_db is not None:
            _april_db.flush_to_disk()
        if _qr_db is not None:
            _qr_db.flush_to_disk()
        _RUNTIME = None
