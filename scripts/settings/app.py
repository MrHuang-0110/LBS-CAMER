# scripts/settings/app.py — 设置页（左右分栏布局）
#
# 左栏 35%：功能列表（图标 + 名称），默认选中第一项
# 右栏 65%：内容区（语言切换 / 关于信息）
# ui_mode = "page"，LVGL 全程管理，无相机参与。
#
# ⚠️ K230 约束：on_enter() 内不得做文件 I/O（open/read），
# 会与 task_handler 的显示 flush 抢资源导致死锁。
# 图标数据由 core/icon_cache 在启动阶段预读到内存，此处只读缓存。

import struct
import lvgl as lv
from scripts._base import BaseScript
from core.event_bus import event_bus
from core.icon_cache import icon_cache
from ui.theme import Colors, make_back_bar_text_style
from core.font_manager import fonts


# ── 布局参数 ──────────────────────────────────────
BAR_H = 52          # 返回栏高度（与 BackBar.HEIGHT 对齐）
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


class SettingsApp(BaseScript):
    SCRIPT_ID = "settings"

    def __init__(self):
        super().__init__()
        self._screen = None
        self._left_panel = None
        self._right_panel = None
        self._divider = None
        self._rows = []        # [(btn_obj, item_id), ...]
        self._active_item = "language"

    # ── 生命周期 ──────────────────────────────────────

    def on_enter(self, ctx):
        super().on_enter(ctx)
        self._build_ui()

    def on_exit(self):
        self._destroy_ui()
        super().on_exit()

    # ── UI 构建 ──────────────────────────────────────

    def _build_ui(self):
        ctx = self.ctx
        lang = ctx.lang

        screen = lv.scr_act()
        screen.set_style_bg_color(lv.color_hex(Colors.BG), 0)
        screen.set_style_bg_opa(255, 0)
        self._screen = screen

        scr_w = screen.get_width()
        scr_h = screen.get_height()
        content_h = scr_h - BAR_H - PANEL_TOP_GAP - PANEL_BOTTOM_GAP
        panel_y = BAR_H + PANEL_TOP_GAP

        # ── 左栏（功能列表）──
        self._left_panel = lv.obj(screen)
        self._left_panel.set_size(LEFT_W, content_h)
        self._left_panel.set_pos(0, panel_y)
        self._left_panel.set_style_bg_color(lv.color_hex(0x1A1A1A), 0)
        self._left_panel.set_style_bg_opa(255, 0)
        self._left_panel.set_style_border_width(0, 0)
        self._left_panel.set_style_pad_all(0, 0)
        self._left_panel.set_style_radius(14, 0)
        self._left_panel.set_style_clip_corner(True, 0)  # 裁剪子对象到圆角内
        self._left_panel.clear_flag(lv.obj.FLAG.SCROLLABLE)

        # ── 分界线（上下各留 DIVIDER_GAP）──
        self._divider = lv.obj(screen)
        self._divider.set_size(DIVIDER_W, content_h - DIVIDER_GAP * 2)
        self._divider.set_pos(LEFT_W, panel_y + DIVIDER_GAP)
        self._divider.set_style_bg_color(lv.color_hex(DIVIDER_COLOR), 0)
        self._divider.set_style_bg_opa(255, 0)
        self._divider.set_style_border_width(0, 0)
        self._divider.set_style_pad_all(0, 0)
        self._divider.set_style_radius(0, 0)
        self._divider.clear_flag(lv.obj.FLAG.SCROLLABLE)
        self._divider.clear_flag(lv.obj.FLAG.CLICKABLE)

        # ── 右栏（内容区，高度与左栏持平）──
        self._right_panel = lv.obj(screen)
        self._right_panel.set_size(RIGHT_W, content_h)
        self._right_panel.set_pos(LEFT_W + DIVIDER_W, panel_y)
        self._right_panel.set_style_bg_color(lv.color_hex(0x222222), 0)
        self._right_panel.set_style_bg_opa(255, 0)
        self._right_panel.set_style_border_width(0, 0)
        self._right_panel.set_style_pad_all(CONTENT_PAD, 0)
        self._right_panel.set_style_radius(14, 0)
        self._right_panel.set_style_clip_corner(True, 0)
        self._right_panel.clear_flag(lv.obj.FLAG.SCROLLABLE)

        # ── 构建左栏行 ──
        items = [
            ("language", "settings.tab_language"),
            ("about",    "settings.tab_about"),
        ]
        for i, (item_id, name_key) in enumerate(items):
            self._build_left_row(i, item_id, name_key)

        # ── 渲染右栏默认内容 ──
        self._render_right(self._active_item)

    def _build_left_row(self, index, item_id, name_key):
        """构建左栏单行：图标(最左) + 名称(图标右侧)，无箭头"""
        lang = self.ctx.lang
        y = index * ROW_H
        active = (item_id == self._active_item)

        row = lv.btn(self._left_panel)
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
            # K230: set_zoom 只缩放像素，不改变 lv.img 布局尺寸(128×128)。
            # 缩放后图标在 img 对象内**居中**，因此需左偏移让图标贴边：
            #   rendered_w = src_w * zoom / 256
            #   padding_left = (src_w - rendered_w) / 2
            #   icon_x = ICON_PAD_LEFT - padding_left
            src_w = struct.unpack('>I', icon_data[16:20])[0]
            rendered_w = src_w * zoom // 256
            icon_x = ICON_PAD_LEFT - (src_w - rendered_w) // 2
            icon_img.align(lv.ALIGN.LEFT_MID, icon_x, 0)

        # 功能名（图标右侧）
        name_label = lv.label(row)
        name_label.set_text(lang.t(name_key))
        text_x = ICON_PAD_LEFT + ICON_TARGET + ICON_TEXT_GAP
        name_label.align(lv.ALIGN.LEFT_MID, text_x, 0)
        name_style = make_back_bar_text_style(fonts.body)
        name_label.add_style(name_style, 0)

        def _on_row(e, iid=item_id):
            if e.get_code() == lv.EVENT.CLICKED:
                self._select_item(iid)
        row.add_event(_on_row, lv.EVENT.CLICKED, None)

        self._rows.append((row, item_id))

    def _select_item(self, item_id):
        """切换左栏选中项 + 刷新右栏"""
        if item_id == self._active_item:
            return
        self._active_item = item_id

        for row, iid in self._rows:
            active = (iid == item_id)
            row.set_style_bg_color(
                lv.color_hex(0x2A2A2A if active else 0x1A1A1A), 0)

        self._right_panel.clean()
        self._render_right(item_id)

    def _render_right(self, item_id):
        if item_id == "language":
            self._render_language()
        elif item_id == "about":
            self._render_about()

    # ── 语言内容 ──────────────────────────────────────

    def _render_language(self):
        ctx = self.ctx
        lang = ctx.lang
        container = self._right_panel

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
                    self._set_lang(c)
            row.add_event(_on_lang, lv.EVENT.CLICKED, None)

    def _set_lang(self, code):
        ctx = self.ctx
        if ctx.lang.lang == code:
            return
        ctx.lang.switch(code)
        ctx.config.set('lang', code)
        ctx.config.save()
        event_bus.emit('lang_changed')
        self._refresh_texts()
        self._right_panel.clean()
        self._render_right(self._active_item)

    # ── 关于内容 ──────────────────────────────────────

    def _render_about(self):
        ctx = self.ctx
        lang = ctx.lang
        container = self._right_panel

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

    # ── 文字刷新（语言切换后）──────────────────────────

    def _refresh_texts(self):
        self._left_panel.clean()
        self._rows = []
        items = [
            ("language", "settings.tab_language"),
            ("about",    "settings.tab_about"),
        ]
        for i, (item_id, name_key) in enumerate(items):
            self._build_left_row(i, item_id, name_key)

    # ── 销毁 ──────────────────────────────────────

    def _destroy_ui(self):
        for obj in (self._left_panel, self._right_panel, self._divider):
            if obj is not None:
                try:
                    obj.delete()
                except Exception:
                    pass
        self._left_panel = None
        self._right_panel = None
        self._divider = None
        self._rows = []
        self._screen = None
