# core/thermal.py — 温度保护（纯 Python，host 可测）
#
# K230D BOX 散热差，持续负载下温度稳定累积（实测 78→100.7°C/29 分钟），
# 100°C 附近 NPU 硬挂死。历史方案曾放大检测间隔冷却（92/95°C 分级 +
# cooled_interval），副作用锁框极迟钝（2026-08-12 去保护）。
#
# 2026-08-13 根治（DVFS）：CPU/KPU 调频降发热、不拉长检测间隔（锁框跟手）。
# 板端实证：die 温度读数 = CPU 活跃主导（NPU 只占小头），且 kpu.run 是 CPU
# 同步等待——KPU 降太狠反更热（忙等延长）。故策略 CPU 强降（f·V²）、KPU
# mode 1 保持满频 / mode 2 才降，档位见 DVFS_KPU_DOWN / DVFS_CPU_DOWN。
# thermal_mode 分级保留（阈值不变），cooled_interval 仅兼容/测试保留。

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
    """温度模式下的检测间隔：正常=base，降频=max(base*2,12)，冷却=max(base*4,30)。

    仅作兼容/测试保留；DVFS 方案不再调用（见 ThermalGuard）。
    """
    if mode == 2:
        return max(base * 4, 30)
    if mode == 1:
        return max(base * 2, 12)
    return base


# ---- DVFS 降频策略（2026-08-13 根治） ----

from core.dvfs import Dvfs  # noqa: E402

# 温度模式 → 降档数。检测频率始终不变（锁框跟手）。
#
# 2026-08-13 二轮修正（板端日志实证 object_detect）：KPU 降太狠反而更热——
# kpu.run 是 CPU 同步等待，KPU 0.8G→0.2G 使检测帧 84ms→117ms，CPU 忙等时间
# 翻倍；而 die 温度读数是 CPU 活跃主导（项目记录 2026-08-11 收官结论），
# NPU 只占小头。
#
# 2026-08-13 三轮（KPU 满频策略）：mode 1(92°C 起) KPU **保持满频** 只降 CPU
# ——KPU 满频检测帧最短(84ms)、CPU 忙等最短；CPU 0.8G(f·V²) 已足以压温
# (二轮实测 95°C 稳态即 CPU 0.8G + KPU 0.4G；KPU 回满频 NPU 功耗虽 ×2 但
# NPU 非温度主导，忙等缩短净效果待板端实测)。mode 2(≥95°C) 才降 KPU 极限兜底。
#   mode 1: KPU 满频(0.8G) + CPU 2 档(0.8G)
#   mode 2: KPU 2 档(0.2G) + CPU 3 档(0.594G)
DVFS_KPU_DOWN = {0: 0, 1: 0, 2: 2}
DVFS_CPU_DOWN = {0: 0, 1: 2, 2: 3}


def kpu_profile_for_mode(mode, n_profiles):
    """温度模式 → KPU 频点 index（0 满频 / 1 降一档 / 2 降两档），截断到 n_profiles-1。"""
    if n_profiles <= 1:
        return 0
    return min(DVFS_KPU_DOWN.get(mode, 0), n_profiles - 1)


def cpu_profile_for_mode(mode, n_profiles):
    """温度模式 → CPU 频点 index（仅 mode 2 降一档），截断到 n_profiles-1。"""
    if n_profiles <= 1:
        return 0
    return min(DVFS_CPU_DOWN.get(mode, 0), n_profiles - 1)


class ThermalGuard:
    """DVFS 温度保护：按温度分级调 CPU/KPU 频点，不拉长检测间隔（锁框跟手）。

    板端每 N 帧 tick 一次（温度寄存器读廉价）。温度阈值沿用 thermal_mode
    （≥92 降频 / ≥95 强冷却 / <88 恢复）；滞回由 92 上、88 下的阈值差天然
    提供（92 才升档、<92 即回落），无需额外状态机。
    仅在目标频点变化时才调 set_profile（避免每帧无谓的 PM 调用）。
    """

    def __init__(self, dvfs=None):
        self._dvfs = dvfs if dvfs is not None else Dvfs()
        self._last_kpu = None
        self._last_cpu = None

    def tick(self, temp):
        mode = thermal_mode(temp)
        kpu = kpu_profile_for_mode(mode, len(self._dvfs.kpu_profiles()))
        cpu = cpu_profile_for_mode(mode, len(self._dvfs.cpu_profiles()))
        if kpu != self._last_kpu:
            self._dvfs.set_kpu(kpu)
            self._last_kpu = kpu
        if cpu != self._last_cpu:
            self._dvfs.set_cpu(cpu)
            self._last_cpu = cpu
        return mode

    @property
    def kpu_index(self):
        return self._last_kpu

    @property
    def cpu_index(self):
        return self._last_cpu
