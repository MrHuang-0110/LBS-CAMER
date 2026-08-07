# core/body_ai.py — 人体姿态识别封装(移植 demo 实验6 人体关键点检测)
#
# 单通道(死机根治 2026-08-07,同 object_detect):AI 直接吃 chn0 VGA 显示帧
# 推理,无 chn2 大帧 DMA 竞争。模型 yolov8n-pose.kmodel 输入 320×320,后处理
# 用 aidemo.person_kp_postprocess(C 加速)。绘制(骨架)在 app 层。
#
# run 返回 (boxes, kpses)(aidemo 定义,同实验6):
#   boxes: 人体框列表,每人 [l,t,r,b,score](rgb888p 坐标)
#   kpses: 关键点数组,每人 19 点 (x,y,s),前 17 点为标准人体关键点

import gc
import time

import nncase_runtime as nn
import ulab.numpy as np
import aidemo
from libs.AIBase import AIBase
from libs.AI2D import Ai2d
from libs.PipeLine import ScopedTiming

# AI 通道分辨率 = 显示通道分辨率(单通道:chn0 VGA 640x480 显示+推理)
RGB888P_SIZE = [640, 480]
DISPLAY_SIZE = [640, 480]

# kmodel 路径(匹配 demo 实验6 存放位置)
PERSON_KP_KMPATH = "/sdcard/examples/kmodel/yolov8n-pose.kmodel"

# 19 对骨骼连线(COCO 17 关键点编号,1 起)
SKELETON = [(16, 14), (14, 12), (17, 15), (15, 13), (12, 13), (6, 12), (7, 13),
            (6, 7), (6, 8), (7, 9), (8, 10), (9, 11), (2, 3), (1, 2), (1, 3),
            (2, 4), (3, 5), (4, 6), (5, 7)]
# 19 条骨骼线颜色(A,B,G,R,K230 draw_line 格式,同 demo)
LIMB_COLORS = [(255, 51, 153, 255), (255, 51, 153, 255), (255, 51, 153, 255),
               (255, 51, 153, 255), (255, 255, 51, 255), (255, 255, 51, 255),
               (255, 255, 51, 255), (255, 255, 128, 0), (255, 255, 128, 0),
               (255, 255, 128, 0), (255, 255, 128, 0), (255, 255, 128, 0),
               (255, 0, 255, 0), (255, 0, 255, 0), (255, 0, 255, 0),
               (255, 0, 255, 0), (255, 0, 255, 0), (255, 0, 255, 0),
               (255, 0, 255, 0)]
# 17 个关键点颜色(A,B,G,R,同 demo)
KPS_COLORS = [(255, 0, 255, 0), (255, 0, 255, 0), (255, 0, 255, 0),
              (255, 0, 255, 0), (255, 0, 255, 0), (255, 255, 128, 0),
              (255, 255, 128, 0), (255, 255, 128, 0), (255, 255, 128, 0),
              (255, 255, 128, 0), (255, 255, 128, 0), (255, 51, 153, 255),
              (255, 51, 153, 255), (255, 51, 153, 255), (255, 51, 153, 255),
              (255, 51, 153, 255), (255, 51, 153, 255)]


def ALIGN_UP(x, align=16):
    return (x + align - 1) // align * align


def configure_ai2d_input(ai2d):
    """配置 ai2d 输入格式并返回 input_is_packed 标志。

    优先 ai2d packed RGB888 枚举(RGB888p_FMT),支持则 chn0 帧零拷贝直接喂;
    否则 NCHW(planar 语义)须软件重排(同 object_ai 单通道方案)。
    """
    input_is_packed = hasattr(nn.ai2d_format, "RGB888p_FMT")
    input_fmt = getattr(nn.ai2d_format, "RGB888p_FMT", nn.ai2d_format.NCHW_FMT)
    ai2d.set_ai2d_dtype(input_fmt, nn.ai2d_format.NCHW_FMT, np.uint8, np.uint8)
    return input_is_packed


class PersonKeyPointApp(AIBase):
    """人体关键点检测(yolov8n-pose.kmodel, 320×320 输入, 17 关键点)。

    移植实验6 PersonKeyPointApp:pad+resize letterbox 预处理,
    aidemo.person_kp_postprocess 后处理(C 加速,输出 rgb888p 坐标)。
    不内置 draw_result(骨架绘制在 app on_frame)。
    """

    def __init__(self, kmodel_path, model_input_size,
                 confidence_threshold=0.2, nms_threshold=0.5,
                 rgb888p_size=None, display_size=None, debug_mode=0):
        if rgb888p_size is None:
            rgb888p_size = RGB888P_SIZE
        if display_size is None:
            display_size = DISPLAY_SIZE
        super().__init__(kmodel_path, model_input_size, rgb888p_size, debug_mode)
        self.kmodel_path = kmodel_path
        self.model_input_size = model_input_size
        self.confidence_threshold = confidence_threshold
        self.nms_threshold = nms_threshold
        self.rgb888p_size = [ALIGN_UP(rgb888p_size[0], 16), rgb888p_size[1]]
        self.display_size = [ALIGN_UP(display_size[0], 16), display_size[1]]
        self.debug_mode = debug_mode
        self.ai2d = Ai2d(debug_mode)
        self.input_is_packed = configure_ai2d_input(self.ai2d)

    def config_preprocess(self, input_image_size=None):
        gc.collect()
        with ScopedTiming("set preprocess config", self.debug_mode > 0):
            ai2d_input_size = input_image_size if input_image_size else self.rgb888p_size
            top, bottom, left, right = self._get_padding_param()
            self.ai2d.pad([0, 0, 0, 0, top, bottom, left, right], 0, [0, 0, 0])
            self.ai2d.resize(nn.interp_method.tf_bilinear, nn.interp_mode.half_pixel)
            self.ai2d.build([1, 3, ai2d_input_size[1], ai2d_input_size[0]],
                            [1, 3, self.model_input_size[1], self.model_input_size[0]])

    def _get_padding_param(self):
        dst_w = self.model_input_size[0]
        dst_h = self.model_input_size[1]
        input_width = self.rgb888p_size[0]
        input_high = self.rgb888p_size[1]
        ratio_w = dst_w / input_width
        ratio_h = dst_h / input_high
        if ratio_w < ratio_h:
            ratio = ratio_w
        else:
            ratio = ratio_h
        new_w = int(ratio * input_width)
        new_h = int(ratio * input_high)
        dw = (dst_w - new_w) / 2
        dh = (dst_h - new_h) / 2
        top = int(round(dh - 0.1))
        bottom = int(round(dh + 0.1))
        left = int(round(dw - 0.1))
        right = int(round(dw - 0.1))
        return top, bottom, left, right

    def postprocess(self, results):
        with ScopedTiming("postprocess", self.debug_mode > 0):
            return aidemo.person_kp_postprocess(
                results[0], [self.rgb888p_size[1], self.rgb888p_size[0]],
                self.model_input_size, self.confidence_threshold, self.nms_threshold)

    def deinit(self):
        try:
            del self.kpu
        except Exception:
            pass
        try:
            del self.ai2d
        except Exception:
            pass
        try:
            self.tensors.clear()
            del self.tensors
        except Exception:
            pass
        gc.collect()
        time.sleep_ms(50)
