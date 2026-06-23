# scripts/settings/app.py — 设置页（左右分栏布局）run(runtime) 范式
#
# 左栏 35%：功能列表（图标 + 名称），默认选中第一项
# 右栏 65%：内容区（语言切换 / 关于信息）
# ui_mode = "page"，LVGL 全程管理，无相机参与。
#
# 走 runtime.init_app 统一路径（与通用脚本模板一致）。page 型单线程纯 UI，
# 主循环只有 lv.task_handler()，无取帧无竞争。
#
# ⚠️ K230 约束：旧生命周期钩子内不得做文件 I/O 的约束已不适用（改为 run() 入口）。
# config.save() 在触摸回调执行，单线程串行无并发 flush，安全。
# 图标数据由 core/icon_cache 在启动阶段预读到内存，此处只读缓存。

import os
import time
import struct
import lvgl as lv
from core.event_bus import event_bus
from core.icon_cache import icon_cache
from ui.theme import Colors, make_back_bar_text_style
from core.font_manager import fonts


# ── 布局参数 ──────────────────────────────────────
BAR_H = 52          # 返回栏高度（与顶栏对齐）
PANEL_TOP_GAP = 6   # 面板与顶栏间距
PANEL_BOTTOM_GAP = 10  # 面板与屏幕底部间距
DIVIDER_GAP = 10    # 分界线上下留白
LEFT_W = 224        # 左栏宽度 (640 * 35%)
RIGHT_W = 412       # 右栏宽度 (640 - LEFT_W - DIVIDER_W)
ROW_H = 56          # 左栏行高
ICON_TARGET = 36    # 左栏图标显示目标尺寸
ICON_PAD_LEFT = 8   # 图标左边距
ICON_TEXT_GAP = 8   # 图标与文字水平间距
CONTENT_PAD = 12    # 右栏内边距
LANG_ROW_H = 48     # 语言行高
ABOUT_ROW_H = 44    # 关于行高
DIVIDER_W = 4       # 左右栏分界线宽度
DIVIDER_COLOR = 0x555555  # 分界线颜色（比两侧面板亮）

# 顶栏按钮
TOP_BTN_SIZE = 48

# ── 模块级 UI 引用（替代旧类 self._xxx）──
_screen = None
_top_bar = None
_left_panel = None
_right_panel = None
_divider = None
_rows = []        # [(btn_obj, item_id), ...]
_active_item = "language"


def _png_zoom(png_data, target):
    """从 PNG 头解析真实尺寸，计算缩放因子（对齐 main_menu._auto_scale_icon）"""
    if not png_data or len(png_data) < 24:
        return 256
    w = struct.unpack('>I', png_data[16:20])[0]
    h = struct.unpack('>I', png_data[20:24])[0]
    if w <= 0 or h <= 0:
        return 256
    zoom = int(min(target / w, target / h) * 256)
    return max(8, min(zoom, 256))


def run(runtime):
    """settings 主入口（reset 框架调 mod.run(runtime)）。page 型，无取帧。

    单线程主循环：仅 lv.task_handler()（处理触摸 + LVGL 重绘）。
    触摸返回钮设 exit_flag → 循环退出 → _destroy_ui → main.py cleanup+reset 回菜单。
    """
    global _active_item, _RUNTIME
    _active_item = "language"
    _RUNTIME = runtime
    exit_flag = [False]
    _build_ui(runtime, exit_flag)
    while not exit_flag[0]:
        os.exitpoint()
        time.sleep_ms(lv.task_handler())
    _destroy_ui()


def _build_top_bar(runtime, exit_flag):
    """顶栏：返回钮(左) + 标题"设置"(中)。对齐通用脚本模板顶栏。"""
    global _top_bar
    lang = runtime.lang

    bar = lv.obj(lv.scr_act())
    bar.set_size(lv.pct(100), BAR_H)
    bar.set_pos(0, 0)
    bar.set_style_bg_color(lv.color_hex(0x1A1A1A), 0)
    bar.set_style_bg_opa(255, 0)
    bar.set_style_border_width(0, 0)
    bar.set_style_pad_all(0, 0)
    bar.set_style_radius(0, 0)
    bar.clear_flag(lv.obj.FLAG.SCROLLABLE)
    _top_bar = bar

    # 返回钮（48×48 透明点击区 + back 图标）
    btn = lv.obj(bar)
    btn.set_size(TOP_BTN_SIZE, TOP_BTN_SIZE)
    btn.align(lv.ALIGN.LEFT_MID, 2, 0)
    btn.set_style_bg_opa(0, 0)
    btn.set_style_border_width(0, 0)
    btn.set_style_shadow_width(0, 0)
    btn.set_style_outline_width(0, 0)
    btn.set_style_outline_opa(0, 0)
    btn.set_style_pad_all(0, 0)
    btn.clear_flag(lv.obj.FLAG.SCROLLABLE)
    btn.add_flag(lv.obj.FLAG.CLICKABLE)

    icon_data, icon_dsc = icon_cache.get_back_icon()
    if icon_dsc is not None and icon_data is not None:
        w = h = 64
        if len(icon_data) >= 24:
            w = struct.unpack('>I', icon_data[16:20])[0]
            h = struct.unpack('>I', icon_data[20:24])[0]
        target = int(TOP_BTN_SIZE * 0.85)
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

    # 标题居中
    title = lv.label(bar)
    title.set_text(lang.t("category.settings"))
    title.align(lv.ALIGN.CENTER, 0, 0)
    title.add_style(make_back_bar_text_style(fonts.body), 0)


def _build_ui(runtime, exit_flag):
    """顶栏(返回钮+标题) + 左右分栏。"""
    global _screen, _left_panel, _right_panel, _divider
    lang = runtime.lang

    screen = lv.scr_act()
    screen.set_style_bg_color(lv.color_hex(Colors.BG), 0)
    screen.set_style_bg_opa(255, 0)
    _screen = screen

    _build_top_bar(runtime, exit_flag)

    scr_w = screen.get_width()
    scr_h = screen.get_height()
    content_h = scr_h - BAR_H - PANEL_TOP_GAP - PANEL_BOTTOM_GAP
    panel_y = BAR_H + PANEL_TOP_GAP

    # ── 左栏（功能列表）──
    _left_panel = lv.obj(screen)
    _left_panel.set_size(LEFT_W, content_h)
    _left_panel.set_pos(0, panel_y)
    _left_panel.set_style_bg_color(lv.color_hex(0x1A1A1A), 0)
    _left_panel.set_style_bg_opa(255, 0)
    _left_panel.set_style_border_width(0, 0)
    _left_panel.set_style_pad_all(0, 0)
    _left_panel.set_style_radius(14, 0)
    _left_panel.set_style_clip_corner(True, 0)
    _left_panel.clear_flag(lv.obj.FLAG.SCROLLABLE)

    # ── 分界线（上下各留 DIVIDER_GAP）──
    _divider = lv.obj(screen)
    _divider.set_size(DIVIDER_W, content_h - DIVIDER_GAP * 2)
    _divider.set_pos(LEFT_W, panel_y + DIVIDER_GAP)
    _divider.set_style_bg_color(lv.color_hex(DIVIDER_COLOR), 0)
    _divider.set_style_bg_opa(255, 0)
    _divider.set_style_border_width(0, 0)
    _divider.set_style_pad_all(0, 0)
    _divider.set_style_radius(0, 0)
    _divider.clear_flag(lv.obj.FLAG.SCROLLABLE)
    _divider.clear_flag(lv.obj.FLAG.CLICKABLE)

    # ── 右栏（内容区，高度与左栏持平）──
    _right_panel = lv.obj(screen)
    _right_panel.set_size(RIGHT_W, content_h)
    _right_panel.set_pos(LEFT_W + DIVIDER_W, panel_y)
    _right_panel.set_style_bg_color(lv.color_hex(0x222222), 0)
    _right_panel.set_style_bg_opa(255, 0)
    _right_panel.set_style_border_width(0, 0)
    _right_panel.set_style_pad_all(CONTENT_PAD, 0)
    _right_panel.set_style_radius(14, 0)
    _right_panel.set_style_clip_corner(True, 0)
    _right_panel.clear_flag(lv.obj.FLAG.SCROLLABLE)

    # ── 构建左栏行 ──
    items = [
        ("language", "settings.tab_language"),
        ("about",    "settings.tab_about"),
    ]
    for i, (item_id, name_key) in enumerate(items):
        _build_left_row(runtime, i, item_id, name_key)

    # ── 渲染右栏默认内容 ──
    _render_right(runtime, _active_item)


def _build_left_row(runtime, index, item_id, name_key):
    """构建左栏单行：图标(最左) + 名称(图标右侧)，无箭头"""
    lang = runtime.lang
    y = index * ROW_H
    active = (item_id == _active_item)

    row = lv.btn(_left_panel)
    row.set_size(LEFT_W, ROW_H)
    row.set_pos(0, y)
    row.set_style_bg_color(
        lv.color_hex(0x2A2A2A if active else 0x1A1A1A), 0)
    row.set_style_bg_opa(255, 0)
    row.set_style_border_width(0, 0)
    row.set_style_shadow_width(0, 0)
    row.set_style_outline_width(0, 0)
    row.set_style_outline_opa(0, 0)
    row.set_style_radius(0, 0)
    row.set_style_pad_all(0, 0)

    # 图标（最左对齐 — 须补偿 set_zoom 后的居中偏移）
    icon_data, icon_dsc = icon_cache.get_settings_icon(item_id)
    if icon_dsc is not None and icon_data is not None:
        icon_img = lv.img(row)
        icon_img.set_src(icon_dsc)
        zoom = _png_zoom(icon_data, ICON_TARGET)
        icon_img.set_zoom(zoom)
        src_w = struct.unpack('>I', icon_data[16:20])[0]
        rendered_w = src_w * zoom // 256
        icon_x = ICON_PAD_LEFT - (src_w - rendered_w) // 2
        icon_img.align(lv.ALIGN.LEFT_MID, icon_x, 0)

    # 功能名（图标右侧）
    name_label = lv.label(row)
    name_label.set_text(lang.t(name_key))
    text_x = ICON_PAD_LEFT + ICON_TARGET + ICON_TEXT_GAP
    name_label.align(lv.ALIGN.LEFT_MID, text_x, 0)
    name_label.add_style(make_back_bar_text_style(fonts.body), 0)

    def _on_row(e, iid=item_id):
        if e.get_code() == lv.EVENT.CLICKED:
            _select_item(iid)
    row.add_event(_on_row, lv.EVENT.CLICKED, None)

    _rows.append((row, item_id))


def _select_item(item_id):
    """切换左栏选中项 + 刷新右栏"""
    global _active_item
    if item_id == _active_item:
        return
    _active_item = item_id

    for row, iid in _rows:
        active = (iid == item_id)
        row.set_style_bg_color(
            lv.color_hex(0x2A2A2A if active else 0x1A1A1A), 0)

    _right_panel.clean()
    _render_right(_ctx_runtime(), item_id)


def _render_right(runtime, item_id):
    if item_id == "language":
        _render_language(runtime)
    elif item_id == "about":
        _render_about(runtime)


def _render_language(runtime):
    lang = runtime.lang
    config = runtime.config
    container = _right_panel

    langs = [
        ("zh_CN", "settings.lang_zh"),
        ("en_US", "settings.lang_en"),
    ]
    for i, (code, key) in enumerate(langs):
        active = (lang.lang == code)
        row = lv.btn(container)
        row.set_size(lv.pct(100), LANG_ROW_H)
        row.align(lv.ALIGN.TOP_MID, 0, i * (LANG_ROW_H + 6))
        row.set_style_bg_color(
            lv.color_hex(0x2A5298 if active else 0x2A2A2A), 0)
        row.set_style_bg_opa(255, 0)
        row.set_style_radius(10, 0)
        row.set_style_border_width(0, 0)
        row.set_style_shadow_width(0, 0)

        lbl = lv.label(row)
        lbl.set_text(lang.t(key))
        lbl.align(lv.ALIGN.LEFT_MID, 12, 0)
        lbl.add_style(make_back_bar_text_style(fonts.body), 0)

        if active:
            cur_lbl = lv.label(row)
            cur_lbl.set_text(lang.t("settings.lang_current"))
            cur_lbl.align(lv.ALIGN.RIGHT_MID, -12, 0)
            cur_lbl.add_style(make_back_bar_text_style(fonts.body), 0)
            cur_lbl.set_style_text_color(
                lv.color_hex(Colors.ACCENT), 0)

        def _on_lang(e, c=code):
            if e.get_code() == lv.EVENT.CLICKED:
                _set_lang(c)
        row.add_event(_on_lang, lv.EVENT.CLICKED, None)


def _set_lang(code):
    """切换语言：switch + 持久化 + 通知 + 刷新"""
    runtime = _ctx_runtime()
    if runtime.lang.lang == code:
        return
    runtime.lang.switch(code)
    runtime.config.set('lang', code)
    runtime.config.save()
    event_bus.emit('lang_changed')
    _refresh_texts(runtime)
    _right_panel.clean()
    _render_right(runtime, _active_item)


def _render_about(runtime):
    lang = runtime.lang
    container = _right_panel

    rows = [
        ("settings.about_app_name", lang.t("settings.about_app_name_val")),
        ("settings.about_version",  lang.t("settings.about_version_val")),
        ("settings.about_platform", lang.t("settings.about_platform_val")),
        ("settings.about_display",  lang.t("settings.about_display_val")),
        ("settings.about_sdk",      lang.t("settings.about_sdk_val")),
        ("settings.about_author",   lang.t("settings.about_author_val")),
    ]
    for i, (key, val) in enumerate(rows):
        row = lv.obj(container)
        row.set_size(lv.pct(100), ABOUT_ROW_H)
        row.align(lv.ALIGN.TOP_MID, 0, i * (ABOUT_ROW_H + 4))
        row.set_style_bg_color(lv.color_hex(0x1A1A1A), 0)
        row.set_style_bg_opa(255, 0)
        row.set_style_border_width(0, 0)
        row.set_style_pad_hor(12, 0)
        row.set_style_pad_ver(0, 0)
        row.clear_flag(lv.obj.FLAG.SCROLLABLE)

        lbl_key = lv.label(row)
        lbl_key.set_text(lang.t(key))
        lbl_key.align(lv.ALIGN.LEFT_MID, 0, 0)
        lbl_key.add_style(make_back_bar_text_style(fonts.body), 0)
        lbl_key.set_style_text_color(lv.color_hex(Colors.TEXT_DIM), 0)

        lbl_val = lv.label(row)
        lbl_val.set_text(val)
        lbl_val.align(lv.ALIGN.RIGHT_MID, 0, 0)
        lbl_val.add_style(make_back_bar_text_style(fonts.body), 0)


def _refresh_texts(runtime):
    """语言切换后刷新左栏文字"""
    global _rows
    _left_panel.clean()
    _rows = []
    items = [
        ("language", "settings.tab_language"),
        ("about",    "settings.tab_about"),
    ]
    for i, (item_id, name_key) in enumerate(items):
        _build_left_row(runtime, i, item_id, name_key)


def _destroy_ui():
    """删顶栏/左右栏/分界线。不碰 runtime 硬件（main.py cleanup 负责）。"""
    global _screen, _top_bar, _left_panel, _right_panel, _divider, _rows
    for obj in (_top_bar, _left_panel, _right_panel, _divider):
        if obj is not None:
            try:
                obj.delete()
            except Exception:
                pass
    _top_bar = None
    _left_panel = None
    _right_panel = None
    _divider = None
    _rows = []
    _screen = None


def _ctx_runtime():
    """返回当前 run() 的 runtime（run 入口缓存到模块级）。

    _select_item/_set_lang 在 LVGL 回调中触发，拿不到 run() 的 runtime 参数，
    通过本函数取模块级缓存的 runtime。
    """
    return _RUNTIME


# run() 入口缓存的 runtime，供 LVGL 回调中的 _select_item/_set_lang 取用
_RUNTIME = None
