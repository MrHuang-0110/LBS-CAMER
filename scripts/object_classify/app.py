# scripts/object_classify/app.py — 物体分类学习器(双 kmodel + 5 ID 选项卡 + KEY 中心学习)
#
# 单通道(死机根治 2026-08-07,同 object_detect): AI 直接吃 chn0 显示帧,
# det(YOLOv8n) + rec(recognition) 共用同一帧。学习器模式(v2,2026-08-07):
# 无已学 ID 时零推理(预览丝滑);点底栏 ID1~ID5 选学习目标槽(选中绿高亮);
# 中央十字对准物体按 K2 → 单次 det+1rec 学中心物体到选中 ID 槽 + 全屏黄框
# 闪烁确认;有已学 ID 时每 DET_INTERVAL 帧降频识别:命中物体不画框,屏幕四边
# 全局框 + 物体中心标 ID%d + 十字架对准最佳匹配。

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
from core.object_classify_db import object_classify_db, database_search
from core.object_classify_lock import pick_box_at_point

BAR_H = 52
PREVIEW_Y = BAR_H
PREVIEW_H = 376
# 中心退化区域(224×224,VGA 画面中心):YOLO(COCO80)未检到时学习退化用,
# 任意物体对准中央即可学,不受类别限制(2026-08-07 用户反馈)
CENTER_BOX = [208, 128, 432, 352]
BAR_BG = 0x1A1A1A

# 25 槽画框颜色表(1~4 历史色 + 5~25 色环;学习 ID 不用白色),共享 core/box_colors
from core.box_colors import BOX_COLORS, BOX_UNKNOWN
# conf 字节 bit7 = 已学习标记(对齐 comm/host_api.LEARNED_FLAG);conf 0~100 恒 <128 不冲突
LEARNED_FLAG = 0x80
# 框/ID 颜色均按槽位取色(1~4 历史色 + 5~25 色环),学习 ID 不用白色
# 检测降频:锁定态每 DET_INTERVAL 帧跑一次 NPU,其余帧用缓存结果画框。
# 2026-08-07 板端(object_detect 验证):2→3 降推理频率提平均帧率。
DET_INTERVAL = 3
# 学习 ID 上限(用户确认 2026-08-07):底栏 5 个 ID 选项卡,对齐 object_classify_db.MAX_SLOTS
MAX_ID_TABS = 5
# 注册成功全屏框闪烁帧数(约 0.4s @30fps):KEY 记录确认反馈
RECORD_FLASH_FRAMES = 12


def _draw_color(hex_color):
    """hex 0xRRGGBB → K230 draw_rectangle color tuple (A, B, G, R)。"""
    r = (hex_color >> 16) & 0xFF
    g = (hex_color >> 8) & 0xFF
    b = hex_color & 0xFF
    return (0xFF, b, g, r)


def _ai_input(img):
    """chn0 packed 帧 → AI 输入 img_np。

    input_is_packed(ai2d 支持 RGB888p_FMT 枚举)时零拷贝直接喂 chn0 帧视图;
    否则 NCHW(planar 语义)须 np.zeros 建 (3,H,W) 并逐通道重排(2026-08-07
    全屏假框根因;ai2d build 声明 [1,3,H,W],重排喂省略 batch=1 的 (3,H,W))。
    调用方推理后须 del img_np 立即释放引用(planar 时即释放重排缓冲)。
    """
    img_np = img.to_numpy_ref()
    if _ocr.input_is_packed:
        return img_np
    _planar = np.zeros((3, img.height(), img.width()), dtype=np.uint8)
    _planar[0] = img_np[:, :, 0]
    _planar[1] = img_np[:, :, 1]
    _planar[2] = img_np[:, :, 2]
    return _planar


def _to_disp_boxes(det_boxes):
    """检测框(rgb888p 坐标) → 显示坐标列表 [(x, y, w, h)]。"""
    disp = []
    for d in det_boxes:
        l, t, r, b = int(d[0]), int(d[1]), int(d[2]), int(d[3])
        x = l * DISPLAY_SIZE[0] // RGB888P_SIZE[0]
        y = t * DISPLAY_SIZE[1] // RGB888P_SIZE[1]
        w = (r - l) * DISPLAY_SIZE[0] // RGB888P_SIZE[0]
        h = (b - t) * DISPLAY_SIZE[1] // RGB888P_SIZE[1]
        disp.append((x, y, w, h))
    return disp


def _draw_preview_box(img, color, thickness):
    """画预览区四边框。K230 draw_rectangle 参数为 (x, y, w, h) 而非对角坐标:
    误按 x1/y1 传参会把宽高当对角点,底部线画到底栏内被盖(2026-08-07 根因);
    传宽=屏宽、高=预览区高时四边贴可视区,线宽向内扩展全可见。"""
    img.draw_rectangle(0, PREVIEW_Y, DISPLAY_SIZE[0], PREVIEW_H,
                       color=color, thickness=thickness)


_RUNTIME = None
_screen = None
_top_bar = None
_bottom_bar = None
_preview = None
_id_registry = None
_ocr = None                 # ObjectClassifyRecognition
_db_features = {}
_overlay = None
_clear_btn = None
_save_btn = None
_close_overlay = False
_det_counter = 0          # 检测降频计数(锁定态每 DET_INTERVAL 帧跑 NPU)
_last_det = ([], [])      # 上轮 (det_boxes, features) 缓存
_last_disp = []           # 上轮 disp_boxes 缓存(非检测帧画框/触摸用)
_last_slots = None        # 上轮槽位(非检测帧复用,主机数据连续)
_last_names = None        # 上轮名称帧列表(非检测帧复用)
_pending_clear_flush = False  # 清除请求:主循环安全窗口写空库(防断电重启旧数据回魂)
_selected_id = 1          # 当前选中的学习 ID 槽(底栏选项卡,KEY 注册目标)
_record_flash = 0         # 注册成功预览区闪烁剩余帧数(>0 时画框+左上角 ID)
_record_slot = None       # 闪烁期间要标注的已注册槽位(框与 ID 均用槽位色)
_tabs = []                # 底栏 ID 选项卡对象列表(高亮刷新用)


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
        print("[object_classify] AI ready, input_is_packed=%s, loaded %d object(s)" % (
            _ocr.input_is_packed, len(_db_features)))
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
    """学习器:DB 空零推理 / K2 学中心物体 / DB 非空降频识别已学 ID(v2 交互)。

    无已学 ID:零 NPU 推理,纯预览 + 中央绿十字,30fps 丝滑。按 K2 → 单次
    det + 提取画面中心(320,240)物体特征 → register_at 注册到选中 ID 槽 →
    全屏黄框闪烁确认。识别(DB 非空):每 DET_INTERVAL 帧 det+全框特征+
    database_search;命中已学 ID 的物体不画物体框——屏幕四边画全局框 +
    各命中物体中心标 ID%d + 中央十字架移到最佳匹配物体中心;未命中十字架
    回中央。单通道(AI 吃 chn0 帧,死机根治)。
    """
    global _det_counter, _last_det, _last_disp, _last_slots, _last_names, _record_flash
    if _RUNTIME is None or _ocr is None:
        return

    # 注册成功确认(闪烁若干帧,K2 学习反馈):预览区槽位色框 + 左上角槽位色 ID
    if _record_flash > 0:
        flash_color = _draw_color(BOX_COLORS.get(_record_slot, BOX_UNKNOWN))
        _draw_preview_box(img, flash_color, 6)
        if _record_slot is not None:
            tag = "ID%d" % _record_slot
            img.draw_string_advanced(8, PREVIEW_Y + 4, 24, tag, color=flash_color)
        _record_flash -= 1

    # K2 学习:按下即学画面中心物体(学完本帧返回,避免同帧双重推理)
    if _id_registry is not None and _id_registry.has_pending():
        _learn_center(img)
        if _RUNTIME is not None and _RUNTIME.host is not None:
            _RUNTIME.host_tick([], [])
        return

    slots = []   # 列表化:统一上限 25,order_slots 按屏幕位置排序
    names = []   # 名称帧(类型 0x0E):[(id, 名称)],已注册目标用 obj<槽号> 标识

    if not _db_features:
        # ── 无已学 ID:零推理,纯预览(30fps 丝滑) ──
        # 屏幕居中绿色十字(对准参考):VGA 640×480 中心 (320, 240)
        img.draw_cross(320, 240, color=(0xFF, 0x00, 0xFF, 0x00), size=20, thickness=2)
        if _RUNTIME is not None and _RUNTIME.host is not None:
            _RUNTIME.host_tick(slots, names)
        return

    # ── 识别模式:降频推理 + 匹配已学 ID ──
    det_boxes, features = _last_det
    disp_boxes = _last_disp
    _det_counter += 1
    do_det = (_det_counter % DET_INTERVAL == 0)
    if do_det:
        # 单通道(死机根治 2026-08-07):AI 直接吃 chn0 显示帧,无 chn2 DMA 竞争
        img_np = None  # 预绑定:输入准备异常时后续 del 不掩真异常
        try:
            img_np = _ai_input(img)
            det_boxes, features = _ocr.run(img_np)
            print("[object_classify] DET n=%d" % len(det_boxes))  # 插桩:定位死机挂点
        except Exception as e:
            print("[object_classify] run error: %s" % e)
            det_boxes, features = [], []
        _last_det = (det_boxes, features)
        # 推理完成立即释放 numpy 引用;帧内 gc 回收 NPU 原生缓冲(坑#16:防累积死机)
        if img_np is not None:
            del img_np
        gc.collect()
        disp_boxes = _to_disp_boxes(det_boxes)
        _last_disp = disp_boxes

    # 匹配已学 ID:命中物体不画物体框,预览区白框 + 左上角槽位色 ID + 十字架对准最佳
    hits = []    # [(idx, slot, score)]
    best = None  # 最佳匹配 (idx, slot, score)
    for i, feat in enumerate(features):
        slot, sc = database_search(feat, _db_features)
        if slot is not None:
            hits.append((i, slot, sc))
            if best is None or sc > best[2]:
                best = (i, slot, sc)

    if best is not None:
        # 预览区四边全局框(识别激活指示),框色 = 最佳命中槽位色
        _draw_preview_box(img, _draw_color(BOX_COLORS.get(best[1], BOX_UNKNOWN)), 4)
        label_x = 8
        for i, slot, sc in hits:
            x, y, w, h = disp_boxes[i]
            slots.append((slot, x, y, w, h, int(sc * 100) | LEARNED_FLAG))
            names.append((slot, "obj%d" % slot))
            # 左上角按槽位颜色标 ID(1~4 历史色 / 5~25 色环),水平排列
            tag = "ID%d" % slot
            img.draw_string_advanced(label_x, PREVIEW_Y + 4, 24, tag,
                                     color=_draw_color(BOX_COLORS.get(slot, BOX_UNKNOWN)))
            label_x += len(tag) * 24 + 8
    # 十字架固定屏幕中央不动(对准参考):VGA 640×480 中心 (320, 240)
    img.draw_cross(320, 240, color=(0xFF, 0x00, 0xFF, 0x00), size=20, thickness=2)

    # 非检测帧:复用上轮槽位,保持主机数据连续(须在 host_tick 前)
    if not do_det and _last_slots is not None:
        slots = _last_slots
        names = _last_names
    else:
        _last_slots = slots
        _last_names = names
    if _RUNTIME is not None and _RUNTIME.host is not None:
        _RUNTIME.host_tick(slots, names)


def _learn_center(img):
    """K2 学习画面中心物体:先 YOLO 命中中心框提特征,无框时退化中心
    固定区域提特征——任意物体对准中央即可学,不受 COCO80 类别限制。
    学习成功:预览区槽位色框闪烁确认 + 左上角 ID + 蜂鸣 + 置 _det_counter
    使下一帧识别首帧必推理(学习帧 on_frame 已 return,避免同帧双重推理)。
    """
    global _det_counter, _record_flash, _record_slot
    img_np = None  # 预绑定:输入准备异常时后续 del 不掩真异常
    feature = None
    try:
        img_np = _ai_input(img)
        det_boxes = _ocr.detector.run(img_np)
        disp_boxes = _to_disp_boxes(det_boxes)
        idx = pick_box_at_point(disp_boxes, 320, 240)
        print("[object_classify] LEARN det n=%d" % len(det_boxes))  # 插桩:定位死机挂点
        if idx is not None:
            feature = _ocr.extract_feature(det_boxes[idx], img_np)
        else:
            # 中心无 YOLO 框(非 COCO80 类物体):退化中心固定区域提特征
            feature = _ocr.extract_feature(CENTER_BOX, img_np)
    except Exception as e:
        print("[object_classify] learn det error: %s" % e)
    if feature is not None:
        try:
            slot = _id_registry.try_register(
                feature, _RUNTIME.buzzer,
                registrar=lambda f: object_classify_db.register_at(f, _selected_id))
            if slot is not None:
                object_classify_db.flush_to_disk()
                _db_features[slot] = object_classify_db.get_features().get(slot)
                _record_slot = slot
                _record_flash = RECORD_FLASH_FRAMES  # 预览区槽位色框闪烁确认
                _det_counter = DET_INTERVAL - 1      # 下一帧识别首帧必推理
        except Exception as e:
            print("[object_classify] learn error: %s" % e)
    else:
        if _RUNTIME is not None and _RUNTIME.buzzer is not None:
            _RUNTIME.buzzer.beep(ms=200)  # 提特征失败,学习失败提示
    if img_np is not None:
        del img_np
    gc.collect()


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


def _on_tab_clicked(e, sid):
    """点 ID 选项卡:设 _selected_id(KEY 注册目标槽) + 刷新高亮 + 蜂鸣。"""
    global _selected_id
    if e.get_code() != lv.EVENT.CLICKED:
        return
    _selected_id = sid
    _refresh_tabs()
    if _RUNTIME is not None and _RUNTIME.buzzer is not None:
        _RUNTIME.buzzer.beep(ms=50)


def _refresh_tabs():
    """按 _selected_id 刷新选项卡高亮(选中绿色,未选深灰)。"""
    for i, tab in enumerate(_tabs):
        if i + 1 == _selected_id:
            tab.set_style_bg_color(lv.color_hex(0x00FF00), 0)
        else:
            tab.set_style_bg_color(lv.color_hex(0x2A2A2A), 0)


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

    # 5 个 ID 选项卡铺满底栏(list 图标右侧到右边缘):点选学习目标 ID,选中绿高亮
    global _tabs
    _tabs = []
    tab_gap = 4
    tab_x = 56
    tab_w = (DISPLAY_SIZE[0] - tab_x - 2 - (MAX_ID_TABS - 1) * tab_gap) // MAX_ID_TABS
    for i in range(1, MAX_ID_TABS + 1):
        tab = lv.btn(_bottom_bar)
        tab.set_size(tab_w, 40)
        tab.align(lv.ALIGN.LEFT_MID, tab_x, 0)
        tab.set_style_bg_opa(255, 0)
        tab.set_style_radius(8, 0)
        tab.set_style_border_width(0, 0)
        tab.set_style_pad_all(0, 0)
        tab_lbl = lv.label(tab)
        tab_lbl.set_text("ID%d" % i)
        tab_lbl.add_style(make_back_bar_text_style(fonts.body), 0)
        tab_lbl.center()
        tab.add_event(lambda e, sid=i: _on_tab_clicked(e, sid), lv.EVENT.CLICKED, None)
        _tabs.append(tab)
        tab_x += tab_w + tab_gap
    _refresh_tabs()

    # 底栏计数(已学习 x/x)已按用户要求去掉


def _destroy_ui():
    """Delete LVGL objects and restore screen opacity for the menu."""
    global _screen, _top_bar, _bottom_bar, _preview
    global _overlay, _clear_btn, _save_btn, _tabs
    objs = [_overlay, _clear_btn, _save_btn, _top_bar, _bottom_bar, _preview]
    objs.extend(_tabs)
    for obj in objs:
        if obj is not None:
            try:
                obj.delete()
            except Exception:
                pass
    _overlay = None
    _clear_btn = None
    _save_btn = None
    _tabs = []
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
