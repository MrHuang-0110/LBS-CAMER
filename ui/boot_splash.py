# ui/boot_splash.py — 开机 LOGO 画面
#
# 流程：蜂鸣一声 → 显示 /resource/logo.png（640×480 全屏）
#       → 阻塞保持 stay_ms（期间持续 task_handler）→ 清理 → 回调进入主菜单
#
# 若 LOGO 文件缺失：蜂鸣 + 纯黑底等待 1s → 回调。

import lvgl as lv
import time

from ui.theme import Colors


class BootSplash:
    """开机画面 — 全屏 LOGO，阻塞等待，完成后回调"""

    LOGO_PATH = "/sdcard/CamerAi/resource/logo.png"

    def __init__(self, buzzer, stay_ms=1500):
        """
        Args:
            buzzer: Buzzer 实例
            stay_ms: LOGO 保持时长（毫秒），默认 1500
        """
        self.buzzer = buzzer
        self.stay_ms = stay_ms
        self._done_callback = None
        self._screen = None
        self._img = None
        # logo 字节/描述符挂 self 保活：lv.img.set_src 只存底层指针，
        # 局部变量被回收会导致重绘访问已释放内存而崩溃。
        self._logo_data = None
        self._logo_dsc = None

    def show(self, on_done=None):
        """显示开机画面，阻塞等待后回调。

        Args:
            on_done: 完成后的回调，用于切换至主菜单
        """
        self._done_callback = on_done

        # 1. 蜂鸣开机提示
        self.buzzer.beep(ms=80)

        # 2. 创建全屏容器
        self._screen = lv.obj(lv.scr_act())
        self._screen.set_size(lv.pct(100), lv.pct(100))
        self._screen.center()
        self._screen.set_style_bg_color(lv.color_hex(Colors.BG), 0)
        self._screen.set_style_bg_opa(255, 0)
        self._screen.set_style_border_width(0, 0)
        self._screen.set_style_pad_all(0, 0)
        self._screen.set_style_radius(0, 0)
        self._screen.clear_flag(lv.obj.FLAG.SCROLLABLE)
        self._screen.move_foreground()

        # 3. 尝试加载 LOGO 图片
        logo_loaded = False
        try:
            with open(self.LOGO_PATH, 'rb') as f:
                self._logo_data = f.read()
            self._logo_dsc = lv.img_dsc_t({
                'data_size': len(self._logo_data),
                'data': self._logo_data,
            })
            self._img = lv.img(self._screen)
            self._img.set_src(self._logo_dsc)
            self._img.center()
            logo_loaded = True
            print("[BootSplash] logo loaded (%d bytes)" % len(self._logo_data))
        except Exception as e:
            print(f"[BootSplash] logo not found: {e}")

        # 4. 渲染一帧，让 LOGO 显示出来
        lv.task_handler()
        time.sleep_ms(50)

        # 5. 阻塞等待（简单可靠，不依赖 LVGL 动画回调）
        wait_ms = self.stay_ms if logo_loaded else max(1000, self.stay_ms)
        print(f"[BootSplash] showing for {wait_ms}ms...")
        start = time.ticks_ms()
        while time.ticks_diff(time.ticks_ms(), start) < wait_ms:
            lv.task_handler()
            time.sleep_ms(10)

        # 6. 清理并回调
        self._on_done()

    def _on_done(self):
        """清理 → 回调"""
        print("[BootSplash] _on_done() — cleaning up...")
        if self._img is not None:
            try:
                self._img.delete()
            except Exception as e:
                print(f"[BootSplash] img.delete() failed: {e}")
            self._img = None
        if self._screen is not None:
            try:
                self._screen.delete()
            except Exception as e:
                print(f"[BootSplash] screen.delete() failed: {e}")
            self._screen = None
        # 字节/描述符在对象删除后才可释放
        self._logo_dsc = None
        self._logo_data = None
        # 强制刷新 LVGL，确保删除生效
        lv.task_handler()
        print("[BootSplash] cleanup done, calling callback...")
        if self._done_callback is not None:
            self._done_callback()
