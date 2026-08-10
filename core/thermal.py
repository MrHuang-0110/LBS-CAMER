# core/thermal.py — 温度保护分级（纯 Python，host 可测）
#
# 2026-08-10 死机排查结论：K230D BOX 散热差，持续 NPU 负载下温度随运行
# 时间稳定累积（实测 78°C → 100.7°C/29 分钟），100°C 附近硬挂死；降频只能
# 推迟不能根治。软件温度保护是防死机兜底：读 machine.temperature() 分级，
# 超阈值时强制放大检测间隔（降 NPU 负载）散热，把温度摁在安全区。
# 阈值取实测曲线经验：90°C 前系统稳定运行 20+ 分钟，故 92°C 起降频、
# 95°C 起强力冷却，88°C 恢复。

TH_TEMP_COOL = 88      # 低于此恢复自适应检测间隔
TH_TEMP_HOT = 92       # 高于此进入降频模式
TH_TEMP_CRITICAL = 95  # 高于此进入冷却模式（NPU 基本休息）


def thermal_mode(temp):
    """温度分级：0 正常 / 1 降频 / 2 冷却。temp 为 None（无温度 API）→ 0。"""
    if temp is None:
        return 0
    if temp >= TH_TEMP_CRITICAL:
        return 2
    if temp >= TH_TEMP_HOT:
        return 1
    return 0


def cooled_interval(base, mode):
    """温度模式下的检测间隔：正常=base，降频=max(base*2,12)，冷却=max(base*4,30)。"""
    if mode == 2:
        return max(base * 4, 30)
    if mode == 1:
        return max(base * 2, 12)
    return base
