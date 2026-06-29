# main_menu_durui_probe.py — DurUI 显示栈最小复刻对照实验
#
# 目的:用 DurUI 原样的显示栈,跑我们现有 ui.main_menu.MainMenu 卡片 UI,
# 看主动 GC 后是否还卡。二分定位:
#   不卡 → 显示栈是元凶(app_runtime.init_menu 显示链路)
#   仍卡 → MainMenu UI 本身是元凶,与显示栈无关
#
# 不配 sensor、不申请 OSD2、不走 reset 框架、不动各识别脚本。
# 仅用于板端实验,跑完还原 /sdcard/main.py。

import gc
import os
import time

import lvgl as lv
import uctypes

from media.display import Display
from media.media import MediaManager
import image

from machine import FPIOA
from machine import Pin
from machine import PWM
from machine import TOUCH


DISPLAY_WIDTH = 640
DISPLAY_HEIGHT = 480
MENU_LV_TASK_SLEEP_MS = 3  # 与 DurUI 主循环一致

# 蜂鸣器:Pin60 → PWM0(对齐 DurUI)
BUZZER_PIN = 60
BUZZER_PWM_CH = 0
BUZZER_FREQ_HZ = 4000
BUZZER_DUTY_ON = 50
BUZZER_DUTY_OFF = 100
_buzzer_pwm = None


class LCD:
    """逐行照搬 DurUI.LCD:Display.ST7701 + MediaManager + 背光 + DIRECT 双缓冲。"""

    def __init__(self, width=DISPLAY_WIDTH, height=DISPLAY_HEIGHT, to_ide=False, fpioa=None, bl_pinx=5, bl_valid=1):
        self._width = width
        self._height = height
        self._to_ide = to_ide
        self.display = Display()
        self.display.init(Display.ST7701, width, height, to_ide=to_ide, quality=100)
        MediaManager.init()

        fpioa.set_function(bl_pinx, fpioa.GPIO0 + bl_pinx)
        pull = Pin.PULL_UP if bl_valid == 0 else Pin.PULL_DOWN
        self.bl = Pin(bl_pinx, Pin.OUT, pull=pull, drive=7)
        self.bl_valid = bl_valid
        self.on()

    def on(self):
        self.bl.value(self.bl_valid)

    def lvgl_flush_cb(self, disp, area, px_map):
        if disp.flush_is_last():
            if self.draw_buf_1.virtaddr() == uctypes.addressof(px_map.__dereference__()):
                self.display.show_image(self.draw_buf_1)
            else:
                self.display.show_image(self.draw_buf_2)
        disp.flush_ready()

    def lvgl_init(self, width, height):
        self.draw_buf_1 = image.Image(width, height, image.BGRA8888)
        self.draw_buf_2 = image.Image(width, height, image.BGRA8888)
        # 先铺纯黑像素,避免缓冲初始值异常(DIRECT 无下层画面时必须不透明)。
        for _fb in (self.draw_buf_1, self.draw_buf_2):
            _fb.draw_rectangle(0, 0, width, height, color=(0, 0, 0), thickness=1, fill=True)
        self.disp = lv.disp_create(width, height)
        self.disp.set_flush_cb(self.lvgl_flush_cb)
        self.disp.set_draw_buffers(
            self.draw_buf_1.bytearray(),
            self.draw_buf_2.bytearray(),
            self.draw_buf_1.size(),
            lv.DISP_RENDER_MODE.DIRECT,
        )


class Touch:
    """逐行照搬 DurUI.Touch:TOUCH(0) + lv indev pointer + read_cb。"""

    def __init__(self):
        self.touch = TOUCH(0)

    def __del__(self):
        del self.touch

    def lvgl_read_cb(self, indev, data):
        x, y, state = 0, 0, lv.INDEV_STATE.RELEASED
        tp = self.touch.read(1)
        if len(tp):
            x, y, event = tp[0].x, tp[0].y, tp[0].event
            if event in (TOUCH.EVENT_DOWN, TOUCH.EVENT_MOVE):
                state = lv.INDEV_STATE.PRESSED
        data.point = lv.point_t({'x': x, 'y': y})
        data.state = state

    def lvgl_init(self):
        self.indev = lv.indev_create()
        self.indev.set_type(lv.INDEV_TYPE.POINTER)
        self.indev.set_read_cb(self.lvgl_read_cb)


def _buzzer_init(fpioa):
    global _buzzer_pwm
    try:
        fpioa.set_function(BUZZER_PIN, fpioa.PWM0 + BUZZER_PWM_CH)
        _buzzer_pwm = PWM(BUZZER_PWM_CH, BUZZER_FREQ_HZ, BUZZER_DUTY_OFF, enable=True)
    except BaseException as e:
        _buzzer_pwm = None
        print("[probe] buzzer init fail:", e)


def _buzzer_beep(ms=80):
    if _buzzer_pwm is None:
        return
    try:
        _buzzer_pwm.duty(BUZZER_DUTY_ON)
        time.sleep_ms(ms)
        _buzzer_pwm.duty(BUZZER_DUTY_OFF)
    except BaseException:
        pass


class _ProbeState:
    """板端诊断状态:每秒打印 mem,seq==5 主动 GC(仅在 task_handler 返回后)。"""

    def __init__(self):
        self.last_ms = 0
        self.seq = 0

    def _diag_tick(self):
        try:
            now = time.ticks_ms()
            if self.last_ms == 0:
                self.last_ms = now
                return
            if time.ticks_diff(now, self.last_ms) < 1000:
                return
            self.seq += 1
            try:
                mem = gc.mem_free()
            except Exception:
                mem = -1
            print("[probe-diag] seq=%d mem=%d" % (self.seq, mem))
            if self.seq == 5:
                print("[probe-diag] proactive gc begin")
                gc.collect()
                print("[probe-diag] proactive gc end mem=%d" % gc.mem_free())
            self.last_ms = now
        except Exception as e:
            print("[probe-diag] failed: %s" % e)


def _on_card_click(category_id):
    """本实验不进脚本(不触碰 reset 框架),仅打印。"""
    print("[probe] card click:", category_id)


def main():
    print("[probe] === DurUI display-stack probe start ===")
    fpioa = FPIOA()

    lcd = LCD(DISPLAY_WIDTH, DISPLAY_HEIGHT, to_ide=False, fpioa=fpioa, bl_pinx=5, bl_valid=1)
    touch = Touch()
    _buzzer_init(fpioa)

    lv.init()
    lcd.lvgl_init(DISPLAY_WIDTH, DISPLAY_HEIGHT)
    touch.lvgl_init()

    # 字体:MainMenu 内部用 core.font_manager.fonts;首次 task_handler 前加载。
    try:
        from core.font_manager import fonts
        fonts.load_all()
    except Exception as e:
        print("[probe] font load warning:", e)

    # 配置/语言:复用 core,均不依赖 sensor。
    from core.config_manager import ConfigManager
    from core.lang import LangManager
    config = ConfigManager()
    config.load()
    lang = LangManager()
    lang.load(config.get('lang', 'zh_CN'))

    from ui.main_menu import MainMenu
    buzzer = _BuzzerShim()
    menu = MainMenu(config, buzzer, lang, on_card_click=_on_card_click)

    # preload_icons 必须在首次 task_handler 前(文件 I/O 安全窗口)。
    menu.preload_icons()
    menu.show()

    _buzzer_beep(80)
    print("[probe] main menu running")

    state = _ProbeState()
    while True:
        os.exitpoint()
        lv.task_handler()
        state._diag_tick()
        time.sleep_ms(MENU_LV_TASK_SLEEP_MS)


class _BuzzerShim:
    """MainMenu 期望 buzzer.beep(ms=...);对齐 DurUI PWM 行为。"""

    def beep(self, ms=50):
        _buzzer_beep(ms)

    def set_enabled(self, enabled):
        pass


if __name__ == "__main__":
    main()
