# hw/lcd.py — LCD 显示驱动（ST7701 横屏 640×480）
# 移植自 demo/基础实验例程/实验1 跑马灯实验/123/main.py

from media.display import Display
from media.media import MediaManager
from media.sensor import Sensor, CAM_CHN_ID_0, CAM_CHN_ID_1, CAM_CHN_ID_2
from machine import Pin
import image
import lvgl as lv
import time
import os
import uctypes


# 屏幕参数
DISPLAY_WIDTH = 640
DISPLAY_HEIGHT = 480


class LCD:
    """ST7701 LCD 显示驱动 + LVGL 双缓冲刷新"""

    def __init__(self, width=640, height=480, to_ide=False,
                 fpioa=None, bl_pinx=5, bl_valid=1):
        self.width = width
        self.height = height
        self.to_ide = to_ide

        # ── 关键初始化顺序（严格对齐官方 camera_single_show_lcd.py）──
        # Sensor 必须在 MediaManager.init() 之前完成 reset + framesize +
        # pixformat 配置，否则 MediaManager 不会为 sensor 通道分配帧缓冲，
        # 串口报 `sensor(0) ... buffer_size 0`，snapshot 拿不到有效帧 → 黑屏。
        #
        # 官方顺序：
        #   Sensor() → reset → set_framesize → set_pixformat
        #     → Display.init(osd_num=2) → MediaManager.init() → run()
        #
        # 本机是相机中心设备，sensor 整机生命周期常驻；启动期只配置不取流
        # （不调 run()）。首次进相机 APP 时 ensure_sensor_running() 调一次
        # run()，之后**永不 stop()**——K230 的 sensor.stop() 会拆掉内部状态，
        # 再次 run() 前必须重新 reset()，而 reset() 在 MediaManager.init() 之后
        # 重复调用会触碰已分配的缓冲池（MediaManager 不可重建）。因此 sensor
        # run() 一次后常驻，相机 APP 退出只停止推帧，不动 sensor 状态机。
        # chn0：预览通道（RGB888 → OSD1 show_image，板端已验证）。
        # chn1：拍照通道（RGB565 → img.save 支持的格式）。K230 的 RGB888
        # 不支持 save()（报 `current format not support save function`），
        # 且通道必须在 MediaManager.init() 之前一次性声明全（池不可重建，
        # 见 K230 坑 #15）。故在此同时配置 chn0(预览) 与 chn1(拍照)。
        # Sensor 必须显式声明原生输入尺寸 1280×960。所有 demo 均用
        # Sensor(width=1280, height=960)；不声明（仅 fps=30）时 chn1 的输出
        # 缩放基准未定义，kd_mpi_vicap_dump_frame 永远超时 → 板端报
        # `sensor(0) snapshot chn(1) failed(3)`（板端实测：关 chn0 预览后
        # chn1 仍 dump 不出帧，排除并发/带宽，确认是输入尺寸+chn1 缩放配置）。
        self.sensor = Sensor(width=1280, height=960, fps=30)
        self.sensor.reset()
        # chn0 — 预览：从 1280×960 下采样到 VGA（所有 demo 验证 OK）
        self.sensor.set_framesize(Sensor.VGA, chn=CAM_CHN_ID_0)
        self.sensor.set_pixformat(Sensor.RGB888, chn=CAM_CHN_ID_0)
        # chn1 — 拍照保存：SXGAM(1280×960)=输入原生尺寸，不缩放（RGB565 支持
        # jpg/bmp save）。严格对齐 demo/实验15 唯一可用的多通道拍照配置；
        # chn1 输出尺寸必须 ≤ 输入，取等（原生）最稳，避免下采样导致 dump 失败。
        self.sensor.set_framesize(Sensor.SXGAM, chn=CAM_CHN_ID_1)
        self.sensor.set_pixformat(Sensor.RGB565, chn=CAM_CHN_ID_1)
        self.capture_chn = CAM_CHN_ID_1
        # chn2 — AI推理输入：RGBP888（planar CHW），1024×768（XGA）。
        # PLANAR 格式：snapshot(chn=CAM_CHN_ID_2).to_numpy_ref() 直接得到
        # planar CHW，零软件转置。对齐官方 AI+LVGL 标杆例程
        # examples/21-AI-With-Others/ai_lvgl.py:225（Sensor.RGBP888）。
        # 注：Sensor.RGBP888 与 PIXEL_FORMAT_RGB_888_PLANAR 是同义的 planar
        # 格式常量；官方所有 AI 例程(ai_lvgl/ai_multi_thread/DataCollectionCamera)
        # 统一用 Sensor.RGBP888，此处对齐。早期误用 RGB888 interleaved + 软件转置
        # CHW 喂 NPU 卡死，见 specs/2026-06-17-face-detect-chn2-planar-fix-design.md。
        # 所有 AI 类 APP 共用此通道，必须启动期声明
        # （MediaManager.init() 之后池不可重建，K230 pitfall #15）。
        self.sensor.set_framesize(Sensor.XGA, chn=CAM_CHN_ID_2)
        self.sensor.set_pixformat(Sensor.RGBP888, chn=CAM_CHN_ID_2)
        self.ai_chn = CAM_CHN_ID_2
        self._sensor_running = False

        # 初始化 Display — osd_num=2 创建 2 个 OSD 层：
        #   OSD1: 相机帧（Display.show_image(LAYER_OSD1)）
        #   OSD2: LVGL UI（flush 回调显式指定）
        # 参考官方 ai_lvgl.py 的 LVGL+相机共存架构。
        self.display = Display()
        self.display.init(Display.ST7701, width, height,
                          to_ide=to_ide, osd_num=2, quality=100)
        # 此时 sensor 通道已配置 → MediaManager 为其分配帧缓冲（buffer_size>0）
        MediaManager.init()

        # 背光 GPIO
        fpioa.set_function(bl_pinx, fpioa.GPIO0 + bl_pinx)
        pull = (Pin.PULL_UP if bl_valid == 0
                else Pin.PULL_DOWN)
        self.bl = Pin(bl_pinx, Pin.OUT, pull=pull, drive=7)
        self.bl_valid = bl_valid
        self.on()

        # LVGL 双缓冲（在 lvgl_init 中创建）
        self.draw_buf_1 = None
        self.draw_buf_2 = None
        self.disp = None

    def __del__(self):
        del self.bl
        os.exitpoint(os.EXITPOINT_ENABLE_SLEEP)
        time.sleep_ms(50)
        try:
            self.sensor.stop()
        except BaseException:
            pass
        self.display.deinit()
        MediaManager.deinit()

    def get_sensor(self):
        """返回启动期已配置的常驻 Sensor。

        chn0 = VGA/RGB888（预览，OSD1）；chn1 = VGA/RGB565（拍照，
        snapshot(chn=self.capture_chn) + img.save 用，见 self.capture_chn）。
        全机只有这一个 sensor 实例，两通道缓冲均在启动期随
        MediaManager.init() 分配。取流由 ensure_sensor_running() 幂等启动。
        """
        return self.sensor

    def ensure_sensor_running(self):
        """幂等启动 sensor 取流——全机生命周期只 run() 一次。

        K230 的 sensor.stop() 会拆掉内部状态，再次 run() 前须 reset()，
        而 reset() 在 MediaManager.init() 之后会触碰缓冲池（不可重建）。
        故采用「run 一次后常驻」策略：相机 APP 反复进出只调本方法，
        第二次起直接返回，sensor 持续取流，APP 退出仅停止推帧。
        """
        if self._sensor_running:
            return
        self.sensor.run()
        self._sensor_running = True

    def get_ai_frame(self):
        """从 chn2 获取一帧 AI 推理图像，转为 CHW numpy 数组（对齐 Demo 格式）。

        ⚠️ 此方法内部用 to_rgb888()+reshape/transpose 软件转置，是 chn2 误配为
        RGB888 interleaved 时期的遗留路径。chn2 已改回 RGB888_PLANAR 后，
        AI 帧应直接 sensor.snapshot(chn=CAM_CHN_ID_2).to_numpy_ref()（见
        face_detect on_frame），无需此方法。保留以备其他脚本，勿新增调用。

        Demo 中 PipeLine.get_frame() 返回的 img 经 image2rgb888array()
        转为 (1, 3, H, W) CHW numpy 数组供 NPU 推理。此方法复现该流程：
        chn2 snapshot → to_rgb888() → numpy reshape/transpose → CHW。

        Returns: ulab numpy ndarray shape (1, 3, 768, 1024) 或 None
        """
        import ulab.numpy as np

        img = self.sensor.snapshot(chn=self.ai_chn)
        if img is None:
            return None

        rgb888 = img.to_rgb888()
        hwc = rgb888.to_numpy_ref()
        shape = hwc.shape
        # HWC → CHW
        tmp = hwc.reshape((shape[0] * shape[1], shape[2]))
        trans = tmp.transpose()
        chw = trans.copy()
        return chw.reshape((1, shape[2], shape[0], shape[1]))

    def on(self):
        """打开背光"""
        self.bl.value(self.bl_valid)

    def off(self):
        """关闭背光"""
        self.bl.value(1 - self.bl_valid)

    # ── LVGL 集成 ──────────────────────────────────────

    def lvgl_flush_cb(self, disp, area, px_map):
        """LVGL flush 回调：将绘制缓冲区送至 OSD2 层

        每次全量 flush 后清零非活跃缓冲。LVGL bg_opa=0 的区域不写像素，
        若非活跃缓冲残留旧帧像素，预览区透明位置是脏数据，遮住下层 OSD1
        相机画面。

        参考官方 ai_lvgl.py 的 OSD1(相机)+OSD2(LVGL) 双层架构。
        """
        if self.draw_buf_1 is None or self.draw_buf_2 is None:
            disp.flush_ready()
            return
        if disp.flush_is_last():
            if self.draw_buf_1.virtaddr() == uctypes.addressof(
                    px_map.__dereference__()):
                self.display.show_image(self.draw_buf_1,
                                        layer=Display.LAYER_OSD2)
                # 清零非活跃缓冲，防止下一帧 bg_opa=0 区域残留旧像素
                try:
                    self.draw_buf_2.bytearray()[:] = bytearray(0)
                except BaseException:
                    pass
            else:
                self.display.show_image(self.draw_buf_2,
                                        layer=Display.LAYER_OSD2)
                try:
                    self.draw_buf_1.bytearray()[:] = bytearray(0)
                except BaseException:
                    pass
            # 对齐官方 ai_lvgl.py disp_drv_flush_cb：flush 后 sleep 10ms。
            # 给 OSD2 show_image DMA 留出完成时间，使 DMA 大部分时间空闲。
            # 否则 flush 高频提交 DMA 持续在途，AI 线程 gc.collect() 撞上
            # 在途 DMA 概率高 → 偶发卡死（板端实测：双线程后 gc 仍偶发卡）。
            time.sleep(0.01)
        disp.flush_ready()

    def lvgl_init(self):
        """创建 LVGL 双缓冲并注册 display driver"""
        self.draw_buf_1 = image.Image(self.width, self.height,
                                       image.BGRA8888)
        self.draw_buf_2 = image.Image(self.width, self.height,
                                       image.BGRA8888)

        self.disp = lv.disp_create(self.width, self.height)
        self.disp.set_flush_cb(self.lvgl_flush_cb)
        self.disp.set_draw_buffers(
            self.draw_buf_1.bytearray(),
            self.draw_buf_2.bytearray(),
            self.draw_buf_1.size(),
            # FULL 模式：每帧整屏重绘。必须与 flush 回调的「清零非活跃缓冲」
            # 配套——DIRECT 模式只重绘脏区,清零会抹掉其余持久 UI（顶栏等）。
            # 对齐官方 ai_lvgl.py 的 LVGL+相机共存渲染模式。
            lv.DISP_RENDER_MODE.FULL,
        )

    def clear_framebuffers(self):
        """清空双缓冲为透明像素 — 相机模式切换前调用

        LVGL bg_opa=0 不写像素，若缓冲区有旧帧残留会遮住下层 OSD1。
        此方法在相机启动前清零缓冲，配合 flush 回调的清非活跃缓冲逻辑，
        确保预览区始终是真正的透明像素。
        """
        for fb in (self.draw_buf_1, self.draw_buf_2):
            if fb is not None:
                try:
                    fb.clear()
                except BaseException:
                    try:
                        fb.draw_rectangle(
                            0, 0, self.width, self.height,
                            color=(0, 0, 0, 0), thickness=1, fill=True,
                        )
                    except BaseException:
                        pass

    def lvgl_deinit(self):
        """销毁 LVGL 双缓冲与 display driver"""
        if self.disp is not None:
            del self.disp
            self.disp = None
        if self.draw_buf_1 is not None:
            del self.draw_buf_1
            self.draw_buf_1 = None
        if self.draw_buf_2 is not None:
            del self.draw_buf_2
            self.draw_buf_2 = None
