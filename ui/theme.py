# ui/theme.py — 深灰主题色板与 LVGL 样式
#
# 色板规范（项目计划 §3.2, v0.13）：
#   背景  #000000    全屏底色（纯黑）
#   卡片面 #222222   所有卡片统一深灰（选中/未选中同色）
#   强调   #4A9EFF   保留色值，卡片不再使用边框
#   主文字 #FFFFFF   类目功能名称（纯白）

import lvgl as lv


# ── 色板常量 ──────────────────────────────────────────

class Colors:
    BG          = 0x000000   # 全屏背景（纯黑）
    CARD        = 0x222222   # 卡片面（统一深灰）
    CARD_SEL    = 0x222222   # 卡片面（选中，与未选中同色）
    ACCENT      = 0x4A9EFF   # 强调/描边
    GLOW        = 0x6DB8FF   # 淡蓝发光边框（选中卡片）
    TEXT        = 0xFFFFFF   # 主文字（纯白）
    TEXT_DIM    = 0x9E9E9E   # 辅助文字
    WHITE       = 0xFFFFFF
    BLACK       = 0x000000
    SEPARATOR   = 0x3A3A3A   # 分隔线


# ── LVGL 样式工厂 ─────────────────────────────────────

def make_bg_style():
    """全屏背景样式"""
    style = lv.style_t()
    style.init()
    style.set_bg_color(lv.color_hex(Colors.BG))
    style.set_bg_opa(255)
    style.set_border_width(0)
    style.set_pad_all(0)
    return style


def make_card_style():
    """卡片样式 — 深灰底、圆角 14px、无边框、无内阴影"""
    style = lv.style_t()
    style.init()
    style.set_bg_color(lv.color_hex(Colors.CARD))
    style.set_bg_opa(255)
    style.set_radius(14)
    style.set_border_width(0)
    style.set_pad_hor(6)
    style.set_pad_ver(0)
    return style


def make_card_icon_style():
    """卡片图标区域样式（72×72）"""
    style = lv.style_t()
    style.init()
    style.set_bg_opa(0)
    style.set_border_width(0)
    style.set_pad_all(0)
    return style


def make_card_text_style(font):
    """卡片文字样式"""
    style = lv.style_t()
    style.init()
    style.set_text_font(font)
    style.set_text_color(lv.color_hex(Colors.TEXT))
    return style


def make_back_bar_style():
    """统一返回栏样式 — 40px 高，深灰半透明"""
    style = lv.style_t()
    style.init()
    style.set_bg_color(lv.color_hex(Colors.CARD))
    style.set_bg_opa(217)  # ~85%
    style.set_border_width(0)
    style.set_pad_all(0)
    style.set_radius(0)
    return style


def make_back_bar_text_style(font):
    """返回栏文字样式 — 清除 LVGL 默认主题的描边/阴影,避免黑底"""
    style = lv.style_t()
    style.init()
    # 字体加载暂时移除：font 可能为 None,跳过设置(用 LVGL 内置字体)。
    if font is not None:
        style.set_text_font(font)
    style.set_text_color(lv.color_hex(Colors.TEXT))
    # 清除 LVGL v8 默认主题给 label 加的文字描边(text_outline)/阴影(shadow),
    # 否则在深色背景上字形周围出现黑底(见 app.py timer_label 修复及注释)。
    style.set_bg_opa(0)
    style.set_border_width(0)
    style.set_pad_all(0)
    try:
        style.set_shadow_width(0)
        style.set_shadow_opa(0)
    except Exception:
        pass
    try:
        style.set_text_outline_width(0)
        style.set_text_outline_opa(0)
    except Exception:
        pass
    return style
