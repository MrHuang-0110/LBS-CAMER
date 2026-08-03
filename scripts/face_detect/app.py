# scripts/face_detect/app.py — Phase 1 face detection on the single-thread template.
#
# Core loop: snapshot chn0 → on_frame(chn2 AI detect + draw boxes) → show_image →
# lv.task_handler. No AI thread, no self media init, no registration/DB in Phase 1.

import gc
import os
import sys
import time
import lvgl as lv
import ulab.numpy as np
from media.display import Display
from media.sensor import CAM_CHN_ID_0, CAM_CHN_ID_2
from core.icon_cache import icon_cache
from core.font_manager import fonts
from core.face_ai import FaceDetectionApp, FaceRegistrationApp, RGB888P_SIZE, DISPLAY_SIZE
from core.face_db import face_db, database_search
from core.id_registry import IdRegistry

BAR_H = 52
PREVIEW_Y = BAR_H
PREVIEW_H = 376
BAR_BG = 0x1A1A1A
# 无人脸时检测降频:静止画面结果不变,减少 NPU 原生缓冲分配频率(坑#16 类累积缓解)
DET_IDLE_INTERVAL = 2
# 多人性能保护:每帧最多识别脸数(与协议 4 槽对齐),超出只画框
REG_MAX_FACES = 4
REG_INTERVAL_2 = 2   # 2~3 人:每 2 帧识别一轮(检测仍每帧跑)
REG_INTERVAL_3 = 3   # ≥4 人:每 3 帧识别一轮
MIN_REG_AREA = 1600  # 注册最小脸面积(VGA px²,≈40×40);太小拒绝注册(特征质量差)

_RUNTIME = None
_screen = None
_top_bar = None
_bottom_bar = None
_preview = None
_face_det = None
_face_reg = None
_db_features = {}
_count_label = None
_id_registry = None
_overlay = None
_clear_btn = None
_save_btn = None
_close_overlay = False
_reg_counter = 0     # 多人跳帧计数(每 interval 帧识别一轮)
_last_slots = None   # 上轮识别槽位(非识别帧复用,保持主机数据连续)
_det_counter = 0     # 无人脸时检测跳帧计数
_last_det = ([], []) # 上轮检测结果缓存(det_boxes, landms)





def _init_ai():
    """Load BOTH kmodels + db_features before the loop.

    ⚠️ 双 kmodel 顺序根因：face_reg kmodel 必须在 face_det.config_preprocess()
    之前加载，否则破坏共享 NPU/AI2D 状态。坑#19。
    kmodel 加载整体包 try/except：任一 kmodel 失败则 _ai_ready=False，
    on_frame 跳过推理，不崩溃。
    """
    global _face_det, _face_reg, _db_features, _ai_ready
    _ai_ready = False
    try:
        anchors_path = "/sdcard/examples/utils/prior_data_320.bin"
        det_kmodel = "/sdcard/examples/kmodel/face_detection_320.kmodel"
        reg_kmodel = "/sdcard/examples/kmodel/face_recognition_mobile.kmodel"
        print("[face_detect] loading anchors...")
        anchors = np.fromfile(anchors_path, dtype=np.float)
        anchors = anchors.reshape((4200, 4))
        print("[face_detect] loading det kmodel...")
        _face_det = FaceDetectionApp(det_kmodel, model_input_size=[320, 320], anchors=anchors,
                                     confidence_threshold=0.5, nms_threshold=0.2,
                                     rgb888p_size=RGB888P_SIZE, display_size=DISPLAY_SIZE,
                                     debug_mode=0)
        print("[face_detect] loading reg kmodel...")
        try:
            _face_reg = FaceRegistrationApp(reg_kmodel, model_input_size=[112, 112],
                                            rgb888p_size=RGB888P_SIZE, debug_mode=0)
            print("[face_detect] reg kmodel ready (512-dim)")
        except Exception as e:
            print("[face_detect] reg kmodel FAILED: %s" % e)
            sys.print_exception(e)
            _face_reg = None
        # 两个 kmodel 都加载完，才 build face_det 的 AI2D（顺序根因）
        _face_det.config_preprocess()
        _db_features = face_db.init_features()
        print("[face_detect] db loaded: %d face(s)" % len(_db_features))
        _ai_ready = True
        print("[face_detect] AI ready")
    except Exception as e:
        print("[face_detect] _init_ai FAILED: %s" % e)
        sys.print_exception(e)
        _face_det = None
        _face_reg = None
        _ai_ready = False

def _init_registry(fpioa):
    global _id_registry
    _id_registry = IdRegistry(fpioa, pin=0)


def _deinit_ai():
    """Best-effort AI cleanup after the main loop exits."""
    global _face_det, _face_reg
    if _face_det is not None:
        try:
            _face_det.deinit()
        except Exception as e:
            print("[face_detect] det deinit warning: %s" % e)
        _face_det = None
    if _face_reg is not None:
        try:
            _face_reg.deinit()
        except Exception as e:
            print("[face_detect] reg deinit warning: %s" % e)
        _face_reg = None


def on_frame(img):
    """Detect on chn2, recognize ALL faces, draw onto chn0 preview, push 4 slots.

    识全部脸：对每个检测框跑 reg + database_search，匹配的 DB slot 填对应组。
    K2 注册仍取最大脸（注册语义不变）。每帧推送4槽位给上位机。
    ⚠️ 多脸 reg 为板端首次验证（坑#16 NPU 累积风险，见 spec 降级方案）。
    """
    if _RUNTIME is None or _face_det is None:
        return
    global _reg_counter, _last_slots, _det_counter, _last_det
    img_ai = _RUNTIME.sensor.snapshot(chn=CAM_CHN_ID_2)
    img_np = img_ai.to_numpy_ref()
    det_boxes, landms = _last_det
    # 无人脸时检测跳帧(有脸每帧保实时,无脸每 2 帧一次),降低 NPU 原生缓冲分配
    if det_boxes:
        _det_counter = 0
        do_det = True
    else:
        _det_counter += 1
        do_det = (_det_counter % DET_IDLE_INTERVAL == 0)
    if do_det:
        det_boxes, landms = _face_det.run(img_np)
        _last_det = (det_boxes, landms)
        gc.collect()  # det 后立即回收 NPU 原生缓冲(坑#16:运动多人时防帧内峰值累积)

    recognition_results = []
    slots = [None, None, None, None]
    global _reg_counter, _last_slots
    n_faces = len(det_boxes) if det_boxes else 0
    k2_pending = _id_registry is not None and _id_registry.has_pending()
    # 多人跳帧:检测每帧跑,reg 按人数降频(1人每帧/2~3人隔帧/≥4人隔两帧)。
    # 按 K2 注册时强制本轮识别(注册优先)。
    interval = 1
    if n_faces >= 4:
        interval = REG_INTERVAL_3
    elif n_faces >= 2:
        interval = REG_INTERVAL_2
    do_reg = interval == 1 or _reg_counter == 0 or k2_pending
    _reg_counter = (_reg_counter + 1) % interval
    if det_boxes and landms and _face_reg is not None and do_reg:
        # 识别前 REG_MAX_FACES 张(超出只画框,防极端多人卡死)
        for i in range(min(len(det_boxes), REG_MAX_FACES)):
            try:
                _face_reg.config_preprocess(landms[i])
                feature = _face_reg.run(img_np)
                gc.collect()  # 每张脸推理后立即回收(坑#16:多人/运动时防帧内原生缓冲峰值叠加致 kpu.run 永久阻塞)
                mid, score = database_search(feature, _db_features)
                if mid is not None:
                    recognition_results.append((i, mid))
                    det = det_boxes[i]
                    x, y, w, h = det[:4]
                    x = int(x * _face_det.display_size[0] // _face_det.rgb888p_size[0])
                    y = int(y * _face_det.display_size[1] // _face_det.rgb888p_size[1])
                    w = int(w * _face_det.display_size[0] // _face_det.rgb888p_size[0])
                    h = int(h * _face_det.display_size[1] // _face_det.rgb888p_size[1])
                    conf = int(score * 100)  # 置信度=识别匹配度(0-100),非检测框分数
                    if 1 <= mid <= 4:
                        slots[mid - 1] = (mid, x, y, w, h, conf)
            except Exception as e:
                print("[face_detect] recog error: %s" % e)
        # K2 注册：注册当前帧最大脸（注册语义不变）
        if k2_pending:
            max_i = max(range(len(det_boxes)),
                        key=lambda j: det_boxes[j][2] * det_boxes[j][3])
            det = det_boxes[max_i]
            w_vga = int(det[2] * _face_det.display_size[0] // _face_det.rgb888p_size[0])
            h_vga = int(det[3] * _face_det.display_size[1] // _face_det.rgb888p_size[1])
            if w_vga * h_vga < MIN_REG_AREA:
                # 脸太小,特征质量差:拒绝注册(长音提示失败)
                if _RUNTIME is not None and _RUNTIME.buzzer is not None:
                    _RUNTIME.buzzer.beep(ms=300)
            else:
                try:
                    _face_reg.config_preprocess(landms[max_i])
                    feature = _face_reg.run(img_np)
                    gc.collect()  # 注册推理后立即回收(坑#16,与识别循环同策略)
                    slot = _id_registry.try_register(feature, _RUNTIME.buzzer)
                    if slot is not None:
                        face_db.flush_to_disk()  # 注册即写（on_frame 内，task_handler 前，坑#2 安全窗口）
                        _db_features[slot] = feature
                        recognition_results.append((max_i, slot))
                        if 1 <= slot <= 4:
                            slots[slot - 1] = (slot, 0, 0, 0, 0, 0)
                        _refresh_count()
                except Exception as e:
                    print("[face_detect] register error: %s" % e)
    # 非识别帧:复用上轮槽位,保持主机数据连续(坐标滞后≤interval 帧,可接受)
    if not do_reg and _last_slots is not None:
        slots = _last_slots
    else:
        _last_slots = slots

    # 屏幕居中绿色十字(对准参考,小一点):VGA 640x480 中心 (320, 240)
    img.draw_cross(320, 240, color=(0xFF, 0x00, 0xFF, 0x00), size=20, thickness=2)

    _face_det.draw_result(img, det_boxes, recognition_results)
    if _RUNTIME is not None and _RUNTIME.host is not None:
        _RUNTIME.host_tick(slots)


def _refresh_count():
    if _count_label is not None:
        try:
            _count_label.set_text(_RUNTIME.lang.t("face_detect.registered", len(_db_features)))
        except Exception:
            pass


def _on_list_clicked(e):
    """弹出清除/保存浮层（叠加在底栏上方）。"""
    global _overlay, _clear_btn, _save_btn
    if e.get_code() != lv.EVENT.CLICKED:
        return
    if _overlay is not None:
        return
    from ui.theme import make_back_bar_text_style
    _overlay = lv.obj(lv.scr_act())
    _overlay.set_size(lv.pct(100), BAR_H)
    _overlay.set_pos(0, PREVIEW_Y + PREVIEW_H - BAR_H)
    _overlay.set_style_bg_color(lv.color_hex(BAR_BG), 0)
    _overlay.set_style_bg_opa(255, 0)
    _overlay.set_style_border_width(0, 0)
    _overlay.set_style_pad_all(0, 0)
    _overlay.set_style_radius(0, 0)
    _overlay.clear_flag(lv.obj.FLAG.SCROLLABLE)
    _overlay.add_flag(lv.obj.FLAG.CLICKABLE)
    _overlay.add_event(_on_overlay_clicked, lv.EVENT.CLICKED, None)

    _clear_btn = lv.btn(_overlay)
    _clear_btn.set_size(120, 40)
    _clear_btn.align(lv.ALIGN.LEFT_MID, 20, 0)
    cl = lv.label(_clear_btn)
    cl.set_text(_RUNTIME.lang.t("face_detect.clear"))
    cl.add_style(make_back_bar_text_style(fonts.body), 0)
    cl.center()
    _clear_btn.add_event(_on_clear_clicked, lv.EVENT.CLICKED, None)

    _save_btn = lv.btn(_overlay)
    _save_btn.set_size(120, 40)
    _save_btn.align(lv.ALIGN.RIGHT_MID, -20, 0)
    sv = lv.label(_save_btn)
    sv.set_text(_RUNTIME.lang.t("face_detect.save"))
    sv.add_style(make_back_bar_text_style(fonts.body), 0)
    sv.center()
    _save_btn.add_event(_on_save_clicked, lv.EVENT.CLICKED, None)


def _on_overlay_clicked(e):
    """点浮层空白处关闭浮层（点清除/保存按钮时按钮消费事件，不触发此）。"""
    global _close_overlay
    if e.get_code() != lv.EVENT.CLICKED:
        return
    _close_overlay = True


def _on_screen_clicked(e):
    """点 screen 任意位置关闭浮层（浮层开着时）。
    点 list/清除/保存按钮时按钮消费 CLICKED 不冒泡到 screen，不误触发。"""
    global _close_overlay
    if e.get_code() != lv.EVENT.CLICKED:
        return
    if _overlay is not None:
        _close_overlay = True


def _on_clear_clicked(e):
    """清除内存特征 + 标志关闭浮层。不删盘（持久化待定）。"""
    global _close_overlay
    if e.get_code() != lv.EVENT.CLICKED:
        return
    face_db.clear()
    _db_features.clear()
    _refresh_count()
    if _RUNTIME is not None and _RUNTIME.buzzer is not None:
        _RUNTIME.buzzer.beep(ms=200)
    _close_overlay = True


def _on_save_clicked(e):
    """空操作（退出自动持久化，当前 no-op）。只标志关闭浮层。"""
    global _close_overlay
    if e.get_code() != lv.EVENT.CLICKED:
        return
    _close_overlay = True


def _process_overlay_close():
    """主循环 deferred 关闭浮层（LVGL use-after-free 防护）。"""
    global _overlay, _clear_btn, _save_btn, _close_overlay
    if not _close_overlay:
        return
    _close_overlay = False
    for obj in (_clear_btn, _save_btn, _overlay):
        if obj is not None:
            try:
                obj.delete()
            except Exception:
                pass
    _clear_btn = None
    _save_btn = None
    _overlay = None


def _build_ui(runtime, exit_flag):
    """Build top bar, transparent preview area, and empty bottom bar."""
    global _screen, _top_bar, _bottom_bar, _preview, _count_label
    screen = lv.scr_act()
    screen.set_style_bg_opa(0, 0)
    screen.add_flag(lv.obj.FLAG.CLICKABLE)
    screen.add_event(_on_screen_clicked, lv.EVENT.CLICKED, None)
    _screen = screen

    _top_bar = lv.obj(screen)
    _top_bar.set_size(lv.pct(100), BAR_H)
    _top_bar.set_pos(0, 0)
    _top_bar.set_style_bg_color(lv.color_hex(BAR_BG), 0)
    _top_bar.set_style_bg_opa(255, 0)
    _top_bar.set_style_border_width(0, 0)
    _top_bar.set_style_pad_all(0, 0)
    _top_bar.set_style_radius(0, 0)
    _top_bar.clear_flag(lv.obj.FLAG.SCROLLABLE)

    btn = lv.obj(_top_bar)
    btn.set_size(64, 64)
    btn.align(lv.ALIGN.LEFT_MID, 2, 0)
    btn.set_style_bg_opa(0, 0)
    btn.set_style_border_width(0, 0)
    btn.set_style_shadow_width(0, 0)
    btn.set_style_outline_width(0, 0)
    btn.set_style_outline_opa(0, 0)
    btn.set_style_pad_all(0, 0)
    btn.clear_flag(lv.obj.FLAG.SCROLLABLE)
    btn.add_flag(lv.obj.FLAG.CLICKABLE)

    icon_data, icon_dsc = icon_cache.get_face_icon("back")
    if icon_dsc is not None and icon_data is not None:
        import struct
        w = h = 64
        if len(icon_data) >= 24:
            w = struct.unpack('>I', icon_data[16:20])[0]
            h = struct.unpack('>I', icon_data[20:24])[0]
        target = int(64 * 0.85)
        zoom = int(min(target / w, target / h) * 256) if w > 0 and h > 0 else 256
        zoom = max(8, min(zoom, 256))
        icon_img = lv.img(btn)
        icon_img.set_src(icon_dsc)
        icon_img.set_zoom(zoom)
        icon_img.center()
    else:
        lbl = lv.label(btn)
        lbl.set_text("<")
        lbl.center()

    def _on_back(e):
        if e.get_code() == lv.EVENT.CLICKED:
            if _RUNTIME is not None and _RUNTIME.buzzer is not None:
                _RUNTIME.buzzer.beep(ms=50)
            exit_flag[0] = True
    btn.add_event(_on_back, lv.EVENT.CLICKED, None)

    title = lv.label(_top_bar)
    title.set_text(runtime.lang.t("category.face_detect"))
    title.align(lv.ALIGN.CENTER, 0, 0)
    from ui.theme import make_back_bar_text_style
    title.add_style(make_back_bar_text_style(fonts.body), 0)

    _preview = lv.obj(screen)
    _preview.set_size(lv.pct(100), PREVIEW_H)
    _preview.set_pos(0, PREVIEW_Y)
    _preview.set_style_bg_opa(0, 0)
    _preview.set_style_border_width(0, 0)
    _preview.set_style_pad_all(0, 0)
    _preview.set_style_radius(0, 0)
    _preview.clear_flag(lv.obj.FLAG.SCROLLABLE)
    _preview.clear_flag(lv.obj.FLAG.CLICKABLE)

    _bottom_bar = lv.obj(screen)
    _bottom_bar.set_size(lv.pct(100), BAR_H)
    _bottom_bar.set_pos(0, PREVIEW_Y + PREVIEW_H)
    _bottom_bar.set_style_bg_color(lv.color_hex(BAR_BG), 0)
    _bottom_bar.set_style_bg_opa(255, 0)
    _bottom_bar.set_style_border_width(0, 0)
    _bottom_bar.set_style_pad_all(0, 0)
    _bottom_bar.set_style_radius(0, 0)
    _bottom_bar.clear_flag(lv.obj.FLAG.SCROLLABLE)

    # list 图标按钮（底栏左侧）→ 点击弹出清除/保存浮层
    list_btn = lv.obj(_bottom_bar)
    list_btn.set_size(48, 48)
    list_btn.align(lv.ALIGN.LEFT_MID, 2, 0)
    list_btn.set_style_bg_opa(0, 0)
    list_btn.set_style_border_width(0, 0)
    list_btn.set_style_pad_all(0, 0)
    list_btn.clear_flag(lv.obj.FLAG.SCROLLABLE)
    list_btn.add_flag(lv.obj.FLAG.CLICKABLE)
    list_icon_data, list_icon_dsc = icon_cache.get_face_icon("list")
    if list_icon_dsc is not None and list_icon_data is not None:
        import struct
        iw = ih = 64
        if len(list_icon_data) >= 24:
            iw = struct.unpack('>I', list_icon_data[16:20])[0]
            ih = struct.unpack('>I', list_icon_data[20:24])[0]
        ltarget = int(48 * 0.85)
        lzoom = int(min(ltarget / iw, ltarget / ih) * 256) if iw > 0 and ih > 0 else 256
        lzoom = max(8, min(lzoom, 256))
        list_img = lv.img(list_btn)
        list_img.set_src(list_icon_dsc)
        list_img.set_zoom(lzoom)
        list_img.center()
    else:
        list_lbl = lv.label(list_btn)
        list_lbl.set_text("list")
        list_lbl.add_style(make_back_bar_text_style(fonts.body), 0)
        list_lbl.center()
    list_btn.add_event(_on_list_clicked, lv.EVENT.CLICKED, None)

    count_label = lv.label(_bottom_bar)
    count_label.set_text(runtime.lang.t("face_detect.registered", len(_db_features)))
    count_label.add_style(make_back_bar_text_style(fonts.body), 0)
    count_label.align(lv.ALIGN.CENTER, 0, 0)
    _count_label = count_label


def _destroy_ui():
    """Delete LVGL objects and restore screen opacity for the menu."""
    global _screen, _top_bar, _bottom_bar, _preview, _count_label, _overlay, _clear_btn, _save_btn
    for obj in (_overlay, _clear_btn, _save_btn, _top_bar, _bottom_bar, _preview, _count_label):
        if obj is not None:
            try:
                obj.delete()
            except Exception:
                pass
    _overlay = None
    _clear_btn = None
    _save_btn = None
    _top_bar = None
    _bottom_bar = None
    _preview = None
    _count_label = None
    try:
        from ui.theme import Colors
        scr = lv.scr_act()
        scr.set_style_bg_color(lv.color_hex(Colors.BG), 0)
        scr.set_style_bg_opa(255, 0)
    except Exception:
        pass
    _screen = None


def run(runtime):
    """Entry point called by reset-framework main.py."""
    global _RUNTIME
    _RUNTIME = runtime
    exit_flag = [False]
    _init_ai()
    _init_registry(runtime.fpioa)
    _build_ui(runtime, exit_flag)
    fc = 0
    try:
        while not exit_flag[0]:
            os.exitpoint()
            img = runtime.sensor.snapshot(chn=CAM_CHN_ID_0)
            try:
                on_frame(img)
            except Exception as e:
                print("[face_detect] on_frame error: %s" % e)
                try:
                    sys.print_exception(e)
                except Exception:
                    pass
            if _id_registry is not None:
                _id_registry.poll_k2()
            _process_overlay_close()
            Display.show_image(img, 0, 0, Display.LAYER_OSD1)
            gc.collect()  # 放在 show_image 之后、task_handler 之前，避免 AI 推理后立即 GC 阻塞 DMA
            time.sleep_ms(lv.task_handler())
            fc += 1
            if fc % 30 == 0:
                print("[face_detect] fc=%d" % fc)
                if fc % 300 == 0:
                    import gc as _gc; print("[face_detect] mem_free=%d" % _gc.mem_free())
    finally:
        _deinit_ai()
        _destroy_ui()
        _RUNTIME = None
        face_db.flush_to_disk()  # 退出兜底写盘（注册即写已在 on_frame 完成；默认 FACE_DB_PATH）
