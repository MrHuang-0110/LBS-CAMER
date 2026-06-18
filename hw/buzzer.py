# hw/buzzer.py — 无源蜂鸣器驱动（PWM0 Pin60 4kHz）
# 移植自 demo/基础实验例程/实验1 跑马灯实验/123/main.py Buzzer 类
# 硬件规格参考 demo/基础实验例程/实验2 蜂鸣器实验/main.py

from machine import PWM
import time


class Buzzer:
    """无源蜂鸣器，PWM 4kHz 驱动"""

    # 默认：Pin60 → PWM0, 4kHz
    FREQ_DEFAULT = 4000

    def __init__(self, fpioa, pinx=60, pwm_ch=0, valid=0):
        """
        Args:
            fpioa: FPIOA 实例
            pinx: 引脚号（默认 60）
            pwm_ch: PWM 通道号（默认 0）
            valid: 有效电平；0=低有效, 1=高有效（默认 0）
        """
        self.valid = valid
        self.pwm_ch = pwm_ch
        self._enabled = True

        duty = 100 if valid == 0 else 0
        fpioa.set_function(pinx, fpioa.PWM0 + pwm_ch)
        self.pwm = PWM(pwm_ch, self.FREQ_DEFAULT, duty, enable=True)
        self.off()  # 初始静音

    # ── 基本控制 ──────────────────────────────────────

    def on(self):
        """持续鸣响（注意：会阻塞，仅调试用）"""
        if not self._enabled:
            return
        # 50% duty 产生 4kHz 方波驱动无源蜂鸣器（参考 demo 实验2）
        self.pwm.duty(50)

    def off(self):
        """停止鸣响"""
        self.pwm.duty(100 if self.valid == 0 else 0)

    # ── 短促提示音 ────────────────────────────────────

    def beep(self, ms=50):
        """短鸣一声（阻塞指定毫秒）

        Args:
            ms: 持续时长（毫秒）。场景值：
                - 80ms  开机提示
                - 50ms  卡片点击启动 / 返回主菜单
                - 30ms  吸附选中新卡 / 设置项切换
        """
        if not self._enabled:
            return
        self.on()
        time.sleep_ms(ms)
        self.off()

    # ── 可选项 ────────────────────────────────────────

    def set_enabled(self, enabled):
        """全局开关蜂鸣器（设置页可关闭触摸音效）"""
        self._enabled = enabled

    @property
    def enabled(self):
        return self._enabled

    def set_loudness(self, loudness):
        """设置响度（0–100）"""
        loudness = max(0, min(100, loudness))
        self.pwm.duty(
            100 - loudness if self.valid == 0 else loudness
        )

    def set_frequency(self, freq):
        """修改震荡频率（默认 4kHz）"""
        self.pwm.freq(freq)
