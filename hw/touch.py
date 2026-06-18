# hw/touch.py — 触摸屏驱动（TOUCH(0) + LVGL indev）
# 移植自 demo/基础实验例程/实验1 跑马灯实验/123/main.py
#
# 安全初始化：TOUCH(0) 在部分硬件上可能因 I2C 不响应而阻塞，
# 将硬件初始化延迟到 hw_init()，构造时不访问硬件。

from machine import TOUCH
import lvgl as lv


class Touch:
    """电容触摸屏驱动，对接 LVGL 输入设备"""

    def __init__(self, index=0):
        self.index = index
        self.touch = None  # 延迟初始化
        self.indev = None
        self._hw_ready = False

    def __del__(self):
        if self.touch is not None:
            del self.touch

    # ── 硬件初始化（可能阻塞，须显式调用）──────────────

    def hw_init(self):
        """初始化触摸硬件（可能阻塞，调用前确保 I2C 就绪）"""
        if self._hw_ready:
            return True
        try:
            self.touch = TOUCH(self.index)
            self._hw_ready = True
            return True
        except Exception as e:
            print(f"[Touch] hw_init failed: {e}")
            return False

    # ── LVGL 集成 ──────────────────────────────────────

    def lvgl_read_cb(self, indev, data):
        """LVGL indev 读取回调"""
        x, y, state = 0, 0, lv.INDEV_STATE.RELEASED

        if self._hw_ready and self.touch is not None:
            try:
                tp = self.touch.read(1)
                if len(tp):
                    x, y, event = tp[0].x, tp[0].y, tp[0].event
                    if event in (TOUCH.EVENT_DOWN, TOUCH.EVENT_MOVE):
                        state = lv.INDEV_STATE.PRESSED
            except Exception:
                pass

        data.point = lv.point_t({'x': x, 'y': y})
        data.state = state

    def lvgl_init(self):
        """注册 LVGL 输入设备（触摸硬件未就绪也可注册）"""
        self.indev = lv.indev_create()
        self.indev.set_type(lv.INDEV_TYPE.POINTER)
        self.indev.set_read_cb(self.lvgl_read_cb)

    def lvgl_deinit(self):
        """移除 LVGL 输入设备"""
        if self.indev is not None:
            del self.indev
            self.indev = None
