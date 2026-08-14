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
from media.display import Display
from media.sensor import CAM_CHN_ID_0, CAM_CHN_ID_2
from core.icon_cache import icon_cache
from core.font_manager import fonts
from core.body_ai import PersonKeyPointApp, PERSON_KP_KMPATH, \
    SKELETON, KPS_COLORS, LIMB_COLORS, RGB888P_SIZE, DISPLAY_SIZE
from core.diagnostics import diag_line, read_temperature
from core.status_hud import status_text
from core.thermal import ThermalGuard

BAR_H = 52
PREVIEW_Y = BAR_H
PREVIEW_H = 376
BAR_BG = 0x1A1A1A

# 检测降频自适应(2026-08-13 对齐 face/object):有目标每 2 帧检测(跟手),
# 无目标 6 帧一次(静止场景 NPU 低负载)。非检测帧用缓存骨架画(稳定不闪)。
# 原固定 3 帧(2026-08-07 板端反馈卡顿 2→3 提帧率);自适应后按场景自调。
DET_INTERVAL_ACTIVE = 2  # 检测到目标:每 2 帧检测(pose 模型大+骨架绘制,摊薄开销)
DET_INTERVAL_IDLE = 6    # 无目标:每 6 帧检测(降 NPU 负载防过热)

_RUNTIME = None
_guard = None  # DVFS 温度保护(ThermalGuard,2026-08-13 根治)
_status_label = None  # 顶栏状态小字(帧率/温度/目标数,2026-08-13)
_screen = None
_top_bar = None
_bottom_bar = None
_preview = None
_body_kp = None
_det_counter = 0          # 检测降频计数(每自适应间隔帧跑完整推理)
_last_had_people = False  # 上轮检测是否有人(自适应间隔:有人高频/无人低频)
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
                kx = _clamp(round(kps[k][0] * DISPLAY_SIZE[0] // RGB888P_SIZE[0]), 0, DISPLAY_SIZE[0] - 1)
                ky = _clamp(round(kps[k][1] * DISPLAY_SIZE[1] // RGB888P_SIZE[1]), 0, DISPLAY_SIZE[1] - 1)
                if kps[k][2] > 0:
                    img.draw_circle(kx, ky, 5, KPS_COLORS[k], 4)
            ske = SKELETON[k]
            p1 = kps[ske[0] - 1]
            p2 = kps[ske[1] - 1]
            if p1[2] > 0.0 and p2[2] > 0.0:
                p1x = _clamp(round(p1[0] * DISPLAY_SIZE[0] // RGB888P_SIZE[0]), 0, DISPLAY_SIZE[0] - 1)
                p1y = _clamp(round(p1[1] * DISPLAY_SIZE[1] // RGB888P_SIZE[1]), 0, DISPLAY_SIZE[1] - 1)
                p2x = _clamp(round(p2[0] * DISPLAY_SIZE[0] // RGB888P_SIZE[0]), 0, DISPLAY_SIZE[0] - 1)
                p2y = _clamp(round(p2[1] * DISPLAY_SIZE[1] // RGB888P_SIZE[1]), 0, DISPLAY_SIZE[1] - 1)
                img.draw_line(p1x, p1y, p2x, p2y, LIMB_COLORS[k], 4)


def _det_interval(had_people):
    """自适应检测间隔:有人保持高频(ACTIVE),无人降频降温(IDLE)。"""
    return DET_INTERVAL_ACTIVE if had_people else DET_INTERVAL_IDLE


def on_frame(img):
    """推理 -> 骨架绘制 -> host_tick(0x11,id=目标排序序号)。

    降频:检测帧跑完整 run(chn2 AI 输入,run 后立即 gc 回收原生缓冲,防坑#16
    累积死机),非检测帧用缓存骨架画(稳定不闪)。
    """
    global _det_counter, _last_pose, _last_slots, _last_had_people
    if _RUNTIME is None or _body_kp is None:
        return
    boxes, kpses = _last_pose
    slots = []
    # 检测降频:纯自适应(有人高频/无人低频),不再被温度放大(2026-08-12 去保护)
    _det_counter += 1
    do_det = (_det_counter % _det_interval(_last_had_people) == 0)
    if do_det:
        # chn2 XGA RGBP888 硬件直出 planar(2026-08-12 对齐 face/object):
        # 单通道 chn0 packed 须 921KB 软件重排;chn2 零重排。
        # ⚠️ 必须 to_numpy_ref() 喂 run(Image 直喂致 nn.from_numpy 挂死)
        img_ai = _RUNTIME.sensor.snapshot(chn=CAM_CHN_ID_2)
        if img_ai is None:
            # chn2 取帧失败兜底:跳过本帧检测(保留缓存骨架,防异常杀循环)
            do_det = False
        else:
            img_np = img_ai.to_numpy_ref()
            try:
                res = _body_kp.run(img_np)
            except Exception as e:
                print("[body_detect] run error: %s" % e)
                res = ([], [])
            # 推理完成立即释放 chn2 帧引用(缩短与显示 DMA 共存期,坑#16)
            del img_np
            del img_ai
            gc.collect()  # 帧内回收 NPU 原生缓冲(坑#16:防累积死机)
            boxes = res[0] or []
            kpses = res[1] or []
            _last_pose = (boxes, kpses)
            _last_had_people = len(boxes) > 0  # 更新自适应间隔状态(有人→下次高频)
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
    global _screen, _top_bar, _bottom_bar, _preview, _status_label
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

    # 顶栏右侧状态小字(2026-08-13):帧率/温度/目标数,通用单位行
    # (字体缺 温/目/°/· 字符 → 纯 ASCII 免重建字体;语言切换内容不变)
    _status_label = lv.label(_top_bar)
    _status_label.set_text("--fps --C -")
    _status_label.align(lv.ALIGN.RIGHT_MID, -8, 0)
    _status_label.add_style(make_back_bar_text_style(fonts.body), 0)

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
    global _RUNTIME, _guard
    _RUNTIME = runtime
    _guard = ThermalGuard()
    _hud_t0 = time.ticks_ms()  # 状态栏 fps 窗口起点(2026-08-13)
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
            # 主循环 gc 每 2 帧(2026-08-13 二轮对齐 face):省 ~1-3ms/帧 CPU 活跃
            if fc % 2 == 0:
                gc.collect()  # 在 show_image 之后 GC,避免阻塞 DMA
            # LVGL 每 2 帧刷新(2026-08-13 二轮):顶/底栏静态,框走 OSD1 不属 LVGL;
            # 每帧 FULL 重渲染是 CPU 热源,lvtask ~2.5ms/帧 → 平均 1.25ms
            if fc % 2 == 0:
                lv.task_handler()
            # 睡眠固定 5ms(2026-08-12 对齐 face):K230 time.sleep_ms 忙等,
            # LVGL 建议的动态空转(约 30ms)是忙等热源;固定 5ms 降温 5~6°C
            time.sleep_ms(5)
            fc += 1
            # 顶栏状态小字(~1s 一次):帧率/温度/目标数(2026-08-13)
            if _status_label is not None and fc % 30 == 0:
                _hud_el = time.ticks_diff(time.ticks_ms(), _hud_t0)
                _hud_fps = 30 * 1000 // _hud_el if _hud_el > 0 else 0
                _hud_t0 = time.ticks_ms()
                _status_label.set_text(status_text(_RUNTIME.lang, _hud_fps,
                                                   read_temperature(),
                                                   len(_last_slots or [])))
            # DVFS 温度保护(2026-08-13 根治):每 30 帧读温调频,不拉长检测间隔(锁框跟手)
            if _guard is not None and fc % 30 == 0:
                _guard.tick(read_temperature())
            if fc % 30 == 0:
                print("[body_detect] fc=%d" % fc)
                if fc % 300 == 0:
                    print(diag_line("[body_detect]", fc))
    finally:
        _deinit_ai()
        _destroy_ui()
        _RUNTIME = None
