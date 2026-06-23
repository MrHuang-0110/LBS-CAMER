# core/face_ai.py — reusable face AI helpers for CamerAi scripts.
#
# Phase 1 exposes detection only. Registration/feature matching are added in
# later phases after the template-based detection path is board-validated.

import gc
import time

import nncase_runtime as nn
import ulab.numpy as np
import aidemo
from libs.AIBase import AIBase
from libs.AI2D import Ai2d
from libs.Utils import ScopedTiming, letterbox_pad_param

RGB888P_SIZE = [1024, 768]
DISPLAY_SIZE = [640, 480]

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
    """hex 0xRRGGBB → K230 draw_line/draw_rectangle color tuple.

    On this board, drawing on Sensor.RGB888 images expects tuple order
    (A, B, G, R), not RGBA.
    """
    r = (hex_color >> 16) & 0xFF
    g = (hex_color >> 8) & 0xFF
    b = hex_color & 0xFF
    return (0xFF, b, g, r)


class FaceDetectionApp(AIBase):
    def __init__(self, kmodel_path, model_input_size, anchors,
                 confidence_threshold=0.5, nms_threshold=0.2,
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
            top, bottom, left, right, _ = letterbox_pad_param(
                self.rgb888p_size, self.model_input_size)
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
                return [], []
            return post_ret[0], post_ret[1]

    def draw_result(self, osd_img, dets, recognition_results=None):
        with ScopedTiming("display_draw", self.debug_mode > 0):
            rec_map = {}
            if recognition_results:
                for det_idx, mid in recognition_results:
                    rec_map[det_idx] = mid
            if dets:
                for i, det in enumerate(dets):
                    x, y, w, h = map(lambda v: int(round(v, 0)), det[:4])
                    x = x * self.display_size[0] // self.rgb888p_size[0]
                    y = y * self.display_size[1] // self.rgb888p_size[1]
                    w = w * self.display_size[0] // self.rgb888p_size[0]
                    h = h * self.display_size[1] // self.rgb888p_size[1]
                    matched_id = rec_map.get(i)
                    color_hex = BOX_COLORS.get(matched_id, BOX_UNKNOWN) if matched_id else BOX_UNKNOWN
                    color = _draw_color(color_hex)
                    osd_img.draw_rectangle(x, y, w, h, color=color, thickness=2)
                    if matched_id is not None:
                        osd_img.draw_string_advanced(x + 2, y + 2, 16,
                                                     "ID%d" % matched_id, color=color)

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
