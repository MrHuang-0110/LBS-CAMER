# scripts/object_classify/app.py — 物体分类(双 kmodel + 点击锁定 + K2 注册 4 槽 + 协议 0x0A)
#
# 单通道(死机根治 2026-08-07,同 object_detect): AI 直接吃 chn0 显示帧,
# det(YOLOv8n) + rec(recognition) 共用同一帧。点预览区任意物体锁定;K2
# 注册锁定物体进 4 槽。

import gc
import os
import sys
import time
import lvgl as lv
import ulab.numpy as np
from media.display import Display
from media.sensor import CAM_CHN_ID_0
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

# 25 槽画框颜色表(1~4 历史色 + 5~25 色环;学习 ID 不用白色),共享 core/box_colors
from core.box_colors import BOX_COLORS, BOX_UNKNOWN
# conf 字节 bit7 = 已学习标记(对齐 comm/host_api.LEARNED_FLAG);conf 0~100 恒 <128 不冲突
LEARNED_FLAG = 0x80
BOX_LOCK = 0xFFD700      # 锁定高亮黄框
# 检测降频:每 DET_INTERVAL 帧跑一次 NPU,其余帧用缓存结果
DET_INTERVAL = 2


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
_ocr = None                 # ObjectClassifyRecognition
_db_features = {}
_locked_feature = None      # 锁定特征(plain list,跨帧持有);None=未锁定
_pending_click = None       # (x,y) 待处理触摸点(VGA 空间),或 None
_overlay = None
_clear_btn = None
_save_btn = None
_close_overlay = False
_det_counter = 0          # 检测降频计数(每 DET_INTERVAL 帧跑 NPU)
_last_det = ([], [])      # 上轮 (det_boxes, features) 缓存
_last_disp = []           # 上轮 disp_boxes 缓存(非检测帧画框/触摸用)
_last_slots = None        # 上轮槽位(非检测帧复用,主机数据连续)
_last_names = None        # 上轮名称帧列表(非检测帧复用)
_pending_clear_flush = False  # 清除请求:主循环安全窗口写空库(防断电重启旧数据回魂)


def _init_ai():
    """Load BOTH kmodels before the loop.

    ⚠️ 双 kmodel 顺序根因(坑#19):rec kmodel 必须在 det.config_preprocess()
    之前加载。ObjectClassifyRecognition.__init__ 已按此顺序加载。
    kmodel 加载整体包 try/except：失败则 _ai_ready=False，on_frame 跳过推理。
    """
    global _ocr, _db_features, _ai_ready
    _ai_ready = False
    try:
        print("[object_classify] loading yolov8n detection + recognition models...")
        _ocr = ObjectClassifyRecognition(
            OBJ_DET_KMPATH, OBJ_RECO_KMPATH,
            det_input_size=[320, 320], rec_input_size=[224, 224],
            confidence_threshold=0.5, nms_threshold=0.2,
            rgb888p_size=RGB888P_SIZE, display_size=DISPLAY_SIZE,
            debug_mode=0)
        _db_features = object_classify_db.init_features()
        _ai_ready = True
        print("[object_classify] AI ready, loaded %d object(s)" % len(_db_features))
    except Exception as e:
        print("[object_classify] _init_ai FAILED: %s" % e)
        sys.print_exception(e)
        _ocr = None
        _ai_ready = False


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

    锁定时:在检测特征里找与 _locked_feature 余弦最相似的(≥0.82),只画该框(黄+十字+
    LOCK);低于阈值 → 锁定丢失,自动解锁。未锁定:每框 database_search,命中槽画彩框+
    ID#,未命中白框。触摸点击命中框 → 锁定该框特征;点空白 → 解锁。K2 注册当前锁定特征。
    """
    global _locked_feature, _pending_click, _det_counter, _last_det, _last_disp, _last_slots, _last_names
    if _RUNTIME is None or _ocr is None:
        return
    det_boxes, features = _last_det
    disp_boxes = _last_disp
    _det_counter += 1
    do_det = (_det_counter % DET_INTERVAL == 0)
    if do_det:
        # 单通道(死机根治 2026-08-07):AI 直接吃 chn0 显示帧,无 chn2 DMA 竞争
        img_np = img.to_numpy_ref()
        # packed RGB888 -> planar CHW: ai2d 若支持 packed 输入枚举(RGB888p_FMT)
        # 则零拷贝直接喂,否则 NCHW(planar 语义)须逐通道重排(2026-08-07 全屏
        # 假框根因;ai2d build 声明 [1,3,H,W],重排喂省略 batch=1 的 (3,H,W))。
        if not _ocr.input_is_packed:
            _planar = np.zeros((3, img.height(), img.width()), dtype=np.uint8)
            _planar[0] = img_np[:, :, 0]
            _planar[1] = img_np[:, :, 1]
            _planar[2] = img_np[:, :, 2]
            img_np = _planar
        try:
            det_boxes, features = _ocr.run(img_np)
        except Exception as e:
            print("[object_classify] run error: %s" % e)
            det_boxes, features = [], []
        _last_det = (det_boxes, features)
        # 推理完成立即释放 numpy 引用;帧内 gc 回收 NPU 原生缓冲(坑#16:防累积死机)
        if not _ocr.input_is_packed:
            del _planar
        del img_np
        gc.collect()
        # 检测框(rgb888p) → 显示坐标(VGA)
        disp_boxes = []
        for d in det_boxes:
            l, t, r, b = int(d[0]), int(d[1]), int(d[2]), int(d[3])
            x = l * DISPLAY_SIZE[0] // RGB888P_SIZE[0]
            y = t * DISPLAY_SIZE[1] // RGB888P_SIZE[1]
            w = (r - l) * DISPLAY_SIZE[0] // RGB888P_SIZE[0]
            h = (b - t) * DISPLAY_SIZE[1] // RGB888P_SIZE[1]
            disp_boxes.append((x, y, w, h))
        _last_disp = disp_boxes

    slots = []   # 列表化:统一上限 25(原固定 4 槽),order_slots 按屏幕位置排序
    names = []   # 名称帧(类型 0x0E):[(id, 名称)],已注册目标用 obj<槽号> 标识
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
                slots.append((slot, x, y, w, h, conf | LEARNED_FLAG))  # 已学习:bit7=1
                names.append((slot, "obj%d" % slot))
            else:
                img.draw_string_advanced(x + 2, y - 24, 24, "LOCK", color=color)
                slots.append((0, x, y, w, h, conf))  # id=0 锁定但未注册(learned=0)
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
                slots.append((slot, x, y, w, h, int(sc * 100) | LEARNED_FLAG))  # 已学习:bit7=1
                names.append((slot, "obj%d" % slot))
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
        except Exception as e:
            print("[object_classify] register error: %s" % e)

    # 非检测帧:复用上轮槽位,保持主机数据连续(须在 host_tick 前)
    if not do_det and _last_slots is not None:
        slots = _last_slots
        names = _last_names
    else:
        _last_slots = slots
        _last_names = names
    if _RUNTIME is not None and _RUNTIME.host is not None:
        _RUNTIME.host_tick(slots, names)


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
    global _close_overlay, _pending_clear_flush
    if e.get_code() != lv.EVENT.CLICKED:
        return
    object_classify_db.clear()
    _db_features.clear()
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
    """Build top bar, transparent clickable preview area, and bottom bar."""
    global _screen, _top_bar, _bottom_bar, _preview
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

    # 底栏计数(已学习 x/x)已按用户要求去掉


def _destroy_ui():
    """Delete LVGL objects and restore screen opacity for the menu."""
    global _screen, _top_bar, _bottom_bar, _preview
    global _overlay, _clear_btn, _save_btn
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
                print("[object_classify] on_frame error: %s" % e)
                try:
                    sys.print_exception(e)
                except Exception:
                    pass
            if _id_registry is not None:
                _id_registry.poll_k2()
            _process_overlay_close()
            if _pending_clear_flush:
                _pending_clear_flush = False
                object_classify_db.flush_to_disk()  # 清除即写空库(task_handler 前安全窗口)
            Display.show_image(img, 0, 0, Display.LAYER_OSD1)
            gc.collect()  # 在 show_image 之后 GC,避免阻塞 DMA
            time.sleep_ms(lv.task_handler())
            fc += 1
            if fc % 30 == 0:
                print("[object_classify] fc=%d" % fc)
                if fc % 300 == 0:
                    import gc as _gc; print("[object_classify] mem_free=%d" % _gc.mem_free())
    finally:
        _deinit_ai()
        _destroy_ui()
        _RUNTIME = None
        object_classify_db.flush_to_disk()  # 退出兜底写盘
