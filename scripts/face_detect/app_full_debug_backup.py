# scripts/face_detect/app.py — 人脸识别 APP（reset 框架独立脚本）
#
# 架构（reset 切换 + 双线程）：
#   main.py 读 .next_script="face_detect" → runtime.init_app("face_detect",fpioa)
#   → 本模块 run(runtime) 入口 → FaceDetectApp(runtime).run()
#   OSD1 层：相机帧（sensor.snapshot() → Display.show_image(LAYER_OSD1)）
#   OSD2 层：LVGL UI（顶栏 + 底栏 + 弹窗/toast）
#   chn2 层：AI 推理帧（sensor.snapshot(chn=CAM_CHN_ID_2).to_numpy_ref() → NPU）
#
# 双线程（对齐官方 ai_lvgl.py，复用裸跑已验证稳定结构）：
#   AI 线程 _ai_loop：snapshot(chn0+chn2) + face_det.run + 逐脸识别 +
#                    K2 注册检查 + _draw_overlay(画 image.Image) + show_image +
#                    UART + gc。绝不碰 LVGL（坑#10 线程安全）。
#   主线程 run() 主循环：task_handler + K2 轮询设注册标志 + 握手 poll +
#                       toast tick + 退出检测。
# sensor.run 由 runtime.init_app 完成（紧贴消费者，避免缓冲满卡死）。
# 每帧 gc.collect()（坑#16）。

import os
import time
import struct
import _thread
import lvgl as lv
from media.display import Display
from core.icon_cache import icon_cache
from core.font_manager import fonts
from ui.theme import Colors, make_back_bar_text_style

# AI 推理依赖（设备端运行时导入，IDE 不可用）
# 以下 import 在 K230 设备上可用，主机端 AST 测试不执行 import
try:
    import ulab.numpy as np
    import nncase_runtime as nn
    import aidemo
    import image as _image_lib
    import math
    from media.sensor import CAM_CHN_ID_0, CAM_CHN_ID_2
except ImportError:
    pass  # IDE 环境无此模块，仅 AST 测试用


# ═══════════════════════════════════════════════════════
# AI 推理类（迁移自正点原子 Demo main2.py）
# ═══════════════════════════════════════════════════════

# 从 libs.AIBase 导入 AIBase（设备端）
from libs.AIBase import AIBase
from libs.AI2D import Ai2d


def _align_up(x, align=16):
    return (x + align - 1) // align * align


class FaceDetApp(AIBase):
    """人脸检测推理"""
    def __init__(self, kmodel_path, model_input_size, anchors,
                 confidence_threshold=0.5, nms_threshold=0.2,
                 rgb888p_size=None, debug_mode=0):
        if rgb888p_size is None:
            rgb888p_size = [1024, 768]
        super().__init__(kmodel_path, model_input_size, rgb888p_size, debug_mode)
        self.kmodel_path = kmodel_path
        self.model_input_size = model_input_size
        self.confidence_threshold = confidence_threshold
        self.nms_threshold = nms_threshold
        self.anchors = anchors
        self.rgb888p_size = [_align_up(rgb888p_size[0], 16), rgb888p_size[1]]
        self.image_size = []
        self.ai2d = Ai2d(debug_mode)
        self.ai2d.set_ai2d_dtype(
            nn.ai2d_format.NCHW_FMT, nn.ai2d_format.NCHW_FMT,
            np.uint8, np.uint8)

    def config_preprocess(self, input_image_size=None):
        ai2d_input_size = input_image_size if input_image_size else self.rgb888p_size
        self.image_size = [ai2d_input_size[1], ai2d_input_size[0]]
        dst_w = self.model_input_size[0]
        dst_h = self.model_input_size[1]
        ratio_w = dst_w / ai2d_input_size[0]
        ratio_h = dst_h / ai2d_input_size[1]
        ratio = ratio_w if ratio_w < ratio_h else ratio_h
        new_w = int(ratio * ai2d_input_size[0])
        new_h = int(ratio * ai2d_input_size[1])
        dw = (dst_w - new_w) / 2
        dh = (dst_h - new_h) / 2
        top = int(round(0))
        bottom = int(round(dh * 2 + 0.1))
        left = int(round(0))
        right = int(round(dw * 2 - 0.1))
        self.ai2d.pad([0, 0, 0, 0, top, bottom, left, right], 0, [104, 117, 123])
        self.ai2d.resize(nn.interp_method.tf_bilinear, nn.interp_mode.half_pixel)
        self.ai2d.build(
            [1, 3, ai2d_input_size[1], ai2d_input_size[0]],
            [1, 3, self.model_input_size[1], self.model_input_size[0]])

    def postprocess(self, results):
        res = aidemo.face_det_post_process(
            self.confidence_threshold, self.nms_threshold,
            self.model_input_size[0], self.anchors,
            self.rgb888p_size, results)
        if len(res) == 0:
            return [], []
        else:
            return res[0], res[1]


class FaceRegistrationApp(AIBase):
    """人脸特征提取推理"""
    def __init__(self, kmodel_path, model_input_size,
                 rgb888p_size=None, debug_mode=0):
        print("[FaceReg] __init__ enter, kmodel=%s" % kmodel_path)
        if rgb888p_size is None:
            rgb888p_size = [1024, 768]
        print("[FaceReg] calling AIBase.__init__...")
        super().__init__(kmodel_path, model_input_size, rgb888p_size, debug_mode)
        print("[FaceReg] AIBase.__init__ done")
        self.kmodel_path = kmodel_path
        self.model_input_size = model_input_size
        print("[FaceReg] setting rgb888p_size...")
        self.rgb888p_size = [_align_up(rgb888p_size[0], 16), rgb888p_size[1]]
        print("[FaceReg] setting umeyama...")
        self.umeyama_args_112 = [
            38.2946, 51.6963,
            73.5318, 51.5014,
            56.0252, 71.7366,
            41.5493, 92.3655,
            70.7299, 92.2041
        ]
        print("[FaceReg] creating Ai2d...")
        self.ai2d = Ai2d(debug_mode)
        print("[FaceReg] setting ai2d_dtype...")
        self.ai2d.set_ai2d_dtype(
            nn.ai2d_format.NCHW_FMT, nn.ai2d_format.NCHW_FMT,
            np.uint8, np.uint8)
        print("[FaceReg] __init__ done")

    def config_preprocess(self, landm, input_image_size=None):
        ai2d_input_size = input_image_size if input_image_size else self.rgb888p_size
        affine_matrix = self._get_affine_matrix(landm)
        self.ai2d.affine(nn.interp_method.cv2_bilinear, 0, 0, 127, 1, affine_matrix)
        self.ai2d.build(
            [1, 3, ai2d_input_size[1], ai2d_input_size[0]],
            [1, 3, self.model_input_size[1], self.model_input_size[0]])

    def postprocess(self, results):
        return results[0][0]

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


# ═══════════════════════════════════════════════════════
# FaceDetectApp
# ═══════════════════════════════════════════════════════

# ── 布局常量（复用 Camera APP）──
BAR_H = 52
PREVIEW_Y = BAR_H
PREVIEW_H = 376  # 480 - 52*2
BAR_BG = 0x1A1A1A
BTN_SIZE = 48
ICON_TARGET = 40

# 十字架
CROSSHAIR_COLOR = 0x44CC44  # 绿色
CROSSHAIR_ARM = 30
CROSSHAIR_GAP = 8

# 人脸框颜色（4个ID）
BOX_COLORS = {
    1: 0x44CC44,   # ID1 绿色
    2: 0x4488FF,   # ID2 蓝色
    3: 0xFF8844,   # ID3 橙色
    4: 0xCC44FF,   # ID4 紫色
}
BOX_UNKNOWN = 0xFFFFFF  # 未注册人脸白色框

WHITE = 0xFFFFFF

# 发送周期
SEND_INTERVAL_MS = 10

# 预热帧数（冷启动硬锁对策，对齐旧颜色识别代码 WARMUP_SKIP_FRAMES/PIPELINE_WARMUP_FRAMES）
# 冷启动时 ISP/缓冲池未就绪，前若干帧若跑 NPU+gc 易永久阻塞（板端实测：直接进
# face_detect 大概率卡 pre-gc；先跑相机 APP"暖一下"能跑几帧）。预热期只做
# snapshot+show_image，不跑 NPU、不 gc，让 ISP 稳定后再进入正常推理。
WARMUP_FRAMES = 6


def _png_zoom(png_data, target):
    """从 PNG 头解析真实尺寸，计算缩放因子"""
    if not png_data or len(png_data) < 24:
        return 256
    w = struct.unpack('>I', png_data[16:20])[0]
    h = struct.unpack('>I', png_data[20:24])[0]
    if w <= 0 or h <= 0:
        return 256
    zoom = int(min(target / w, target / h) * 256)
    return max(8, min(zoom, 256))


def _make_icon(parent, icon_data, icon_dsc, target_size, x, y):
    """在 parent 上创建图标（K230 set_zoom 居中补偿模式）"""
    if icon_dsc is None or icon_data is None:
        return None, x

    img = lv.img(parent)
    img.set_src(icon_dsc)
    zoom = _png_zoom(icon_data, target_size)
    img.set_zoom(zoom)

    src_w = struct.unpack('>I', icon_data[16:20])[0]
    rendered_w = src_w * zoom // 256
    actual_x = x - (src_w - rendered_w) // 2
    img.align(lv.ALIGN.LEFT_MID, actual_x, 0)
    return img, actual_x


class FaceDetectApp:
    """人脸识别 APP（reset 框架 run(runtime) 独立脚本）。

    生命周期：run() 入口 → _init_db/_init_ai_models/_build_ui → 启 AI 线程 →
    主循环（task_handler+K2+握手+toast+退出）→ 停 AI 线程 → _deinit_ai_models/
    _flush_db/_destroy_ui → 返回（main.py 清 .next_script + reset）。
    """

    def __init__(self, runtime):
        # reset 框架运行时（持有 sensor/display/host/lang/config/buzzer）
        self.rt = runtime
        # AI模型
        self._face_det = None
        self._face_reg = None
        self._anchors = None

        # 人脸数据库（特征由 core.face_db 管理，此处为内存引用）
        self._db_features = {}     # {1: np_array(128), ...}
        self._db_dirty = False     # 退出时需刷盘
        self._db_clear_pending = False  # 退出时需删文件

        # 当前帧 AI 推理结果（供注册和发送使用）
        self._current_boxes = []        # list of [x,y,w,h,score]
        self._current_landmarks = []    # list of landm arrays
        self._recognition_results = []  # [(box, matched_id, score), ...]
        # 上一帧 snapshot image.Image 的延迟引用（见 _ai_loop 注释）。
        # Display.show_image(OSD1) 的 DMA 可能异步/延迟到下次 task_handler
        # 才真正搬运 img 像素；若 _ai_loop 末尾 gc.collect() 立即回收 img，
        # DMA 会读到已归还的 VB → 死锁。持有一帧让 DMA 跨过 gc 安全区。
        self._prev_img = None

        # 双线程标志（见 _ai_loop / run）
        self._ai_running = False       # AI 线程循环条件
        self._ai_thread_alive = False  # AI 线程是否在跑（run 退出等待用）
        self._register_pending = False  # K2 请求注册（主线程设，AI线程消费）
        self._exit_requested = False   # 返回按钮设（主循环退出条件）
        self._status_dirty = False     # AI 线程注册后设，主线程消费调 _update_status_text
                                       # （_update_status_text 碰 LVGL，必须在主线程）

        # UI
        self._screen = None
        self._top_bar = None
        self._bottom_bar = None
        self._preview_bg = None
        self._title_label = None
        self._status_label = None
        self._back_btn = None           # ⚠️ 挂 self 防 GC 误杀
        self._back_icon = None          # ⚠️ 挂 self 防 GC 误杀（lv.img）
        self._back_fallback_label = None  # ⚠️ 挂 self 防 GC 误杀
        self._list_btn = None           # ⚠️ 挂 self 防 GC 误杀
        self._list_icon = None          # ⚠️ 挂 self 防 GC 误杀（lv.img）
        self._list_fallback_label = None  # ⚠️ 挂 self 防 GC 误杀
        self._popup = None             # 弹出菜单
        self._popup_overlay = None     # 弹出菜单蒙层（截获中屏点击）
        self._toast = None             # 保存成功 toast

        # 发送计时
        self._last_send_ticks = 0
        self._frame_count = 0  # 诊断：帧计数器

    # ── 生命周期入口（替代 on_enter/on_frame/on_exit/on_key）──

    def run(self):
        """reset 框架入口。

        ⚠️ 临时定位：main.py 对 face_detect 跳过 runtime.init_app，本方法自己
        全套 init（对齐裸跑 test_face_baseline_camerai_sensor.py media_init/
        lvgl_init）。验证 runtime.init_app 是否元凶。验证后恢复用 runtime。
        """
        import gc as _gc
        import uctypes
        import image
        from media.sensor import (Sensor, CAM_CHN_ID_0, CAM_CHN_ID_1, CAM_CHN_ID_2)
        from media.display import Display
        from media.media import MediaManager
        from machine import Pin

        _gc.collect()
        print("[FaceDetect] run: mem_free=%d (gc后)" % _gc.mem_free())

        # ── 自己 media_init（对齐裸跑：Display.init 先 → sensor → MediaManager）──
        Display.init(Display.ST7701, 640, 480, to_ide=False, osd_num=2, quality=100)
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
        # 存到 runtime 供 _ai_loop 用
        self.rt.sensor = sensor
        self._own_display = Display
        print("[FaceDetect] run: media_init done (self-init)")

        # ── 自己 lvgl_init（对齐裸跑）──
        lv.init()
        self.rt.draw_buf_1 = image.Image(640, 480, image.BGRA8888)
        self.rt.draw_buf_2 = image.Image(640, 480, image.BGRA8888)
        self.rt.draw_buf_1.clear()
        self.rt.draw_buf_2.clear()
        self.rt.lv_disp = lv.disp_create(640, 480)
        self.rt.lv_disp.set_flush_cb(self._own_flush_cb)
        self.rt.lv_disp.set_color_format(lv.COLOR_FORMAT.ARGB8888)
        self.rt.lv_disp.set_draw_buffers(
            self.rt.draw_buf_1.bytearray(), self.rt.draw_buf_2.bytearray(),
            self.rt.draw_buf_1.size(), lv.DISP_RENDER_MODE.FULL)
        scr = lv.scr_act()
        scr.set_style_bg_opa(0, 0)
        print("[FaceDetect] run: lvgl_init done (self-init)")

        # ── services（lang/config，buzzer 定位期 None）──
        from core.config_manager import ConfigManager
        from core.lang import LangManager
        self.rt.config = ConfigManager()
        self.rt.config.load()
        self.rt.lang = LangManager()
        self.rt.lang.load(self.rt.config.get('lang', 'zh_CN'))
        self.rt.buzzer = None  # 定位期不创建
        print("[FaceDetect] run: services done (self-init)")

        print("[FaceDetect] run: begin _init_db")
        self._init_db()
        print("[FaceDetect] run: begin _build_ui (SKIPPED baseline isolation)")
        # self._build_ui()  # 临时严格裸跑对齐：不建任何 LVGL UI
        # 启动 AI 线程
        self._ai_running = True
        self._register_pending = False
        _thread.start_new_thread(self._ai_loop, ())
        print("[FaceDetect] run: AI thread started")
        # 主循环（裸跑式：time.sleep_ms(lv.task_handler())）
        print("[FaceDetect] run: main loop begin")
        try:
            while not self._exit_requested:
                os.exitpoint()
                time.sleep_ms(lv.task_handler())
        except BaseException as e:
            print("[FaceDetect] main loop exc: %s" % e)
            import sys as _sys
            _sys.print_exception(e)
        # 退出
        print("[FaceDetect] run: exit requested")
        self._ai_running = False
        for _ in range(100):
            if not self._ai_thread_alive:
                break
            time.sleep_ms(10)
        try:
            self._deinit_ai_models()
        except Exception:
            pass
        print("[FaceDetect] run() done")

    def _own_flush_cb(self, disp, area, px_map):
        """裸跑式 flush_cb（对齐 test_face_baseline_camerai_sensor.py）。"""
        import uctypes
        import time
        if disp.flush_is_last():
            if self.rt.draw_buf_1.virtaddr() == uctypes.addressof(
                    px_map.__dereference__()):
                self.rt.draw_buf_2.bytearray()[:] = bytearray(0)
                self._own_display.show_image(self.rt.draw_buf_1, layer=self._own_display.LAYER_OSD2)
            else:
                self.rt.draw_buf_1.bytearray()[:] = bytearray(0)
                self._own_display.show_image(self.rt.draw_buf_2, layer=self._own_display.LAYER_OSD2)
            time.sleep(0.01)
        disp.flush_ready()

    # ── AI 模型释放（从旧 on_exit 抽出）──

    def _deinit_ai_models(self):
        """释放 AI 模型（del kpu/ai2d/tensors，绕开 AIBase.deinit 的
        gc.collect()+sleep_ms 组合——曾观察到该组合永不返回）。

        NPU 内存池由系统自动回收，不显式 shrink（曾死锁）。
        """
        print("[FaceDetect] deinit AI models")
        if self._face_det is not None:
            print("[FaceDetect]   det: del kpu/ai2d/tensors...")
            try:
                # 不调 .deinit()——绕开内部的 gc.collect() + sleep_ms 组合
                if hasattr(self._face_det, 'kpu'):
                    del self._face_det.kpu
                if hasattr(self._face_det, 'ai2d'):
                    del self._face_det.ai2d
                if hasattr(self._face_det, 'tensors'):
                    self._face_det.tensors.clear()
                    del self._face_det.tensors
                print("[FaceDetect]   det: del OK")
            except Exception as e:
                print("[FaceDetect]   det del error: %s" % e)
            self._face_det = None
            print("[FaceDetect]   det: ref cleared")

        if self._face_reg is not None:
            print("[FaceDetect]   reg: del kpu/ai2d/tensors...")
            try:
                if hasattr(self._face_reg, 'kpu'):
                    del self._face_reg.kpu
                if hasattr(self._face_reg, 'ai2d'):
                    del self._face_reg.ai2d
                if hasattr(self._face_reg, 'tensors'):
                    self._face_reg.tensors.clear()
                    del self._face_reg.tensors
                print("[FaceDetect]   reg: del OK")
            except Exception as e:
                print("[FaceDetect]   reg del error: %s" % e)
            self._face_reg = None
            print("[FaceDetect]   reg: ref cleared")

        self._anchors = None
        print("[FaceDetect] deinit AI models done")

    def _flush_db(self):
        """刷盘 DB 特征 / 删文件。

        在 lv.task_handler() 已返回、DMA 空闲的安全窗口做文件 I/O（run 末尾
        调，主线程上下文）。必须在 _deinit_ai_models 之后或之前均可——此处
        放 _deinit 之后，numpy 数组仍由 self._db_features 持有，刷盘安全。
        """
        from core.face_db import face_db
        n_features = len(self._db_features) if self._db_features else 0
        print("[FaceDetect] flush_db: dirty=%s features=%d clear=%s"
              % (self._db_dirty, n_features, self._db_clear_pending))
        if self._db_clear_pending:
            print("[FaceDetect] clearing DB files...")
            face_db.clear_disk()
            self._db_clear_pending = False
        elif self._db_dirty or n_features > 0:
            print("[FaceDetect] flushing DB to disk...")
            face_db.flush_to_disk()
            self._db_dirty = False
        else:
            print("[FaceDetect] DB unchanged, skip flush")

    # ── AI 线程 ──────────────────────────────────

    def _ai_loop(self):
        """AI 线程（双线程架构，复用裸跑已验证稳定结构）。

        每帧：
          1. snapshot(chn0) 预览帧 + snapshot(chn2) AI 帧（PLANAR CHW）
          2. 预热期（前 WARMUP_FRAMES 帧）：只 snapshot+show_image，不跑 NPU、
             不 gc（ISP 稳定后再进入正常推理——冷启动硬锁对策）
          3. face_det.run(img_np) → boxes/landmarks
          4. 逐脸 _search_face 识别 → recognition_results
          5. K2 注册检查（_register_pending → _do_register）
          6. _draw_overlay 画十字架+人脸框+ID 到 img_0
          7. Display.show_image(img_0, LAYER_OSD1)
          8. UART 上送（按 SEND_INTERVAL_MS 周期）
          9. gc.collect()（坑#16，每帧必须）

        绝不碰 LVGL（坑#10 线程安全）。状态文字更新通过 _status_dirty 标志
        交主线程消费（_update_status_text 碰 LVGL）。
        """
        import gc as _gc
        sensor = self.rt.sensor
        self._ai_thread_alive = True
        print("[FaceDetect] AI loop begin")
        # ⚠️ 决定性验证：_ai_loop 逐行照搬裸跑 face_det_thread（含 ScopedTiming），
        # 用 self.rt.sensor/display。如果这都卡 → 差异在 _ai_loop 之外（init/run/UI）；
        # 如果稳定 → 逐步还原 face_detect 逻辑定位。
        print("[FaceDetect] AI thread: begin _init_ai_models")
        self._init_ai_models()
        print("[FaceDetect] AI thread: models loaded")
        _ai_fc = 0
        try:
            while self._ai_running:
                _ai_fc += 1
                _dbg = (_ai_fc <= 40 or _ai_fc % 30 == 0)
                if _dbg:
                    print("[FD ai] fc=%d mem=%d" % (_ai_fc, _gc.mem_free()))
                    print("[FD ai]   pre-snap0")
                img_0 = sensor.snapshot(chn=CAM_CHN_ID_0)
                if _dbg: print("[FD ai]   pre-snap2")
                img_2 = sensor.snapshot(chn=CAM_CHN_ID_2)
                if _dbg: print("[FD ai]   pre-tonumpy")
                img_np = img_2.to_numpy_ref()
                if _dbg: print("[FD ai]   pre-det")
                res = self._face_det.run(img_np)
                if _dbg: print("[FD ai]   post-det")
                # 不画框（裸跑 draw_result 也关掉对齐）
                if _dbg: print("[FD ai]   pre-show")
                Display.show_image(img_0, 0, 0, Display.LAYER_OSD1)
                if _dbg: print("[FD ai]   post-show")
                if _dbg: print("[FD ai]   pre-gc")
                _gc.collect()
                if _dbg: print("[FD ai]   post-gc")
        except Exception as e:
            print("[FaceDetect] AI loop exception: %s" % e)
            import sys as _sys
            _sys.print_exception(e)
        finally:
            self._ai_thread_alive = False
            self._prev_img = None
            print("[FaceDetect] AI loop exited (fc=%d)" % _ai_fc)


    def _draw_overlay(self, img, recognition_results):
        """在相机帧 img 上画十字架 + 人脸框 + ID 标签（image 模块画图）。

        替代原 LVGL 对象池方案——双线程下 AI 线程不碰 LVGL 对象，改画到
        img 随 show_image(OSD1) 一起显示。对齐官方 ai_lvgl.py draw_result。
        color 用 RGBA 四元组（image 模块要求）。
        """
        # 十字架（绿色，中心 320,240——img 是 chn0 VGA 640×480）
        cx, cy = 320, 240
        arm, gap = CROSSHAIR_ARM, CROSSHAIR_GAP
        green = (0x44, 0xCC, 0x44, 0xFF)
        img.draw_line(cx, cy - arm, cx, cy - gap, color=green, thickness=2)
        img.draw_line(cx, cy + gap, cx, cy + arm, color=green, thickness=2)
        img.draw_line(cx - arm, cy, cx - gap, cy, color=green, thickness=2)
        img.draw_line(cx + gap, cy, cx + arm, cy, color=green, thickness=2)

        # 人脸框 + ID（chn2 坐标 1024×768 → img 坐标 640×480）
        ai_w = self._face_det.rgb888p_size[0] if self._face_det else 1024
        ai_h = self._face_det.rgb888p_size[1] if self._face_det else 768
        for i, (box, matched_id, _) in enumerate(recognition_results):
            if i >= 4:
                break
            x, y, w, h = [int(v) for v in box[:4]]
            sx = x * 640 // ai_w
            sy = y * 480 // ai_h
            sw = max(w * 640 // ai_w, 4)
            sh = max(h * 480 // ai_h, 4)
            color_int = BOX_COLORS.get(matched_id, BOX_UNKNOWN)
            col = ((color_int >> 16) & 0xFF, (color_int >> 8) & 0xFF,
                   color_int & 0xFF, 0xFF)
            img.draw_rectangle(sx, sy, sw, sh, color=col, thickness=2)
            if matched_id is not None:
                img.draw_string_advanced(sx + 2, sy + 2, 16,
                                         "ID%d" % matched_id, color=col)

    def _do_register(self, frame):
        """将当前帧最大人脸注册到下一个空槽位（AI 线程调用）。

        NPU 推理（face_reg.run）集中 AI 线程，避免跨线程 NPU。主线程 K2
        只设 _register_pending 标志，由 _ai_loop 检查并调用本方法。

        frame: 当前帧 chn2 planar CHW（_ai_loop 传入局部变量，不跨帧持久化
        到 self——跨帧持有 VB-backed view 致 gc 卡死，见坑#14 变体）。

        注册成功后不直接调 _update_status_text（碰 LVGL，主线程才能调）——
        _ai_loop 调本方法后会设 self._status_dirty=True，主循环消费。
        """
        if not self._current_boxes:
            if self.rt.buzzer is not None:
                self.rt.buzzer.beep(ms=30)
            return
        if not frame or not self._current_landmarks:
            if self.rt.buzzer is not None:
                self.rt.buzzer.beep(ms=30)
            return
        # 找最大人脸（按面积）
        largest_idx = 0
        largest_area = 0
        for i, box in enumerate(self._current_boxes):
            w = box[2] if len(box) > 2 else 0
            h = box[3] if len(box) > 3 else 0
            area = w * h
            if area > largest_area:
                largest_area = area
                largest_idx = i
        if largest_idx >= len(self._current_landmarks):
            if self.rt.buzzer is not None:
                self.rt.buzzer.beep(ms=30)
            return
        landm = self._current_landmarks[largest_idx]
        try:
            self._face_reg.config_preprocess(landm)
            feature = self._face_reg.run(frame)
        except Exception as e:
            print("[FaceDetect] feature extract failed: %s" % e)
            if self.rt.buzzer is not None:
                self.rt.buzzer.beep(ms=30)
            return
        for slot in range(1, 5):
            if slot not in self._db_features:
                self._register_face(feature, slot)
                # 不在此调 _update_status_text（碰 LVGL）——_ai_loop 设
                # _status_dirty，主循环消费
                if self.rt.buzzer is not None:
                    self.rt.buzzer.beep(ms=80)
                return
        if self.rt.buzzer is not None:
            self.rt.buzzer.beep(ms=200)

    # ── AI 模型初始化 ──────────────────────────────

    def _init_ai_models(self):
        """加载人脸检测+识别模型"""
        import ulab.numpy as np_local
        import sys as _sys

        det_kmodel = "/sdcard/examples/kmodel/face_detection_320.kmodel"
        reg_kmodel = "/sdcard/examples/kmodel/face_recognition_mobile.kmodel"
        anchors_path = "/sdcard/examples/utils/prior_data_320.bin"

        det_input = [320, 320]
        reg_input = [112, 112]
        rgb888p = [1024, 768]  # chn2 XGA（AI 帧源，对齐官方 demo + lcd.py chn2 配置）

        # 加载 anchors
        anchors = np_local.fromfile(anchors_path, dtype=np_local.float)
        self._anchors = anchors.reshape((4200, 4))

        # 人脸检测：先只加载 kmodel，不构建 AI2D 预处理缓冲。
        # 第二个 kmodel 加载前保留最大连续内存，避免 KPU/AI2D 内存池卡死。
        print("[FaceDetect] loading face_det model...")
        try:
            self._face_det = FaceDetApp(
                det_kmodel, model_input_size=det_input,
                anchors=self._anchors,
                confidence_threshold=0.5, nms_threshold=0.2,
                rgb888p_size=rgb888p, debug_mode=0)
            print("[FaceDetect] A — face_det kmodel loaded")
        except Exception as e:
            print("[FaceDetect] face_det FAILED: %s" % e)
            _sys.print_exception(e)
            raise
        print("[FaceDetect] B — skip runtime gc before face_reg load")

        # 人脸特征提取
        # ⚠️ 临时定位：跳过 face_reg kmodel 加载，验证"2 个 kmodel 占 NPU 池
        # 致 fc~35 卡"假设。裸跑只 1 个 kmodel 稳定 fc=3060。验证后恢复。
        print("[FaceDetect] loading face_reg model... (SKIPPED for diagnosis)")
        # try:
        #     self._face_reg = FaceRegistrationApp(
        #         reg_kmodel, model_input_size=reg_input,
        #         rgb888p_size=rgb888p, debug_mode=0)
        #     print("[FaceDetect] D — face_reg model loaded")
        # except Exception as e:
        #     print("[FaceDetect] face_reg model load FAILED: %s" % e)
        #     _sys.print_exception(e)
        #     raise
        self._face_reg = None

        try:
            self._face_det.config_preprocess()
            print("[FaceDetect] E — face_det preprocess built")
        except Exception as e:
            print("[FaceDetect] face_det preprocess FAILED: %s" % e)
            _sys.print_exception(e)
            raise

    # ── 数据库管理 ──────────────────────────────────

    def _init_db(self):
        """加载人脸特征库到内存。

        face_db.init_features() 使用 np.fromfile() 直接读取 .bin 文件——
        与 _init_ai_models() 加载 anchors 相同的 I/O 路径，
        在 lv.task_handler() 内部验证安全。
        main.py 启动期不再做任何 face_db 文件 I/O。
        """
        from core.face_db import face_db
        self._db_features = face_db.init_features()
        print(f"[FaceDetect] _init_db: {len(self._db_features)} face(s) ready")

    def _save_db(self):
        """保存按钮：标记脏数据，实际文件回写由 run 末尾 _flush_db 统一刷盘"""
        self._db_dirty = True
        print("[FaceDetect] _save_db: marked dirty for exit flush")

    def _register_face(self, feature, slot_id):
        """注册人脸到指定槽位 + 标记脏数据（退出时自动刷盘）。

        设 _db_dirty=True 是关键——确保 _flush_db 无条件走 flush 路径。
        之前的 bug：K2 注册后 _db_dirty 仍为 False，_flush_db 条件
        `self._db_dirty or self._db_features` 偶发未命中（曾出现
        _db_features 未被正确传递到 face_db 的情况）。
        """
        self._db_features[slot_id] = feature
        self._db_dirty = True  # ← K2 注册自动标记脏数据
        print(f"[FaceDetect] registered face → id{slot_id} (memory, dirty)")

    def _clear_db(self):
        """删除全部人脸数据（仅内存，文件删除由 run 末尾 _flush_db 统一处理）"""
        self._db_features.clear()
        self._db_clear_pending = True  # 标记退出时删文件而非刷盘
        print("[FaceDetect] database cleared (memory)")

    def _search_face(self, feature):
        """在数据库中搜索匹配的人脸，返回 (matched_id, score)"""
        import ulab.numpy as np_local
        if not self._db_features:
            return None, 0.0

        feature = feature / np_local.linalg.norm(feature)
        best_id = None
        best_score = 0.0
        threshold = 0.75

        for i, db_feat in self._db_features.items():
            db_feat = db_feat / np_local.linalg.norm(db_feat)
            score = np_local.dot(feature, db_feat) / 2 + 0.5
            if score > best_score and score >= threshold:
                best_score = score
                best_id = i
        return best_id, best_score

    # ── 数据发送 ──────────────────────────────────

    def _send_recognition_data(self):
        """组装4组识别数据并通过 UART 发送"""
        # ⚠️ 临时隔离测试：关闭 UART 发送，验证 UART1 引脚(40/41)是否与
        # sensor/ISP 冲突致 AI 线程偶发卡死。验证后恢复。
        return
        slots = [None, None, None, None]

        for i, (box, matched_id, score) in enumerate(self._recognition_results):
            if i >= 4:
                break
            x, y, w, h = [int(v) for v in box[:4]]
            conf = int(score * 100)
            fid = matched_id if matched_id is not None else 0
            slots[i] = (fid, x, y, w, h, conf)

        try:
            self.rt.host.send_face_data(slots)
        except Exception as e:
            print(f"[FaceDetect] send error: {e}")

    # ── UI 构建 ──────────────────────────────────

    def _build_ui(self):
        screen = lv.scr_act()
        screen.set_style_bg_opa(0, 0)  # 透明透出 OSD1 相机画面
        self._screen = screen

        self._build_top_bar()
        self._build_preview_area()   # 保留：建 preview_bg 容器（toast/弹窗挂点）
        self._build_bottom_bar()
        # ⚠️ 临时验证：隐藏顶/底栏 LVGL 对象，让 LVGL 屏空，验证 show_image(OSD1)
        # 是否还卡（根因假设：LVGL flush 撞 show_image DMA）。验证后恢复。
        if self._top_bar is not None:
            self._top_bar.add_flag(lv.obj.FLAG.HIDDEN)
        if self._bottom_bar is not None:
            self._bottom_bar.add_flag(lv.obj.FLAG.HIDDEN)
        # 十字架+人脸框改画到 image.Image（_draw_overlay），不再用 LVGL 对象

    # ── 顶栏 ──────────────────────────────────────

    def _build_top_bar(self):
        """顶栏：返回按钮(左) + 标题(居中) — 复用 Camera APP 模式"""
        lang = self.rt.lang
        bar = lv.obj(self._screen)
        bar.set_size(lv.pct(100), BAR_H)
        bar.set_pos(0, 0)
        bar.set_style_bg_color(lv.color_hex(BAR_BG), 0)
        bar.set_style_bg_opa(255, 0)
        bar.set_style_border_width(0, 0)
        bar.set_style_pad_all(0, 0)
        bar.set_style_radius(0, 0)
        bar.clear_flag(lv.obj.FLAG.SCROLLABLE)
        self._top_bar = bar

        # 返回按钮
        btn = lv.obj(bar)
        btn.set_size(BTN_SIZE, BTN_SIZE)
        btn.align(lv.ALIGN.LEFT_MID, 2, 0)
        btn.set_style_bg_opa(0, 0)
        btn.set_style_border_width(0, 0)
        btn.set_style_shadow_width(0, 0)
        btn.set_style_outline_width(0, 0)
        btn.set_style_outline_opa(0, 0)
        btn.set_style_pad_all(0, 0)
        btn.clear_flag(lv.obj.FLAG.SCROLLABLE)
        btn.add_flag(lv.obj.FLAG.CLICKABLE)
        self._back_btn = btn  # ⚠️ 必须挂 self：防 GC 误杀 LVGL Python 包装器

        icon_data, icon_dsc = icon_cache.get_camera_icon("back")
        if icon_data is not None and icon_dsc is not None:
            img, _ = _make_icon(btn, icon_data, icon_dsc, ICON_TARGET, 4, 0)
            self._back_icon = img  # ⚠️ 必须挂 self：防 GC 误杀 lv.img 包装器
            self._back_fallback_label = None
        else:
            lbl = lv.label(btn)
            lbl.set_text(lang.t("face_detect.back_fb"))
            lbl.center()
            self._back_icon = None
            self._back_fallback_label = lbl  # ⚠️ 必须挂 self

        # 返回按钮：设退出标志（主循环检测后退出 run）。reset 框架无 ctx.request_exit
        btn.add_event(
            lambda e: setattr(self, "_exit_requested", True)
            if e.get_code() == lv.EVENT.CLICKED else None,
            lv.EVENT.CLICKED, None)

        # 标题居中
        title = lv.label(bar)
        title.set_text(lang.t("category.face_detect"))
        title.align(lv.ALIGN.CENTER, 0, 0)
        title_style = make_back_bar_text_style(fonts.body)
        title.add_style(title_style, 0)
        self._title_label = title

    # ── 预览区（含十字架）──────────────────────────

    def _build_preview_area(self):
        """预览区：透明背景透出 OSD1 相机画面 + 绿色十字架"""
        preview = lv.obj(self._screen)
        preview.set_size(lv.pct(100), PREVIEW_H)
        preview.set_pos(0, PREVIEW_Y)
        preview.set_style_bg_opa(0, 0)
        preview.set_style_border_width(0, 0)
        preview.set_style_pad_all(0, 0)
        preview.set_style_radius(0, 0)
        preview.clear_flag(lv.obj.FLAG.SCROLLABLE)
        preview.clear_flag(lv.obj.FLAG.CLICKABLE)
        self._preview_bg = preview

    # ── 底栏 ──────────────────────────────────────

    def _build_bottom_bar(self):
        """底栏：list图标(左) + 状态文字(中)"""
        lang = self.rt.lang
        bar = lv.obj(self._screen)
        bar.set_size(lv.pct(100), BAR_H)
        bar.set_pos(0, PREVIEW_Y + PREVIEW_H)
        bar.set_style_bg_color(lv.color_hex(BAR_BG), 0)
        bar.set_style_bg_opa(255, 0)
        bar.set_style_border_width(0, 0)
        bar.set_style_pad_all(0, 0)
        bar.set_style_radius(0, 0)
        bar.clear_flag(lv.obj.FLAG.SCROLLABLE)
        self._bottom_bar = bar

        # ── list.png 按钮（左侧）──
        list_btn = lv.obj(bar)
        list_btn.set_size(BTN_SIZE, BTN_SIZE)
        list_btn.align(lv.ALIGN.LEFT_MID, 24, 0)
        list_btn.set_style_bg_opa(0, 0)
        list_btn.set_style_border_width(0, 0)
        list_btn.set_style_shadow_width(0, 0)
        list_btn.set_style_outline_width(0, 0)
        list_btn.set_style_outline_opa(0, 0)
        list_btn.set_style_pad_all(0, 0)
        list_btn.clear_flag(lv.obj.FLAG.SCROLLABLE)
        list_btn.add_flag(lv.obj.FLAG.CLICKABLE)
        self._list_btn = list_btn  # ⚠️ 必须挂 self：防 GC 误杀 LVGL Python 包装器

        icon_data, icon_dsc = icon_cache.get_face_icon("list")
        if icon_data is not None and icon_dsc is not None:
            img, _ = _make_icon(list_btn, icon_data, icon_dsc, ICON_TARGET, 4, 0)
            self._list_icon = img  # ⚠️ 必须挂 self：防 GC 误杀 lv.img 包装器
            self._list_fallback_label = None
        else:
            # 图标加载失败时用文字代替
            lbl = lv.label(list_btn)
            lbl.set_text(lang.t("face_detect.list_fb"))
            lbl.center()
            self._list_icon = None
            self._list_fallback_label = lbl  # ⚠️ 必须挂 self

        list_btn.add_event(
            lambda e: self._on_list_click()
            if e.get_code() == lv.EVENT.CLICKED else None,
            lv.EVENT.CLICKED, None)

        # ── 状态文字（中间）──
        self._status_label = lv.label(bar)
        self._status_label.align(lv.ALIGN.CENTER, 0, 0)
        self._update_status_text()

    def _update_status_text(self):
        """更新底栏中间的状态文字"""
        if self._status_label is None:
            return
        lang = self.rt.lang
        registered = sorted(self._db_features.keys())
        if registered:
            ids_text = " ".join(f"ID{i}" for i in registered)
            text = lang.t("face_detect.registered") + ": " + ids_text
        else:
            text = lang.t("face_detect.press_k2")
        self._status_label.set_text(text)
        style = make_back_bar_text_style(fonts.body)
        self._status_label.add_style(style, 0)

    # ── 弹出菜单 ──────────────────────────────────

    def _on_list_click(self):
        if self._popup is not None:
            self._dismiss_popup()
            return
        self._show_popup()

    def _show_popup(self):
        """从底栏上方升起一个与底栏等宽高的浮窗，含"保存"和"清除"两个按钮。

        - 尺寸：100% × BAR_H（与底栏一致）
        - 位置：BOTTOM_MID, y = -(BAR_H + 8)，即正好浮在底栏正上方 8px
        - 关闭：点击中屏（蒙层 dismiss_overlay）→ 关闭
        - 蒙层透明铺满预览区，仅截获点击；不影响 OSD1 相机预览
        """
        # 全屏透明蒙层：吃点击 → dismiss popup（不挡相机预览，bg_opa=0）
        # 蒙层覆盖整屏，但因为后续 popup 也挂 self._screen 且后建，
        # 默认 z 序在蒙层之上 → 点 popup 内按钮不会被蒙层吞。
        overlay = lv.obj(self._screen)
        overlay.set_size(lv.pct(100), lv.pct(100))
        overlay.set_pos(0, 0)
        overlay.set_style_bg_opa(0, 0)
        overlay.set_style_border_width(0, 0)
        overlay.set_style_pad_all(0, 0)
        overlay.set_style_radius(0, 0)
        overlay.clear_flag(lv.obj.FLAG.SCROLLABLE)
        overlay.add_flag(lv.obj.FLAG.CLICKABLE)
        overlay.add_event(
            lambda e: self._dismiss_popup()
            if e.get_code() == lv.EVENT.CLICKED else None,
            lv.EVENT.CLICKED, None)
        self._popup_overlay = overlay

        # 浮窗：与底栏等宽高，浮在底栏上方 8px
        popup = lv.obj(self._screen)
        popup.set_size(lv.pct(100), BAR_H)
        popup.align(lv.ALIGN.BOTTOM_MID, 0, -(BAR_H + 8))
        popup.set_style_bg_color(lv.color_hex(BAR_BG), 0)
        popup.set_style_bg_opa(240, 0)
        popup.set_style_radius(0, 0)
        popup.set_style_border_width(0, 0)
        popup.set_style_pad_all(0, 0)
        popup.clear_flag(lv.obj.FLAG.SCROLLABLE)
        # popup 本身不需要 CLICKABLE — 蒙层在它下层，点 popup 空白处不会
        # 触发蒙层 CLICKED（事件冒泡到 popup 后被自身吞掉，因为 popup 默认
        # 不 CLICKABLE 也不冒泡到父）。点按钮才走按钮的 CLICKED 回调。
        self._popup = popup

        lang = self.rt.lang
        # 两个按钮各占一半宽度
        items = [
            (lang.t("face_detect.save"), lambda: self._on_save()),
            (lang.t("face_detect.clear"), lambda: self._on_clear()),
        ]
        half_w = 320  # 屏宽 640 / 2
        for i, (text, callback) in enumerate(items):
            btn = lv.obj(popup)
            btn.set_size(half_w, BAR_H)
            btn.set_pos(i * half_w, 0)
            btn.set_style_bg_opa(0, 0)
            btn.set_style_border_width(0, 0)
            btn.set_style_radius(0, 0)
            btn.set_style_pad_all(0, 0)
            btn.clear_flag(lv.obj.FLAG.SCROLLABLE)
            btn.add_flag(lv.obj.FLAG.CLICKABLE)

            lbl = lv.label(btn)
            lbl.set_text(text)
            lbl.center()
            lbl.set_style_text_color(lv.color_hex(WHITE), 0)
            lbl.add_style(make_back_bar_text_style(fonts.body), 0)

            btn.add_event(
                lambda e, cb=callback: cb()
                if e.get_code() == lv.EVENT.CLICKED else None,
                lv.EVENT.CLICKED, None)

    def _dismiss_popup(self):
        """关闭浮窗 + 蒙层"""
        if self._popup is not None:
            try:
                self._popup.delete()
            except Exception:
                pass
            self._popup = None
        # 蒙层后删除（避免 popup 子 obj 引用混乱）
        overlay = getattr(self, '_popup_overlay', None)
        if overlay is not None:
            try:
                overlay.delete()
            except Exception:
                pass
            self._popup_overlay = None

    def _on_save(self):
        """保存按钮：持久化所有人脸数据 + 显示"保存成功"提示

        写入由 run 末尾 _flush_db 统一执行（此时 lv.task_handler 已返回，
        DMA 空闲）。此方法仅确认脏标记。K2 注册已自动设脏，按保存按钮
        可额外保证。
        """
        self._save_db()  # 确保 _db_dirty=True（K2 注册已设）
        if self.rt.buzzer is not None:
            self.rt.buzzer.beep(ms=50)
        self._dismiss_popup()
        self._show_toast(self.rt.lang.t("face_detect.save_success"), duration_ms=1200)

    def _on_clear(self):
        """清除按钮：删除全部人脸数据"""
        self._clear_db()
        self._update_status_text()
        if self.rt.buzzer is not None:
            self.rt.buzzer.beep(ms=100)
        self._dismiss_popup()

    # ── Toast 提示 ─────────────────────────────────

    def _show_toast(self, text, duration_ms=1200):
        """白底黑字提示框，显示在屏幕中央上半，duration_ms 后自动消失。

        非阻塞：保存到期时间戳，由 on_frame 每帧检查超时销毁。
        重复调用会重置上一个 toast。
        """
        import time as _time
        # 先清掉旧 toast（避免叠加）
        self._dismiss_toast()

        toast = lv.obj(self._screen)
        toast.set_size(220, 36)
        toast.align(lv.ALIGN.TOP_MID, 0, BAR_H + 8)
        toast.set_style_bg_color(lv.color_hex(WHITE), 0)
        toast.set_style_bg_opa(255, 0)
        toast.set_style_radius(10, 0)
        toast.set_style_border_width(0, 0)
        toast.set_style_pad_all(0, 0)
        toast.clear_flag(lv.obj.FLAG.SCROLLABLE)
        toast.clear_flag(lv.obj.FLAG.CLICKABLE)

        lbl = lv.label(toast)
        lbl.set_text(text)
        lbl.center()
        lbl.set_style_text_color(lv.color_hex(0x000000), 0)
        lbl.add_style(make_back_bar_text_style(fonts.body), 0)

        self._toast = toast
        self._toast_expire_ticks = _time.ticks_add(_time.ticks_ms(),
                                                    duration_ms)

    def _dismiss_toast(self):
        """立即销毁 toast"""
        if self._toast is not None:
            try:
                self._toast.delete()
            except Exception:
                pass
            self._toast = None

    def _tick_toast(self):
        """run 主循环调用：超时即销毁"""
        if self._toast is None:
            return
        import time as _time
        if _time.ticks_diff(_time.ticks_ms(),
                            self._toast_expire_ticks) >= 0:
            self._dismiss_toast()

    # ── 销毁 ──────────────────────────────────

    def _destroy_ui(self):
        """释放所有 LVGL 对象"""
        print("[FaceDetect] _destroy_ui: enter")
        print("[FaceDetect] _destroy_ui: popup")
        self._dismiss_popup()
        self._dismiss_toast()

        for attr in ('_top_bar', '_bottom_bar', '_preview_bg'):
            print("[FaceDetect] _destroy_ui: deleting %s" % attr)
            obj = getattr(self, attr, None)
            if obj is not None:
                try:
                    obj.delete()
                except Exception:
                    pass
                setattr(self, attr, None)

        self._status_label = None
        self._title_label = None

        # 清除挂 self 的 LVGL 对象引用
        print("[FaceDetect] _destroy_ui: clearing refs")
        self._back_btn = None
        self._back_icon = None
        self._back_fallback_label = None
        self._list_btn = None
        self._list_icon = None
        self._list_fallback_label = None

        # 恢复屏幕背景不透明
        print("[FaceDetect] _destroy_ui: restoring bg")
        try:
            scr = lv.scr_act()
            scr.set_style_bg_color(lv.color_hex(Colors.BG), 0)
            scr.set_style_bg_opa(255, 0)
        except Exception:
            pass
        self._screen = None
        print("[FaceDetect] _destroy_ui: done")


# ═══════════════════════════════════════════════════════
# reset 框架模块入口
# ═══════════════════════════════════════════════════════

def run(runtime):
    """face_detect 独立脚本入口（reset 切换框架）。

    main.py 脚本模式：runtime.init_app("face_detect", fpioa) → 本模块 run(runtime)。
    构造 FaceDetectApp(runtime) 并调其 run() 实例方法。返回后 main.py 清
    .next_script + machine.reset() 回主菜单。
    """
    app = FaceDetectApp(runtime)
    app.run()
