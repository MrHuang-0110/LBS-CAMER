# scripts/object_classify/app.py — 物体分类(双 kmodel + 点击锁定 + K2 注册 4 槽 + 协议 0x0A)
#
# 复刻 body_detect 模式: chn2 YOLOv8n 检测任意物体 + recognition 提特征
# → database_search 余弦匹配分辨 ID → 画框 + ID 标签 → host_tick。
# 增量: 点预览区任意物体锁定(只跟踪该物体,特征余弦逐帧匹配);K2 注册锁定物体进 4 槽。

import gc
import os
import sys
import time
import lvgl as lv
from media.display import Display
from media.sensor import CAM_CHN_ID_0, CAM_CHN_ID_2
from core.icon_cache import icon_cache
from core.font_manager import fonts
from core.id_registry import IdRegistry
from core.object_classify_ai import ObjectClassifyRecognition, \
    OBJ_DET_KMPATH, OBJ_RECO_KMPATH, RGB888P_SIZE, DISPLAY_SIZE
from core.object_classify_db import object_classify_db, database_search, \
    to_feature_list, OBJECT_CLASSIFY_DB_PATH
from core.object_classify_lock import select_lock_index, pick_box_at_point

BAR_H = 52
PREVIEW_Y = BAR_H
PREVIEW_H = 376
BAR_BG = 0x1A1A1A

# 4 槽颜色(同 face_detect/body_detect BOX_COLORS)
BOX_COLORS = {
    1: 0x44CC44,   # 绿
    2: 0x4488FF,   # 蓝
    3: 0xFF8844,   # 橙
    4: 0xCC44FF,   # 紫
}
BOX_UNKNOWN = 0xFFFFFF   # 未注册白框
BOX_LOCK = 0xFFD700      # 锁定高亮黄框


def _draw_color(hex_color):
    """hex 0xRRGGBB → K230 draw_rectangle color tuple (A, B, G, R)。"""
    r = (hex_color >> 16) & 0xFF
    g = (hex_color >> 8) & 0xFF
    b = hex_color & 0xFF
    return (0xFF, b, g, r)


_RUNTIME = None
_screen = None
_top_bar = None
_bottom_bar = None
_preview = None
_count_label = None
_id_registry = None
_ocr = None                 # ObjectClassifyRecognition
_db_features = {}
_locked_feature = None      # 锁定特征(plain list,跨帧持有);None=未锁定
_pending_click = None       # (x,y) 待处理触摸点(VGA 空间),或 None
_overlay = None
_clear_btn = None
_save_btn = None
_close_overlay = False


def _init_ai():
    """Load BOTH kmodels before the loop.

    ⚠️ 双 kmodel 顺序根因(坑#19):rec kmodel 必须在 det.config_preprocess()
    之前加载。ObjectClassifyRecognition.__init__ 已按此顺序加载。
    """
    global _ocr, _db_features
    print("[object_classify] loading yolov8n detection + recognition models...")
    _ocr = ObjectClassifyRecognition(
        OBJ_DET_KMPATH, OBJ_RECO_KMPATH,
        det_input_size=[320, 320], rec_input_size=[224, 224],
        confidence_threshold=0.5, nms_threshold=0.2,
        rgb888p_size=RGB888P_SIZE, display_size=DISPLAY_SIZE,
        debug_mode=0)
    _db_features = object_classify_db.init_features()
    print("[object_classify] AI ready, loaded %d object(s)" % len(_db_features))


def _init_registry(fpioa):
    global _id_registry
    _id_registry = IdRegistry(fpioa, pin=0)


def _deinit_ai():
    global _ocr
    if _ocr is not None:
        try:
            _ocr.deinit()
        except Exception as e:
            print("[object_classify] deinit warning: %s" % e)
        _ocr = None


def on_frame(img):
    """chn2 检测+提特征 → 锁定跟踪或余弦匹配 → 画框 + ID 标签 → host_tick。

    锁定时:在检测特征里找与 _locked_feature 余弦最相似的(≥0.75),只画该框(黄+十字+
    LOCK);低于阈值 → 锁定丢失,自动解锁。未锁定:每框 database_search,命中槽画彩框+
    ID#,未命中白框。触摸点击命中框 → 锁定该框特征;点空白 → 解锁。K2 注册当前锁定特征。
    """
    global _locked_feature, _pending_click
    if _RUNTIME is None or _ocr is None:
        return
    img_ai = _RUNTIME.sensor.snapshot(chn=CAM_CHN_ID_2)
    img_np = img_ai.to_numpy_ref()
    try:
        det_boxes, features = _ocr.run(img_np)
    except Exception as e:
        print("[object_classify] run error: %s" % e)
        det_boxes, features = [], []

    # 检测框(rgb888p) → 显示坐标(VGA)
    disp_boxes = []
    for d in det_boxes:
        l, t, r, b = int(d[0]), int(d[1]), int(d[2]), int(d[3])
        x = l * DISPLAY_SIZE[0] // RGB888P_SIZE[0]
        y = t * DISPLAY_SIZE[1] // RGB888P_SIZE[1]
        w = (r - l) * DISPLAY_SIZE[0] // RGB888P_SIZE[0]
        h = (b - t) * DISPLAY_SIZE[1] // RGB888P_SIZE[1]
        disp_boxes.append((x, y, w, h))

    slots = [None, None, None, None]
    filled = set()

    # 触摸点击处理(优先,可能改变锁定态)
    if _pending_click is not None:
        px, py = _pending_click
        _pending_click = None
        idx = pick_box_at_point(disp_boxes, px, py)
        if idx is not None and idx < len(features):
            # 拷成 plain list 跨帧持有(避 ulab ndarray 缓冲被 NPU 复用)
            _locked_feature = to_feature_list(features[idx])
            if _RUNTIME.buzzer is not None:
                _RUNTIME.buzzer.beep(ms=50)
        else:
            _locked_feature = None  # 点空白 → 解锁

    if _locked_feature is not None:
        # 锁定模式:特征余弦逐帧匹配锁定目标
        idx, score = select_lock_index(_locked_feature, features)
        if idx is not None and idx < len(disp_boxes):
            x, y, w, h = disp_boxes[idx]
            color = _draw_color(BOX_LOCK)
            img.draw_rectangle(x, y, w, h, color=color, thickness=5)
            img.draw_cross(x + w // 2, y + h // 2,
                           color=(0xFF, 0x00, 0xD7, 0xFF), size=24, thickness=2)
            conf = int(score * 100)
            slot, _sc = database_search(features[idx], _db_features)
            if slot is not None:
                img.draw_string_advanced(x + 2, y - 24, 24,
                                         "ID%d LOCK" % slot, color=color)
                slots[slot - 1] = (slot, x, y, w, h, conf)
            else:
                img.draw_string_advanced(x + 2, y - 24, 24, "LOCK", color=color)
                slots[0] = (0, x, y, w, h, conf)  # id=0 表示锁定但未注册
        else:
            # 锁定丢失:目标离开画面/被遮挡 → 自动解锁
            _locked_feature = None

    if _locked_feature is None:
        # 未锁定模式:每框余弦匹配 DB
        for i, feat in enumerate(features):
            x, y, w, h = disp_boxes[i]
            slot, sc = database_search(feat, _db_features)
            if slot is not None and slot not in filled:
                color = _draw_color(BOX_COLORS.get(slot, BOX_UNKNOWN))
                img.draw_rectangle(x, y, w, h, color=color, thickness=4)
                img.draw_string_advanced(x + 2, y - 24, 24,
                                         "ID%d" % slot, color=color)
                slots[slot - 1] = (slot, x, y, w, h, int(sc * 100))
                filled.add(slot)
            else:
                color = _draw_color(BOX_UNKNOWN)
                img.draw_rectangle(x, y, w, h, color=color, thickness=2)
                img.draw_string_advanced(x + 2, y - 24, 24, "object", color=color)

    # 屏幕居中绿色十字(对准参考):VGA 640×480 中心 (320, 240)
    img.draw_cross(320, 240, color=(0xFF, 0x00, 0xFF, 0x00), size=20, thickness=2)

    # K2 注册:当前锁定物体特征进空槽(复用 IdRegistry 的 K2 边沿/超时/蜂鸣)
    if _id_registry is not None and _id_registry.has_pending() \
            and _locked_feature is not None:
        try:
            slot = _id_registry.try_register(
                _locked_feature, _RUNTIME.buzzer,
                registrar=object_classify_db.register)
            if slot is not None:
                object_classify_db.flush_to_disk()
                _db_features[slot] = object_classify_db.get_features().get(slot)
                _refresh_count()
        except Exception as e:
            print("[object_classify] register error: %s" % e)

    if _RUNTIME is not None and _RUNTIME.host is not None:
        _RUNTIME.host_tick(slots)
    gc.collect()


def _refresh_count():
    if _count_label is not None:
        try:
            _count_label.set_text(
                _RUNTIME.lang.t("object_classify.registered", len(_db_features)))
        except Exception:
            pass


def _on_preview_clicked(e):
    """点预览区:记录屏幕坐标(VGA 空间),on_frame 里 pick_box_at_point。"""
    global _pending_click
    if e.get_code() != lv.EVENT.CLICKED:
        return
    if _overlay is not None:
        global _close_overlay
        _close_overlay = True
        return
    try:
        # K230 MicroPython LVGL 绑定:get_point 需传预分配 point_t 填充(同 color_detect)。
        indev = lv.indev_get_act()
        if indev is not None:
            pt = lv.point_t()
            indev.get_point(pt)
            _pending_click = (pt.x, pt.y)
    except Exception as ex:
        print("[object_classify] get_point error: %s" % ex)


def _on_list_clicked(e):
    """弹出清除/保存浮层(叠加在底栏上方)。"""
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
    cl.set_text(_RUNTIME.lang.t("object_classify.clear"))
    cl.add_style(make_back_bar_text_style(fonts.body), 0)
    cl.center()
    _clear_btn.add_event(_on_clear_clicked, lv.EVENT.CLICKED, None)

    _save_btn = lv.btn(_overlay)
    _save_btn.set_size(120, 40)
    _save_btn.align(lv.ALIGN.RIGHT_MID, -20, 0)
    sv = lv.label(_save_btn)
    sv.set_text(_RUNTIME.lang.t("object_classify.save"))
    sv.add_style(make_back_bar_text_style(fonts.body), 0)
    sv.center()
    _save_btn.add_event(_on_save_clicked, lv.EVENT.CLICKED, None)


def _on_overlay_clicked(e):
    global _close_overlay
    if e.get_code() != lv.EVENT.CLICKED:
        return
    _close_overlay = True


def _on_clear_clicked(e):
    global _close_overlay
    if e.get_code() != lv.EVENT.CLICKED:
        return
    object_classify_db.clear()
    _db_features.clear()
    _refresh_count()
    if _RUNTIME is not None and _RUNTIME.buzzer is not None:
        _RUNTIME.buzzer.beep(ms=200)
    _close_overlay = True


def _on_save_clicked(e):
    global _close_overlay
    if e.get_code() != lv.EVENT.CLICKED:
        return
    _close_overlay = True


def _process_overlay_close():
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
    """Build top bar, transparent clickable preview area, and bottom bar."""
    global _screen, _top_bar, _bottom_bar, _preview, _count_label
    screen = lv.scr_act()
    screen.set_style_bg_opa(0, 0)
    screen.add_flag(lv.obj.FLAG.CLICKABLE)
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

    icon_data, icon_dsc = icon_cache.get_object_classify_icon("back")
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
    title.set_text(runtime.lang.t("category.object_classify"))
    title.align(lv.ALIGN.CENTER, 0, 0)
    from ui.theme import make_back_bar_text_style
    title.add_style(make_back_bar_text_style(fonts.body), 0)

    # 透明预览区(透出 OSD1,可点击锁定物体)
    _preview = lv.obj(screen)
    _preview.set_size(lv.pct(100), PREVIEW_H)
    _preview.set_pos(0, PREVIEW_Y)
    _preview.set_style_bg_opa(0, 0)
    _preview.set_style_border_width(0, 0)
    _preview.set_style_pad_all(0, 0)
    _preview.set_style_radius(0, 0)
    _preview.clear_flag(lv.obj.FLAG.SCROLLABLE)
    _preview.add_flag(lv.obj.FLAG.CLICKABLE)
    _preview.add_event(_on_preview_clicked, lv.EVENT.CLICKED, None)

    _bottom_bar = lv.obj(screen)
    _bottom_bar.set_size(lv.pct(100), BAR_H)
    _bottom_bar.set_pos(0, PREVIEW_Y + PREVIEW_H)
    _bottom_bar.set_style_bg_color(lv.color_hex(BAR_BG), 0)
    _bottom_bar.set_style_bg_opa(255, 0)
    _bottom_bar.set_style_border_width(0, 0)
    _bottom_bar.set_style_pad_all(0, 0)
    _bottom_bar.set_style_radius(0, 0)
    _bottom_bar.clear_flag(lv.obj.FLAG.SCROLLABLE)

    # list 图标按钮(底栏左侧) → 点击弹出清除/保存浮层
    list_btn = lv.obj(_bottom_bar)
    list_btn.set_size(48, 48)
    list_btn.align(lv.ALIGN.LEFT_MID, 2, 0)
    list_btn.set_style_bg_opa(0, 0)
    list_btn.set_style_border_width(0, 0)
    list_btn.set_style_pad_all(0, 0)
    list_btn.clear_flag(lv.obj.FLAG.SCROLLABLE)
    list_btn.add_flag(lv.obj.FLAG.CLICKABLE)
    list_icon_data, list_icon_dsc = icon_cache.get_object_classify_icon("list")
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
        list_lbl.set_text("=")
        list_lbl.add_style(make_back_bar_text_style(fonts.body), 0)
        list_lbl.center()
    list_btn.add_event(_on_list_clicked, lv.EVENT.CLICKED, None)

    count_label = lv.label(_bottom_bar)
    count_label.set_text(runtime.lang.t("object_classify.registered", len(_db_features)))
    count_label.add_style(make_back_bar_text_style(fonts.body), 0)
    count_label.align(lv.ALIGN.CENTER, 0, 0)
    _count_label = count_label


def _destroy_ui():
    """Delete LVGL objects and restore screen opacity for the menu."""
    global _screen, _top_bar, _bottom_bar, _preview, _count_label
    global _overlay, _clear_btn, _save_btn
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
                print("[object_classify] on_frame error: %s" % e)
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
                print("[object_classify] fc=%d" % fc)
    finally:
        _deinit_ai()
        _destroy_ui()
        _RUNTIME = None
        object_classify_db.flush_to_disk()  # 退出兜底写盘
