# core/gesture_ai.py — 手势检测与识别封装(单通道 + recognition 通用特征)
#
# 双 kmodel: hand_det.kmodel(手掌检测,512×512,9 anchors) +
#           recognition.kmodel(通用特征提取,224×224,512 维)
#
# 2026-08-07 v2：手势特征从 hand_reco(4 类 softmax) 换 recognition.kmodel
# (512 维通用特征)——4 维 softmax 分布区分"任意手势"能力弱,板端误匹配
# ("学一个手势,换手势也识别成功");512 维强特征同 object_classify 已验证
# (阈值 0.82 + margin 0.06)。
#
# 单通道(死机根治 2026-08-07,同 object_detect/body_detect):AI 直接吃 chn0
# VGA 显示帧推理,无 chn2 大帧 DMA 竞争。
#
# 镜像 core/object_ai.py 封装风格。不内置 draw_result——画框/标签由 app on_frame 负责。

import gc
import time

import nncase_runtime as nn
import ulab.numpy as np
import aicube
from libs.AIBase import AIBase
from libs.AI2D import Ai2d
from libs.PipeLine import ScopedTiming

# AI 通道分辨率 = chn2 XGA 1024x768(2026-08-12 对齐 face/object 硬件直出)。
# 单通道 chn0 packed 须 921KB 软件重排;chn2 RGBP888 planar 直出零重排。
# ⚠️ ISP chn2 不支持 VGA 小尺寸,配 XGA(坑#20)。
RGB888P_SIZE = [1024, 768]
DISPLAY_SIZE = [640, 480]

# kmodel 路径(匹配 demo 存放位置;recognition.kmodel 同 object_classify)
HAND_DET_KMPATH = "/sdcard/examples/kmodel/hand_det.kmodel"
GESTURE_RECO_KMPATH = "/sdcard/examples/kmodel/recognition.kmodel"

# 9 个 hardcode anchors(同 demo 实验9,不从 .bin 读)
HAND_ANCHORS = [26, 27, 53, 52, 75, 71, 80, 99, 106, 82,
                99, 134, 140, 113, 161, 172, 245, 276]

# 每帧最多提特征的手掌数(防手多/误检时 NPU 推理次数暴涨,2026-08-10 死机排查)
REG_MAX_HANDS = 4


def ALIGN_UP(x, align=16):
    return (x + align - 1) // align * align


def configure_ai2d_input(ai2d):
    """配置 ai2d 输入格式并返回 input_is_packed 标志。

    优先 ai2d packed RGB888 枚举(RGB888p_FMT,K230 中 p=packed,与
    Sensor.RGBP888 的 P=planar 命名相反),支持则 chn0 帧零拷贝直接喂;
    否则 NCHW(planar 语义)须软件重排(2026-08-07 全屏假框根因)。
    """
    input_is_packed = hasattr(nn.ai2d_format, "RGB888p_FMT")
    input_fmt = getattr(nn.ai2d_format, "RGB888p_FMT", nn.ai2d_format.NCHW_FMT)
    ai2d.set_ai2d_dtype(input_fmt, nn.ai2d_format.NCHW_FMT, np.uint8, np.uint8)
    return input_is_packed


class HandDetectionApp(AIBase):
    """手掌检测(hand_det.kmodel, anchor-based)。"""

    def __init__(self, kmodel_path, model_input_size, anchors,
                 confidence_threshold=0.2, nms_threshold=0.5,
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
        self.input_is_packed = configure_ai2d_input(self.ai2d)

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
                self.strides, 1,
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


class GestureFeatureApp(AIBase):
    """recognition.kmodel 手势特征提取(224×224, crop 手掌框, 512 维)。

    镜像 object_classify_ai.FeatureExtractionApp,但 _get_crop_param 适配
    hand_det 检测框格式 [cls, score, x1, y1, x2, y2](坐标索引 2-5)。
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
        """hand_det 框 [cls, score, x1, y1, x2, y2] → 方形放大 crop(同 demo 逻辑)。"""
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
            return results[0][0]  # 512 维通用特征(任意手势 ID 学习用)

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


class HandRecognition:
    """手势检测+特征提取组合:先检手掌再提 512 维特征,返回检测框+特征。

    任意手势 ID 学习链路:det_boxes + feats 等长列表,app 层 gesture_db
    余弦匹配(阈值 0.82)分配/识别 ID。
    """

    def __init__(self, hand_det_kmodel, rec_kmodel,
                 det_input_size=None, rec_input_size=None,
                 anchors=None,
                 confidence_threshold=0.2, nms_threshold=0.5,
                 strides=None, rgb888p_size=None, display_size=None, debug_mode=0):
        if det_input_size is None:
            det_input_size = [512, 512]
        if rec_input_size is None:
            rec_input_size = [224, 224]
        if anchors is None:
            anchors = HAND_ANCHORS
        if strides is None:
            strides = [8, 16, 32]
        if rgb888p_size is None:
            rgb888p_size = RGB888P_SIZE
        if display_size is None:
            display_size = DISPLAY_SIZE
        self.hand_det_kmodel = hand_det_kmodel
        self.rec_kmodel = rec_kmodel
        self.det_input_size = det_input_size
        self.rec_input_size = rec_input_size
        self.anchors = anchors
        self.confidence_threshold = confidence_threshold
        self.nms_threshold = nms_threshold
        self.strides = strides
        self.rgb888p_size = [ALIGN_UP(rgb888p_size[0], 16), rgb888p_size[1]]
        self.display_size = [ALIGN_UP(display_size[0], 16), display_size[1]]
        self.debug_mode = debug_mode

        self.hand_det = HandDetectionApp(
            self.hand_det_kmodel, model_input_size=self.det_input_size,
            anchors=self.anchors,
            confidence_threshold=self.confidence_threshold,
            nms_threshold=self.nms_threshold,
            strides=self.strides,
            rgb888p_size=self.rgb888p_size, display_size=self.display_size,
            debug_mode=0)
        # ⚠️ 双 kmodel 顺序根因(坑#19):feature kmodel 必须先加载,
        # 再 hand_det.config_preprocess(),否则破坏共享 NPU/AI2D 状态。
        self.feature = GestureFeatureApp(
            self.rec_kmodel, model_input_size=self.rec_input_size,
            rgb888p_size=self.rgb888p_size, display_size=self.display_size)
        # 透传子类 input_is_packed(两个子类同固件下结果一致),供 app 层单通道判定
        self.input_is_packed = self.hand_det.input_is_packed
        self.hand_det.config_preprocess()

    def run(self, img_np):
        """推理当前帧。返回 (hand_det_res, hand_feats)(等长,都是已过滤)。

        hand_det_res: 手掌检测框列表,每框 [cls, score, x1,y1,x2,y2](仅通过边界过滤的)。
        hand_feats: 每框 512 维特征 list(recognition.kmodel,同索引对应)。
        过滤:高度 < 0.1×rgb888p_h 剔除;边缘窄掌剔除(同 demo 逻辑)。
        """
        det_boxes = self.hand_det.run(img_np)
        hand_det_res = []
        hand_feats = []
        # 每帧最多提特征的手掌数(同 face_detect REG_MAX_FACES 截断):手多/误检
        # 多时防 feature 推理次数暴涨 → NPU 负载与温度失控(2026-08-10 死机排查)
        for det_box in det_boxes[:REG_MAX_HANDS]:
            x1, y1, x2, y2 = det_box[2], det_box[3], det_box[4], det_box[5]
            w, h = int(x2 - x1), int(y2 - y1)
            # 边界过滤(同 demo)
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
            self.feature.config_preprocess(det_box)
            feat = self.feature.run(img_np)
            hand_det_res.append(det_box)
            try:
                hand_feats.append(feat.tolist())  # ulab → list(512 维)
            except Exception:
                hand_feats.append(list(feat))
        return hand_det_res, hand_feats

    def deinit(self):
        try:
            self.hand_det.deinit()
        except Exception:
            pass
        try:
            self.feature.deinit()
        except Exception:
            pass
