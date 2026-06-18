# core/id_registry.py — 可复用注册控制器
#
# K2 = GPIO0 输入（fpioa.set_function(0, FPIOA.GPIO0)）。
# 主线程软件边沿检测：按下瞬间置 pending=True（只一次，防按住连触发）。
# AI 线程每帧调 try_register(feature)：pending(2秒内) → face_db.register
# + 蜂鸣 + 清 pending。
#
# 不依赖具体 AI 模型：脚本自己提 feature 传入。后续手势/物体脚本复用：
# 只需自己提特征 → 调 id_registry.try_register，K2 轮询/slot/写盘零重写。

import time
from machine import Pin, FPIOA


class IdRegistry:
    """可复用注册控制器：K2 按键 + face_db.register 协作。

    主线程：poll_k2() 边沿检测，按下置 pending（2秒超时）。
    AI 线程：try_register(feature, buzzer) 消费 pending，调 face_db.register。

    不绑定任何 AI 模型——调用方自己提特征传入。
    """

    def __init__(self, fpioa, pin=0, valid_level=0):
        """valid_level：按下时电平（默认 0=低电平有效，K230D BOX K2 上拉+按下接地）。"""
        fpioa.set_function(pin, FPIOA.GPIO0 + pin)
        self._k2 = Pin(pin, Pin.IN, Pin.PULL_UP)
        self._valid_level = valid_level
        self._prev_pressed = False
        self._pending = False
        self._pending_time = 0
        self._last_slot = None

    def poll_k2(self):
        """主线程 task_handler 间隙调。软件边沿：松开→按下 瞬间置 pending。
        只触发一次，按住不松不重复置 pending。"""
        pressed = (self._k2.value() == self._valid_level)
        if pressed and not self._prev_pressed:
            self._pending = True
            self._pending_time = time.ticks_ms()
        self._prev_pressed = pressed

    def try_register(self, feature, buzzer=None):
        """AI 线程每帧调。pending(2秒内) → face_db.register + 蜂鸣 + 清 pending。
        返回 slot_id(1-4) 或 None（没按/超时/失败）。

        feature：512维 ndarray（由脚本 face_reg.run 提取，不重复 NPU 推理）。
        buzzer：Buzzer 实例或 None（无 buzzer 时静默，守卫安全）。
        """
        if not self._pending:
            return None
        # 2 秒超时：防"按了→走开→别人来→误注册"
        if time.ticks_diff(time.ticks_ms(), self._pending_time) > 2000:
            self._pending = False
            print("[IdRegistry] pending timeout, discarded")
            return None
        self._pending = False
        try:
            from core.face_db import face_db
            slot = face_db.register(feature)
            self._last_slot = slot
            if buzzer is not None:
                buzzer.beep(ms=80)
            return slot
        except Exception as e:
            print("[IdRegistry] register failed: %s" % e)
            if buzzer is not None:
                buzzer.beep(ms=200)
            return None

    @property
    def last_slot(self):
        """上次注册分配的 slot_id(1-4)，供 UI 反馈。None=从未注册。"""
        return self._last_slot
