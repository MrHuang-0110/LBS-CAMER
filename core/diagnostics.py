# core/diagnostics.py — 板端死机排查插桩（纯 Python，host 可测）
#
# 2026-08-10 死机复现排查：face/object/gesture/body 四个 AI 脚本运行几分钟
# 后硬挂死（画面冻结+串口静默）+ K230 发烫，object_classify/color/tag 不死。
# 主循环按 fc % 300 打印一行诊断（fc/mem_free/温度/时间戳），用于区分
# H1 内存/MMZ 泄漏（mem_free 持续下降）vs H2 过热（temp 持续升高）。
# 温度 API machine.temperature() 是 v1.2.2+ 固件新增，单位未知按原值打印；
# 无 API/读取失败时打印 N/A，不影响主循环。

import gc
import time


def _mem_free():
    """gc.mem_free()（MicroPython API）。host/异常 → None。"""
    try:
        return gc.mem_free()
    except Exception:
        return None


def _ticks_ms():
    """time.ticks_ms()（MicroPython API）。host/异常 → None。"""
    try:
        return time.ticks_ms()
    except Exception:
        return None


def read_temperature():
    """读芯片温度（machine.temperature，v1.2.2+）。无 API/异常 → None。"""
    try:
        import machine
        fn = getattr(machine, "temperature", None)
        if fn is None:
            return None
        return fn()
    except Exception:
        return None


def diag_line(label, fc):
    """死机插桩一行：fc + mem_free + 温度 + ticks_ms。host 可测格式。"""
    mem = _mem_free()
    mem_s = "N/A" if mem is None else str(mem)
    temp = read_temperature()
    temp_s = "N/A" if temp is None else str(temp)
    ts = _ticks_ms()
    ts_s = "N/A" if ts is None else str(ts)
    return "[DIAG] %s fc=%d mem=%s temp=%s t=%s" % (
        label, fc, mem_s, temp_s, ts_s)
