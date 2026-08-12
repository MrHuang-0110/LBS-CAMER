# scripts/object_detect/app.py — YOLOv8n COCO80 物体识别。
#
# 复用 _template 单线程主循环。单通道(死机根治 2026-08-07):AI 直接吃 chn0
# VGA RGB888 显示帧推理(官方 ai_lvgl 同构),无 chn2 DMA 竞争。底栏仅左侧 list
# 图标(清除/保存浮层)。KEY2 按类别注册,走 object_db.register via
# registrar。注册框显示 ID号+英文类名,未注册白框。协议类型 0x05 上传槽位。

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
from core.geometry import clamp_rect
from core.diagnostics import diag_line

BAR_H = 52
PREVIEW_Y = BAR_H
PREVIEW_H = 376
BAR_BG = 0x1A1A1A

# 25 槽画框颜色表(1~4 历史色 + 5~25 色环;学习 ID 不用白色),共享 core/box_colors
from core.box_colors import BOX_COLORS, BOX_UNKNOWN
# conf 字节 bit7 = 已学习标记(对齐 comm/host_api.LEARNED_FLAG);conf 0~100 恒 <128 不冲突
LEARNED_FLAG = 0x80
# 检测降频自适应(2026-08-12 对齐 face_detect):有目标高频/无目标低频。
# ACTIVE 原 1(每帧)用户实测卡——object 是纯 Python NMS + packed 软件重排,
# 每帧开销大,调 2 摊薄(约 30ms 一框仍跟手);无目标 6 帧降 NPU 负载。
DET_INTERVAL_ACTIVE = 2  # 检测到目标:每 2 帧检测一次(摊薄 postprocess 开销)
DET_INTERVAL_IDLE = 6    # 无目标:每 6 帧检测一次(降 NPU 负载防过热)

KMODEL_PATH = "/sdcard/examples/kmodel/yolov8n_320.kmodel"
_OBJ_DB_PATH = "/sdcard/CamerAi/data/object_db.json"
# AI 输入 = chn2 XGA 1024x768 RGBP888 硬件直出 planar(2026-08-12 对齐
# face_detect):单通道 chn0 packed 须 921KB 软件重排(on_max=136ms 卡顿);
# chn2 零重排。⚠️ ISP chn2 不支持 VGA,配 XGA(坑#20)。显示仍为 chn0 VGA。
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


def _rect_area(det):
    """检测框面积 (r-l)*(b-t)，每类取最大实例用。"""
    return (det[2] - det[0]) * (det[3] - det[1])


def _det_interval(had_objects):
    """自适应检测间隔:有目标保持实时(ACTIVE),无目标降频降温(IDLE)。"""
    return DET_INTERVAL_ACTIVE if had_objects else DET_INTERVAL_IDLE


_RUNTIME = None
_screen = None
_top_bar = None
_bottom_bar = None
_preview = None
_id_registry = None
_object_det = None
_db = None
_overlay = None
_clear_btn = None
_save_btn = None
_close_overlay = False
_det_counter = 0          # 检测降频计数(每自适应间隔帧跑 NPU)
_last_had_objects = True  # 上轮检测是否有目标(自适应间隔:有目标高频/无目标低频)
_last_max = {}            # 上轮 per_class_max 缓存(非检测帧画框用)
_last_slots = None        # 上轮槽位(非检测帧复用,主机数据连续)
_last_names = None        # 上轮名称帧列表(非检测帧复用)
_pending_clear_flush = False  # 清除请求:主循环安全窗口写空库(防断电重启旧数据回魂)


def _init_ai():
    global _object_det, _ai_ready
    _ai_ready = False
    try:
        print("[object_detect] loading det kmodel...")
        _object_det = ObjectDetectionApp(
            KMODEL_PATH, labels=COCO_LABELS, model_input_size=[320, 320],
            max_boxes_num=20, confidence_threshold=0.4, nms_threshold=0.2,
            rgb888p_size=[RGB888P_W, RGB888P_H], display_size=[DISPLAY_W, DISPLAY_H],
            debug_mode=0)
        _object_det.config_preprocess()
        _ai_ready = True
        print("[object_detect] AI ready")
    except Exception as e:
        print("[object_detect] _init_ai FAILED: %s" % e)
        sys.print_exception(e)
        _object_det = None
        _ai_ready = False


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
    """单通道检测 -> 每类取最大实例 -> 匹配 DB -> 画框 -> 十字 -> host_tick。

    AI 直接吃传入的 chn0 显示帧(img.to_numpy_ref),无 chn2 独立帧(死机根治
    2026-08-07)。每类别取面积最大实例画框+填槽,注册类彩色框+ID号+英文类名,
    未注册白框。KEY2 注册当前帧最大框的类别。
    """
    if _RUNTIME is None or _object_det is None:
        return
    global _det_counter, _last_max, _last_slots, _last_names, _last_had_objects
    per_class_max = _last_max
    slots = []   # 列表化:统一上限 25(原固定 4 槽),order_slots 按屏幕位置排序
    names = []   # 名称帧(类型 0x0E):[(id, COCO 类别名)],仅已注册槽位
    # 检测降频:自适应(无目标低频/有目标高频),不再被温度放大(2026-08-12 去保护)
    _det_counter += 1
    do_det = (_det_counter % _det_interval(_last_had_objects) == 0)
    if do_det:
        # chn2 XGA RGBP888 硬件直出 planar(2026-08-12 对齐 face_detect):
        # 单通道 chn0 packed 须 921KB 软件重排(on_max=136ms 卡顿);chn2 零
        # 重排。⚠️ 必须 to_numpy_ref() 喂 run(Image 直喂致 nn.from_numpy 挂死)
        img_ai = _RUNTIME.sensor.snapshot(chn=CAM_CHN_ID_2)
        if img_ai is None:
            # chn2 取帧失败兜底:跳过本帧检测(保留缓存结果,防异常杀循环)
            do_det = False
        else:
            img_np = img_ai.to_numpy_ref()
            try:
                dets = _object_det.run(img_np)
            except Exception as e:
                print("[object_detect] run error: %s" % e)
                dets = []
            # 推理完成立即释放 chn2 帧引用(缩短与显示 DMA 共存期,坑#16)
            del img_np
            del img_ai
            gc.collect()
            # 每类别取面积最大实例
            per_class_max = {}   # class_id -> [l,t,r,b,score,cid]
            for det in dets:
                try:
                    l, t, r, b, score, cid = [float(v) for v in det]
                except Exception:
                    continue
                cid = int(cid)
                rect = [l, t, r, b, score, cid]
                cur = per_class_max.get(cid)
                if cur is None or _rect_area(rect) > _rect_area(cur):
                    per_class_max[cid] = rect
            _last_max = per_class_max
            _last_had_objects = bool(per_class_max)  # 更新自适应间隔状态(无目标→下次低频)
            # KEY2 注册:当前帧最大框的类别(检测帧才有新鲜 dets)
            if _id_registry is not None and _id_registry.has_pending() and per_class_max:
                max_cid = max(per_class_max.values(), key=_rect_area)[5]
                try:
                    slot = _id_registry.try_register(max_cid, _RUNTIME.buzzer,
                                                     registrar=_db.register)
                    if slot is not None:
                        _db.flush_to_disk(_OBJ_DB_PATH)  # 注册即写(task_handler 前安全窗口)
                except Exception as e:
                    print("[object_detect] register error: %s" % e)
    # 画框:每帧(检测帧新框/非检测帧缓存框;类别精确匹配每帧重算,无窜脸)
    for cid, det in per_class_max.items():
        l, t, r, b, score, _ = det
        slot, _score = _db.match(cid)
        x = int(l) * DISPLAY_W // RGB888P_W
        y = int(t) * DISPLAY_H // RGB888P_H
        w = int(r - l) * DISPLAY_W // RGB888P_W
        h = int(b - t) * DISPLAY_H // RGB888P_H
        # 收进可视区(模型输出贴边/异常值防越界 1px 驱动挂死)
        x, y, w, h = clamp_rect(x, y, w, h, DISPLAY_W, DISPLAY_H)
        conf = int(score * 100)
        if slot is not None:
            color = _draw_color(BOX_COLORS.get(slot, BOX_UNKNOWN))
            img.draw_rectangle(x, y, w, h, color=color, thickness=4)
            img.draw_string_advanced(x, y - 24, 24,
                                     "ID%d %s" % (slot, COCO_LABELS[cid]), color=color)
            slots.append((slot, x, y, w, h, conf | LEARNED_FLAG))  # 已学习:bit7=1
            names.append((slot, COCO_LABELS[cid]))
        else:
            color = _draw_color(BOX_UNKNOWN)
            img.draw_rectangle(x, y, w, h, color=color, thickness=2)
            slots.append((0, x, y, w, h, conf))  # 未注册类别:id=0 + learned=0

    # 屏幕居中绿色十字(对准参考,小一点):VGA 640x480 中心 (320, 240)
    img.draw_cross(320, 240, color=(0xFF, 0x00, 0xFF, 0x00), size=20, thickness=2)

    # 非检测帧:复用上轮槽位,保持主机数据连续
    if not do_det and _last_slots is not None:
        slots = _last_slots
        names = _last_names
    else:
        _last_slots = slots
        _last_names = names
    if _RUNTIME.host is not None:
        _RUNTIME.host_tick(slots, names)


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
    """清 db 内存 + 置持久化标志(主循环安全窗口写空库)。"""
    global _close_overlay, _pending_clear_flush
    if e.get_code() != lv.EVENT.CLICKED:
        return
    if _db is not None:
        _db.clear()
    _pending_clear_flush = True  # 清除即写盘:主循环 task_handler 前安全窗口执行(防断电重启旧数据回魂)
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

    # 底栏计数(已学习 x/x)已按用户要求去掉


def _destroy_ui():
    global _screen, _top_bar, _bottom_bar, _preview
    global _overlay, _clear_btn, _save_btn
    for obj in (_clear_btn, _save_btn, _overlay, _top_bar, _bottom_bar, _preview):
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
    global _RUNTIME, _db, _pending_clear_flush
    _RUNTIME = runtime
    _db = ObjectDB()
    _db.load_from_disk(_OBJ_DB_PATH)
    exit_flag = [False]
    _init_ai()
    _init_registry(runtime.fpioa)
    _build_ui(runtime, exit_flag)
    fc = 0
    _perf_t = time.ticks_us()  # 每帧分段耗时插桩(诊断用,2026-08-12 对齐 face)
    _win_on_sum = 0
    _win_on_min = 1 << 30
    _win_on_max = 0
    _win_tot_sum = 0
    try:
        while not exit_flag[0]:
            os.exitpoint()
            img = runtime.sensor.snapshot(chn=CAM_CHN_ID_0)
            _p1 = time.ticks_us()
            try:
                on_frame(img)
            except Exception as e:
                print("[object_detect] on_frame error: %s" % e)
                try:
                    sys.print_exception(e)
                except Exception:
                    pass
            _p2 = time.ticks_us()
            if _id_registry is not None:
                _id_registry.poll_k2()
            _process_overlay_close()
            if _pending_clear_flush:
                _pending_clear_flush = False
                if _db is not None:
                    _db.flush_to_disk(_OBJ_DB_PATH)  # 清除即写空库(task_handler 前安全窗口)
            Display.show_image(img, 0, 0, Display.LAYER_OSD1)
            _p3 = time.ticks_us()
            gc.collect()  # 在 show_image 之后 GC,避免阻塞 DMA
            _p4 = time.ticks_us()
            lv.task_handler()
            _p4b = time.ticks_us()
            # 睡眠固定 5ms(2026-08-12 对齐 face):K230 time.sleep_ms 忙等,
            # LVGL 建议的动态空转(约 30ms)是忙等热源;固定 5ms 降温 5~6°C
            time.sleep_ms(5)
            _p5 = time.ticks_us()
            fc += 1
            # 30 帧窗口统计:on_min=普通帧、on_max=检测帧,对齐 face debug 输出
            _on_us = _p2 - _p1
            _win_on_sum += _on_us
            _win_tot_sum += _p5 - _perf_t
            if _on_us < _win_on_min:
                _win_on_min = _on_us
            if _on_us > _win_on_max:
                _win_on_max = _on_us
            if fc % 30 == 0:
                print("[perf] win30 on_avg=%d on_min=%d on_max=%d total_avg=%d "
                      "lvtask=%d sleep=%d show=%d" % (
                          _win_on_sum // 30, _win_on_min, _win_on_max,
                          _win_tot_sum // 30, _p4b - _p4, _p5 - _p4b, _p3 - _p2))
                print("[object_detect] fc=%d" % fc)
                _win_on_sum = 0
                _win_on_min = 1 << 30
                _win_on_max = 0
                _win_tot_sum = 0
                if fc % 300 == 0:
                    print(diag_line("[object_detect]", fc))
            _perf_t = time.ticks_us()
    finally:
        _deinit_ai()
        _destroy_ui()
        if _db is not None:
            _db.flush_to_disk(_OBJ_DB_PATH)
        _RUNTIME = None
