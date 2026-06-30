# scripts/object_detect/app.py — YOLOv8n COCO80 物体识别。
#
# 复用 _template 单线程主循环。chn2 XGA RGBP888 做 AI 推理(同 face_detect),
# chn0 VGA RGB888 显示。底栏仅左侧 list 图标(清除/保存浮层)+ 计数。KEY2 按类别
# 注册(最多4类),走 object_db.register via registrar。注册框显示 ID号+英文类名,
# 未注册白框。协议类型 0x05 上传4槽位。持久化预留(flush_to_disk no-op)。

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
from core.object_ai import ObjectDetectionApp, COCO_LABELS
from core.object_db import ObjectDB

BAR_H = 52
PREVIEW_Y = BAR_H
PREVIEW_H = 376
BAR_BG = 0x1A1A1A

# 画框配色对齐 face_detect/tag_detect:未注册白框,注册按 slot 取彩色。
BOX_COLORS = {
    1: 0x44CC44,   # 绿
    2: 0x4488FF,   # 蓝
    3: 0xFF8844,   # 橙
    4: 0xCC44FF,   # 紫
}
BOX_UNKNOWN = 0xFFFFFF   # 未注册白框

KMODEL_PATH = "/sdcard/examples/kmodel/yolov8n_320.kmodel"
_OBJ_DB_PATH = "/sdcard/CamerAi/data/object_db.json"
RGB888P_W = 1024
RGB888P_H = 768
DISPLAY_W = 640
DISPLAY_H = 480


def _draw_color(hex_color):
    """hex 0xRRGGBB -> K230 draw_rectangle color tuple (A, B, G, R)。"""
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
_object_det = None
_db = None
_overlay = None
_clear_btn = None
_save_btn = None
_close_overlay = False


def _init_ai():
    global _object_det
    print("[object_detect] loading det kmodel...")
    _object_det = ObjectDetectionApp(
        KMODEL_PATH, labels=COCO_LABELS, model_input_size=[320, 320],
        max_boxes_num=20, confidence_threshold=0.5, nms_threshold=0.2,
        rgb888p_size=[RGB888P_W, RGB888P_H], display_size=[DISPLAY_W, DISPLAY_H],
        debug_mode=0)
    _object_det.config_preprocess()
    print("[object_detect] AI ready")


def _init_registry(fpioa):
    global _id_registry
    _id_registry = IdRegistry(fpioa, pin=0)


def _deinit_ai():
    global _object_det
    if _object_det is not None:
        try:
            _object_det.deinit()
        except Exception as e:
            print("[object_detect] det deinit warning: %s" % e)
        _object_det = None


def on_frame(img):
    """chn2 检测 -> 每类取最大实例 -> 匹配 DB -> 画框 -> 十字 -> host_tick。

    每类别取面积最大实例画框+填槽(协议每槽一坐标)。注册类彩色框+ID号+英文类名,
    未注册白框。KEY2 注册当前帧最大框的类别。
    """
    if _RUNTIME is None or _object_det is None:
        return
    img_ai = _RUNTIME.sensor.snapshot(chn=CAM_CHN_ID_2)
    img_np = img_ai.to_numpy_ref()
    try:
        dets = _object_det.run(img_np)
    except Exception as e:
        print("[object_detect] run error: %s" % e)
        dets = []

    slots = [None, None, None, None]
    # 每类别取面积最大实例
    per_class_max = {}   # class_id -> [l,t,r,b,score,cid]
    for det in dets:
        try:
            l, t, r, b, score, cid = [float(v) for v in det]
        except Exception:
            continue
        cid = int(cid)
        area = (r - l) * (b - t)
        cur = per_class_max.get(cid)
        if cur is None or area > (cur[2] - cur[0]) * (cur[3] - cur[1]):
            per_class_max[cid] = [l, t, r, b, score, cid]

    for cid, det in per_class_max.items():
        l, t, r, b, score, _ = det
        slot, _score = _db.match(cid)
        x = int(l) * DISPLAY_W // RGB888P_W
        y = int(t) * DISPLAY_H // RGB888P_H
        w = int(r - l) * DISPLAY_W // RGB888P_W
        h = int(b - t) * DISPLAY_H // RGB888P_H
        conf = int(score * 100)
        if slot is not None:
            color = _draw_color(BOX_COLORS.get(slot, BOX_UNKNOWN))
            img.draw_rectangle(x, y, w, h, color=color, thickness=4)
            img.draw_string_advanced(x, y - 24, 24,
                                     "ID%d %s" % (slot, COCO_LABELS[cid]), color=color)
            if 1 <= slot <= 4:
                slots[slot - 1] = (slot, x, y, w, h, conf)
        else:
            color = _draw_color(BOX_UNKNOWN)
            img.draw_rectangle(x, y, w, h, color=color, thickness=2)

    # 屏幕居中绿色十字(对准参考,小一点):VGA 640x480 中心 (320, 240)
    img.draw_cross(320, 240, color=(0xFF, 0x00, 0xFF, 0x00), size=20, thickness=2)

    # KEY2 注册:当前帧最大框的类别 -> 下一槽
    if _id_registry is not None and _id_registry.has_pending() and per_class_max:
        max_cid = max(per_class_max.values(),
                      key=lambda d: (d[2] - d[0]) * (d[3] - d[1]))[5]
        try:
            slot = _id_registry.try_register(max_cid, _RUNTIME.buzzer,
                                             registrar=_db.register)
            if slot is not None:
                _db.flush_to_disk(_OBJ_DB_PATH)
                _refresh_count()
        except Exception as e:
            print("[object_detect] register error: %s" % e)

    if _RUNTIME is not None and _RUNTIME.host is not None:
        _RUNTIME.host_tick(slots)
    gc.collect()


def _refresh_count():
    if _count_label is not None and _RUNTIME is not None and _db is not None:
        try:
            _count_label.set_text(_RUNTIME.lang.t("object_detect.registered", _db.count))
        except Exception:
            pass


def _on_list_clicked(e):
    """弹出清除/保存浮层(对齐 face_detect/tag_detect)。"""
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
    cl.set_text(_RUNTIME.lang.t("object_detect.clear"))
    cl.add_style(make_back_bar_text_style(fonts.body), 0)
    cl.center()
    _clear_btn.add_event(_on_clear_clicked, lv.EVENT.CLICKED, None)

    _save_btn = lv.btn(_overlay)
    _save_btn.set_size(120, 40)
    _save_btn.align(lv.ALIGN.RIGHT_MID, -20, 0)
    sv = lv.label(_save_btn)
    sv.set_text(_RUNTIME.lang.t("object_detect.save"))
    sv.add_style(make_back_bar_text_style(fonts.body), 0)
    sv.center()
    _save_btn.add_event(_on_save_clicked, lv.EVENT.CLICKED, None)


def _on_overlay_clicked(e):
    global _close_overlay
    if e.get_code() != lv.EVENT.CLICKED:
        return
    _close_overlay = True


def _on_screen_clicked(e):
    global _close_overlay
    if e.get_code() != lv.EVENT.CLICKED:
        return
    if _overlay is not None:
        _close_overlay = True


def _on_clear_clicked(e):
    """清 db 内存 + 蜂鸣 + 关浮层。不删盘(持久化待定)。"""
    global _close_overlay
    if e.get_code() != lv.EVENT.CLICKED:
        return
    if _db is not None:
        _db.clear()
    _refresh_count()
    if _RUNTIME is not None and _RUNTIME.buzzer is not None:
        _RUNTIME.buzzer.beep(ms=200)
    _close_overlay = True


def _on_save_clicked(e):
    """空操作(退出自动持久化,当前 no-op)。只标志关闭浮层。"""
    global _close_overlay
    if e.get_code() != lv.EVENT.CLICKED:
        return
    _close_overlay = True


def _process_overlay_close():
    """主循环 deferred 关闭浮层(LVGL use-after-free 防护)。"""
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
    """顶栏(back+标题) + 透明预览 + 底栏(list图标 + 计数)。"""
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

    icon_data, icon_dsc = icon_cache.get_object_icon("back")
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
    title.set_text(runtime.lang.t("category.object_detect"))
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

    # list 图标(点击弹清除/保存浮层)
    list_btn = lv.obj(_bottom_bar)
    list_btn.set_size(48, 48)
    list_btn.align(lv.ALIGN.LEFT_MID, 2, 0)
    list_btn.set_style_bg_opa(0, 0)
    list_btn.set_style_border_width(0, 0)
    list_btn.set_style_pad_all(0, 0)
    list_btn.clear_flag(lv.obj.FLAG.SCROLLABLE)
    list_btn.add_flag(lv.obj.FLAG.CLICKABLE)
    list_btn.add_event(_on_list_clicked, lv.EVENT.CLICKED, None)
    list_icon_data, list_icon_dsc = icon_cache.get_object_icon("list")
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

    count_label = lv.label(_bottom_bar)
    count_label.set_text(runtime.lang.t("object_detect.registered", 0))
    count_label.add_style(make_back_bar_text_style(fonts.body), 0)
    count_label.align(lv.ALIGN.CENTER, 0, 0)
    _count_label = count_label


def _destroy_ui():
    global _screen, _top_bar, _bottom_bar, _preview, _count_label
    global _overlay, _clear_btn, _save_btn
    for obj in (_clear_btn, _save_btn, _overlay, _top_bar, _bottom_bar, _preview, _count_label):
        if obj is not None:
            try:
                obj.delete()
            except Exception:
                pass
    _clear_btn = None
    _save_btn = None
    _overlay = None
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
    """reset 框架入口。单线程主循环:snapshot chn0 -> on_frame -> show OSD1 -> task_handler。"""
    global _RUNTIME, _db
    _RUNTIME = runtime
    _db = ObjectDB()
    _db.load_from_disk(_OBJ_DB_PATH)
    exit_flag = [False]
    _init_ai()
    _init_registry(runtime.fpioa)
    _build_ui(runtime, exit_flag)
    _refresh_count()  # load 后刷新计数（显示已学习数量）
    fc = 0
    try:
        while not exit_flag[0]:
            os.exitpoint()
            img = runtime.sensor.snapshot(chn=CAM_CHN_ID_0)
            try:
                on_frame(img)
            except Exception as e:
                print("[object_detect] on_frame error: %s" % e)
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
                print("[object_detect] fc=%d" % fc)
    finally:
        _deinit_ai()
        _destroy_ui()
        if _db is not None:
            _db.flush_to_disk(_OBJ_DB_PATH)
        _RUNTIME = None
