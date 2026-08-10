# scripts/body_detect/app.py — 人体姿态识别(单通道 + yolov8n-pose 骨架 + 协议 0x11)
#
# 2026-08-07 重构:
# - 单通道(死机根治,同 object_detect):AI 直接吃 chn0 VGA 显示帧推理,无 chn2
#   XGA 大帧 DMA 竞争 → 移除原"检测+通用特征提取"双 kmodel 与注册链路。
# - 模型 yolov8n-pose.kmodel:每帧输出人体框 + 17 关键点,画骨架(点+骨骼线)。
# - 主机协议 0x11:数据格式不变(N×10B),id = 按屏幕位置排序后的目标序号(最左=1),
#   learned 恒 0,conf = 检测置信度。
# - 降频:每 DET_INTERVAL 帧跑一次 NPU,非检测帧用缓存骨架画(死机防护)。

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
from core.body_ai import PersonKeyPointApp, PERSON_KP_KMPATH, \
    SKELETON, KPS_COLORS, LIMB_COLORS, RGB888P_SIZE, DISPLAY_SIZE
from core.diagnostics import diag_line

BAR_H = 52
PREVIEW_Y = BAR_H
PREVIEW_H = 376
BAR_BG = 0x1A1A1A

# 检测降频:每 DET_INTERVAL 帧跑一次 NPU(非检测帧用缓存骨架画,减 DMA/NPU 竞争)
# 2026-08-07 板端反馈卡顿: 2→3(同 object_detect 提平均帧率)
DET_INTERVAL = 3

_RUNTIME = None
_screen = None
_top_bar = None
_bottom_bar = None
_preview = None
_body_kp = None
_det_counter = 0          # 检测降频计数(每 DET_INTERVAL 帧跑完整推理)
_last_pose = ([], [])     # 上轮 (boxes, kpses) 缓存
_last_slots = None        # 上轮槽位(非检测帧复用,主机数据连续)


def _init_ai():
    """Load yolov8n-pose kmodel before the loop. 失败则 _ai_ready=False。"""
    global _body_kp, _ai_ready
    _ai_ready = False
    try:
        print("[body_detect] loading pose kmodel...")
        _body_kp = PersonKeyPointApp(
            PERSON_KP_KMPATH, model_input_size=[320, 320],
            confidence_threshold=0.2, nms_threshold=0.5,
            rgb888p_size=RGB888P_SIZE, display_size=DISPLAY_SIZE,
            debug_mode=0)
        _body_kp.config_preprocess()
        _ai_ready = True
        print("[body_detect] AI ready")
    except Exception as e:
        print("[body_detect] _init_ai FAILED: %s" % e)
        sys.print_exception(e)
        _body_kp = None
        _ai_ready = False


def _deinit_ai():
    global _body_kp
    if _body_kp is not None:
        try:
            _body_kp.deinit()
        except Exception as e:
            print("[body_detect] deinit warning: %s" % e)
        _body_kp = None


def _clamp(v, lo, hi):
    """坐标夹取到可视区,防绘制越界挂死(坑:K230 绘制越界致死机)。"""
    if v < lo:
        return lo
    if v > hi:
        return hi
    return v


def _draw_skeleton(img, boxes, kpses):
    """画骨骼:17 关键点圆 + 19 骨骼线(坐标 1:1,chn0 VGA = 显示 VGA)。

    只画骨架不画人体框(2026-08-07 用户确认);boxes 仅作 kpses 索引。
    """
    for i in range(len(boxes)):
        kps = kpses[i] if i < len(kpses) else []
        # aidemo 返回 COCO 17 关键点(坐标+score);demo 的 range(17+2) 是画
        # 19 条骨骼线,不是 19 个点——只要求 17 点,否则整人跳过只剩框
        if len(kps) < 17:
            continue
        for k in range(len(SKELETON)):
            if k < len(KPS_COLORS):
                kx = _clamp(round(kps[k][0]), 0, DISPLAY_SIZE[0] - 1)
                ky = _clamp(round(kps[k][1]), 0, DISPLAY_SIZE[1] - 1)
                if kps[k][2] > 0:
                    img.draw_circle(kx, ky, 5, KPS_COLORS[k], 4)
            ske = SKELETON[k]
            p1 = kps[ske[0] - 1]
            p2 = kps[ske[1] - 1]
            if p1[2] > 0.0 and p2[2] > 0.0:
                p1x = _clamp(round(p1[0]), 0, DISPLAY_SIZE[0] - 1)
                p1y = _clamp(round(p1[1]), 0, DISPLAY_SIZE[1] - 1)
                p2x = _clamp(round(p2[0]), 0, DISPLAY_SIZE[0] - 1)
                p2y = _clamp(round(p2[1]), 0, DISPLAY_SIZE[1] - 1)
                img.draw_line(p1x, p1y, p2x, p2y, LIMB_COLORS[k], 4)


def on_frame(img):
    """单通道推理 -> 骨架绘制 -> host_tick(0x11,id=目标排序序号)。

    降频:检测帧跑完整 run(chn0 帧推理,run 后立即 gc 回收原生缓冲,防坑#16
    累积死机),非检测帧用缓存骨架画(稳定不闪)。
    """
    global _det_counter, _last_pose, _last_slots
    if _RUNTIME is None or _body_kp is None:
        return
    boxes, kpses = _last_pose
    slots = []
    _det_counter += 1
    do_det = (_det_counter % DET_INTERVAL == 0)
    if do_det:
        # 单通道:AI 直接吃 chn0 显示帧,无 chn2 DMA 竞争(死机根治 2026-08-07)
        img_np = img.to_numpy_ref()
        if not _body_kp.input_is_packed:
            _planar = np.zeros((3, img.height(), img.width()), dtype=np.uint8)
            _planar[0] = img_np[:, :, 0]
            _planar[1] = img_np[:, :, 1]
            _planar[2] = img_np[:, :, 2]
            img_np = _planar
        try:
            res = _body_kp.run(img_np)
        except Exception as e:
            print("[body_detect] run error: %s" % e)
            res = ([], [])
        if not _body_kp.input_is_packed:
            del _planar
        del img_np
        gc.collect()  # 帧内回收 NPU 原生缓冲(坑#16:防累积死机)
        boxes = res[0] or []
        kpses = res[1] or []
        _last_pose = (boxes, kpses)
        # 槽位:id = 按 (x,y) 升序后的目标序号(最左=1),learned 恒 0
        # aidemo 人体框可能只有 4 元 [l,t,r,b] 无 score → 缺省 conf=100
        items = []
        for i in range(len(boxes)):
            try:
                bbox = [float(v) for v in boxes[i][:4]]
                l, t, r, b = bbox
                conf = int(float(boxes[i][4]) * 100) if len(boxes[i]) >= 5 else 100
            except Exception:
                continue
            x = int(l) * DISPLAY_SIZE[0] // RGB888P_SIZE[0]
            y = int(t) * DISPLAY_SIZE[1] // RGB888P_SIZE[1]
            w = int(r - l) * DISPLAY_SIZE[0] // RGB888P_SIZE[0]
            h = int(b - t) * DISPLAY_SIZE[1] // RGB888P_SIZE[1]
            items.append((x, y, w, h, conf))
        items.sort(key=lambda t: (t[0], t[1]))
        slots = [(i + 1, x, y, w, h, conf)
                 for i, (x, y, w, h, conf) in enumerate(items)]
    # 画骨架:每帧(检测帧新骨架/非检测帧缓存骨架)
    _draw_skeleton(img, boxes, kpses)
    # 屏幕居中绿色十字(对准参考):VGA 640×480 中心 (320, 240)
    img.draw_cross(320, 240, color=(0xFF, 0x00, 0xFF, 0x00), size=20, thickness=2)
    # 非检测帧:复用上轮槽位,保持主机数据连续
    if not do_det and _last_slots is not None:
        slots = _last_slots
    else:
        _last_slots = slots
    if _RUNTIME.host is not None:
        _RUNTIME.host_tick(slots)


def _build_ui(runtime, exit_flag):
    """顶栏(back+标题) + 透明预览 + 底栏背景(无功能按钮)。"""
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

    icon_data, icon_dsc = icon_cache.get_body_icon("back")
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
    title.set_text(runtime.lang.t("category.body_detect"))
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


def _destroy_ui():
    """Delete LVGL objects and restore screen opacity for the menu."""
    global _screen, _top_bar, _bottom_bar, _preview
    for obj in (_top_bar, _bottom_bar, _preview):
        if obj is not None:
            try:
                obj.delete()
            except Exception:
                pass
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
    """Entry point called by reset-framework main.py. 单线程主循环。"""
    global _RUNTIME
    _RUNTIME = runtime
    exit_flag = [False]
    _init_ai()
    _build_ui(runtime, exit_flag)
    fc = 0
    try:
        while not exit_flag[0]:
            os.exitpoint()
            img = runtime.sensor.snapshot(chn=CAM_CHN_ID_0)
            try:
                on_frame(img)
            except Exception as e:
                print("[body_detect] on_frame error: %s" % e)
                try:
                    sys.print_exception(e)
                except Exception:
                    pass
            Display.show_image(img, 0, 0, Display.LAYER_OSD1)
            gc.collect()  # 在 show_image 之后 GC,避免阻塞 DMA
            time.sleep_ms(lv.task_handler())
            fc += 1
            if fc % 30 == 0:
                print("[body_detect] fc=%d" % fc)
                if fc % 300 == 0:
                    print(diag_line("[body_detect]", fc))
    finally:
        _deinit_ai()
        _destroy_ui()
        _RUNTIME = None
