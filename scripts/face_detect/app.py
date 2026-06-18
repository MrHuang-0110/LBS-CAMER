# scripts/face_detect/app.py — minimal official-style face detect baseline
#
# This file is intentionally small and mirrors test_face_baseline_camerai_sensor.py.
# Purpose: prove the official AI+LVGL two-thread baseline is stable when launched
# through CamerAi reset-switch main.py. Business features are added back only after
# this baseline passes board validation.

import os
import sys
import time
import gc
import math
import _thread
import uctypes

import lvgl as lv
import image
import nncase_runtime as nn
import ulab.numpy as np
import aidemo
from media.sensor import Sensor, CAM_CHN_ID_0, CAM_CHN_ID_1, CAM_CHN_ID_2
from media.display import Display
from media.media import MediaManager
from libs.AIBase import AIBase
from libs.AI2D import Ai2d
from libs.Utils import ScopedTiming, letterbox_pad_param


DISPLAY_WIDTH = 640
DISPLAY_HEIGHT = 480
RGB888P_SIZE = [1024, 768]
DISPLAY_SIZE = [640, 480]

# 十字架（两条相交线，绿色，无 gap）
CROSSHAIR_ARM = 20
CROSSHAIR_COLOR = 0x44CC44  # 绿色（hex，_draw_color 转 K230 draw 元组）

# 人脸框颜色（4个ID）+ 未注册（hex 整数，draw 时拆 RGBA）
BOX_COLORS = {
    1: 0x44CC44,   # ID1 绿色
    2: 0x4488FF,   # ID2 蓝色
    3: 0xFF8844,   # ID3 橙色
    4: 0xCC44FF,   # ID4 紫色
}
BOX_UNKNOWN = 0xFFFFFF  # 未注册白色


def _draw_color(hex_color):
    """hex 0xRRGGBB → K230 draw_line/draw_rectangle 颜色元组。

    板端验证：在 Sensor.RGB888 的 img 上 draw_line/draw_rectangle 的 color
    元组按 (A, B, G, R) 解释，而非 (R,G,B,A)。曾用 (0,255,0,255) 画绿色却
    显示紫色（G 落到 B 位、255 落到 R 位→品红）。故 hex 0xRRGGBB 须输出
    (0xFF, B, G, R)。白色不变（与通道顺序无关）。
    """
    r = (hex_color >> 16) & 0xFF
    g = (hex_color >> 8) & 0xFF
    b = hex_color & 0xFF
    return (0xFF, b, g, r)


RECOG_THRESHOLD = 0.75  # cosine 相似度阈值（官方 main2.py 默认，注册后板端调优）


def database_search(feature, db_features, threshold=RECOG_THRESHOLD):
    """当前人脸特征与特征库余弦比对，返回匹配 slot_id 或 None。

    对齐官方 main2.py:305 database_search。512 维特征（mobile kmodel）。
    db_features: {slot_id: np_array}（启动期主线程读入，AI 线程只读，无锁安全）。
    空库 / 低于阈值 / 坏特征 → None。
    """
    if not db_features:
        return None
    try:
        feat_norm = np.linalg.norm(feature)
        if feat_norm == 0:
            return None
        feature = feature / feat_norm
    except Exception:
        return None
    best_id = None
    best_score = 0.0
    for slot_id, db_feat in db_features.items():
        try:
            norm = np.linalg.norm(db_feat)
            if norm == 0:
                continue
            db_n = db_feat / norm
            score = np.dot(feature, db_n) / 2 + 0.5
        except Exception:
            continue
        if score > best_score:
            best_score = score
            best_id = slot_id
    if best_score < threshold:
        return None
    return best_id

sensor = None
disp_img1 = None
disp_img2 = None
face_det = None
face_reg = None
running = False
fc = 0
db_features = {}  # 人脸特征库 {slot_id: np_array}（Step 3 读入，Step 5 用于识别）
id_registry = None  # Step 7: K2 注册控制器（run() 初始化，AI 线程 try_register）
_rt = None  # Step 7: AI 线程访问 runtime.buzzer（face_det_thread 是模块级函数）


def ALIGN_UP(x, align=16):
    return (x + align - 1) // align * align


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
                return [], []
            return post_ret[0], post_ret[1]   # boxes, landms（对齐官方 main2.py:81）

    def draw_result(self, osd_img, dets, recognition_results=None):
        with ScopedTiming("display_draw", self.debug_mode > 0):
            # recognition_results = [(det_index, matched_id), ...]（每帧只识别最大脸）
            # 按 det_index 查表，只有识别的那张脸彩色，其他白框
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
                    # 框颜色：默认白色，有匹配 ID 时按 ID 着色
                    matched_id = rec_map.get(i)
                    color_hex = BOX_COLORS.get(matched_id, BOX_UNKNOWN) if matched_id else BOX_UNKNOWN
                    color = _draw_color(color_hex)
                    osd_img.draw_rectangle(x, y, w, h, color=color, thickness=2)
                    # ID 标签
                    if matched_id is not None:
                        osd_img.draw_string_advanced(x + 2, y + 2, 16,
                                                     "ID%d" % matched_id, color=color)

    def deinit(self):
        del self.kpu
        del self.ai2d
        self.tensors.clear()
        del self.tensors
        gc.collect()
        time.sleep_ms(50)


class FaceRegistrationApp(AIBase):
    """人脸特征提取推理（face_recognition_mobile.kmodel，512 维特征）。

    用 mobile 版（2.65MB）非标准版（44MB）：LVGL 双线程后 ~3.7MB free
    装不下 44MB 标准 kmodel（AIBase.__init__ 死锁）。官方 main2.py 用标准
    版是因无 LVGL 内存充裕。512 维特征 → face_db EXPECTED_BYTES=512*4。
    Step 4：仅加载 kmodel（构造即 AIBase.__init__ 加载到 NPU），不 run。
    config_preprocess/postprocess 的 umeyama+affine 逻辑在 Step 5 填充。
    """
    def __init__(self, kmodel_path, model_input_size, rgb888p_size=None, debug_mode=0):
        if rgb888p_size is None:
            rgb888p_size = RGB888P_SIZE
        super().__init__(kmodel_path, model_input_size, rgb888p_size, debug_mode)
        self.kmodel_path = kmodel_path
        self.model_input_size = model_input_size
        self.rgb888p_size = [ALIGN_UP(rgb888p_size[0], 16), rgb888p_size[1]]
        # 112×112 标准对齐 5 关键点（umeyama 目标）
        self.umeyama_args_112 = [
            38.2946, 51.6963,
            73.5318, 51.5014,
            56.0252, 71.7366,
            41.5493, 92.3655,
            70.7299, 92.2041
        ]
        self.ai2d = Ai2d(debug_mode)
        self.ai2d.set_ai2d_dtype(nn.ai2d_format.NCHW_FMT, nn.ai2d_format.NCHW_FMT,
                                 np.uint8, np.uint8)

    def config_preprocess(self, landm, input_image_size=None):
        # Step 5: umeyama 对齐 + affine。landm = 5 关键点（10 元素扁平列表）。
        ai2d_input_size = input_image_size if input_image_size else self.rgb888p_size
        affine_matrix = self._get_affine_matrix(landm)
        self.ai2d.affine(nn.interp_method.cv2_bilinear, 0, 0, 127, 1, affine_matrix)
        self.ai2d.build(
            [1, 3, ai2d_input_size[1], ai2d_input_size[0]],
            [1, 3, self.model_input_size[1], self.model_input_size[0]])

    def _get_affine_matrix(self, sparse_points):
        matrix_dst = self._image_umeyama_112(sparse_points)
        return [matrix_dst[0][0], matrix_dst[0][1], matrix_dst[0][2],
                matrix_dst[1][0], matrix_dst[1][1], matrix_dst[1][2]]

    def _image_umeyama_112(self, src):
        SRC_NUM = 5
        src_mean = [0.0, 0.0]
        dst_mean = [0.0, 0.0]
        for i in range(0, SRC_NUM * 2, 2):
            src_mean[0] += src[i]
            src_mean[1] += src[i + 1]
            dst_mean[0] += self.umeyama_args_112[i]
            dst_mean[1] += self.umeyama_args_112[i + 1]
        src_mean[0] /= SRC_NUM
        src_mean[1] /= SRC_NUM
        dst_mean[0] /= SRC_NUM
        dst_mean[1] /= SRC_NUM
        src_demean = [[0.0, 0.0] for _ in range(SRC_NUM)]
        dst_demean = [[0.0, 0.0] for _ in range(SRC_NUM)]
        for i in range(SRC_NUM):
            src_demean[i][0] = src[2 * i] - src_mean[0]
            src_demean[i][1] = src[2 * i + 1] - src_mean[1]
            dst_demean[i][0] = self.umeyama_args_112[2 * i] - dst_mean[0]
            dst_demean[i][1] = self.umeyama_args_112[2 * i + 1] - dst_mean[1]
        A = [[0.0, 0.0], [0.0, 0.0]]
        for i in range(2):
            for k in range(2):
                for j in range(SRC_NUM):
                    A[i][k] += dst_demean[j][i] * src_demean[j][k]
                A[i][k] /= SRC_NUM
        T = [[1, 0, 0], [0, 1, 0], [0, 0, 1]]
        U, S, V = self._svd22([A[0][0], A[0][1], A[1][0], A[1][1]])
        T[0][0] = U[0] * V[0] + U[1] * V[2]
        T[0][1] = U[0] * V[1] + U[1] * V[3]
        T[1][0] = U[2] * V[0] + U[3] * V[2]
        T[1][1] = U[2] * V[1] + U[3] * V[3]
        src_demean_mean = [0.0, 0.0]
        src_demean_var = [0.0, 0.0]
        for i in range(SRC_NUM):
            src_demean_mean[0] += src_demean[i][0]
            src_demean_mean[1] += src_demean[i][1]
        src_demean_mean[0] /= SRC_NUM
        src_demean_mean[1] /= SRC_NUM
        for i in range(SRC_NUM):
            src_demean_var[0] += (src_demean_mean[0] - src_demean[i][0]) ** 2
            src_demean_var[1] += (src_demean_mean[1] - src_demean[i][1]) ** 2
        src_demean_var[0] /= SRC_NUM
        src_demean_var[1] /= SRC_NUM
        scale = 1.0 / (src_demean_var[0] + src_demean_var[1]) * (S[0] + S[1])
        T[0][2] = dst_mean[0] - scale * (T[0][0] * src_mean[0] + T[0][1] * src_mean[1])
        T[1][2] = dst_mean[1] - scale * (T[1][0] * src_mean[0] + T[1][1] * src_mean[1])
        T[0][0] *= scale
        T[0][1] *= scale
        T[1][0] *= scale
        T[1][1] *= scale
        return T

    def _svd22(self, a):
        s = [0.0, 0.0]
        u = [0.0, 0.0, 0.0, 0.0]
        v = [0.0, 0.0, 0.0, 0.0]
        s[0] = (math.sqrt((a[0] - a[3]) ** 2 + (a[1] + a[2]) ** 2)
                + math.sqrt((a[0] + a[3]) ** 2 + (a[1] - a[2]) ** 2)) / 2
        s[1] = abs(s[0] - math.sqrt((a[0] - a[3]) ** 2 + (a[1] + a[2]) ** 2))
        v[2] = math.sin(math.atan2(
            2 * (a[0] * a[1] + a[2] * a[3]),
            a[0] ** 2 - a[1] ** 2 + a[2] ** 2 - a[3] ** 2) / 2) if s[0] > s[1] else 0
        v[0] = math.sqrt(1 - v[2] ** 2)
        v[1] = -v[2]
        v[3] = v[0]
        u[0] = -(a[0] * v[0] + a[1] * v[2]) / s[0] if s[0] != 0 else 1
        u[2] = -(a[2] * v[0] + a[3] * v[2]) / s[0] if s[0] != 0 else 0
        u[1] = (a[0] * v[1] + a[1] * v[3]) / s[1] if s[1] != 0 else -u[2]
        u[3] = (a[2] * v[1] + a[3] * v[3]) / s[1] if s[1] != 0 else u[0]
        v[0] = -v[0]
        v[2] = -v[2]
        return u, s, v

    def postprocess(self, results):
        return results[0][0]

    def deinit(self):
        del self.kpu
        del self.ai2d
        self.tensors.clear()
        del self.tensors
        gc.collect()
        time.sleep_ms(50)


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
    global sensor
    Display.init(Display.ST7701, width=DISPLAY_WIDTH, height=DISPLAY_HEIGHT,
                 to_ide=False, osd_num=2)
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
    disp_img1 = image.Image(DISPLAY_SIZE[0], DISPLAY_SIZE[1], image.BGRA8888)
    disp_img2 = image.Image(DISPLAY_SIZE[0], DISPLAY_SIZE[1], image.BGRA8888)
    disp_img1.clear()
    disp_img2.clear()
    disp_drv.set_draw_buffers(disp_img1.bytearray(), disp_img2.bytearray(),
                              disp_img1.size(), lv.DISP_RENDER_MODE.FULL)
    scr = lv.scr_act()
    scr.set_style_bg_opa(0, 0)


def face_det_thread():
    global sensor, face_det, running, fc, _rt
    kmodel_path = "/sdcard/examples/kmodel/face_detection_320.kmodel"
    anchors_path = "/sdcard/examples/utils/prior_data_320.bin"
    anchors = np.fromfile(anchors_path, dtype=np.float)
    anchors = anchors.reshape((4200, 4))
    face_det = FaceDetectionApp(kmodel_path, model_input_size=[320, 320], anchors=anchors,
                                confidence_threshold=0.5, nms_threshold=0.2,
                                rgb888p_size=RGB888P_SIZE, display_size=DISPLAY_SIZE,
                                debug_mode=0)
    face_det.config_preprocess()
    print("[baseline-face] face_det_thread begin")
    while running:
        with ScopedTiming("total", 2):
            fc += 1
            if fc <= 20 or fc % 30 == 0:
                print("[baseline-face] fc=%d" % fc)
            img_0 = sensor.snapshot(chn=CAM_CHN_ID_0)
            img_2 = sensor.snapshot(chn=CAM_CHN_ID_2)
            img_np = img_2.to_numpy_ref()
            det_boxes, landms = face_det.run(img_np)
            # Step 5: 每帧只识别最大人脸。db_features 空时全 None（白框）。
            recognition_results = []
            if det_boxes and landms and face_reg is not None:
                try:
                    max_i = max(range(len(det_boxes)),
                                key=lambda i: det_boxes[i][2] * det_boxes[i][3])
                    face_reg.config_preprocess(landms[max_i])
                    feature = face_reg.run(img_np)
                    matched_id = database_search(feature, db_features)
                    recognition_results.append((max_i, matched_id))
                    # Step 7: K2 注册（复用刚提的特征，不重复 NPU 推理）。
                    # _rt.buzzer=None 守卫：无 buzzer 时静默（id_registry 内部已守卫）。
                    if id_registry is not None:
                        id_registry.try_register(feature, _rt.buzzer if _rt else None)
                except Exception as e:
                    print("[baseline-face] recog error: %s" % e)
            # 十字架（绿色，两条相交线，中心 320,240）
            cx, cy = 320, 240
            arm = CROSSHAIR_ARM
            ch_color = _draw_color(CROSSHAIR_COLOR)
            img_0.draw_line(cx, cy - arm, cx, cy + arm, color=ch_color, thickness=2)
            img_0.draw_line(cx - arm, cy, cx + arm, cy, color=ch_color, thickness=2)
            face_det.draw_result(img_0, det_boxes, recognition_results)
            Display.show_image(img_0, 0, 0, Display.LAYER_OSD1)
        gc.collect()
    face_det.deinit()
    print("[baseline-face] face_det_thread exited")


def run(runtime):
    global running, db_features, _rt
    _rt = runtime
    print("[baseline-face] run() begin")
    media_init()
    lvgl_init()
    gc.collect()
    print("[baseline-face] init done, mem=%d" % gc.mem_free())
    # Step 3: 读入人脸特征库到内存（仅读，不推理）。
    # ⚠️ 必须在主线程、AI 线程启动 + task_handler 循环之前完成文件 I/O
    # （坑#2 变体：AI 线程里 open().read() 会与主线程 task_handler 的 display
    # DMA flush 竞争死锁——曾放 face_det_thread 致一运行就卡）。对齐 backup：
    # 在 run() 主线程、拉起 AI 线程之前调用。
    from core.face_db import face_db
    db_features = face_db.init_features()
    print("[baseline-face] db loaded: %d face(s)" % len(db_features))
    # Step 4: 加载 face_reg kmodel（mobile 版 2.65MB，512 维）。
    # ⚠️ 坑#18：kmodel 加载是文件 I/O，必须在主线程、AI 线程启动前完成。
    # ⚠️ 坑#19：必须用 mobile（2.65MB）；标准版 44MB 在 LVGL 双线程后 ~3.7MB
    # free 下 AIBase.__init__ 死锁。只 load 不 run（config_preprocess Step 5 填）。
    global face_reg
    reg_kmodel = "/sdcard/examples/kmodel/face_recognition_mobile.kmodel"
    try:
        face_reg = FaceRegistrationApp(reg_kmodel, model_input_size=[112, 112],
                                       rgb888p_size=RGB888P_SIZE, debug_mode=0)
        print("[baseline-face] face_reg kmodel loaded (512-dim)")
    except Exception as e:
        print("[baseline-face] face_reg load FAILED: %s" % e)
        sys.print_exception(e)
        face_reg = None
    gc.collect()
    print("[baseline-face] after face_reg, mem=%d" % gc.mem_free())
    # Step 7: K2 注册控制器（复用 face_db.register + 轮转覆盖）。
    # ⚠️ GPIO0 硬件初始化在主线程、AI 线程启动前（与 sensor 配置同窗口）。
    global id_registry
    from core.id_registry import IdRegistry
    id_registry = IdRegistry(runtime.fpioa, pin=0)
    running = True
    time.sleep_ms(100)
    _thread.start_new_thread(face_det_thread, ())
    try:
        while True:
            os.exitpoint()
            id_registry.poll_k2()              # Step 7: 主线程 K2 边沿检测
            time.sleep_ms(lv.task_handler())
    except BaseException as e:
        sys.print_exception(e)
    running = False
    time.sleep_ms(200)
    print("[baseline-face] run() exit, fc=%d" % fc)
