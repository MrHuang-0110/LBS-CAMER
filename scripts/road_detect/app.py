# scripts/road_detect/app.py — 道路识别(LAB 阈值 find_blobs + 逐行质心绿线)。
#
# 复用 _template 单线程主循环 + color_detect UI。chn0 VGA RGB888 显示+取色,
# chn1 QVGA RGB565 find_blobs 检测。屏幕点击取色→RGB→LAB→±10 容差→3采样并集阈值。
# 逐行求道路像素质心 x,连绿色折线。默认 ID1,list 浮层"保存"直接持久化,协议 0x07。
# 左表 3 槽采色历史(底色=采样色),无 KEY2/IdRegistry。

import os
import sys
import time
import image
import lvgl as lv
from media.display import Display
from media.sensor import CAM_CHN_ID_0, CAM_CHN_ID_1
from core.icon_cache import icon_cache
from core.font_manager import fonts
from core.road_db import RoadDB
from core.geometry import clamp_rect

BAR_H = 52
PREVIEW_Y = BAR_H
PREVIEW_H = 376
BAR_BG = 0x1A1A1A
CARD_BG = 0x2A2A2A
CARD_ACTIVE = 0x2E7D32
DET_SCALE = 2
TOLERANCE = 10
_ROAD_DB_PATH = "/sdcard/CamerAi/data/road_db.json"
L_LO, L_HI = 0, 100
AB_LO, AB_HI = -128, 127

# 道路识别暂为单摄像源预览(不跑AI、隐藏底栏/左表/滑块)。
# 后续完善时改 True:恢复 chn1 检测通道(_channels_for)、底栏 UI、on_frame 检测分支。
_DETECTION_ENABLED = False

# 道路绿线/框颜色(ABGR):绿色
ROAD_GREEN = (0xFF, 0x00, 0xFF, 0x00)

THRESH_CELLS = [
    ("Lmin", "Lmin", 0, 100, 0),
    ("Lmax", "Lmax", 0, 100, 100),
    ("Amin", "Amin", -128, 127, -10),
    ("Amax", "Amax", -128, 127, 10),
    ("Bmin", "Bmin", -128, 127, -10),
    ("Bmax", "Bmax", -128, 127, 10),
]


def _draw_color(hex_color):
    r = (hex_color >> 16) & 0xFF
    g = (hex_color >> 8) & 0xFF
    b = hex_color & 0xFF
    return (0xFF, b, g, r)


def _rgb_to_lab(r, g, b):
    """sRGB [0,255] -> Lab。L:0-100, A/B:-128~127。"""
    def _linear(c):
        c = c / 255.0
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4
    rl = _linear(r); gl = _linear(g); bl = _linear(b)
    x = (rl * 0.4124564 + gl * 0.3575761 + bl * 0.1804375) / 0.95047
    y = (rl * 0.2126729 + gl * 0.7151522 + bl * 0.0721750) / 1.00000
    z = (rl * 0.0193339 + gl * 0.1191920 + bl * 0.9503041) / 1.08883
    def _f(t):
        return t ** (1/3) if t > 0.008856 else (7.787 * t + 16/116)
    fx = _f(x); fy = _f(y); fz = _f(z)
    L = 116 * fy - 16; A = 500 * (fx - fy); B = 200 * (fy - fz)
    L = max(0, min(100, round(L)))
    A = max(-128, min(127, round(A)))
    B = max(-128, min(127, round(B)))
    return (L, A, B)


def _make_threshold(lab):
    """LAB 中心值 -> 6 阈值 ±10 容差,裁剪。"""
    L, A, B = lab
    Lmin = max(0, L - 10); Lmax = min(100, L + 10)
    Amin = max(-128, A - 10); Amax = min(127, A + 10)
    Bmin = max(-128, B - 10); Bmax = min(127, B + 10)
    return (Lmin, Lmax, Amin, Amax, Bmin, Bmax)


def _union_threshold(samples):
    """3 采样并集阈值。"""
    if not samples:
        return None
    valid = [s for s in samples if s is not None]
    if not valid:
        return None
    ths = [_make_threshold(lab) for lab, _rgb in valid]
    Lmin = min(th[0] for th in ths); Lmax = max(th[1] for th in ths)
    Amin = min(th[2] for th in ths); Amax = max(th[3] for th in ths)
    Bmin = min(th[4] for th in ths); Bmax = max(th[5] for th in ths)
    return (Lmin, Lmax, Amin, Amax, Bmin, Bmax)


def _row_centroids(find_blobs_fn, blob_rect, step=8):
    """逐行求道路质心 x。用 find_blobs 逐行 ROI(C 实现)替代逐像素 get_pixel+LAB,
    避免板端每帧上万次 Python 调用导致卡顿(对齐实验12 黑线循迹 demo 做法)。

    find_blobs_fn(row_y) -> [blob, ...]:该行道路 blob 列表(blob 有 cx()/pixels())。
    blob_rect: [x, y, w, h](大道路 blob 的 rect,限定逐行扫描范围)。
    返回: [(cx, row_y), ...];每行取 pixels 最大的 blob 的 cx。
    """
    x, y, w, h = blob_rect
    centroids = []
    for row_y in range(int(y), int(y + h), step):
        blobs = find_blobs_fn(row_y)
        if blobs:
            best = max(blobs, key=lambda b: b.pixels())
            centroids.append((best.cx(), row_y))
    return centroids


_RUNTIME = None
_screen = None
_top_bar = None
_bottom_bar = None
_preview = None
_table = None
_table_cells = {}
_table_rows = [None, None, None]
_count_label = None
_road_db = None
_slider = None
_thresh_labels = {}
_thresh_cells = {}
_selected_key = "Lmin"
_thresh_values = {"Lmin": 0, "Lmax": 100, "Amin": -10, "Amax": 10,
                  "Bmin": -10, "Bmax": 10}
_pending_click = None
_swatch = [None, None, None]
_overlay = None
_clear_btn = None
_save_btn = None
_close_overlay = False
_pending_flush = False  # 清除/保存写盘请求:主循环安全窗口 flush(坑#2:LVGL 回调内 flush 会死锁)


def _select_cell(key):
    global _selected_key
    if _selected_key == key:
        _selected_key = None
        key = None
    else:
        _selected_key = key
    for k, cell in _thresh_cells.items():
        try:
            cell.set_style_bg_color(
                lv.color_hex(CARD_ACTIVE if k == key else CARD_BG), 0)
        except Exception:
            pass
    if _slider is not None:
        if key is not None:
            _slider.clear_flag(lv.obj.FLAG.HIDDEN)
            for k, _label, lo, hi, _dflt in THRESH_CELLS:
                if k == key:
                    _slider.set_range(lo, hi)
                    _slider.set_value(_thresh_values.get(key, lo), lv.ANIM.OFF)
                    break
        else:
            _slider.add_flag(lv.obj.FLAG.HIDDEN)


def _on_slider_changed(e):
    if e.get_code() != lv.EVENT.VALUE_CHANGED:
        return
    if _slider is None or _selected_key is None:
        return
    val = _slider.get_value()
    _thresh_values[_selected_key] = val
    lbl = _thresh_labels.get(_selected_key)
    if lbl is not None:
        try:
            lbl.set_text(str(val))
        except Exception:
            pass


def _make_cell(parent, key, label_text, lo, hi, dflt, align_x, cell_w):
    from ui.theme import make_back_bar_text_style
    cell = lv.btn(parent)
    cell.set_size(cell_w, 44)
    cell.align(lv.ALIGN.LEFT_MID, align_x, 0)
    cell.set_style_bg_color(
        lv.color_hex(CARD_ACTIVE if key == _selected_key else CARD_BG), 0)
    cell.set_style_bg_opa(255, 0)
    cell.set_style_text_color(lv.color_hex(0xFFFFFF), 0)
    cell.set_style_radius(6, 0)
    cell.set_style_border_width(0, 0)
    cell.set_style_shadow_width(0, 0)
    cell.set_style_pad_all(2, 0)

    name_lbl = lv.label(cell)
    name_lbl.set_text(label_text)
    name_lbl.add_style(make_back_bar_text_style(fonts.caption), 0)
    name_lbl.align(lv.ALIGN.TOP_MID, 0, 0)

    val_lbl = lv.label(cell)
    val_lbl.set_text(str(_thresh_values.get(key, dflt)))
    val_lbl.add_style(make_back_bar_text_style(fonts.body), 0)
    val_lbl.align(lv.ALIGN.BOTTOM_MID, 0, 0)
    _thresh_labels[key] = val_lbl

    def _on_click(e, _k=key):
        if e.get_code() == lv.EVENT.CLICKED:
            _select_cell(_k)
    cell.add_event(_on_click, lv.EVENT.CLICKED, None)
    _thresh_cells[key] = cell
    return cell


def _refresh_table():
    if _table is None:
        return
    for col in range(3):
        lbl = _table_cells.get((0, col))
        if lbl is not None:
            try:
                lbl.set_text(["L", "A", "B"][col])
            except Exception:
                pass
    for i in range(3):
        entry = _swatch[i]
        row_obj = _table_rows[i]
        if row_obj is not None:
            try:
                if entry is not None:
                    rgb = entry[1]
                    row_obj.set_style_bg_color(lv.color_hex(rgb), 0)
                    row_obj.set_style_bg_opa(255, 0)
                else:
                    row_obj.set_style_bg_color(lv.color_hex(0x222222), 0)
                    row_obj.set_style_bg_opa(180, 0)
            except Exception:
                pass
        for col in range(3):
            lbl = _table_cells.get((i + 1, col))
            if lbl is None:
                continue
            try:
                if entry is not None:
                    lbl.set_text(str(entry[0][col]))
                else:
                    lbl.set_text("-")
            except Exception:
                pass


def _refresh_count():
    if _count_label is not None and _RUNTIME is not None:
        try:
            saved = _road_db.saved if _road_db is not None else False
            key = "road_detect.saved" if saved else "road_detect.id1"
            _count_label.set_text(_RUNTIME.lang.t(key))
        except Exception:
            pass


def _on_preview_clicked(e):
    global _pending_click
    if e.get_code() != lv.EVENT.CLICKED:
        return
    if _overlay is not None:
        global _close_overlay
        _close_overlay = True
        return
    try:
        indev = lv.indev_get_act()
        if indev is not None:
            pt = lv.point_t()
            indev.get_point(pt)
            _pending_click = (pt.x, pt.y)
    except Exception as ex:
        print("[road_detect] get_point error: %s" % ex)


def _on_list_clicked(e):
    global _overlay, _clear_btn, _save_btn
    if e.get_code() != lv.EVENT.CLICKED:
        return
    if _overlay is not None:
        return
    from ui.theme import make_back_bar_text_style
    _overlay = lv.obj(lv.scr_act())
    _overlay.set_size(lv.pct(100), BAR_H)
    _overlay.set_pos(0, PREVIEW_Y + PREVIEW_H - BAR_H)
    _overlay.set_style_bg_color(lv.color_hex(BAR_BG), 0)
    _overlay.set_style_bg_opa(255, 0)
    _overlay.set_style_border_width(0, 0)
    _overlay.set_style_pad_all(0, 0)
    _overlay.set_style_radius(0, 0)
    _overlay.clear_flag(lv.obj.FLAG.SCROLLABLE)
    _overlay.add_flag(lv.obj.FLAG.CLICKABLE)
    _overlay.add_event(_on_overlay_clicked, lv.EVENT.CLICKED, None)

    _clear_btn = lv.btn(_overlay)
    _clear_btn.set_size(120, 40)
    _clear_btn.align(lv.ALIGN.LEFT_MID, 20, 0)
    cl = lv.label(_clear_btn)
    cl.set_text(_RUNTIME.lang.t("color_detect.clear"))
    cl.add_style(make_back_bar_text_style(fonts.body), 0)
    cl.center()
    _clear_btn.add_event(_on_clear_clicked, lv.EVENT.CLICKED, None)

    _save_btn = lv.btn(_overlay)
    _save_btn.set_size(120, 40)
    _save_btn.align(lv.ALIGN.RIGHT_MID, -20, 0)
    sv = lv.label(_save_btn)
    sv.set_text(_RUNTIME.lang.t("color_detect.save"))
    sv.add_style(make_back_bar_text_style(fonts.body), 0)
    sv.center()
    _save_btn.add_event(_on_save_clicked, lv.EVENT.CLICKED, None)


def _on_overlay_clicked(e):
    global _close_overlay
    if e.get_code() != lv.EVENT.CLICKED:
        return
    _close_overlay = True


def _on_clear_clicked(e):
    global _close_overlay, _pending_flush
    if e.get_code() != lv.EVENT.CLICKED:
        return
    _road_db.clear()
    _pending_flush = True  # 清除即写盘:主循环 task_handler 前安全窗口执行(坑#2:回调内 flush 会死锁)
    _refresh_count()
    if _RUNTIME is not None and _RUNTIME.buzzer is not None:
        _RUNTIME.buzzer.beep(ms=200)
    _close_overlay = True


def _on_save_clicked(e):
    global _close_overlay, _pending_flush
    if e.get_code() != lv.EVENT.CLICKED:
        return
    cur_th = _current_threshold_tuple()
    lab_mid = ((cur_th[0] + cur_th[1]) // 2,
               (cur_th[2] + cur_th[3]) // 2,
               (cur_th[4] + cur_th[5]) // 2)
    latest_rgb = _swatch[2][1] if _swatch[2] is not None else 0xFFFFFF
    _road_db.save(cur_th, lab_mid, latest_rgb, list(_swatch))
    _pending_flush = True  # 保存即写盘:主循环安全窗口(坑#2:回调内 flush 会死锁)
    _refresh_count()
    if _RUNTIME is not None and _RUNTIME.buzzer is not None:
        _RUNTIME.buzzer.beep(ms=200)
    print("[road_detect] saved -> ID1 (lab=%r)" % (lab_mid,))
    _close_overlay = True


def _process_overlay_close():
    global _overlay, _clear_btn, _save_btn, _close_overlay
    if not _close_overlay:
        return
    _close_overlay = False
    for obj in (_clear_btn, _save_btn, _overlay):
        if obj is not None:
            try:
                obj.delete()
            except Exception:
                pass
    _clear_btn = None
    _save_btn = None
    _overlay = None


def _build_ui(runtime, exit_flag):
    global _screen, _top_bar, _bottom_bar, _preview, _table, _count_label, _slider
    screen = lv.scr_act()
    screen.set_style_bg_opa(0, 0)
    screen.add_flag(lv.obj.FLAG.CLICKABLE)
    _screen = screen

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
    icon_data, icon_dsc = icon_cache.get_road_icon("back")
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
    title.set_text(runtime.lang.t("category.road_detect"))
    title.align(lv.ALIGN.CENTER, 0, 0)
    from ui.theme import make_back_bar_text_style
    title.add_style(make_back_bar_text_style(fonts.body), 0)

    if not _DETECTION_ENABLED:
        # 预览模式:只顶栏 + chn0 全屏预览(OSD1 透出),不建左表/滑块/底栏。
        return

    _TABLE_X = 4
    _TABLE_Y = BAR_H + 4
    _TABLE_W = 150
    _ROW_H = 26
    _table = lv.obj(screen)
    _table.set_size(_TABLE_W, _ROW_H * 4)
    _table.set_pos(_TABLE_X, _TABLE_Y)
    _table.set_style_bg_opa(0, 0)
    _table.set_style_border_width(1, 0)
    _table.set_style_border_color(lv.color_hex(0x444444), 0)
    _table.set_style_pad_all(2, 0)
    _table.set_style_radius(0, 0)
    _table.clear_flag(lv.obj.FLAG.SCROLLABLE)
    _table.clear_flag(lv.obj.FLAG.CLICKABLE)
    _table_cells.clear()
    col_w = (_TABLE_W - 4) // 3
    for r in range(4):
        if r == 0:
            row_obj = _table
            row_obj.set_style_bg_color(lv.color_hex(0x333333), 0)
            row_obj.set_style_bg_opa(200, 0)
        else:
            row_obj = lv.obj(_table)
            row_obj.set_size(_TABLE_W - 4, _ROW_H)
            row_obj.set_pos(0, r * _ROW_H)
            row_obj.set_style_bg_color(lv.color_hex(0x222222), 0)
            row_obj.set_style_bg_opa(180, 0)
            row_obj.set_style_border_width(0, 0)
            row_obj.set_style_pad_all(0, 0)
            row_obj.set_style_radius(0, 0)
            row_obj.clear_flag(lv.obj.FLAG.SCROLLABLE)
            row_obj.clear_flag(lv.obj.FLAG.CLICKABLE)
            _table_rows[r - 1] = row_obj
        for c in range(3):
            cell_lbl = lv.label(row_obj)
            cell_lbl.set_pos(c * col_w + 2, 4)
            cell_lbl.add_style(make_back_bar_text_style(fonts.caption), 0)
            cell_lbl.set_style_text_color(lv.color_hex(0xFFFFFF), 0)
            _table_cells[(r, c)] = cell_lbl
    _refresh_table()

    _PREVIEW_W = 600
    _preview = lv.obj(screen)
    _preview.set_size(_PREVIEW_W, PREVIEW_H)
    _preview.set_pos(0, PREVIEW_Y)
    _preview.set_style_bg_opa(0, 0)
    _preview.set_style_border_width(0, 0)
    _preview.set_style_pad_all(0, 0)
    _preview.set_style_radius(0, 0)
    _preview.clear_flag(lv.obj.FLAG.SCROLLABLE)
    _preview.add_flag(lv.obj.FLAG.CLICKABLE)
    _preview.add_event(_on_preview_clicked, lv.EVENT.CLICKED, None)

    _slider = lv.slider(screen)
    _slider.set_size(20, 300)
    _slider.set_pos(612, 90)
    _slider.set_range(0, 100)
    _slider.set_value(_thresh_values[_selected_key], lv.ANIM.OFF)
    _slider.add_event(_on_slider_changed, lv.EVENT.VALUE_CHANGED, None)

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
    list_btn.add_flag(lv.obj.FLAG.CLICKABLE)
    list_btn.add_event(_on_list_clicked, lv.EVENT.CLICKED, None)
    list_icon_data, list_icon_dsc = icon_cache.get_road_icon("list")
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

    _COUNT_W = 90
    _cells_start = 56
    _cells_total = 640 - _cells_start - _COUNT_W
    _cell_w = _cells_total // 6
    for i, (key, label_text, lo, hi, dflt) in enumerate(THRESH_CELLS):
        _make_cell(_bottom_bar, key, label_text, lo, hi, dflt,
                   _cells_start + i * _cell_w, _cell_w - 4)

    count_label = lv.label(_bottom_bar)
    count_label.set_text(runtime.lang.t("road_detect.id1"))
    count_label.add_style(make_back_bar_text_style(fonts.body), 0)
    count_label.align(lv.ALIGN.RIGHT_MID, -8, 0)
    _count_label = count_label


def _current_threshold_tuple():
    return (_thresh_values["Lmin"], _thresh_values["Lmax"],
            _thresh_values["Amin"], _thresh_values["Amax"],
            _thresh_values["Bmin"], _thresh_values["Bmax"])


def _apply_sample(lab, rgb):
    """采样色压入左表 3 槽,并集刷新 6 阈值。"""
    global _swatch
    _swatch = [_swatch[1], _swatch[2], (lab, rgb)]
    # 并集阈值:从 3 槽计算并集
    union_th = _union_threshold(_swatch)
    if union_th is not None:
        _thresh_values["Lmin"] = union_th[0]
        _thresh_values["Lmax"] = union_th[1]
        _thresh_values["Amin"] = union_th[2]
        _thresh_values["Amax"] = union_th[3]
        _thresh_values["Bmin"] = union_th[4]
        _thresh_values["Bmax"] = union_th[5]
    for key in _thresh_labels:
        lbl = _thresh_labels[key]
        if lbl is not None:
            try:
                lbl.set_text(str(_thresh_values[key]))
            except Exception:
                pass
    if _slider is not None:
        for k, _label, lo, hi, _dflt in THRESH_CELLS:
            if k == _selected_key:
                _slider.set_range(lo, hi)
                _slider.set_value(_thresh_values[_selected_key], lv.ANIM.OFF)
                break
    _refresh_table()


def _find_largest_blob(img_det, th):
    """find_blobs 取最大 blob,无返回 None。"""
    try:
        th_list = [int(v) for v in th]
        blobs = img_det.find_blobs([th_list], pixels_threshold=30,
                                   area_threshold=30, merge=True)
    except Exception as e:
        print("[road_detect] find_blobs error: %s (th=%r)" % (e, th))
        return None
    if not blobs:
        return None
    best = max(blobs, key=lambda b: b.pixels())
    return best.rect()


def on_frame(img):
    """单摄像源预览(暂不跑AI)。后续完善时改 _DETECTION_ENABLED=True 启用检测分支。"""
    if _RUNTIME is None:
        return
    if not _DETECTION_ENABLED:
        # 预览模式:仅显示 chn0(主循环 show_image),不检测。协议心跳用空 slots。
        if _RUNTIME.host is not None:
            _RUNTIME.host_tick(None)
        return
    # --- 以下为检测分支(_DETECTION_ENABLED=True 时启用)---
    img_det = _RUNTIME.sensor.snapshot(chn=CAM_CHN_ID_1)
    slots = [None, None, None, None]
    cur_th = _current_threshold_tuple()

    # 处理 pending_click 取色(chn1 RGB565 get_pixel)
    global _pending_click
    if _pending_click is not None:
        cx, cy = _pending_click
        _pending_click = None
        try:
            qx = max(0, min(cx // DET_SCALE, img_det.width() - 1))
            qy = max(0, min(cy // DET_SCALE, img_det.height() - 1))
            pixel = img_det.get_pixel(qx, qy)
            if isinstance(pixel, (tuple, list)):
                r, g, b = int(pixel[0]), int(pixel[1]), int(pixel[2])
            elif isinstance(pixel, int):
                r = ((pixel >> 11) & 0x1F) << 3
                g = ((pixel >> 5) & 0x3F) << 2
                b = (pixel & 0x1F) << 3
            else:
                raise ValueError("get_pixel returned %r" % type(pixel))
            lab = _rgb_to_lab(r, g, b)
            rgb_hex = ((r & 0xFF) << 16) | ((g & 0xFF) << 8) | (b & 0xFF)
            _apply_sample(lab, rgb_hex)
            cur_th = _current_threshold_tuple()
        except Exception as e:
            print("[road_detect] sample error: %s" % e)

    # 道路检测:find_blobs 取最大 blob
    rect = _find_largest_blob(img_det, cur_th)
    if rect is not None:
        x, y, w, h = [int(v) for v in rect]
        # 画道路 bbox 绿框(ch0 VGA, ×2 缩放,贴边时收进可视区防越界挂死)
        bx, by, bw, bh = clamp_rect(x * DET_SCALE, y * DET_SCALE,
                                    w * DET_SCALE, h * DET_SCALE,
                                    img.width(), img.height())
        img.draw_rectangle(bx, by, bw, bh,
                           color=ROAD_GREEN, thickness=2)
        # 逐行质心:find_blobs 逐行 ROI(C 实现,避免逐像素 get_pixel+LAB 卡顿),
        # 限定在大 blob 的 x 范围内扫描,每行取最大 blob 的 cx,连绿色折线。
        th_list = [int(v) for v in cur_th]
        def _scan_row(_row_y, _x=x, _w=w, _th=th_list, _img=img_det):
            try:
                return _img.find_blobs([_th], roi=(_x, _row_y, _w, 1),
                                       pixels_threshold=1, area_threshold=1,
                                       merge=True)
            except Exception:
                return []
        centroids = _row_centroids(_scan_row, [x, y, w, h], step=8)
        if len(centroids) >= 2:
            for i in range(len(centroids) - 1):
                cx1 = int(centroids[i][0] * DET_SCALE)
                cy1 = int(centroids[i][1] * DET_SCALE)
                cx2 = int(centroids[i + 1][0] * DET_SCALE)
                cy2 = int(centroids[i + 1][1] * DET_SCALE)
                img.draw_line(cx1, cy1, cx2, cy2, color=ROAD_GREEN, thickness=4)
        # 报槽 1
        slots[0] = (1, bx, by, bw, bh, 100)

    if _RUNTIME is not None and _RUNTIME.host is not None:
        _RUNTIME.host_tick(slots)


def _destroy_ui():
    global _screen, _top_bar, _bottom_bar, _preview, _table, _count_label, _slider
    global _overlay, _clear_btn, _save_btn, _table_rows
    for obj in (_clear_btn, _save_btn, _overlay, _slider, _table,
                _top_bar, _bottom_bar, _preview, _count_label):
        if obj is not None:
            try:
                obj.delete()
            except Exception:
                pass
    _clear_btn = None; _save_btn = None; _overlay = None
    _slider = None; _table = None; _top_bar = None
    _bottom_bar = None; _preview = None; _count_label = None
    _thresh_labels.clear()
    _thresh_cells.clear()
    _table_cells.clear()
    _table_rows = [None, None, None]
    try:
        from ui.theme import Colors
        scr = lv.scr_act()
        scr.set_style_bg_color(lv.color_hex(Colors.BG), 0)
        scr.set_style_bg_opa(255, 0)
    except Exception:
        pass
    _screen = None


def run(runtime):
    global _RUNTIME, _road_db, _pending_flush
    _RUNTIME = runtime
    _road_db = RoadDB()
    entry = _road_db.load_from_disk(_ROAD_DB_PATH)
    if _DETECTION_ENABLED and entry is not None:
        # 还原 6 阈值
        th = entry['threshold']
        _thresh_values.update({
            "Lmin": th[0], "Lmax": th[1],
            "Amin": th[2], "Amax": th[3],
            "Bmin": th[4], "Bmax": th[5],
        })
        # 还原左表 3 槽
        global _swatch
        samples = entry.get('samples', [])
        while len(samples) < 3:
            samples.append(None)
        _swatch = samples[:3]
    exit_flag = [False]
    _build_ui(runtime, exit_flag)
    if _DETECTION_ENABLED:
        _refresh_count()
        # 还原后刷新 6 格标签
        for key in _thresh_labels:
            lbl = _thresh_labels[key]
            if lbl is not None:
                try:
                    lbl.set_text(str(_thresh_values[key]))
                except Exception:
                    pass
        _refresh_table()
    fc = 0
    try:
        while not exit_flag[0]:
            os.exitpoint()
            img = runtime.sensor.snapshot(chn=CAM_CHN_ID_0)
            try:
                on_frame(img)
            except Exception as e:
                print("[road_detect] on_frame error: %s" % e)
                try:
                    sys.print_exception(e)
                except Exception:
                    pass
            _process_overlay_close()
            if _pending_flush:
                _pending_flush = False
                if _road_db is not None:
                    _road_db.flush_to_disk(_ROAD_DB_PATH)  # 清除/保存即写盘(task_handler 前安全窗口)
            Display.show_image(img, 0, 0, Display.LAYER_OSD1)
            time.sleep_ms(lv.task_handler())
            fc += 1
            if fc % 30 == 0:
                print("[road_detect] fc=%d" % fc)
    finally:
        _destroy_ui()
        if _road_db is not None:
            _road_db.flush_to_disk(_ROAD_DB_PATH)
        _RUNTIME = None
