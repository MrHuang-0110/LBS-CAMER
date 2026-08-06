# scripts/gesture_detect/app.py — 手势识别(双 kmodel + K2 注册 4 槽 + 协议 0x08)
#
# 复刻 object_detect 模式: chn2 AI 检测 → gesture_db.match(label_idx)
# → 填 slots → K2 registrar → host_tick。画十字 + 彩色框 + ID 标签。

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
from core.gesture_ai import HandRecognition, HAND_LABELS, HAND_ANCHORS, RGB888P_SIZE, DISPLAY_SIZE
from core.gesture_db import gesture_db, GESTURE_DB_PATH

BAR_H = 52
PREVIEW_Y = BAR_H
PREVIEW_H = 376
BAR_BG = 0x1A1A1A

# 25 槽画框颜色表(1~4 历史色 + 5~25 色环;学习 ID 不用白色),共享 core/box_colors
from core.box_colors import BOX_COLORS, BOX_UNKNOWN
# conf 字节 bit7 = 已学习标记(对齐 comm/host_api.LEARNED_FLAG);conf 0~100 恒 <128 不冲突
LEARNED_FLAG = 0x80
# 检测/识别一体降频:每 DET_INTERVAL 帧跑一次完整 run(chn2 取流+NPU),
# 其余帧用缓存框+缓存识别结果画框——降低 chn2 DMA 与显示 DMA 竞争(同 face_detect 死机修复)
DET_INTERVAL = 2
MIN_REG_AREA = 3600  # 注册最小手掌面积(rgb888p px²,≈60×60);太小拒绝注册


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
_id_registry = None
_hand_rec = None
_db_slots = {}
_overlay = None
_clear_btn = None
_save_btn = None
_close_overlay = False
_det_counter = 0          # 检测降频计数(每 DET_INTERVAL 帧跑完整 run)
_last_det = ([], [])      # 上轮 (det_boxes, rec_results) 缓存
_last_rec = {}            # 上轮识别结果 {det_idx: slot}
_last_slots = None        # 上轮槽位(非检测帧复用,主机数据连续)
_last_names = None        # 上轮名称帧列表(非检测帧复用)
_pending_clear_flush = False  # 清除请求:主循环安全窗口写空库(防断电重启旧数据回魂)


def _init_ai():
    """Load BOTH kmodels before the loop.

    ⚠️ 双 kmodel 顺序根因:hand_rec kmodel 必须在 hand_det.config_preprocess()
    之前加载,否则破坏共享 NPU/AI2D 状态(坑#19,同 face_detect)。
    kmodel 加载整体包 try/except：失败则 _ai_ready=False，on_frame 跳过推理。
    """
    global _hand_rec, _db_slots, _ai_ready
    _ai_ready = False
    try:
        det_kmodel = "/sdcard/examples/kmodel/hand_det.kmodel"
        rec_kmodel = "/sdcard/examples/kmodel/hand_reco.kmodel"
        print("[gesture_detect] loading hand detection + recognition models...")
        _hand_rec = HandRecognition(
            det_kmodel, rec_kmodel,
            det_input_size=[512, 512], rec_input_size=[224, 224],
            labels=HAND_LABELS, anchors=HAND_ANCHORS,
            confidence_threshold=0.2, nms_threshold=0.5,
            rgb888p_size=RGB888P_SIZE, display_size=DISPLAY_SIZE,
            debug_mode=0)
        _db_slots = gesture_db.init_features()
        _ai_ready = True
        print("[gesture_detect] AI ready, loaded %d gesture(s)" % len(_db_slots))
    except Exception as e:
        print("[gesture_detect] _init_ai FAILED: %s" % e)
        sys.print_exception(e)
        _hand_rec = None
        _ai_ready = False


def _init_registry(fpioa):
    global _id_registry
    _id_registry = IdRegistry(fpioa, pin=0)


def _deinit_ai():
    global _hand_rec
    if _hand_rec is not None:
        try:
            _hand_rec.deinit()
        except Exception as e:
            print("[gesture_detect] deinit warning: %s" % e)
        _hand_rec = None


def _draw_hand_boxes(img, det_boxes, rec_results, rec):
    """画手掌框 + ID/手势名标签。rec: {det_idx: slot};无 slot → 白框手势名。"""
    for i, (det_box, (label_idx, score)) in enumerate(zip(det_boxes, rec_results)):
        x1, y1, x2, y2 = det_box[2], det_box[3], det_box[4], det_box[5]
        x = int(x1) * DISPLAY_SIZE[0] // RGB888P_SIZE[0]
        y = int(y1) * DISPLAY_SIZE[1] // RGB888P_SIZE[1]
        w = int(x2 - x1) * DISPLAY_SIZE[0] // RGB888P_SIZE[0]
        h = int(y2 - y1) * DISPLAY_SIZE[1] // RGB888P_SIZE[1]
        label_name = HAND_LABELS[label_idx]
        slot = rec.get(i)
        if slot:
            color = _draw_color(BOX_COLORS.get(slot, BOX_UNKNOWN))
            img.draw_rectangle(x, y, w, h, color=color, thickness=4)
            img.draw_string_advanced(x + 2, y - 24, 24,
                                     "ID%d %s" % (slot, label_name), color=color)
        else:
            color = _draw_color(BOX_UNKNOWN)
            img.draw_rectangle(x, y, w, h, color=color, thickness=2)
            img.draw_string_advanced(x + 2, y - 24, 24, label_name, color=color)


def on_frame(img):
    """chn2 检测 → 每只手分类 → match DB → 画框 + ID 标签 → host_tick。

    检测/识别一体降频(DET_INTERVAL):检测帧跑完整 run(chn2 取流+NPU,
    run 后立即 gc 回收原生缓冲,防坑#16 累积死机),非检测帧用缓存框+
    缓存识别结果画框(ID 稳定不闪,索引与缓存框一致无窜脸)。K2 注册只在
    检测帧(有新鲜 rec_results)。
    """
    global _det_counter, _last_det, _last_rec, _last_slots
    if _RUNTIME is None or _hand_rec is None:
        return
    det_boxes, rec_results = _last_det
    rec = _last_rec
    slots = []   # 列表化:统一上限 25(原固定 4 槽),order_slots 按屏幕位置排序
    names = []   # 名称帧(类型 0x0E):[(id, 手势名)],仅已注册槽位
    _det_counter += 1
    do_det = (_det_counter % DET_INTERVAL == 0)
    if do_det:
        img_ai = _RUNTIME.sensor.snapshot(chn=CAM_CHN_ID_2)
        img_np = img_ai.to_numpy_ref()
        try:
            det_boxes, rec_results = _hand_rec.run(img_np)
        except Exception as e:
            print("[gesture_detect] run error: %s" % e)
            det_boxes, rec_results = [], []
        _last_det = (det_boxes, rec_results)
        # 推理完成立即清 chn2 大帧引用:帧内 gc 及时回收 2.25MB 原生缓冲,
        # 缩短其与显示 DMA(OSD1 show_image + OSD2 LVGL FULL flush)的共存期
        del img_np
        del img_ai
        gc.collect()  # 帧内回收 NPU 原生缓冲(坑#16:多目标连续推理防累积死机)
        # 识别匹配 + 填槽(帧内 ID 去重:一个 slot 只标一个框)
        rec = {}
        filled_slots = set()
        for i, (det_box, (label_idx, score)) in enumerate(zip(det_boxes, rec_results)):
            x1, y1, x2, y2 = det_box[2], det_box[3], det_box[4], det_box[5]
            x = int(x1) * DISPLAY_SIZE[0] // RGB888P_SIZE[0]
            y = int(y1) * DISPLAY_SIZE[1] // RGB888P_SIZE[1]
            w = int(x2 - x1) * DISPLAY_SIZE[0] // RGB888P_SIZE[0]
            h = int(y2 - y1) * DISPLAY_SIZE[1] // RGB888P_SIZE[1]
            conf = int(score * 100)
            slot, _dummy = gesture_db.match(label_idx)
            if slot is not None and slot not in filled_slots:
                filled_slots.add(slot)
                rec[i] = slot
                slots.append((slot, x, y, w, h, conf | LEARNED_FLAG))  # 已学习:bit7=1
                names.append((slot, HAND_LABELS[label_idx]))
        _last_rec = rec
        # K2 注册:当前帧最大手掌的标签(检测帧才有新鲜 rec_results)
        if _id_registry is not None and _id_registry.has_pending() and det_boxes:
            max_i = max(range(len(det_boxes)),
                        key=lambda j: (det_boxes[j][4] - det_boxes[j][2])
                                      * (det_boxes[j][5] - det_boxes[j][3]))
            det = det_boxes[max_i]
            if max_i < len(rec_results) and \
                    (det[4] - det[2]) * (det[5] - det[3]) >= MIN_REG_AREA:
                reg_label_idx = rec_results[max_i][0]
                try:
                    slot = _id_registry.try_register(
                        reg_label_idx, _RUNTIME.buzzer,
                        registrar=gesture_db.register)
                    if slot is not None:
                        gesture_db.flush_to_disk()  # 注册即写(task_handler 前安全窗口)
                        _db_slots[slot] = reg_label_idx
                except Exception as e:
                    print("[gesture_detect] register error: %s" % e)
    # 画框:每帧(检测帧新框/非检测帧缓存框;ID 与框同源,索引一致)
    _draw_hand_boxes(img, det_boxes, rec_results, rec)
    # 屏幕居中绿色十字(对准参考):VGA 640×480 中心 (320, 240)
    img.draw_cross(320, 240, color=(0xFF, 0x00, 0xFF, 0x00), size=20, thickness=2)
    # 非检测帧:复用上轮槽位,保持主机数据连续
    if not do_det and _last_slots is not None:
        slots = _last_slots
        names = _last_names
    else:
        _last_slots = slots
        _last_names = names
    if _RUNTIME is not None and _RUNTIME.host is not None:
        _RUNTIME.host_tick(slots, names)


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
    cl.set_text(_RUNTIME.lang.t("gesture_detect.clear"))
    cl.add_style(make_back_bar_text_style(fonts.body), 0)
    cl.center()
    _clear_btn.add_event(_on_clear_clicked, lv.EVENT.CLICKED, None)

    _save_btn = lv.btn(_overlay)
    _save_btn.set_size(120, 40)
    _save_btn.align(lv.ALIGN.RIGHT_MID, -20, 0)
    sv = lv.label(_save_btn)
    sv.set_text(_RUNTIME.lang.t("gesture_detect.save"))
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
    global _close_overlay, _pending_clear_flush
    if e.get_code() != lv.EVENT.CLICKED:
        return
    gesture_db.clear()
    _db_slots.clear()
    _pending_clear_flush = True  # 清除即写盘:主循环 task_handler 前安全窗口执行(防断电重启旧数据回魂)
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
    """Build top bar, transparent preview area, and bottom bar."""
    global _screen, _top_bar, _bottom_bar, _preview
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

    icon_data, icon_dsc = icon_cache.get_gesture_icon("back")
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
    title.set_text(runtime.lang.t("category.gesture_detect"))
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

    # list 图标按钮(底栏左侧) → 点击弹出清除/保存浮层
    list_btn = lv.obj(_bottom_bar)
    list_btn.set_size(48, 48)
    list_btn.align(lv.ALIGN.LEFT_MID, 2, 0)
    list_btn.set_style_bg_opa(0, 0)
    list_btn.set_style_border_width(0, 0)
    list_btn.set_style_pad_all(0, 0)
    list_btn.clear_flag(lv.obj.FLAG.SCROLLABLE)
    list_btn.add_flag(lv.obj.FLAG.CLICKABLE)
    list_icon_data, list_icon_dsc = icon_cache.get_gesture_icon("list")
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

    # 底栏计数(已学习 x/x)已按用户要求去掉


def _destroy_ui():
    """Delete LVGL objects and restore screen opacity for the menu."""
    global _screen, _top_bar, _bottom_bar, _preview, _overlay, _clear_btn, _save_btn
    for obj in (_overlay, _clear_btn, _save_btn, _top_bar, _bottom_bar, _preview):
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
    global _RUNTIME, _pending_clear_flush
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
                print("[gesture_detect] on_frame error: %s" % e)
                try:
                    sys.print_exception(e)
                except Exception:
                    pass
            if _id_registry is not None:
                _id_registry.poll_k2()
            _process_overlay_close()
            if _pending_clear_flush:
                _pending_clear_flush = False
                gesture_db.flush_to_disk()  # 清除即写空库(task_handler 前安全窗口)
            Display.show_image(img, 0, 0, Display.LAYER_OSD1)
            gc.collect()  # 在 show_image 之后 GC,避免阻塞 DMA
            time.sleep_ms(lv.task_handler())
            fc += 1
            if fc % 30 == 0:
                print("[gesture_detect] fc=%d" % fc)
                if fc % 300 == 0:
                    import gc as _gc; print("[gesture_detect] mem_free=%d" % _gc.mem_free())
    finally:
        _deinit_ai()
        _destroy_ui()
        _RUNTIME = None
        gesture_db.flush_to_disk()  # 退出兜底写盘
