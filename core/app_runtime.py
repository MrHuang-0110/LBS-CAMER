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
from machine import Pin, FPIOA, TOUCH
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

    def _init_menu_display_and_media(self, to_ide=False):
        """主菜单专用显示链路：对齐 DurUI，不申请 OSD2 分层。"""
        self.display = Display()
        self.display.init(Display.ST7701, self.width, self.height,
                          to_ide=to_ide, quality=100)
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

    def _lvgl_init(self, render_mode=lv.DISP_RENDER_MODE.FULL, opaque_bg=False):
        self.draw_buf_1 = image.Image(self.width, self.height, image.BGRA8888)
        self.draw_buf_2 = image.Image(self.width, self.height, image.BGRA8888)
        if opaque_bg:
            # 主菜单无底层相机画面，DIRECT 模式下须先铺不透明纯黑(alpha=255)，
            # 否则 clear() 把 alpha 清成 0 → 全透明 → 屏幕黑屏/不显示。
            # 对齐 DurUI lvgl_init 的缓冲初始铺黑。
            for _fb in (self.draw_buf_1, self.draw_buf_2):
                _fb.draw_rectangle(0, 0, self.width, self.height,
                                   color=(0, 0, 0), thickness=1, fill=True)
        else:
            self.draw_buf_1.clear()
            self.draw_buf_2.clear()
        self.lv_disp = lv.disp_create(self.width, self.height)
        self.lv_disp.set_flush_cb(self._flush_cb)
        # 主菜单(DurUI 栈)对齐 DurUI probe：不设 color_format。
        # set_color_format(ARGB8888) 与 BGRA8888 字节序不匹配，DIRECT 下黑屏。
        # 脚本模式(FULL+OSD2)保留 set_color_format，历来工作正常。
        if not opaque_bg:
            self.lv_disp.set_color_format(lv.COLOR_FORMAT.ARGB8888)
        self.lv_disp.set_draw_buffers(
            self.draw_buf_1.bytearray(), self.draw_buf_2.bytearray(),
            self.draw_buf_1.size(), render_mode)

    def _flush_cb(self, disp, area, px_map):
        """LVGL flush 回调。

        主菜单对齐 DurUI：DIRECT + 单层 show_image，不指定 OSD2、不清缓冲。
        脚本模式保留 FULL + OSD2 + 清非活跃缓冲，避免影响摄像头叠加层。
        """
        if self.draw_buf_1 is None or self.draw_buf_2 is None:
            disp.flush_ready()
            return
        if disp.flush_is_last():
            if self.category_id == "main_menu":
                if self.draw_buf_1.virtaddr() == uctypes.addressof(
                        px_map.__dereference__()):
                    self.display.show_image(self.draw_buf_1)
                else:
                    self.display.show_image(self.draw_buf_2)
            else:
                if self.draw_buf_1.virtaddr() == uctypes.addressof(
                        px_map.__dereference__()):
                    self.draw_buf_2.bytearray()[:] = bytearray(0)
                    self.display.show_image(self.draw_buf_1, layer=Display.LAYER_OSD2)
                else:
                    self.draw_buf_1.bytearray()[:] = bytearray(0)
                    self.display.show_image(self.draw_buf_2, layer=Display.LAYER_OSD2)
            time.sleep(0.002)  # 对齐官方 ai_lvgl.py,2ms 让步 DMA 而非 10ms(减少帧延迟)
        disp.flush_ready()

    def _init_touch(self):
        """触摸 init：构造 + lvgl_init（注册 indev）+ hw_init（TOUCH 硬件）。
        必须在 lv.init() + _lvgl_init()（disp_create）之后调。"""
        from hw.touch import Touch
        self.touch = Touch(index=0)
        self.touch.lvgl_init()
        self.touch.hw_init()

    def _init_menu_touch(self):
        """主菜单专用触摸：逐行照搬 DurUI probe 的 Touch（直接 TOUCH(0) + 裸 read_cb）。

        probe（GC 后不死）与 run_menu（GC 后死）显示栈对齐后仍差异于此：
        probe 直接构造 TOUCH(0)，read_cb 无 try/except；hw/touch.Touch 延迟到
        hw_init() 构造并用 try/except 包 read。主菜单对齐 probe 以隔离该变量。
        脚本模式仍用 _init_touch（hw/touch）。
        """
        touch_obj = TOUCH(0)

        def _read_cb(indev, data):
            x, y, state = 0, 0, lv.INDEV_STATE.RELEASED
            tp = touch_obj.read(1)
            if len(tp):
                x, y, event = tp[0].x, tp[0].y, tp[0].event
                if event in (TOUCH.EVENT_DOWN, TOUCH.EVENT_MOVE):
                    state = lv.INDEV_STATE.PRESSED
            data.point = lv.point_t({'x': x, 'y': y})
            data.state = state

        indev = lv.indev_create()
        indev.set_type(lv.INDEV_TYPE.POINTER)
        indev.set_read_cb(_read_cb)
        # 保活：touch_obj 与 _read_cb 闭包须挂在 self 上，防 GC 后 C 侧悬空。
        self._menu_touch = touch_obj
        self._menu_touch_cb = _read_cb
        self._menu_indev = indev
        self.touch = None  # 主菜单不走 hw/touch

    def init_menu(self, fpioa):
        """主菜单模式 init：DurUI 风格 LCD-only 显示链路 + LVGL/触摸/字体/图标/host。"""
        self.fpioa = fpioa
        self.category_id = "main_menu"
        self._init_menu_display_and_media()
        self._init_backlight(fpioa)
        lv.init()
        self._lvgl_init(lv.DISP_RENDER_MODE.DIRECT, opaque_bg=True)
        self._init_menu_touch()
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
        耗时插桩(2026-08-07)：各阶段 ticks_ms 打点，板端看切换耗时分布再优化。
        """
        _t0 = time.ticks_ms()
        self.fpioa = fpioa
        self.category_id = category_id
        channels = self._channels_for(category_id)
        # 1. Display.init 先（对齐裸跑）
        self.display = Display()
        self.display.init(Display.ST7701, self.width, self.height,
                          to_ide=False, osd_num=2, quality=100)
        print("[Runtime] init Display: %d ms" % time.ticks_diff(time.ticks_ms(), _t0))
        # 2. Sensor 配置（Display.init 后、MediaManager.init 前）
        self._config_sensor(channels)
        print("[Runtime] init sensor: %d ms" % time.ticks_diff(time.ticks_ms(), _t0))
        # 3. MediaManager.init（sensor 配置后）
        MediaManager.init()
        print("[Runtime] init MediaManager: %d ms" % time.ticks_diff(time.ticks_ms(), _t0))
        self._init_backlight(fpioa)
        # render mode 统一 FULL：flush_cb 每次清零非活跃缓冲，只在 FULL(整屏重绘)
        # 下安全；PARTIAL 只刷脏区，清零会抹掉持久 UI(顶栏等)——见 hw/lcd.py 注释。
        # camera 拍照闪光触发脏区后 PARTIAL+清零导致顶底栏消失；face_detect 每帧
        # 画框同理会崩。单线程主循环下 FULL 无 OSD1/OSD2 DMA 竞争(模板 FULL 已
        # 板端验证稳定)，故 stream/page 统一 FULL。对齐官方 ai_lvgl.py + hw/lcd.py。
        lv.init()
        self._lvgl_init(lv.DISP_RENDER_MODE.FULL)
        print("[Runtime] init lvgl: %d ms" % time.ticks_diff(time.ticks_ms(), _t0))
        self._init_touch()
        from core.font_manager import fonts
        try:
            # 切换提速(2026-08-07)：脚本 UI 只用 body，color_detect/road_detect
            # 补 caption；不再全量加载字体（省 title 50px + Phase2 探测）。
            # 主菜单仍走全量加载。
            if category_id in ("color_detect", "road_detect"):
                fonts.load("body", "caption")
            else:
                fonts.load("body")
        except Exception as e:
            print("[Runtime] font load warning: %s" % e)
        print("[Runtime] init fonts: %d ms" % time.ticks_diff(time.ticks_ms(), _t0))
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
        elif category_id == "color_detect":
            icon_cache.preload_color_icons()
        elif category_id == "road_detect":
            icon_cache.preload_road_icons()
        elif category_id == "gesture_detect":
            icon_cache.preload_gesture_icons()
        elif category_id == "body_detect":
            icon_cache.preload_body_icons()
        elif category_id == "object_classify":
            icon_cache.preload_object_classify_icons()
        self._init_services(fpioa)
        print("[Runtime] init services: %d ms" % time.ticks_diff(time.ticks_ms(), _t0))
        # sensor.run 紧贴脚本主循环（消费者就绪后才 run，避免缓冲满卡死）
        self.sensor.run()
        self._sensor_running = True
        print("[Runtime] init_app total: %d ms" % time.ticks_diff(time.ticks_ms(), _t0))

    def _channels_for(self, category_id):
        """按 category 决定 sensor 通道配置。"""
        chs = [(CAM_CHN_ID_0, Sensor.VGA, Sensor.RGB888)]
        if category_id == "face_detect":
            # 过热修复(2026-08-11):恢复 chn2 XGA RGBP888 作 AI 输入(官方
            # main2 同构)。单通道吃 chn0 RGB888 须每检测帧 921KB 软件重排
            # (on=108ms CPU 满载→100.7°C 死机);chn2 RGBP888 planar 直出零
            # 重排。⚠️ chn2 framesize 必须 XGA:K230 ISP chn2 不支持 VGA 小
            # 尺寸,配 VGA 致 snapshot(chn=2) 挂死(2026-08-11 二次修复)。
            chs.append((CAM_CHN_ID_2, Sensor.XGA, Sensor.RGBP888))
        elif category_id == "camera":
            chs.append((CAM_CHN_ID_1, Sensor.SXGAM, Sensor.RGB565))
        elif category_id == "tag_detect":
            # chn1 QVGA RGB565 专做检测（官方 AprilTag/QR demo 同款）；
            # chn0 VGA RGB888 显示。rect ×2 映射显示（QVGA→VGA 整数缩放）。
            chs.append((CAM_CHN_ID_1, Sensor.QVGA, Sensor.RGB565))
        elif category_id == "object_detect":
            # 卡顿修复(2026-08-12):加 chn2 XGA RGBP888 作 AI 输入(对齐 face)，
            # 消 921KB 软件重排(on_max=136ms)。
            chs.append((CAM_CHN_ID_2, Sensor.XGA, Sensor.RGBP888))
        elif category_id == "color_detect":
            # chn1 QVGA RGB565 专做 find_blobs 颜色检测(同 tag_detect)；
            # chn0 VGA RGB888 显示+取色。blob rect ×2 映射显示(QVGA→VGA)。
            chs.append((CAM_CHN_ID_1, Sensor.QVGA, Sensor.RGB565))
        elif category_id == "road_detect":
            # 暂时单通道 chn0 VGA RGB888 预览(不跑AI、隐藏底栏)。
            # 后续完善时改 app.py 的 _DETECTION_ENABLED=True 并恢复 chn1 QVGA RGB565 检测。
            pass
        elif category_id == "gesture_detect":
            # 卡顿修复(2026-08-12):加 chn2 XGA RGBP888 作 AI 输入(对齐 face)，
            # 消 921KB 软件重排。
            chs.append((CAM_CHN_ID_2, Sensor.XGA, Sensor.RGBP888))
        elif category_id == "body_detect":
            # 卡顿修复(2026-08-12):加 chn2 XGA RGBP888 作 AI 输入(对齐 face)，
            # 消 921KB 软件重排。
            chs.append((CAM_CHN_ID_2, Sensor.XGA, Sensor.RGBP888))
        elif category_id == "object_classify":
            # 卡顿修复(2026-08-13):加 chn2 XGA RGBP888 作 AI 输入(对齐 face)，
            # 消 921KB 软件重排(on_max=136ms)。
            chs.append((CAM_CHN_ID_2, Sensor.XGA, Sensor.RGBP888))
        elif category_id == "image_classify":
            # 暂时单通道 chn0 VGA RGB888 预览(不跑AI)。
            # 后续完善时改 app.py 的 _DETECTION_ENABLED=True 并在此附加 chn2 AI 通道。
            pass
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

    def host_tick(self, slots=None, names=None):
        """每帧调：握手轮询 + 按当前 category 推送数据（可选名称帧）。

        slots=None → 4组全0（主菜单/相机/settings）。face_detect 传匹配槽位。
        names 非 None → 数据帧后追加名称帧（类型 0x0E，见 comm/host_api）。
        """
        if self.host is not None:
            self.host.tick(self.category_id, slots, names)

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
