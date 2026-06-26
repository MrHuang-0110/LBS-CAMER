# core/app_runtime.py — reset 框架公共 init 模块
#
# 每个 APP 进程独立 init（reset 切换架构）。封装 Display/MediaManager/
# sensor/LVGL/host/字体/图标，按 category 决定 sensor 通道配置。
#
# 对齐官方综合例程：脚本自己 init 全套，进程独立，无跨脚本状态污染。
#
# K230 硬约束：
#   - 坑#2：init 后首次 task_handler 前完成文件 I/O（字体/图标/kmodel）
#   - 坑#15：sensor 通道须 MediaManager.init() 前声明
#   - reset 架构每进程独立 init/deinit，不再"常驻不拆"

import os
from media.display import Display
from media.media import MediaManager
from media.sensor import Sensor, CAM_CHN_ID_0, CAM_CHN_ID_1, CAM_CHN_ID_2
from machine import Pin, FPIOA
import image
import lvgl as lv
import time
import uctypes

DISPLAY_WIDTH = 640
DISPLAY_HEIGHT = 480


class AppRuntime:
    """每进程独立的硬件/LVGL/host 运行时。

    main.py 按 .next_script 决定调 init_menu（主菜单）或
    init_app(category_id)（脚本模式）。脚本退出调 cleanup。
    """

    def __init__(self):
        self.width = DISPLAY_WIDTH
        self.height = DISPLAY_HEIGHT
        self.fpioa = None
        self.sensor = None
        self.display = None
        self.draw_buf_1 = None
        self.draw_buf_2 = None
        self.lv_disp = None
        self.bl = None
        self.host = None
        self.lang = None
        self.config = None
        self.buzzer = None
        self.touch = None
        self._sensor_running = False
        self.category_id = None

    def _init_display_and_media(self, to_ide=False):
        self.display = Display()
        self.display.init(Display.ST7701, self.width, self.height,
                          to_ide=to_ide, osd_num=2, quality=100)
        MediaManager.init()

    def _init_backlight(self, fpioa, bl_pinx=5, bl_valid=1):
        fpioa.set_function(bl_pinx, fpioa.GPIO0 + bl_pinx)
        pull = Pin.PULL_UP if bl_valid == 0 else Pin.PULL_DOWN
        self.bl = Pin(bl_pinx, Pin.OUT, pull=pull, drive=7)
        self.bl.value(bl_valid)

    def _config_sensor(self, channels):
        """配置 sensor 通道。channels: list of (chn_id, framesize, pixformat)。
        必须在 MediaManager.init() 之前调。"""
        self.sensor = Sensor(width=1280, height=960, fps=30)
        self.sensor.reset()
        for chn_id, framesize, pixformat in channels:
            self.sensor.set_framesize(framesize, chn=chn_id)
            self.sensor.set_pixformat(pixformat, chn=chn_id)

    def _lvgl_init(self, render_mode=lv.DISP_RENDER_MODE.FULL):
        self.draw_buf_1 = image.Image(self.width, self.height, image.BGRA8888)
        self.draw_buf_2 = image.Image(self.width, self.height, image.BGRA8888)
        self.draw_buf_1.clear()
        self.draw_buf_2.clear()
        self.lv_disp = lv.disp_create(self.width, self.height)
        self.lv_disp.set_flush_cb(self._flush_cb)
        self.lv_disp.set_color_format(lv.COLOR_FORMAT.ARGB8888)
        self.lv_disp.set_draw_buffers(
            self.draw_buf_1.bytearray(), self.draw_buf_2.bytearray(),
            self.draw_buf_1.size(), render_mode)

    def _flush_cb(self, disp, area, px_map):
        """LVGL flush 回调（对齐官方 ai_lvgl.py disp_drv_flush_cb）。"""
        if self.draw_buf_1 is None or self.draw_buf_2 is None:
            disp.flush_ready()
            return
        if disp.flush_is_last():
            if self.draw_buf_1.virtaddr() == uctypes.addressof(
                    px_map.__dereference__()):
                self.draw_buf_2.bytearray()[:] = bytearray(0)
                self.display.show_image(self.draw_buf_1, layer=Display.LAYER_OSD2)
            else:
                self.draw_buf_1.bytearray()[:] = bytearray(0)
                self.display.show_image(self.draw_buf_2, layer=Display.LAYER_OSD2)
            time.sleep(0.01)
        disp.flush_ready()

    def _init_touch(self):
        """触摸 init：构造 + lvgl_init（注册 indev）+ hw_init（TOUCH 硬件）。
        必须在 lv.init() + _lvgl_init()（disp_create）之后调。"""
        from hw.touch import Touch
        self.touch = Touch(index=0)
        self.touch.lvgl_init()
        self.touch.hw_init()

    def init_menu(self, fpioa):
        """主菜单模式 init：Display/MediaManager/sensor(chn0)/LVGL/触摸/字体/图标/host。"""
        self.fpioa = fpioa
        self.category_id = "main_menu"
        self._config_sensor([(CAM_CHN_ID_0, Sensor.VGA, Sensor.RGB888)])
        self._init_display_and_media()
        self._init_backlight(fpioa)
        lv.init()
        self._lvgl_init()
        self._init_touch()
        from core.font_manager import fonts
        try:
            fonts.load_all()
        except Exception as e:
            print("[Runtime] font load warning: %s" % e)
        from core.icon_cache import icon_cache
        icon_cache.preload_settings_icons()
        icon_cache.preload_camera_icons()
        icon_cache.preload_face_icons()
        self._init_services(fpioa)

    def init_app(self, category_id, fpioa):
        """脚本模式 init：按 category 配 sensor 通道 + 全套 init + 触摸 + sensor.run。

        ⚠️ init 顺序对齐裸跑 test_face_baseline_camerai_sensor.py media_init：
        Display.init → Sensor/reset/配通道 → MediaManager.init → sensor.run。
        （之前 sensor 配置在 Display.init 前，与裸跑相反，致 face_detect 卡 fc~8。）
        """
        self.fpioa = fpioa
        self.category_id = category_id
        channels = self._channels_for(category_id)
        # 1. Display.init 先（对齐裸跑）
        self.display = Display()
        self.display.init(Display.ST7701, self.width, self.height,
                          to_ide=False, osd_num=2, quality=100)
        # 2. Sensor 配置（Display.init 后、MediaManager.init 前）
        self._config_sensor(channels)
        # 3. MediaManager.init（sensor 配置后）
        MediaManager.init()
        self._init_backlight(fpioa)
        # render mode 统一 FULL：flush_cb 每次清零非活跃缓冲，只在 FULL(整屏重绘)
        # 下安全；PARTIAL 只刷脏区，清零会抹掉持久 UI(顶栏等)——见 hw/lcd.py 注释。
        # camera 拍照闪光触发脏区后 PARTIAL+清零导致顶底栏消失；face_detect 每帧
        # 画框同理会崩。单线程主循环下 FULL 无 OSD1/OSD2 DMA 竞争(模板 FULL 已
        # 板端验证稳定)，故 stream/page 统一 FULL。对齐官方 ai_lvgl.py + hw/lcd.py。
        lv.init()
        self._lvgl_init(lv.DISP_RENDER_MODE.FULL)
        self._init_touch()
        from core.font_manager import fonts
        try:
            fonts.load_all()
        except Exception as e:
            print("[Runtime] font load warning: %s" % e)
        # 预读脚本图标（task_handler 前完成文件 I/O，坑#2）：
        #   back 图标 — 所有脚本顶栏返回钮共用
        #   camera 图标 — camera 顶栏返回/底栏图库·模式钮用（仅 camera 需要）
        from core.icon_cache import icon_cache
        icon_cache.preload_back_icon()
        if category_id == "camera":
            icon_cache.preload_camera_icons()
        elif category_id == "face_detect":
            icon_cache.preload_face_icons()
        elif category_id == "tag_detect":
            icon_cache.preload_tag_icons()
        elif category_id == "object_detect":
            icon_cache.preload_object_icons()
        self._init_services(fpioa)
        # sensor.run 紧贴脚本主循环（消费者就绪后才 run，避免缓冲满卡死）
        self.sensor.run()
        self._sensor_running = True

    def _channels_for(self, category_id):
        """按 category 决定 sensor 通道配置。"""
        chs = [(CAM_CHN_ID_0, Sensor.VGA, Sensor.RGB888)]
        if category_id == "face_detect":
            chs.append((CAM_CHN_ID_2, Sensor.XGA, Sensor.RGBP888))
        elif category_id == "camera":
            chs.append((CAM_CHN_ID_1, Sensor.SXGAM, Sensor.RGB565))
        elif category_id == "tag_detect":
            # chn1 QVGA RGB565 专做检测（官方 AprilTag/QR demo 同款）；
            # chn0 VGA RGB888 显示。rect ×2 映射显示（QVGA→VGA 整数缩放）。
            chs.append((CAM_CHN_ID_1, Sensor.QVGA, Sensor.RGB565))
        elif category_id == "object_detect":
            # chn2 XGA RGBP888 专做 AI 推理(同 face_detect AI 通道)；
            # chn0 VGA RGB888 显示。检测框 rgb888p->display 整数缩放。
            chs.append((CAM_CHN_ID_2, Sensor.XGA, Sensor.RGBP888))
        elif category_id == "_template":
            pass  # 模板纯显示，单通道 chn0（复用默认）
        return chs

    def _init_services(self, fpioa):
        from core.config_manager import ConfigManager
        from core.lang import LangManager
        from comm.host_api import HostAPI
        self.config = ConfigManager()
        self.config.load()
        from hw.buzzer import Buzzer
        self.buzzer = Buzzer(fpioa, pinx=60, pwm_ch=0, valid=0)
        self.buzzer.set_enabled(self.config.get('buzzer_enabled', True))
        self.lang = LangManager()
        self.lang.load(self.config.get('lang', 'zh_CN'))
        fpioa.set_function(40, FPIOA.UART1_TXD)
        fpioa.set_function(41, FPIOA.UART1_RXD)
        self.host = HostAPI()

    def host_tick(self, slots=None):
        """每帧调：握手轮询 + 按当前 category 推送4组数据。

        slots=None → 4组全0（主菜单/相机/settings）。face_detect 传匹配槽位。
        """
        if self.host is not None:
            self.host.tick(self.category_id, slots)

    def cleanup(self):
        """脚本退出前清理（显式，虽 reset 会清）。"""
        try:
            if self._sensor_running:
                self.sensor.stop()
        except BaseException:
            pass
        try:
            if self.touch is not None:
                if self.touch.indev is not None:
                    del self.touch.indev
                    self.touch.indev = None
                del self.touch
                self.touch = None
        except BaseException:
            pass
        try:
            if self.lv_disp is not None:
                del self.lv_disp
        except BaseException:
            pass
        try:
            del self.draw_buf_1
            del self.draw_buf_2
        except BaseException:
            pass
        try:
            lv.deinit()
        except BaseException:
            pass
        try:
            self.display.deinit()
        except BaseException:
            pass
        try:
            MediaManager.deinit()
        except BaseException:
            pass
