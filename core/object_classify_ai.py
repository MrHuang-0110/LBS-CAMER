# core/object_classify_ai.py — 物体分类:YOLOv8n 检测 + recognition.kmodel 特征
#
# 双 kmodel: yolov8n_320.kmodel(COCO80 物体检测,320×320,复用 object_ai.ObjectDetectionApp)
#           + recognition.kmodel(通用特征提取,224×224,复刻 body_ai.PersonRecognitionApp)
#
# 检测输出 [l,t,r,b,score,class_id](object_ai 格式,rgb888p 坐标,float);
# 特征用 crop 检测框 + resize 224×224(无对齐,同 body_ai)。
# ObjectClassifyRecognition.run 返回 (det_boxes, features) 等长列表(≤MAX_DET_BOXES,
# 已按检测顺序截断,控每帧特征提取量)。
#
# 板端专用(导入 nncase/ulab),无 host 单测。

import gc
import time

import nncase_runtime as nn
import ulab.numpy as np
from libs.AIBase import AIBase
from libs.AI2D import Ai2d
from libs.PipeLine import ScopedTiming
from core.object_ai import ObjectDetectionApp, COCO_LABELS, configure_ai2d_input

# 推理帧分辨率(单通道 2026-08-07:AI 直接吃 chn0 显示帧 VGA 640x480,无独立
# 推理通道;历史 chn2 XGA 2.25MB/帧硬件 DMA 与显示 DMA 竞争累积致几分钟死机,
# 已随 object_classify 单通道化根治;det 320 + rec 224 用 VGA 足够)
RGB888P_SIZE = [640, 480]
DISPLAY_SIZE = [640, 480]

# kmodel 路径(匹配 demo 存放位置;同 object_ai / body_ai)
OBJ_DET_KMPATH = "/sdcard/examples/kmodel/yolov8n_320.kmodel"
OBJ_RECO_KMPATH = "/sdcard/examples/kmodel/recognition.kmodel"

# 每帧最多提特征的检测框数(控推理量:1 次 yolov8n + N 次 recognition)
MAX_DET_BOXES = 5


def ALIGN_UP(x, align=16):
    return (x + align - 1) // align * align


class FeatureExtractionApp(AIBase):
    """recognition.kmodel 特征提取(224×224, crop 检测框, 无对齐)。

    镜像 body_ai.PersonRecognitionApp,区别:config_preprocess 接收 (x1,y1,x2,y2)
    四元组(object_ai 检测框 [l,t,r,b] 格式,非 person anchor 格式 det_box[2:6])。
    """

    def __init__(self, kmodel_path, model_input_size=None, rgb888p_size=None,
                 display_size=None, debug_mode=0):
        if model_input_size is None:
            model_input_size = [224, 224]
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
        self.input_is_packed = configure_ai2d_input(self.ai2d)

    def config_preprocess(self, det_box, input_image_size=None):
        gc.collect()
        with ScopedTiming("set preprocess config", self.debug_mode > 0):
            ai2d_input_size = input_image_size if input_image_size else self.rgb888p_size
            self.crop_params = self._get_crop_param(det_box)
            self.ai2d.crop(self.crop_params[0], self.crop_params[1],
                           self.crop_params[2], self.crop_params[3])
            self.ai2d.resize(nn.interp_method.tf_bilinear, nn.interp_mode.half_pixel)
            self.ai2d.build([1, 3, ai2d_input_size[1], ai2d_input_size[0]],
                            [1, 3, self.model_input_size[1], self.model_input_size[0]])

    def _get_crop_param(self, det_box):
        x1, y1, x2, y2 = det_box[0], det_box[1], det_box[2], det_box[3]
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


class ObjectClassifyRecognition:
    """物体检测+特征提取组合:先 YOLOv8n 检任意物体,再对每框提特征。

    返回 (det_boxes, features):等长列表(≤max_boxes),按检测顺序对应。
    det_boxes 元素为 [l,t,r,b,score,class_id](rgb888p 坐标);features 为特征向量。
    """

    def __init__(self, det_kmodel=OBJ_DET_KMPATH, rec_kmodel=OBJ_RECO_KMPATH,
                 det_input_size=None, rec_input_size=None,
                 max_boxes=MAX_DET_BOXES, confidence_threshold=0.5, nms_threshold=0.2,
                 rgb888p_size=None, display_size=None, debug_mode=0):
        if det_input_size is None:
            det_input_size = [320, 320]
        if rec_input_size is None:
            rec_input_size = [224, 224]
        if rgb888p_size is None:
            rgb888p_size = RGB888P_SIZE
        if display_size is None:
            display_size = DISPLAY_SIZE
        self.det_input_size = det_input_size
        self.rec_input_size = rec_input_size
        self.max_boxes = max_boxes
        self.rgb888p_size = [ALIGN_UP(rgb888p_size[0], 16), rgb888p_size[1]]
        self.display_size = [ALIGN_UP(display_size[0], 16), display_size[1]]
        self.debug_mode = debug_mode

        # ⚠️ 双 kmodel 顺序根因(坑#19,同 face/body/gesture_detect):
        # rec kmodel 必须在 det.config_preprocess() 之前加载,否则破坏共享 NPU/AI2D 状态。
        self.feature = FeatureExtractionApp(
            rec_kmodel, model_input_size=self.rec_input_size,
            rgb888p_size=self.rgb888p_size, display_size=self.display_size)
        self.detector = ObjectDetectionApp(
            det_kmodel, labels=COCO_LABELS, model_input_size=self.det_input_size,
            confidence_threshold=confidence_threshold, nms_threshold=nms_threshold,
            rgb888p_size=self.rgb888p_size, display_size=self.display_size,
            debug_mode=0)
        self.detector.config_preprocess()
        # 单通道(死机根治 2026-08-07):AI 吃 chn0 显示帧;det 与 feature 共用
        # 同一输入格式(packed 零拷贝 or planar 重排),app 按此分支。
        self.input_is_packed = self.detector.input_is_packed

    def run(self, img_np):
        """推理当前帧。返回 (det_res, feat_res) 等长(≤max_boxes)。

        det_res: 物体检测框列表,每框 [l,t,r,b,score,class_id](rgb888p 坐标)。
        feat_res: [feature_vec, ...] 每个物体的特征向量(同索引对应)。
        过滤:过小框(<2px)跳过(避提特征崩)。
        """
        det_boxes = self.detector.run(img_np)
        det_res = []
        feat_res = []
        try:
            n = len(det_boxes)
        except Exception:
            n = 0
        for i in range(n):
            if i >= self.max_boxes:
                break
            d = det_boxes[i]
            l, t, r, b = int(d[0]), int(d[1]), int(d[2]), int(d[3])
            if r - l < 2 or b - t < 2:
                continue  # 过小框跳过
            self.feature.config_preprocess((l, t, r, b))
            feature = self.feature.run(img_np)
            det_res.append(d)
            feat_res.append(feature)
        return det_res, feat_res

    def deinit(self):
        try:
            self.detector.deinit()
        except Exception:
            pass
        try:
            self.feature.deinit()
        except Exception:
            pass
