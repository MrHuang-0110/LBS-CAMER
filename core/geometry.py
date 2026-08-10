# core/geometry.py — 绘制坐标收边辅助（纯 Python，host 可直接测试）
#
# K230 的 draw_rectangle 是 (x,y,w,h) 语义而非对角坐标，且宽高越界 1px
# 会偶发触发驱动硬挂死（画面冻结+串口静默，见 项目记录.md 2026-08-07
# object_classify 节：w=640 时右边界超 OSD1 0..639）。所有画框路径统一经
# clamp_rect 把矩形收进可视区 [0..max_w-1]×[0..max_h-1]，贴边检测框
# （blob/apriltag/模型输出经缩放）不会画出右/下边界。

def clamp_rect(x, y, w, h, max_w, max_h):
    """把 (x,y,w,h) 收进可视区，保证 x+w <= max_w-1 且 y+h <= max_h-1。

    先夹起点到 [0, max-1]，再收宽高满足边界约束；宽高最小 1（不产生空框）。
    输入可为 float，统一 int 截断返回。安全条件与坑清单"宽高各减 1"一致。
    """
    x = max(0, min(int(x), max_w - 1))
    y = max(0, min(int(y), max_h - 1))
    w = max(1, min(int(w), max_w - 1 - x))
    h = max(1, min(int(h), max_h - 1 - y))
    return x, y, w, h
