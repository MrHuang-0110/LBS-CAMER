# scripts/face_detect/app.py — face recognition (register + match + clear) on template.
#
# Single-thread loop: snapshot chn0 → on_frame(chn2 detect + reg + match + draw) →
# show_image → lv.task_handler. K2 short-press registers; bottom-bar list overlay
# clears. All disk I/O deferred to exit (pitfall #2). Per-frame gc (pitfall #16).

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
TITLE_TEXT = "人脸识别"

_RUNTIME = None
_screen = None
_top_bar = None
_bottom_bar = None
_preview = None
_face_det = None
_face_reg = None
_db_features = {}
_id_registry = None
_count_label = None
_overlay = None
_clear_btn = None
_save_btn = None
_close_overlay = False


def _init_ai():
    """Load BOTH kmodels + db_features before the loop.

    ⚠️ 双 kmodel 顺序根因（板端 frame1 卡死黑屏）：face_reg kmodel 必须在
    face_det.config_preprocess() 之前加载。若 config_preprocess（build face_det
    AI2D）先执行，再加载第二 kmodel 会破坏共享 NPU/AI2D 状态 → 后续 face_det.run
    卡死。对齐旧 Step4 修订：两个 kmodel 都先加载，再 config_preprocess。坑#19。
    """
    global _face_det, _face_reg, _db_features
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


def _init_registry(fpioa):
    global _id_registry
    _id_registry = IdRegistry(fpioa, pin=0)


def _deinit_ai():
    """Release NPU models after the main loop exits (flush handled in run())."""
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
    """Detect on chn2, recognize + register largest face, draw onto chn0 preview."""
    if _RUNTIME is None or _face_det is None:
        return
    img_ai = _RUNTIME.sensor.snapshot(chn=CAM_CHN_ID_2)
    img_np = img_ai.to_numpy_ref()
    det_boxes, landms = _face_det.run(img_np)

    recognition_results = []
    if det_boxes and landms and _face_reg is not None:
        try:
            max_i = max(range(len(det_boxes)),
                        key=lambda i: det_boxes[i][2] * det_boxes[i][3])
            _face_reg.config_preprocess(landms[max_i])
            feature = _face_reg.run(img_np)
            matched_id = database_search(feature, _db_features)
            recognition_results.append((max_i, matched_id))
            if _id_registry is not None and _id_registry.has_pending():
                slot = _id_registry.try_register(feature, _RUNTIME.buzzer)
                if slot is not None:
                    _db_features[slot] = feature
                    recognition_results = [(max_i, slot)]
                    _refresh_count()
        except Exception as e:
            print("[face_detect] recog error: %s" % e)

    _face_det.draw_result(img, det_boxes, recognition_results)
    gc.collect()


def _refresh_count():
    if _count_label is not None:
        try:
            _count_label.set_text("已注册 %d/4" % len(_db_features))
        except Exception:
            pass


def _build_ui(runtime, exit_flag):
    global _screen, _top_bar, _bottom_bar, _preview, _count_label
    screen = lv.scr_act()
    screen.set_style_bg_opa(0, 0)
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
    btn.set_size(48, 48)
    btn.align(lv.ALIGN.LEFT_MID, 2, 0)
    btn.set_style_bg_opa(0, 0)
    btn.set_style_border_width(0, 0)
    btn.set_style_shadow_width(0, 0)
    btn.set_style_outline_width(0, 0)
    btn.set_style_outline_opa(0, 0)
    btn.set_style_pad_all(0, 0)
    btn.clear_flag(lv.obj.FLAG.SCROLLABLE)
    btn.add_flag(lv.obj.FLAG.CLICKABLE)

    icon_data, icon_dsc = icon_cache.get_back_icon()
    if icon_dsc is not None and icon_data is not None:
        import struct
        w = h = 64
        if len(icon_data) >= 24:
            w = struct.unpack('>I', icon_data[16:20])[0]
            h = struct.unpack('>I', icon_data[20:24])[0]
        target = int(48 * 0.85)
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
            exit_flag[0] = True
    btn.add_event(_on_back, lv.EVENT.CLICKED, None)

    title = lv.label(_top_bar)
    title.set_text(TITLE_TEXT)
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

    # list 图标按钮（底栏左侧）→ 弹出清除/保存浮层
    list_btn = lv.obj(_bottom_bar)
    list_btn.set_size(48, 48)
    list_btn.align(lv.ALIGN.LEFT_MID, 2, 0)
    list_btn.set_style_bg_opa(0, 0)
    list_btn.set_style_border_width(0, 0)
    list_btn.set_style_pad_all(0, 0)
    list_btn.clear_flag(lv.obj.FLAG.SCROLLABLE)
    list_btn.add_flag(lv.obj.FLAG.CLICKABLE)
    list_lbl = lv.label(list_btn)
    list_lbl.set_text("list")
    list_lbl.center()
    list_btn.add_event(_on_list_clicked, lv.EVENT.CLICKED, None)

    _count_label = lv.label(_bottom_bar)
    _count_label.set_text("已注册 %d/4" % len(_db_features))
    _count_label.align(lv.ALIGN.CENTER, 0, 0)
    from ui.theme import make_back_bar_text_style as _bbts
    _count_label.add_style(_bbts(fonts.body), 0)


def _on_list_clicked(e):
    """Open the clear/save overlay. Does not touch disk."""
    global _overlay, _clear_btn, _save_btn
    if e.get_code() != lv.EVENT.CLICKED:
        return
    if _overlay is not None:
        return
    _overlay = lv.obj(lv.scr_act())
    _overlay.set_size(640, BAR_H)
    _overlay.set_pos(0, PREVIEW_Y + PREVIEW_H)
    _overlay.set_style_bg_color(lv.color_hex(BAR_BG), 0)
    _overlay.set_style_bg_opa(255, 0)
    _overlay.set_style_border_width(0, 0)
    _overlay.set_style_pad_all(0, 0)
    _overlay.set_style_radius(0, 0)
    _overlay.clear_flag(lv.obj.FLAG.SCROLLABLE)

    _clear_btn = lv.btn(_overlay)
    _clear_btn.set_size(120, 40)
    _clear_btn.align(lv.ALIGN.LEFT_MID, 20, 0)
    cl = lv.label(_clear_btn)
    cl.set_text("清除")
    cl.center()
    _clear_btn.add_event(_on_clear_clicked, lv.EVENT.CLICKED, None)

    _save_btn = lv.btn(_overlay)
    _save_btn.set_size(120, 40)
    _save_btn.align(lv.ALIGN.RIGHT_MID, -20, 0)
    sv = lv.label(_save_btn)
    sv.set_text("保存")
    sv.center()
    _save_btn.add_event(_on_save_clicked, lv.EVENT.CLICKED, None)


def _on_clear_clicked(e):
    """Clear memory + flag close. No disk I/O, no overlay delete here (deferred)."""
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
    """No-op persistence (auto on exit). Just close overlay (deferred)."""
    global _close_overlay
    if e.get_code() != lv.EVENT.CLICKED:
        return
    _close_overlay = True


def _process_overlay_close():
    """Main-loop deferred overlay close (LVGL use-after-free guard)."""
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


def _destroy_ui():
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
            time.sleep_ms(lv.task_handler())
            fc += 1
            if fc % 30 == 0:
                print("[face_detect] fc=%d" % fc)
    finally:
        try:
            face_db.flush_to_disk()
        except Exception as e:
            print("[face_detect] persist warning: %s" % e)
        _deinit_ai()
        _destroy_ui()
        _RUNTIME = None
