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
# L 范围 0-100,A/B 范围 -128~127
L_LO, L_HI = 0, 100
AB_LO, AB_HI = -128, 127

# 画框配色对齐 tag_detect:未注册白框,注册按 slot 取彩色。
BOX_COLORS = {
    1: 0x44CC44,   # 绿
    2: 0x4488FF,   # 蓝
    3: 0xFF8844,   # 橙
    4: 0xCC44FF,   # 紫
}
BOX_UNKNOWN = 0xFFFFFF   # 未注册白框

# 6 阈值格定义:(key, label_key, lo, hi, default)
THRESH_CELLS = [
    ("Lmin", "color_detect.Lmin", 0, 100, 0),
    ("Lmax", "color_detect.Lmax", 0, 100, 100),
    ("Amin", "color_detect.Amin", -128, 127, -10),
    ("Amax", "color_detect.Amax", -128, 127, 10),
    ("Bmin", "color_detect.Bmin", -128, 127, -10),
    ("Bmax", "color_detect.Bmax", -128, 127, 10),
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
