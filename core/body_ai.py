# core/body_ai.py — 人体检测与特征提取封装(移植 demo 实验5 + 实验20)
#
# 双 kmodel: person_detect_yolov5n.kmodel(人体检测,640×640,9 anchors,YOLOv5n) +
#           recognition.kmodel(通用特征提取,224×224,实验20)
#
# 检测用 aicube.anchorbasedet_post_process(同 gesture_ai 的 HandDetectionApp);
# 特征用 crop 检测框 + resize 224×224(无关键点对齐,人体无 umeyama)。
# PersonRecognition.run 返回 (det_boxes, features) 等长过滤后列表(避 zip 错位)。

import gc
import time

import nncase_runtime as nn
import ulab.numpy as np
import aicube
from libs.AIBase import AIBase
from libs.AI2D import Ai2d
from libs.PipeLine import ScopedTiming

# AI 通道分辨率(对齐 face_detect 的 chn2 XGA RGBP888)
RGB888P_SIZE = [1024, 768]
DISPLAY_SIZE = [640, 480]

# kmodel 路径(匹配 demo 实验5/实验20 存放位置)
PERSON_DET_KMPATH = "/sdcard/examples/kmodel/person_detect_yolov5n.kmodel"
PERSON_RECO_KMPATH = "/sdcard/examples/kmodel/recognition.kmodel"

# 9 个 hardcode anchors(同 demo 实验5 person_detect_yolov5n)
PERSON_ANCHORS = [10, 13, 16, 30, 33, 23, 30, 61, 62, 45,
                  59, 119, 116, 90, 156, 198, 373, 326]

# 1 类标签(同 demo 实验5)
PERSON_LABELS = ["person"]

# 4 槽颜色(同 face_detect BOX_COLORS)
BOX_COLORS = {
    1: 0x44CC44,
    2: 0x4488FF,
    3: 0xFF8844,
    4: 0xCC44FF,
}
BOX_UNKNOWN = 0xFFFFFF


def ALIGN_UP(x, align=16):
    return (x + align - 1) // align * align


def _draw_color(hex_color):
    """hex 0xRRGGBB → K230 draw_rectangle color tuple (A, B, G, R)。"""
    r = (hex_color >> 16) & 0xFF
    g = (hex_color >> 8) & 0xFF
    b = hex_color & 0xFF
    return (0xFF, b, g, r)


class PersonDetectionApp(AIBase):
    """人体检测(person_detect_yolov5n.kmodel, anchor-based YOLOv5n)。"""

    def __init__(self, kmodel_path, model_input_size, anchors,
                 confidence_threshold=0.2, nms_threshold=0.6,
                 strides=None, rgb888p_size=None, display_size=None, debug_mode=0):
        if strides is None:
            strides = [8, 16, 32]
        if rgb888p_size is None:
            rgb888p_size = RGB888P_SIZE
        if display_size is None:
            display_size = DISPLAY_SIZE
        super().__init__(kmodel_path, model_input_size, rgb888p_size, debug_mode)
        self.kmodel_path = kmodel_path
        self.model_input_size = model_input_size
        self.confidence_threshold = confidence_threshold
        self.nms_threshold = nms_threshold
        self.anchors = anchors
        self.strides = strides
        self.rgb888p_size = [ALIGN_UP(rgb888p_size[0], 16), rgb888p_size[1]]
        self.display_size = [ALIGN_UP(display_size[0], 16), display_size[1]]
        self.debug_mode = debug_mode
        self.ai2d = Ai2d(debug_mode)
        self.ai2d.set_ai2d_dtype(nn.ai2d_format.NCHW_FMT, nn.ai2d_format.NCHW_FMT,
                                 np.uint8, np.uint8)

    def config_preprocess(self, input_image_size=None):
        gc.collect()
        with ScopedTiming("set preprocess config", self.debug_mode > 0):
            ai2d_input_size = input_image_size if input_image_size else self.rgb888p_size
            top, bottom, left, right = self._get_padding_param()
            self.ai2d.pad([0, 0, 0, 0, top, bottom, left, right], 0, [114, 114, 114])
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
        right = int(round(dw + 0.1))
        return top, bottom, left, right

    def postprocess(self, results):
        with ScopedTiming("postprocess", self.debug_mode > 0):
            dets = aicube.anchorbasedet_post_process(
                results[0], results[1], results[2],
                self.model_input_size, self.rgb888p_size,
                self.strides, len(PERSON_LABELS),
                self.confidence_threshold, self.nms_threshold,
                self.anchors, False)
            return dets

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


class PersonRecognitionApp(AIBase):
    """人体特征提取(recognition.kmodel, 224×224 输入, crop 检测框,无对齐)。

    移植实验20 SelfLearningApp 的预处理(crop+resize)与 postprocess(返回特征向量)。
    无关键点→不做 umeyama 仿射对齐(区别于 FaceRegistrationApp)。
    """

    def __init__(self, kmodel_path, model_input_size,
                 rgb888p_size=None, display_size=None, debug_mode=0):
        if rgb888p_size is None:
            rgb888p_size = RGB888P_SIZE
        if display_size is None:
            display_size = DISPLAY_SIZE
        super().__init__(kmodel_path, model_input_size, rgb888p_size, debug_mode)
        self.kmodel_path = kmodel_path
        self.model_input_size = model_input_size
        self.rgb888p_size = [ALIGN_UP(rgb888p_size[0], 16), rgb888p_size[1]]
        self.display_size = [ALIGN_UP(display_size[0], 16), display_size[1]]
        self.crop_params = []
        self.debug_mode = debug_mode
        self.ai2d = Ai2d(debug_mode)
        self.ai2d.set_ai2d_dtype(nn.ai2d_format.NCHW_FMT, nn.ai2d_format.NCHW_FMT,
                                 np.uint8, np.uint8)

    def config_preprocess(self, det, input_image_size=None):
        gc.collect()
        with ScopedTiming("set preprocess config", self.debug_mode > 0):
            ai2d_input_size = input_image_size if input_image_size else self.rgb888p_size
            self.crop_params = self._get_crop_param(det)
            self.ai2d.crop(self.crop_params[0], self.crop_params[1],
                           self.crop_params[2], self.crop_params[3])
            self.ai2d.resize(nn.interp_method.tf_bilinear, nn.interp_mode.half_pixel)
            self.ai2d.build([1, 3, ai2d_input_size[1], ai2d_input_size[0]],
                            [1, 3, self.model_input_size[1], self.model_input_size[0]])

    def _get_crop_param(self, det_box):
        x1, y1, x2, y2 = det_box[2], det_box[3], det_box[4], det_box[5]
        w, h = int(x2 - x1), int(y2 - y1)
        length = max(w, h) / 2
        cx = (x1 + x2) / 2
        cy = (y1 + y2) / 2
        ratio_num = 1.26 * length
        x1_kp = int(max(0, cx - ratio_num))
        y1_kp = int(max(0, cy - ratio_num))
        x2_kp = int(min(self.rgb888p_size[0] - 1, cx + ratio_num))
        y2_kp = int(min(self.rgb888p_size[1] - 1, cy + ratio_num))
        w_kp = int(x2_kp - x1_kp + 1)
        h_kp = int(y2_kp - y1_kp + 1)
        return [x1_kp, y1_kp, w_kp, h_kp]

    def postprocess(self, results):
        with ScopedTiming("postprocess", self.debug_mode > 0):
            return results[0][0]

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


class PersonRecognition:
    """人体检测+特征提取组合:先检人体再提特征,返回检测框+特征(等长,已过滤)。"""

    def __init__(self, person_det_kmodel, person_rec_kmodel,
                 det_input_size=None, rec_input_size=None,
                 anchors=None,
                 confidence_threshold=0.2, nms_threshold=0.6,
                 strides=None, rgb888p_size=None, display_size=None, debug_mode=0):
        if det_input_size is None:
            det_input_size = [640, 640]
        if rec_input_size is None:
            rec_input_size = [224, 224]
        if anchors is None:
            anchors = PERSON_ANCHORS
        if strides is None:
            strides = [8, 16, 32]
        if rgb888p_size is None:
            rgb888p_size = RGB888P_SIZE
        if display_size is None:
            display_size = DISPLAY_SIZE
        self.person_det_kmodel = person_det_kmodel
        self.person_rec_kmodel = person_rec_kmodel
        self.det_input_size = det_input_size
        self.rec_input_size = rec_input_size
        self.anchors = anchors
        self.confidence_threshold = confidence_threshold
        self.nms_threshold = nms_threshold
        self.strides = strides
        self.rgb888p_size = [ALIGN_UP(rgb888p_size[0], 16), rgb888p_size[1]]
        self.display_size = [ALIGN_UP(display_size[0], 16), display_size[1]]
        self.debug_mode = debug_mode

        # ⚠️ 双 kmodel 顺序根因(坑#19,同 face_detect/gesture_detect):
        # rec kmodel 必须在 det.config_preprocess() 之前加载。
        self.person_rec = PersonRecognitionApp(
            self.person_rec_kmodel, model_input_size=self.rec_input_size,
            rgb888p_size=self.rgb888p_size, display_size=self.display_size)
        self.person_det = PersonDetectionApp(
            self.person_det_kmodel, model_input_size=self.det_input_size,
            anchors=self.anchors,
            confidence_threshold=self.confidence_threshold,
            nms_threshold=self.nms_threshold,
            strides=self.strides,
            rgb888p_size=self.rgb888p_size, display_size=self.display_size,
            debug_mode=0)
        self.person_det.config_preprocess()

    def run(self, img_np):
        """推理当前帧。返回 (det_res, feat_res)(等长,都是已过滤)。

        det_res: 人体检测框列表,每框 [..., x1,y1,x2,y2, ...](仅通过边界过滤的)。
        feat_res: [feature_vec, ...] 每个人体的特征向量(同索引对应)。
        过滤:高度 < 0.1×rgb888p_h 剔除;边缘窄框剔除(同 demo 实验5 逻辑)。
        """
        det_boxes = self.person_det.run(img_np)
        det_res = []
        feat_res = []
        for det_box in det_boxes:
            x1, y1, x2, y2 = det_box[2], det_box[3], det_box[4], det_box[5]
            w, h = int(x2 - x1), int(y2 - y1)
            # 边界过滤(同 demo 实验5)
            if h < (0.1 * self.rgb888p_size[1]):
                continue
            if (w < (0.25 * self.rgb888p_size[0])
                    and ((x1 < (0.03 * self.rgb888p_size[0]))
                         or (x2 > (0.97 * self.rgb888p_size[0])))):
                continue
            if (w < (0.15 * self.rgb888p_size[0])
                    and ((x1 < (0.01 * self.rgb888p_size[0]))
                         or (x2 > (0.99 * self.rgb888p_size[0])))):
                continue
            self.person_rec.config_preprocess(det_box)
            feature = self.person_rec.run(img_np)
            det_res.append(det_box)
            feat_res.append(feature)
        return det_res, feat_res

    def deinit(self):
        try:
            self.person_det.deinit()
        except Exception:
            pass
        try:
            self.person_rec.deinit()
        except Exception:
            pass
