# core/box_colors.py — 注册槽位画框颜色表(1~25) + 未注册白框
#
# 学习 ID 上限放开到 25 后,槽位画框颜色必须同步扩展:
#   - 1~4 槽保持历史颜色(绿/蓝/橙/紫,兼容既有板端习惯)
#   - 5~25 槽取 HSV 色环均匀 21 等分(S=0.95, V=0.85)
#   - 学习 ID 的框永远不用白色;白色(BOX_UNKNOWN)只用于未学习目标(id=0)
#
# 纯常量模块,无 MicroPython 依赖,host 可真单测。
# 供各注册类脚本与 core/face_ai.py / core/body_ai.py 共享(避免 8 处重复定义)。

BOX_UNKNOWN = 0xFFFFFF   # 未注册/未学习目标白框

BOX_COLORS = {
    1: 0x44CC44,   # 绿(历史)
    2: 0x4488FF,   # 蓝(历史)
    3: 0xFF8844,   # 橙(历史)
    4: 0xCC44FF,   # 紫(历史)
    5: 0xCE0000,
    6: 0xCE3B00,
    7: 0xCE7600,
    8: 0xCEB000,
    9: 0xB0CE00,
    10: 0x76CE00,
    11: 0x3BCE00,
    12: 0x00CE00,
    13: 0x00CE3B,
    14: 0x00CE76,
    15: 0x00CEB0,
    16: 0x00B0CE,
    17: 0x0076CE,
    18: 0x003BCE,
    19: 0x0000CE,
    20: 0x3B00CE,
    21: 0x7600CE,
    22: 0xB000CE,
    23: 0xCE00B0,
    24: 0xCE0076,
    25: 0xCE003B,
}


def box_color(slot):
    """按注册槽号取框色。未知槽(0 / 超界 / 未注册) → BOX_UNKNOWN 白框。"""
    return BOX_COLORS.get(slot, BOX_UNKNOWN)
