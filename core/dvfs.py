# core/dvfs.py — CPU/KPU DVFS 降频封装（纯 Python，host 可测）
#
# 2026-08-13 高温死机根治（探测实证 tools/pm_thermal_probe.py，固件 v1.3）：
# K230D CPU1/KPU 支持 DVFS，MicroPython 经 `from mpp import pm` 暴露。
# 板端只读探测实测频点：
#   CPU: 1.6G@0.8V / 1.188G@0.7V / 0.8G@0.68V / 0.594G@0.66V / 0.4G@0.64V / 0.2G@0.62V
#   KPU: 0.8G@0.8V / 0.4G@0.8V / 0.2G@0.8V / 0.1G@0.8V（电压固定 0.8V）
#
# 关键结论：KPU 各频点电压恒定 0.8V → 动态功耗 ∝ 频率，降频线性降 NPU 发热；
# CPU 各频点频率+电压同降 → 功耗 ∝ f·V²，降频对 CPU 活跃主导的 die 温度更显著。
# （温度读数反映 CPU 活跃率而非 NPU 负载，见 项目记录.md 2026-08-11 收官结论）
#
# 本模块是 pm 的唯一封装入口：业务/测试只经此读写，便于只读契约与 host mock。
# 板端 `from mpp import pm` 失败时（host / 旧固件）静默降级为 no-op（available=False）。

try:
    from mpp import pm as _pm
except Exception:
    _pm = None

# 探测实测默认频点表（host 测试基准 + 板端读不到 list_profiles 时的 fallback）。
CPU_PROFILES = [
    [1600000000, 800000], [1188000000, 700000], [800000000, 680000],
    [594000000, 660000], [400000000, 640000], [200000000, 620000],
]
KPU_PROFILES = [
    [800000000, 800000], [400000000, 800000],
    [200000000, 800000], [100000000, 800000],
]


class Dvfs:
    """CPU/KPU DVFS 读写封装。板端用真实 pm；host 测试注入 mock 或 pm=None。"""

    def __init__(self, pm_module=_pm):
        self._pm = pm_module
        self._cpu_profiles = self._read_profiles("cpu", CPU_PROFILES)
        self._kpu_profiles = self._read_profiles("kpu", KPU_PROFILES)
        self._cpu_idx = None
        self._kpu_idx = None

    @property
    def available(self):
        return self._pm is not None

    @property
    def cpu_index(self):
        return self._cpu_idx

    @property
    def kpu_index(self):
        return self._kpu_idx

    def _domain(self, dom):
        if self._pm is None:
            return None
        return getattr(self._pm, dom, None)

    def _read_profiles(self, dom, fallback):
        d = self._domain(dom)
        if d is None:
            return list(fallback)
        try:
            profs = d.list_profiles()
        except Exception:
            return list(fallback)
        if not profs:
            return list(fallback)
        return [list(p) for p in profs]

    def cpu_profiles(self):
        return [list(p) for p in self._cpu_profiles]

    def kpu_profiles(self):
        return [list(p) for p in self._kpu_profiles]

    def set_cpu(self, idx):
        d = self._domain("cpu")
        if d is None or idx < 0 or idx >= len(self._cpu_profiles):
            return False
        try:
            d.set_profile(idx)
        except Exception:
            return False
        self._cpu_idx = idx
        return True

    def set_kpu(self, idx):
        d = self._domain("kpu")
        if d is None or idx < 0 or idx >= len(self._kpu_profiles):
            return False
        try:
            d.set_profile(idx)
        except Exception:
            return False
        self._kpu_idx = idx
        return True

    def get_cpu_freq(self):
        d = self._domain("cpu")
        if d is None:
            return None
        try:
            return d.get_freq()
        except Exception:
            return None

    def get_kpu_freq(self):
        d = self._domain("kpu")
        if d is None:
            return None
        try:
            return d.get_freq()
        except Exception:
            return None
