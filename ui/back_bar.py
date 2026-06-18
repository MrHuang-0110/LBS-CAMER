# ui/back_bar.py — 统一返回栏（AppBar）
#
# 高 52px，深灰底，固定屏幕顶部。
# 左：返回按钮（48×48 图片图标，从 icon_cache 读取，零文件 I/O）
# 中：脚本标题（居中，body 字体）
# 右：状态区（40px，可选）

import lvgl as lv
from ui.theme import Colors, make_back_bar_style, make_back_bar_text_style
from core.font_manager import fonts
from core.icon_cache import icon_cache


class BackBar:
    """统一返回栏 — 由 ScriptRunner 自动挂载到所有脚本"""

    HEIGHT = 52
    BTN_SIZE = 48
    STATUS_WIDTH = 40

    def __init__(self, title, on_back=None, icon_path=None):
        self.title = title
        self.on_back = on_back
        self._bar = None
        self._btn = None
        self._label = None

    def show(self):
        if self._bar is not None:
            return

        screen = lv.scr_act()

        # ── 背景条 ──
        self._bar = lv.obj(screen)
        self._bar.set_size(lv.pct(100), self.HEIGHT)
        self._bar.align(lv.ALIGN.TOP_MID, 0, 0)
        bar_style = make_back_bar_style()
        self._bar.add_style(bar_style, 0)
        self._bar.clear_flag(lv.obj.FLAG.SCROLLABLE)
        self._bar.move_foreground()

        # ── 返回按钮（48×48 透明点击区 + 图片图标）──
        self._btn = lv.obj(self._bar)
        self._btn.set_size(self.BTN_SIZE, self.BTN_SIZE)
        self._btn.align(lv.ALIGN.LEFT_MID, 2, 0)
        # 完全透明：清除所有可见属性
        self._btn.set_style_bg_opa(0, 0)
        self._btn.set_style_border_width(0, 0)
        self._btn.set_style_border_opa(0, 0)
        self._btn.set_style_shadow_width(0, 0)
        self._btn.set_style_shadow_opa(0, 0)
        self._btn.set_style_outline_width(0, 0)
        self._btn.set_style_outline_opa(0, 0)
        self._btn.set_style_pad_all(0, 0)
        self._btn.clear_flag(lv.obj.FLAG.SCROLLABLE)
        self._btn.add_flag(lv.obj.FLAG.CLICKABLE)

        # 图标从 icon_cache 取（启动阶段已预读），零文件 I/O
        icon_data, icon_dsc = icon_cache.get_back_icon()
        if icon_dsc is not None and icon_data is not None:
            # 解析 PNG 真实尺寸算 zoom（back.png 是 64×64，目标填满 48px 区域）
            import struct
            w = h = 64
            if len(icon_data) >= 24:
                w = struct.unpack('>I', icon_data[16:20])[0]
                h = struct.unpack('>I', icon_data[20:24])[0]
            target = int(self.BTN_SIZE * 0.85)  # 留 15% 内边距
            zoom = int(min(target / w, target / h) * 256) if w > 0 and h > 0 else 256
            zoom = max(8, min(zoom, 256))
            icon_img = lv.img(self._btn)
            icon_img.set_src(icon_dsc)
            icon_img.set_zoom(zoom)
            icon_img.center()
        else:
            # 回退：ASCII "<"
            btn_label = lv.label(self._btn)
            btn_text_style = make_back_bar_text_style(fonts.body)
            btn_label.add_style(btn_text_style, 0)
            btn_label.set_text("<")
            btn_label.center()

        if self.on_back is not None:
            self._btn.add_event(
                lambda e: self.on_back() if e.get_code() == lv.EVENT.CLICKED else None,
                lv.EVENT.CLICKED, None,
            )

        # ── 标题（居中）──
        self._label = lv.label(self._bar)
        self._label.set_text(self.title)
        self._label.align(lv.ALIGN.CENTER, 0, 0)
        title_style = make_back_bar_text_style(fonts.body)
        self._label.add_style(title_style, 0)

        # ── 状态区（右）──
        status = lv.obj(self._bar)
        status.set_size(self.STATUS_WIDTH, self.HEIGHT)
        status.align(lv.ALIGN.RIGHT_MID, 0, 0)
        status.set_style_bg_opa(0, 0)
        status.set_style_border_width(0, 0)
        status.clear_flag(lv.obj.FLAG.SCROLLABLE)

    def set_title(self, title):
        self.title = title
        if self._label is not None:
            self._label.set_text(title)

    def hide(self):
        if self._bar is not None:
            try:
                self._bar.delete()
            except Exception:
                pass
            self._bar = None
            self._btn = None
            self._label = None
