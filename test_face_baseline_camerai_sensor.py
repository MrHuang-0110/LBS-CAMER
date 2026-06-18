# test_face_baseline.py — 官方 ai_lvgl.py 风格基线测试脚本
#
# 目的：systematic-debugging 基线对照。逐字照搬官方
# examples/21-AI-With-Others/ai_lvgl.py 的稳定双线程结构（face_det_thread
# + 主线程 task_handler + 每帧 gc），仅适配 640×480 屏。不加任何 CamerAi
# 附加逻辑（无 UI/注册/UART/预热/对象池）。
#
# 用法：复位板子后，串口/IDE 直接运行本脚本（不要跑 main.py，显示栈只能
# init 一次）。观察能否稳定跑几百帧。
#   稳定 → 板子+官方结构 OK，CamerAi face_detect 卡死在我们的附加逻辑
#   也卡 → 板子/sensor 配置问题
#
# 关键对照点（与 CamerAi face_detect 的差异）：
#   - Sensor(fps=30) 无 width/height（CamerAi 用 Sensor(1280,960)）
#   - chn0 640×480 RGB888 / chn2 1280×720 RGBP888（CamerAi chn2 1024×768）
#   - 无预热帧、无 _prev_img、无 _current_frame_data 跨帧持久化
#   - flush_cb 带 time.sleep(0.01)
#   - 主循环 time.sleep_ms(lv.task_handler())

import os, gc, sys, time, math
import _thread
import nncase_runtime as nn
import ulab.numpy as np
import image
import aidemo
from media.sensor import Sensor, CAM_CHN_ID_0, CAM_CHN_ID_2
from media.display import Display
from media.media import MediaManager
import lvgl as lv
from machine import TOUCH
from machine import FPIOA
from libs.AIBase import AIBase
from libs.AI2D import Ai2d
from libs.Utils import ScopedTiming, letterbox_pad_param


def ALIGN_UP(x, align=16):
    return (x + align - 1) // align * align


DISPLAY_WIDTH = ALIGN_UP(640, 16)
DISPLAY_HEIGHT = 480
# CamerAi lcd.py 的 sensor 配置：chn2 1024×768（非基线的 1280×720）
rgb888p_size = [1024, 768]
display_size = [640, 480]

sensor = None
osd_img = None
disp_img1 = None
disp_img2 = None
cur_state = 0
import uctypes


class FaceDetectionApp(AIBase):
    def __init__(self, kmodel_path, model_input_size, anchors,
                 confidence_threshold=0.5, nms_threshold=0.2,
                 rgb888p_size=[224, 224], display_size=[1920, 1080], debug_mode=0):
        super().__init__(kmodel_path, model_input_size, rgb888p_size, debug_mode)
        self.kmodel_path = kmodel_path
        self.model_input_size = model_input_size
        self.confidence_threshold = confidence_threshold
        self.nms_threshold = nms_threshold
        self.anchors = anchors
        self.rgb888p_size = [ALIGN_UP(rgb888p_size[0], 16), rgb888p_size[1]]
        self.display_size = [ALIGN_UP(display_size[0], 16), display_size[1]]
        self.debug_mode = debug_mode
        self.ai2d = Ai2d(debug_mode)
        self.ai2d.set_ai2d_dtype(nn.ai2d_format.NCHW_FMT, nn.ai2d_format.NCHW_FMT,
                                 np.uint8, np.uint8)

    def config_preprocess(self, input_image_size=None):
        with ScopedTiming("set preprocess config", self.debug_mode > 0):
            ai2d_input_size = input_image_size if input_image_size else self.rgb888p_size
            top, bottom, left, right, _ = letterbox_pad_param(self.rgb888p_size, self.model_input_size)
            self.ai2d.pad([0, 0, 0, 0, top, bottom, left, right], 0, [104, 117, 123])
            self.ai2d.resize(nn.interp_method.tf_bilinear, nn.interp_mode.half_pixel)
            self.ai2d.build([1, 3, ai2d_input_size[1], ai2d_input_size[0]],
                            [1, 3, self.model_input_size[1], self.model_input_size[0]])

    def postprocess(self, results):
        with ScopedTiming("postprocess", self.debug_mode > 0):
            post_ret = aidemo.face_det_post_process(
                self.confidence_threshold, self.nms_threshold,
                self.model_input_size[1], self.anchors, self.rgb888p_size, results)
            if len(post_ret) == 0:
                return post_ret
            else:
                return post_ret[0]

    def draw_result(self, osd_img, dets):
        with ScopedTiming("display_draw", self.debug_mode > 0):
            if dets:
                for det in dets:
                    x, y, w, h = map(lambda x: int(round(x, 0)), det[:4])
                    x = x * self.display_size[0] // self.rgb888p_size[0]
                    y = y * self.display_size[1] // self.rgb888p_size[1]
                    w = w * self.display_size[0] // self.rgb888p_size[0]
                    h = h * self.display_size[1] // self.rgb888p_size[1]
                    osd_img.draw_rectangle(x, y, w, h, color=(255, 255, 0, 255), thickness=2)

    def deinit(self):
        del self.kpu
        del self.ai2d
        self.tensors.clear()
        del self.tensors
        gc.collect()
        time.sleep_ms(50)


_face_det = None
_fc = 0

def face_det_thread():
    global sensor, osd_img, rgb888p_size, display_size, cur_state, _face_det, _fc
    kmodel_path = "/sdcard/examples/kmodel/face_detection_320.kmodel"
    anchors_path = "/sdcard/examples/utils/prior_data_320.bin"
    anchors = np.fromfile(anchors_path, dtype=np.float)
    anchors = anchors.reshape((4200, 4))
    _face_det = FaceDetectionApp(kmodel_path, model_input_size=[320, 320], anchors=anchors,
                                 confidence_threshold=0.5, nms_threshold=0.2,
                                 rgb888p_size=rgb888p_size, display_size=display_size, debug_mode=0)
    _face_det.config_preprocess()
    print("[baseline] face_det_thread begin")
    while cur_state == 2:
        with ScopedTiming("total", 2):
            _fc += 1
            if _fc <= 20 or _fc % 30 == 0:
                print("[baseline] fc=%d" % _fc)
            img_0 = sensor.snapshot(chn=CAM_CHN_ID_0)
            img_2 = sensor.snapshot(chn=CAM_CHN_ID_2)
            img_np = img_2.to_numpy_ref()
            res = _face_det.run(img_np)
            _face_det.draw_result(img_0, res)
            Display.show_image(img_0, 0, 0, Display.LAYER_OSD1)
        gc.collect()
    _face_det.deinit()
    print("[baseline] face_det_thread exited")


def disp_drv_flush_cb(disp_drv, area, color):
    global disp_img1, disp_img2
    if disp_drv.flush_is_last() == True:
        if disp_img1.virtaddr() == uctypes.addressof(color.__dereference__()):
            disp_img2.bytearray()[:] = bytearray(0)
            Display.show_image(disp_img1, layer=Display.LAYER_OSD2)
        else:
            disp_img1.bytearray()[:] = bytearray(0)
            Display.show_image(disp_img2, layer=Display.LAYER_OSD2)
        time.sleep(0.01)
    disp_drv.flush_ready()


def media_init():
    global sensor, osd_img, rgb888p_size, display_size
    Display.init(Display.ST7701, width=DISPLAY_WIDTH, height=DISPLAY_HEIGHT,
                 to_ide=False, osd_num=2)
    # CamerAi lcd.py 的 Sensor 配置：1280×960 原生 + 3 通道
    # （chn0 VGA预览/chn1 SXGAM拍照/chn2 XGA AI），对齐 hw/lcd.py:54-75
    from media.sensor import CAM_CHN_ID_1
    sensor = Sensor(width=1280, height=960, fps=30)
    sensor.reset()
    sensor.set_framesize(Sensor.VGA, chn=CAM_CHN_ID_0)
    sensor.set_pixformat(Sensor.RGB888, chn=CAM_CHN_ID_0)
    sensor.set_framesize(Sensor.SXGAM, chn=CAM_CHN_ID_1)
    sensor.set_pixformat(Sensor.RGB565, chn=CAM_CHN_ID_1)
    sensor.set_framesize(Sensor.XGA, chn=CAM_CHN_ID_2)
    sensor.set_pixformat(Sensor.RGBP888, chn=CAM_CHN_ID_2)
    MediaManager.init()
    sensor.run()


def lvgl_init():
    global disp_img1, disp_img2
    lv.init()
    disp_drv = lv.disp_create(DISPLAY_WIDTH, DISPLAY_HEIGHT)
    disp_drv.set_flush_cb(disp_drv_flush_cb)
    disp_drv.set_color_format(lv.COLOR_FORMAT.ARGB8888)
    disp_img1 = image.Image(display_size[0], display_size[1], image.BGRA8888)
    disp_img2 = image.Image(display_size[0], display_size[1], image.BGRA8888)
    disp_img1.clear()
    disp_img2.clear()
    disp_drv.set_draw_buffers(disp_img1.bytearray(), disp_img2.bytearray(),
                              disp_img1.size(), lv.DISP_RENDER_MODE.FULL)
    scr = lv.scr_act()
    scr.set_style_bg_opa(0, 0)


def main():
    global cur_state
    media_init()
    lvgl_init()
    import gc as _gc
    print("[baseline] init done, mem_free=%d, starting face_det thread" % _gc.mem_free())
    cur_state = 2
    time.sleep_ms(100)
    _thread.start_new_thread(face_det_thread, ())
    try:
        while True:
            time.sleep_ms(lv.task_handler())
    except BaseException as e:
        import sys
        sys.print_exception(e)
        cur_state = 0
        time.sleep_ms(100)
    cur_state = 0
    time.sleep_ms(200)
    print("[baseline] main exited, fc=%d" % _fc)


main()
