# core/object_ai.py — YOLOv8n COCO80 物体检测封装(AIBase 子类)
#
# 镜像 face_ai.FaceDetectionApp 封装风格 + 移植 demo/AI类实验例程/实验15 后处理。
# - kmodel: /sdcard/examples/kmodel/yolov8n_320.kmodel,输入 [320,320]
# - AI2D: resize(rgb888p -> 320x320,不做 letterbox,同 demo)
# - postprocess: YOLOv8 输出 [N,84] -> argmax 取类 -> conf 阈值 -> 纯 Python NMS
#   返回 [[l,t,r,b,score,class_id], ...](rgb888p 坐标,float)
# - 不内置 draw_result(画框在 app on_frame 按注册槽上色)
#
# 纯 Python NMS(无 aidemo C 加速):板端帧率可能低于 face_detect,每帧 gc.collect。

import gc
import time

import nncase_runtime as nn
import ulab.numpy as np
from libs.AIBase import AIBase
from libs.AI2D import Ai2d
from libs.PipeLine import ScopedTiming

# 推理帧分辨率(单通道 2026-08-07:AI 直接吃 chn0 显示帧 VGA 640x480,无独立
# 推理通道;历史 chn2 XGA 2.25MB/帧硬件 DMA 与显示 DMA 竞争累积致几分钟死机,
# 已随 object_detect 单通道化根治;det 输入 320x320 用 VGA 足够)
RGB888P_SIZE = [640, 480]
DISPLAY_SIZE = [640, 480]

# COCO 80 类英文标签(从 demo 实验15 拷贝)
COCO_LABELS = [
    "person", "bicycle", "car", "motorcycle", "airplane", "bus", "train", "truck",
    "boat", "traffic light", "fire hydrant", "stop sign", "parking meter", "bench",
    "bird", "cat", "dog", "horse", "sheep", "cow", "elephant", "bear", "zebra",
    "giraffe", "backpack", "umbrella", "handbag", "tie", "suitcase", "frisbee",
    "skis", "snowboard", "sports ball", "kite", "baseball bat", "baseball glove",
    "skateboard", "surfboard", "tennis racket", "bottle", "wine glass", "cup",
    "fork", "knife", "spoon", "bowl", "banana", "apple", "sandwich", "orange",
    "broccoli", "carrot", "hot dog", "pizza", "donut", "cake", "chair", "couch",
    "potted plant", "bed", "dining table", "toilet", "tv", "laptop", "mouse",
    "remote", "keyboard", "cell phone", "microwave", "oven", "toaster", "sink",
    "refrigerator", "book", "clock", "vase", "scissors", "teddy bear",
    "hair drier", "toothbrush",
]


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


class ObjectDetectionApp(AIBase):
    def __init__(self, kmodel_path, labels=None, model_input_size=None,
                 max_boxes_num=50, confidence_threshold=0.2, nms_threshold=0.2,
                 rgb888p_size=None, display_size=None, debug_mode=0):
        if labels is None:
            labels = COCO_LABELS
        if model_input_size is None:
            model_input_size = [320, 320]
        if rgb888p_size is None:
            rgb888p_size = RGB888P_SIZE
        if display_size is None:
            display_size = DISPLAY_SIZE
        super().__init__(kmodel_path, model_input_size, rgb888p_size, debug_mode)
        self.kmodel_path = kmodel_path
        self.labels = labels
        self.model_input_size = model_input_size
        self.confidence_threshold = confidence_threshold
        self.nms_threshold = nms_threshold
        self.max_boxes_num = max_boxes_num
        self.rgb888p_size = [ALIGN_UP(rgb888p_size[0], 16), rgb888p_size[1]]
        self.display_size = [ALIGN_UP(display_size[0], 16), display_size[1]]
        self.debug_mode = debug_mode
        self.x_factor = float(self.rgb888p_size[0]) / self.model_input_size[0]
        self.y_factor = float(self.rgb888p_size[1]) / self.model_input_size[1]
        self.ai2d = Ai2d(debug_mode)
        self.input_is_packed = configure_ai2d_input(self.ai2d)

    def config_preprocess(self, input_image_size=None):
        gc.collect()
        with ScopedTiming("set preprocess config", self.debug_mode > 0):
            ai2d_input_size = input_image_size if input_image_size else self.rgb888p_size
            self.ai2d.resize(nn.interp_method.tf_bilinear, nn.interp_mode.half_pixel)
            self.ai2d.build([1, 3, ai2d_input_size[1], ai2d_input_size[0]],
                            [1, 3, self.model_input_size[1], self.model_input_size[0]])

    def postprocess(self, results):
        with ScopedTiming("postprocess", self.debug_mode > 0):
            result = results[0]
            result = result.reshape((result.shape[0] * result.shape[1], result.shape[2]))
            output_data = result.transpose()
            boxes_ori = output_data[:, 0:4]
            scores_ori = output_data[:, 4:]
            confs_ori = np.max(scores_ori, axis=-1)
            inds_ori = np.argmax(scores_ori, axis=-1)

            # 向量化预算所有候选的 lbrt(8400 行一次性算),避免在 Python 循环里逐个做浮点。
            # ulab 不支持 fancy indexing(arr[mask]),故仍用 Python 循环筛选,但循环体
            # 只做"过阈则取预算值"的轻量取值,不再逐个算 xywh->lbrt 浮点。
            # ⚠️ ulab ndarray 无 .astype() ,int 转换在取值时用 int() 完成。
            xs = boxes_ori[:, 0]
            ys = boxes_ori[:, 1]
            ws = boxes_ori[:, 2]
            hs = boxes_ori[:, 3]
            lefts = (xs - 0.5 * ws) * self.x_factor
            tops = (ys - 0.5 * hs) * self.y_factor
            rights = (xs + 0.5 * ws) * self.x_factor
            bottoms = (ys + 0.5 * hs) * self.y_factor

            boxes, scores, inds = [], [], []
            n = len(confs_ori)
            for i in range(n):
                if confs_ori[i] > self.confidence_threshold:
                    scores.append(confs_ori[i])
                    inds.append(inds_ori[i])
                    boxes.append([int(lefts[i]), int(tops[i]),
                                  int(rights[i]), int(bottoms[i])])
            if len(boxes) == 0:
                return []
            boxes = np.array(boxes)
            scores = np.array(scores)
            inds = np.array(inds)
            keep = self.nms(boxes, scores, self.nms_threshold)
            dets = np.concatenate(
                (boxes, scores.reshape((len(boxes), 1)), inds.reshape((len(boxes), 1))),
                axis=1)
            dets_out = []
            for keep_i in keep:
                dets_out.append(dets[keep_i])
            dets_out = np.array(dets_out)
            dets_out = dets_out[:self.max_boxes_num, :]
            return dets_out

    def nms(self, boxes, scores, thresh):
        """纯 Python NMS(移植 demo 实验15)。返回 keep 索引列表。"""
        x1, y1, x2, y2 = boxes[:, 0], boxes[:, 1], boxes[:, 2], boxes[:, 3]
        areas = (x2 - x1 + 1) * (y2 - y1 + 1)
        order = np.argsort(scores, axis=0)[::-1]
        keep = []
        while order.size > 0:
            i = order[0]
            keep.append(i)
            new_x1, new_y1, new_x2, new_y2 = [], [], [], []
            new_areas = []
            for order_i in order:
                new_x1.append(x1[order_i])
                new_x2.append(x2[order_i])
                new_y1.append(y1[order_i])
                new_y2.append(y2[order_i])
                new_areas.append(areas[order_i])
            new_x1 = np.array(new_x1)
            new_x2 = np.array(new_x2)
            new_y1 = np.array(new_y1)
            new_y2 = np.array(new_y2)
            xx1 = np.maximum(x1[i], new_x1)
            yy1 = np.maximum(y1[i], new_y1)
            xx2 = np.minimum(x2[i], new_x2)
            yy2 = np.minimum(y2[i], new_y2)
            w = np.maximum(0.0, xx2 - xx1 + 1)
            h = np.maximum(0.0, yy2 - yy1 + 1)
            inter = w * h
            new_areas = np.array(new_areas)
            ovr = inter / (areas[i] + new_areas - inter)
            new_order = []
            for ovr_i, ind in enumerate(ovr):
                if ind < thresh:
                    new_order.append(order[ovr_i])
            order = np.array(new_order, dtype=np.uint8)
        return keep

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
