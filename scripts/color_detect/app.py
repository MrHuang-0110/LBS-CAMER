# scripts/color_detect/app.py — 颜色识别(LAB 阈值 find_blobs + 屏幕取色)。
#
# 复用 _template 单线程主循环 + tag_detect 双通道。chn0 VGA RGB888 显示+取色,
# chn1 QVGA RGB565 find_blobs 检测。屏幕点击取色→RGB→LAB→±10容差6阈值→立即检测。
# KEY2 注册当前检测色到 4 槽(轮转),每帧注册色 find_blobs 画 ID 彩框,协议 0x06。
# 左表 3 槽采色历史(底色=采样色),与 ID 独立。

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
from core.color_db import ColorDB
from core.geometry import clamp_rect

BAR_H = 52
PREVIEW_Y = BAR_H
PREVIEW_H = 376
BAR_BG = 0x1A1A1A
CARD_BG = 0x2A2A2A
CARD_ACTIVE = 0x2E7D32   # 选中格绿色
# chn1 QVGA(320x240) -> chn0 VGA(640x480):坐标 x2 整数缩放
DET_SCALE = 2
# 取色容差(L/A/B 统一 ±10)
TOLERANCE = 10
_COLOR_DB_PATH = "/sdcard/CamerAi/data/color_db.json"
# L 范围 0-100,A/B 范围 -128~127
L_LO, L_HI = 0, 100
AB_LO, AB_HI = -128, 127

# 25 槽画框颜色表(1~4 历史色 + 5~25 色环;学习 ID 不用白色),共享 core/box_colors
from core.box_colors import BOX_COLORS, BOX_UNKNOWN
# conf 字节 bit7 = 已学习标记(对齐 comm/host_api.LEARNED_FLAG);conf 0~100 恒 <128 不冲突
LEARNED_FLAG = 0x80

# 6 阈值格定义:(key, 显示文本, lo, hi, default)。标签用短英文(Lmin/Lmax/...),
# 通用缩写不走 i18n,3 字母在 52px 格内可读。
THRESH_CELLS = [
    ("Lmin", "Lmin", 0, 100, 0),
    ("Lmax", "Lmax", 0, 100, 100),
    ("Amin", "Amin", -128, 127, -10),
    ("Amax", "Amax", -128, 127, 10),
    ("Bmin", "Bmin", -128, 127, -10),
    ("Bmax", "Bmax", -128, 127, 10),
]


def _draw_color(hex_color):
    """hex 0xRRGGBB -> K230 draw_rectangle color tuple (A, B, G, R)。"""
    r = (hex_color >> 16) & 0xFF
    g = (hex_color >> 8) & 0xFF
    b = hex_color & 0xFF
    return (0xFF, b, g, r)


def _rgb_to_lab(r, g, b):
    """sRGB [0,255] -> Lab。L:0-100, A/B:-128~127(标准 sRGB→XYZ→Lab D65)。

    纯 Python,仅取色时调一次,无性能压力。
    函数体用字面量范围(不引用模块常量),便于独立测试 exec。
    """
    def _linear(c):
        c = c / 255.0
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4
    rl = _linear(r)
    gl = _linear(g)
    bl = _linear(b)
    # sRGB→XYZ (D65)
    x = (rl * 0.4124564 + gl * 0.3575761 + bl * 0.1804375) / 0.95047
    y = (rl * 0.2126729 + gl * 0.7151522 + bl * 0.0721750) / 1.00000
    z = (rl * 0.0193339 + gl * 0.1191920 + bl * 0.9503041) / 1.08883
    def _f(t):
        return t ** (1 / 3) if t > 0.008856 else (7.787 * t + 16 / 116)
    fx = _f(x)
    fy = _f(y)
    fz = _f(z)
    L = 116 * fy - 16
    A = 500 * (fx - fy)
    B = 200 * (fy - fz)
    # 裁剪到有效范围(显示/存储用整数)
    L = max(0, min(100, round(L)))
    A = max(-128, min(127, round(A)))
    B = max(-128, min(127, round(B)))
    return (L, A, B)


def _make_threshold(lab):
    """LAB 中心值 -> 6 阈值 (Lmin,Lmax,Amin,Amax,Bmin,Bmax),容差 ±10,裁剪。

    函数体用字面量范围(不引用模块常量),便于独立测试 exec。
    """
    L, A, B = lab
    Lmin = max(0, L - 10)
    Lmax = min(100, L + 10)
    Amin = max(-128, A - 10)
    Amax = min(127, A + 10)
    Bmin = max(-128, B - 10)
    Bmax = min(127, B + 10)
    return (Lmin, Lmax, Amin, Amax, Bmin, Bmax)


_RUNTIME = None
_screen = None
_top_bar = None
_bottom_bar = None
_preview = None
_table = None          # 左表容器(自建 4×3 网格,非 lv.table)
_table_cells = {}      # {(row,col): label_obj} 12 格标签
_table_rows = [None, None, None]  # 3 个采色行容器(供设底色)
_id_registry = None
_color_db = None
_slider = None         # 共享滑块
_thresh_labels = {}    # {key: label_obj} 6 格数值标签
_thresh_cells = {}     # {key: cell_obj} 6 格容器
_selected_key = "Lmin" # 当前选中格
_thresh_values = {"Lmin": 0, "Lmax": 100, "Amin": -10, "Amax": 10,
                  "Bmin": -10, "Bmax": 10}  # 当前 6 阈值
_pending_click = None  # (x,y) 待取色,或 None
_swatch = [None, None, None]  # 左表 3 槽采色历史 (lab, rgb) 或 None
_overlay = None
_clear_btn = None
_save_btn = None
_close_overlay = False
_pending_clear_flush = False  # 清除写盘请求:主循环安全窗口 flush(防断电重启旧数据回魂)


def _init_registry(fpioa):
    global _id_registry
    _id_registry = IdRegistry(fpioa, pin=0)


def _select_cell(key):
    """选中某阈值格(置绿)+ 滑块 range/value 同步。再次点击同一格取消选中(置灰)。"""
    global _selected_key
    if _selected_key == key:
        _selected_key = None
        key = None  # 取消选中格
    else:
        _selected_key = key
    for k, cell in _thresh_cells.items():
        try:
            cell.set_style_bg_color(
                lv.color_hex(CARD_ACTIVE if k == key else CARD_BG), 0)
        except Exception:
            pass
    # 同步滑块 range + value；取消选中时隐藏滑块
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
    """滑块值变化 -> 更新选中格数值 + _thresh_values。"""
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
    """建一个阈值格(可点选)+ 数值标签。label_text 为直接显示文本(短英文)。"""
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
    """刷新左表(自建 4×3 网格):表头 L/A/B + 3 行采色历史(底色=采样 RGB)。"""
    if _table is None:
        return
    # 表头行(0):L / A / B
    for col in range(3):
        lbl = _table_cells.get((0, col))
        if lbl is not None:
            try:
                lbl.set_text(["L", "A", "B"][col])
            except Exception:
                pass
    # 采色历史 3 行(1-3):每行底色=采样 RGB,文字=LAB 三值
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


def _on_preview_clicked(e):
    """点预览区取色:记录屏幕坐标(VGA 空间)。"""
    global _pending_click
    if e.get_code() != lv.EVENT.CLICKED:
        return
    if _overlay is not None:
        global _close_overlay
        _close_overlay = True
        return
    try:
        # K230 MicroPython LVGL 绑定:get_point 需传入预分配 point_t 填充,
        # 无参调用不返回 point(demo 标准用法:indev.get_point(point))。
        indev = lv.indev_get_act()
        if indev is not None:
            pt = lv.point_t()
            indev.get_point(pt)
            _pending_click = (pt.x, pt.y)
    except Exception as ex:
        print("[color_detect] get_point error: %s" % ex)


def _on_list_clicked(e):
    """弹出清除/保存浮层(对齐 tag_detect)。"""
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
    global _close_overlay, _pending_clear_flush
    if e.get_code() != lv.EVENT.CLICKED:
        return
    _color_db.clear()
    _pending_clear_flush = True  # 清除即写盘:主循环 task_handler 前安全窗口执行(防断电重启旧数据回魂)
    if _RUNTIME is not None and _RUNTIME.buzzer is not None:
        _RUNTIME.buzzer.beep(ms=200)
    _close_overlay = True


def _on_save_clicked(e):
    global _close_overlay
    if e.get_code() != lv.EVENT.CLICKED:
        return
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
    """顶栏(返回+标题) + 左表 + 透明预览(可取色) + 底栏(list+6格+滑块)。"""
    global _screen, _top_bar, _bottom_bar, _preview, _table, _slider
    screen = lv.scr_act()
    screen.set_style_bg_opa(0, 0)
    screen.add_flag(lv.obj.FLAG.CLICKABLE)
    _screen = screen

    # 顶栏
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
    icon_data, icon_dsc = icon_cache.get_color_icon("back")
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
    title.set_text(runtime.lang.t("category.color_detect"))
    title.align(lv.ALIGN.CENTER, 0, 0)
    from ui.theme import make_back_bar_text_style
    title.add_style(make_back_bar_text_style(fonts.body), 0)

    # 左表(自建 4×3 网格,非 lv.table):顶栏左下方,叠在预览区左缘。
    # 用 obj 网格而非 lv.table —— table cell 默认白底+选中外框不可控,
    # 且无法按行设底色(采样色)。网格每行一个 obj 容器,底色=采样 RGB。
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
            row_obj = _table  # 表头行用容器本身背景(透明)
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

    # 透明预览区(透出 OSD1,可点击取色)。右侧留 40px 给竖向滑块。
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

    # 共享滑块(预览区右侧,竖向):调选中阈值格的值
    _slider = lv.slider(screen)
    _slider.set_size(20, 300)
    _slider.set_pos(612, 90)
    _slider.set_range(0, 100)
    _slider.set_value(_thresh_values[_selected_key], lv.ANIM.OFF)
    _slider.add_event(_on_slider_changed, lv.EVENT.VALUE_CHANGED, None)

    # 底栏:list 图标 + 6 阈值格铺满 + 计数(滑块已移出)
    _bottom_bar = lv.obj(screen)
    _bottom_bar.set_size(lv.pct(100), BAR_H)
    _bottom_bar.set_pos(0, PREVIEW_Y + PREVIEW_H)
    _bottom_bar.set_style_bg_color(lv.color_hex(BAR_BG), 0)
    _bottom_bar.set_style_bg_opa(255, 0)
    _bottom_bar.set_style_border_width(0, 0)
    _bottom_bar.set_style_pad_all(0, 0)
    _bottom_bar.set_style_radius(0, 0)
    _bottom_bar.clear_flag(lv.obj.FLAG.SCROLLABLE)

    # list 图标(左)
    list_btn = lv.obj(_bottom_bar)
    list_btn.set_size(48, 48)
    list_btn.align(lv.ALIGN.LEFT_MID, 2, 0)
    list_btn.set_style_bg_opa(0, 0)
    list_btn.set_style_border_width(0, 0)
    list_btn.set_style_pad_all(0, 0)
    list_btn.clear_flag(lv.obj.FLAG.SCROLLABLE)
    list_btn.add_flag(lv.obj.FLAG.CLICKABLE)
    list_btn.add_event(_on_list_clicked, lv.EVENT.CLICKED, None)
    list_icon_data, list_icon_dsc = icon_cache.get_color_icon("list")
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

    # 6 阈值格铺满底栏中段:list(48) 之后到计数(右~90)之间均分
    _COUNT_W = 90
    _cells_start = 56
    _cells_total = 640 - _cells_start - _COUNT_W
    _cell_w = _cells_total // 6
    for i, (key, label_text, lo, hi, dflt) in enumerate(THRESH_CELLS):
        _make_cell(_bottom_bar, key, label_text, lo, hi, dflt,
                   _cells_start + i * _cell_w, _cell_w - 4)


def _current_threshold_tuple():
    """从 _thresh_values 取当前 6 阈值 tuple。"""
    return (_thresh_values["Lmin"], _thresh_values["Lmax"],
            _thresh_values["Amin"], _thresh_values["Amax"],
            _thresh_values["Bmin"], _thresh_values["Bmax"])


def _apply_sample(lab, rgb):
    """采样色套用 ±10 阈值 -> 更新 6 格数值 + 滑块;压入左表 3 槽循环。"""
    global _swatch
    th = _make_threshold(lab)
    _thresh_values["Lmin"] = th[0]
    _thresh_values["Lmax"] = th[1]
    _thresh_values["Amin"] = th[2]
    _thresh_values["Amax"] = th[3]
    _thresh_values["Bmin"] = th[4]
    _thresh_values["Bmax"] = th[5]
    # 刷新 6 格数值标签
    for key in _thresh_labels:
        lbl = _thresh_labels[key]
        if lbl is not None:
            try:
                lbl.set_text(str(_thresh_values[key]))
            except Exception:
                pass
    # 同步滑块到选中格
    if _slider is not None:
        for k, _label, lo, hi, _dflt in THRESH_CELLS:
            if k == _selected_key:
                _slider.set_range(lo, hi)
                _slider.set_value(_thresh_values[_selected_key], lv.ANIM.OFF)
                break
    # 压入左表 3 槽循环(覆盖最旧:0->1->2->0)
    _swatch = [_swatch[1], _swatch[2], (lab, rgb)]
    _refresh_table()


def _find_largest_blob(img_det, th):
    """find_blobs 取最大 blob(rect [x,y,w,h] in QVGA),无返回 None。

    K230 CanMV find_blobs 的 thresholds 要求 list[list[int]],阈值元素须是
    list 而非 tuple(传 tuple 报 'can't convert tuple to int')。对齐 demo
    实验11:threshold 为 list,find_blobs([threshold])。
    """
    try:
        th_list = [int(v) for v in th]
        blobs = img_det.find_blobs([th_list], pixels_threshold=30,
                                   area_threshold=30, merge=True)
    except Exception as e:
        print("[color_detect] find_blobs error: %s (th=%r)" % (e, th))
        return None
    if not blobs:
        return None
    best = max(blobs, key=lambda b: b.pixels())
    return best.rect()  # [x, y, w, h]


def on_frame(img):
    """chn1 find_blobs 检测 -> 当前色白框 + 注册色彩色ID框 -> chn0 画框 -> host_tick。"""
    if _RUNTIME is None:
        return
    img_det = _RUNTIME.sensor.snapshot(chn=CAM_CHN_ID_1)
    slots = []   # 列表化:统一上限 25(原固定 4 槽),order_slots 按屏幕位置排序
    cur_th = _current_threshold_tuple()

    # 处理 pending_click 取色。
    # ⚠️ K230 MicroPython 的 image.get_pixel 对 chn0 RGB888 snapshot 返回 None
    # (板端实测:合法坐标 640x480 仍返回 None,该绑定版本未实现)。
    # 改用 chn1 RGB565 img_det 取色 —— RGB565 是 find_blobs 依赖的格式,get_pixel
    # 在其上有效。触摸坐标是 VGA 空间,÷ DET_SCALE(2) 转回 QVGA。
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
                # RGB565 打包:(R<<11)|(G<<5)|B, 5/6/5 位 → 扩展到 8 位
                r = ((pixel >> 11) & 0x1F) << 3
                g = ((pixel >> 5) & 0x3F) << 2
                b = (pixel & 0x1F) << 3
            else:
                print("[color_detect] get_pixel(565) returned %r at (%d,%d)"
                      % (pixel, qx, qy))
                raise ValueError("get_pixel returned %r" % type(pixel))
            lab = _rgb_to_lab(r, g, b)
            rgb_hex = ((r & 0xFF) << 16) | ((g & 0xFF) << 8) | (b & 0xFF)
            _apply_sample(lab, rgb_hex)
            cur_th = _current_threshold_tuple()
        except Exception as e:
            print("[color_detect] sample error: %s" % e)

    # 当前色检测 -> 白框(未注册)
    rect = _find_largest_blob(img_det, cur_th)
    if rect is not None:
        x, y, w, h = [int(v) for v in rect]
        color = _draw_color(BOX_UNKNOWN)
        bx, by, bw, bh = clamp_rect(x * DET_SCALE, y * DET_SCALE,
                                    w * DET_SCALE, h * DET_SCALE,
                                    img.width(), img.height())
        img.draw_rectangle(bx, by, bw, bh, color=color, thickness=2)
        # 当前色未注册(与任一注册色阈值不完全相等) → 上报 id=0 + learned=0
        cur_registered = any(entry['threshold'] == cur_th
                             for _i, entry in _color_db.iter_slots())
        if not cur_registered:
            slots.append((0, bx, by, bw, bh, 100))

    # 注册色检测 -> 彩色 ID 框 + 填 slots
    for entry_idx, entry in _color_db.iter_slots():
        r2 = _find_largest_blob(img_det, entry['threshold'])
        if r2 is not None:
            x, y, w, h = [int(v) for v in r2]
            box_color = BOX_COLORS.get(entry_idx, BOX_UNKNOWN)
            color = _draw_color(box_color)
            bx, by, bw, bh = clamp_rect(x * DET_SCALE, y * DET_SCALE,
                                        w * DET_SCALE, h * DET_SCALE,
                                        img.width(), img.height())
            img.draw_rectangle(bx, by, bw, bh, color=color, thickness=4)
            img.draw_string_advanced(bx, by - 24, 24,
                                     "ID%d" % entry_idx, color=color)
            slots.append((entry_idx, bx, by, bw, bh, 100 | LEARNED_FLAG))  # 已学习:bit7=1

    # 居中绿色十字(对齐 tag_detect)
    img.draw_cross(320, 240, color=(0xFF, 0x00, 0xFF, 0x00), size=20, thickness=2)

    # KEY2 注册:pending 即注册当前 6 阈值到 4 槽。
    # ⚠️ 不依赖当前帧 find_blobs 命中(rect)——颜色阈值本身就是特征,
    # 用户取色/调滑块设好阈值后按 KEY2 就该注册,无需画面里此刻有色块。
    if _id_registry is not None and _id_registry.has_pending():
        lab_mid = ((cur_th[0] + cur_th[1]) // 2,
                   (cur_th[2] + cur_th[3]) // 2,
                   (cur_th[4] + cur_th[5]) // 2)
        latest_rgb = _swatch[2][1] if _swatch[2] is not None else 0xFFFFFF
        slot = _id_registry.try_register(
            (cur_th, lab_mid), _RUNTIME.buzzer,
            registrar=lambda th: _color_db.register(th, rgb=latest_rgb))
        if slot is not None:
            _color_db.flush_to_disk(_COLOR_DB_PATH)  # 注册即写（on_frame 内，task_handler 前）
            print("[color_detect] registered -> ID%d (lab=%r)" % (slot, lab_mid))
        else:
            print("[color_detect] KEY2 pending but register returned None")

    if _RUNTIME is not None and _RUNTIME.host is not None:
        _RUNTIME.host_tick(slots)


def _destroy_ui():
    global _screen, _top_bar, _bottom_bar, _preview, _table, _slider
    global _overlay, _clear_btn, _save_btn, _table_rows
    for obj in (_clear_btn, _save_btn, _overlay, _slider, _table,
                _top_bar, _bottom_bar, _preview):
        if obj is not None:
            try:
                obj.delete()
            except Exception:
                pass
    _clear_btn = None
    _save_btn = None
    _overlay = None
    _slider = None
    _table = None
    _top_bar = None
    _bottom_bar = None
    _preview = None
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
    """reset 框架入口。单线程主循环:snapshot chn0 -> on_frame -> show OSD1 -> task_handler。"""
    global _RUNTIME, _color_db, _pending_clear_flush
    _RUNTIME = runtime
    _color_db = ColorDB()
    _color_db.load_from_disk(_COLOR_DB_PATH)  # 启动加载（首次 task_handler 前安全窗口）
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
                print("[color_detect] on_frame error: %s" % e)
                try:
                    sys.print_exception(e)
                except Exception:
                    pass
            if _id_registry is not None:
                _id_registry.poll_k2()
            _process_overlay_close()
            if _pending_clear_flush:
                _pending_clear_flush = False
                if _color_db is not None:
                    _color_db.flush_to_disk(_COLOR_DB_PATH)  # 清除即写空库(task_handler 前安全窗口)
            Display.show_image(img, 0, 0, Display.LAYER_OSD1)
            time.sleep_ms(lv.task_handler())
            fc += 1
            if fc % 30 == 0:
                print("[color_detect] fc=%d" % fc)
    finally:
        _destroy_ui()
        if _color_db is not None:
            _color_db.flush_to_disk(_COLOR_DB_PATH)
        _RUNTIME = None
