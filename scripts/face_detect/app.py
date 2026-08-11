# scripts/face_detect/app.py — 人脸检测/识别/注册(单线程模板)。
#
# 主循环:snapshot chn0 → on_frame(chn2 AI 检测+识别+画框) → show_image →
# lv.task_handler。单线程串行:无 AI 线程、无自建 media/Display。
# 检测/识别按 DET_IDLE_INTERVAL 与 REG_INTERVAL_* 降频;K2 注册取最大脸;
# 注册即写/清除即写走主循环 task_handler 前安全窗口 flush(坑#2 不冲突)。

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
from core.diagnostics import diag_line, read_temperature
from core.thermal import thermal_mode, cooled_interval

BAR_H = 52
PREVIEW_Y = BAR_H
PREVIEW_H = 376
BAR_BG = 0x1A1A1A
# 检测降频自适应(2026-08-10 死机排查):无脸低频/有脸高频
# - 静止无脸场景(实测主场景)只跑 det 单模型,温度随运行累积到 100°C 死机;
#   DET_INTERVAL_IDLE=6 → det 每秒 10→3.3 次(@20fps),负载降 2/3,防过热
# - 有脸场景 DET_INTERVAL_ACTIVE=2 保持实时(框延迟≤1 帧)
# 业务功能不变(检测到脸后自动回到高频,人离开自动降频)
DET_INTERVAL_ACTIVE = 2  # 检测到脸:每 2 帧检测一次(实时)
DET_INTERVAL_IDLE = 6    # 无脸:每 6 帧检测一次(降负载防过热)
# 识别降频(帧率优先):按人数分级间隔识别一轮;非识别帧复用上轮槽位,
# 2026-08-10 死机排查: 识别间隔 2/4/6 → 4/8/12(识别频率减半,负载大头),
# ID 更新延迟≤600ms(4 人),脸数上限与识别能力不变(保持业务功能)
REG_MAX_FACES = 4    # 多人性能保护:每帧最多识别脸数(与协议 4 槽对齐),超出只画框
REG_INTERVAL_1 = 4   # 1 人:每 4 帧识别一轮
REG_INTERVAL_2 = 8   # 2~3 人:每 8 帧识别一轮
REG_INTERVAL_3 = 12  # ≥4 人:每 12 帧识别一轮
MIN_REG_AREA = 1600  # 注册最小脸面积(VGA px²,≈40×40);太小拒绝注册(特征质量差)
TRACK_RADIUS = 80    # 非识别帧 ID 关联半径(chn2 1024x768 像素;人脸帧间位移 <80 正常)
# conf 字节 bit7 = 已学习标记(对齐 comm/host_api.LEARNED_FLAG);conf 0~100 恒 <128 不冲突
LEARNED_FLAG = 0x80

_RUNTIME = None
_screen = None
_top_bar = None
_bottom_bar = None
_preview = None
_face_det = None
_face_reg = None
_db_features = {}
_id_registry = None
_overlay = None
_clear_btn = None
_save_btn = None
_close_overlay = False
_reg_counter = 0     # 识别跳帧计数(每 reg_interval 帧识别一轮)
_last_slots = None   # 上轮识别槽位(非识别帧复用,保持主机数据连续)
_det_counter = 0     # 检测跳帧计数(每自适应间隔帧检测一次)
_last_had_face = True  # 上轮检测是否有脸(自适应间隔:有脸高频/无脸低频)
_thermal_mode = 0    # 温度保护模式(0 正常/1 降频/2 冷却,core/thermal)
_thermal_counter = 0  # 温度读取计数(每 30 帧读一次 machine.temperature)
_last_det = ([], []) # 上轮检测结果缓存(det_boxes, landms)
_pending_clear_flush = False  # 清除请求:主循环安全窗口立即写空库(防断电重启旧数据回魂)
_last_track = []        # 识别帧目标缓存[(cx, cy, mid), ...]:非识别帧最近邻关联画 ID 框





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


def _box_center(det):
    """检测框中心 (cx, cy)（chn2 坐标，未缩放）。"""
    return det[0] + det[2] // 2, det[1] + det[3] // 2


def _associate_to_tracked(det_boxes, tracked, radius):
    """非识别帧:按中心最近邻把缓存跟踪目标关联到新框,返回 [(det_idx, mid), ...]。

    防窜脸:最近邻 + 半径阈值,新脸无近邻不画 ID,位置微动 ID 稳定;
    一个跟踪目标只关联一个框(已占用跳过)。radius 语义=半径内严格更近才匹配。
    """
    results = []
    if not det_boxes or not tracked:
        return results
    taken = set()
    radius2 = radius * radius
    for i, det in enumerate(det_boxes):
        cx, cy = _box_center(det)
        best_t = None
        best_d2 = radius2
        for t, (tx, ty, mid) in enumerate(tracked):
            if t in taken:
                continue
            d2 = (tx - cx) * (tx - cx) + (ty - cy) * (ty - cy)
            if d2 < best_d2:
                best_d2 = d2
                best_t = t
        if best_t is not None:
            taken.add(best_t)
            results.append((i, tracked[best_t][2]))
    return results


def _det_interval(had_face):
    """自适应检测间隔:有脸保持实时(ACTIVE),无脸降频降温(IDLE)。"""
    return DET_INTERVAL_ACTIVE if had_face else DET_INTERVAL_IDLE


def on_frame(img):
    """Detect on chn2, recognize ALL faces, draw onto chn0 preview, push 4 slots.

    识全部脸：对每个检测框跑 reg + database_search，匹配的 DB slot 填对应组。
    K2 注册仍取最大脸（注册语义不变）。每帧推送4槽位给上位机。
    ⚠️ 多脸 reg 为板端首次验证（坑#16 NPU 累积风险，见 spec 降级方案）。
    """
    if _RUNTIME is None or _face_det is None:
        return
    global _reg_counter, _last_slots, _det_counter, _last_det, _last_track, _last_had_face
    global _thermal_mode, _thermal_counter
    det_boxes, landms = _last_det
    recognition_results = []
    slots = []  # 列表化:统一上限 25(原固定 4 槽),order_slots 按屏幕位置排序
    # 温度保护:每 30 帧读一次温度更新热模式(超温强制放大检测间隔防 100°C 死机)
    _thermal_counter += 1
    if _thermal_counter % 30 == 0:
        _new_mode = thermal_mode(read_temperature())
        if _new_mode != _thermal_mode:
            if _new_mode:
                print("[face_detect] thermal mode=%d" % _new_mode)
            _thermal_mode = _new_mode
    # 检测降频:自适应(无脸低频/有脸高频)再被温度模式放大(cooled_interval)
    _det_counter += 1
    do_det = (_det_counter % cooled_interval(
        _det_interval(_last_had_face), _thermal_mode) == 0)
    if do_det:
        # 检测帧才取 AI 输入(chn2 XGA RGBP888 planar,官方同构):
        # 非检测帧跳过 det,减 chn2 取流与显示 DMA 竞争(2026-08-03 验证)
        # 过热修复(2026-08-11):单通道吃 chn0 RGB888 须每检测帧 921KB 软件
        # planar 重排(on=108ms CPU 满载→100.7°C 死机);chn2 RGBP888 硬件
        # 直出 planar,to_numpy_ref() 零拷贝视图直接喂 AI2D(NCHW)
        # ⚠️ 必须 to_numpy_ref() 喂 run(app_full_debug_backup/test_face_baseline
        # 历史验证路径);直接传 Image 对象致 nn.from_numpy 挂死(2026-08-11)
        img_ai = _RUNTIME.sensor.snapshot(chn=CAM_CHN_ID_2)
        if _det_counter <= 4:
            print("[dbg] det#%d post-snap2 %s" % (_det_counter, img_ai))
        if img_ai is None:
            # chn2 取帧失败兜底:跳过本帧检测(保留缓存结果,防异常杀循环)
            do_reg = False
        else:
            img_np = img_ai.to_numpy_ref()
            if _det_counter <= 4:
                print("[dbg] det#%d pre-detrun" % _det_counter)
            det_boxes, landms = _face_det.run(img_np)
            if _det_counter <= 4:
                print("[dbg] det#%d post-detrun det=%d" % (_det_counter, len(det_boxes) if det_boxes else 0))
            _last_det = (det_boxes, landms)
            # 临时诊断(过热修复后):每 30 帧打印检测框数+AI 帧尺寸
            if _det_counter % 30 == 0:
                _n_det = len(det_boxes) if det_boxes else 0
                _sz = img_ai.size() if hasattr(img_ai, "size") else (0, 0)
                print("[face_detect] det=%d size=%s" % (_n_det, str(_sz)))
            gc.collect()  # det 后立即回收 NPU 原生缓冲(坑#16:运动多人时防帧内峰值累积)
            # 检测坐标(chn2 XGA 1024x768)→ VGA 640x480 缩放(×640/1024,×480/768)
            disp_w, disp_h = _face_det.display_size
            rgb_w, rgb_h = _face_det.rgb888p_size
            # 识别判定仅在检测帧(有新鲜 det 结果与 img_np):按人数降频,K2 注册强制。
            # ⚠️ 不可放检测块外:do_reg 若落在非检测帧,img_np 未定义 → NameError
            n_faces = len(det_boxes) if det_boxes else 0
            _last_had_face = n_faces > 0  # 更新自适应间隔状态(无脸→下次低频)
            k2_pending = _id_registry is not None and _id_registry.has_pending()
            reg_interval = REG_INTERVAL_1
            if n_faces >= 4:
                reg_interval = REG_INTERVAL_3
            elif n_faces >= 2:
                reg_interval = REG_INTERVAL_2
            do_reg = (_reg_counter == 0 or k2_pending) and \
                (_thermal_mode == 0 or k2_pending)  # 热模式:reg 仅 K2 强制才跑(降 NPU 负载)
            _reg_counter = (_reg_counter + 1) % reg_interval
            if det_boxes and landms and _face_reg is not None and do_reg:
                # 识别帧:重建跟踪目标缓存 + 帧内 ID 去重(一个 ID 只标一个框)
                _last_track = []
                used_mid = set()
                # 识别前 REG_MAX_FACES 张(超出只画框,防极端多人卡死)
                for i in range(min(len(det_boxes), REG_MAX_FACES)):
                    try:
                        _face_reg.config_preprocess(landms[i])
                        feature = _face_reg.run(img_np)
                        gc.collect()  # 每张脸推理后立即回收(坑#16:多人/运动时防帧内原生缓冲峰值叠加致 kpu.run 永久阻塞)
                        mid, score = database_search(feature, _db_features)
                        if mid is None or mid in used_mid:
                            continue  # 无匹配或帧内已用 ID:跳过(防两张脸同标一个 ID)
                        used_mid.add(mid)
                        recognition_results.append((i, mid))
                        det = det_boxes[i]
                        x, y, w, h = det[:4]
                        x = int(x * disp_w // rgb_w)
                        y = int(y * disp_h // rgb_h)
                        w = int(w * disp_w // rgb_w)
                        h = int(h * disp_h // rgb_h)
                        conf = int(score * 100)  # 置信度=识别匹配度(0-100),非检测框分数
                        if 1 <= mid <= 25:
                            slots.append((mid, x, y, w, h, conf | LEARNED_FLAG))  # 已学习:bit7=1
                        cx, cy = _box_center(det)
                        _last_track.append((cx, cy, mid))
                    except Exception as e:
                        print("[face_detect] recog error: %s" % e)
                # K2 注册：注册当前帧最大脸（注册语义不变）
                if k2_pending:
                    max_i = max(range(len(det_boxes)),
                                key=lambda j: det_boxes[j][2] * det_boxes[j][3])
                    max_det = det_boxes[max_i]
                    w_vga = int(max_det[2] * disp_w // rgb_w)
                    h_vga = int(max_det[3] * disp_h // rgb_h)
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
                                cx, cy = _box_center(max_det)
                                _last_track.append((cx, cy, slot))
                                if 1 <= slot <= 25:
                                    slots.append((slot, 0, 0, 0, 0, LEARNED_FLAG))  # 注册反馈:0 坐标+已学习
                        except Exception as e:
                            print("[face_detect] register error: %s" % e)
                # 未注册人脸(未进入 recognition_results 的 det)上报 id=0 + learned=0:
                # 主机可区分已学习目标(id 1~25)与未学习目标(id=0)
                matched_idx = set(i for i, _mid in recognition_results)
                for i in range(min(len(det_boxes), REG_MAX_FACES)):
                    if i in matched_idx:
                        continue
                    det = det_boxes[i]
                    slots.append((0,
                                  int(det[0] * disp_w // rgb_w),
                                  int(det[1] * disp_h // rgb_h),
                                  int(det[2] * disp_w // rgb_w),
                                  int(det[3] * disp_h // rgb_h), 100))
                # 识别/注册全部完成:立即释放 chn2 帧引用,
                # 缩短其与显示 DMA(OSD1 show_image + OSD2 LVGL FULL flush)的共存期
                del img_np
                del img_ai
    else:
        do_reg = False
    # 非识别帧:按中心最近邻把缓存 ID 关联到新框(防旧结果贴错新框窜脸;
    # 新脸无近邻不画 ID, 位置微动 ID 稳定)
    if not do_reg:
        recognition_results = _associate_to_tracked(det_boxes, _last_track, TRACK_RADIUS)
    # 非识别帧:复用缓存槽位,保持主机数据连续(坐标滞后≤1轮识别,可接受)
    if not do_reg and _last_slots is not None:
        slots = _last_slots
    else:
        _last_slots = slots

    # 屏幕居中绿色十字(对准参考,小一点):VGA 640x480 中心 (320, 240)
    img.draw_cross(320, 240, color=(0xFF, 0x00, 0xFF, 0x00), size=20, thickness=2)

    _face_det.draw_result(img, det_boxes, recognition_results)
    if _RUNTIME is not None and _RUNTIME.host is not None:
        _RUNTIME.host_tick(slots)


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
    """清除:清空内存特征 + 置写盘标志,主循环安全窗口立即写空库(防断电旧数据回魂)。"""
    global _close_overlay, _pending_clear_flush
    if e.get_code() != lv.EVENT.CLICKED:
        return
    face_db.clear()
    _db_features.clear()
    _pending_clear_flush = True  # 清除即写盘:主循环 task_handler 前执行(坑#2 安全窗口)
    if _RUNTIME is not None and _RUNTIME.buzzer is not None:
        _RUNTIME.buzzer.beep(ms=200)
    _close_overlay = True


def _on_save_clicked(e):
    """保存:持久化已由注册即写/退出兜底覆盖,此处仅关闭浮层。"""
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
    _perf_t = time.ticks_us()  # 每帧分段耗时插桩(诊断用,2026-08-10)
    try:
        while not exit_flag[0]:
            os.exitpoint()
            _dbg = fc < 8
            if _dbg:
                print("[dbg] f%d pre-snap0" % fc)
            img = runtime.sensor.snapshot(chn=CAM_CHN_ID_0)
            if _dbg:
                print("[dbg] f%d post-snap0 %s" % (fc, img))
            _p1 = time.ticks_us()
            try:
                on_frame(img)
            except Exception as e:
                print("[face_detect] on_frame error: %s" % e)
                try:
                    sys.print_exception(e)
                except Exception:
                    pass
            _p2 = time.ticks_us()
            if _dbg:
                print("[dbg] f%d post-onframe" % fc)
            if _id_registry is not None:
                _id_registry.poll_k2()
            _process_overlay_close()
            if _pending_clear_flush:
                _pending_clear_flush = False
                face_db.flush_to_disk()  # 清除即写空库(task_handler 前安全窗口,防断电/异常退出后旧数据回魂)
            if _dbg:
                print("[dbg] f%d post-pollk2" % fc)
            Display.show_image(img, 0, 0, Display.LAYER_OSD1)
            _p3 = time.ticks_us()
            if _dbg:
                print("[dbg] f%d post-show" % fc)
            gc.collect()  # 放在 show_image 之后、task_handler 之前，避免 AI 推理后立即 GC 阻塞 DMA
            _p4 = time.ticks_us()
            if _dbg:
                print("[dbg] f%d post-gc" % fc)
            _lv_wait = lv.task_handler()
            _p4b = time.ticks_us()
            if _dbg:
                print("[dbg] f%d post-lvtask" % fc)
            time.sleep_ms(_lv_wait)
            _p5 = time.ticks_us()
            fc += 1
            if fc % 30 == 0:
                print("[perf] snap=%d on=%d show=%d gc=%d lvtask=%d sleep=%d total=%d" % (
                    _p1 - _perf_t, _p2 - _p1, _p3 - _p2,
                    _p4 - _p3, _p4b - _p4, _p5 - _p4b, _p5 - _perf_t))
                print("[face_detect] fc=%d" % fc)
                if fc % 300 == 0:
                    print(diag_line("[face_detect]", fc))
            _perf_t = time.ticks_us()
    finally:
        _deinit_ai()
        _destroy_ui()
        _RUNTIME = None
        face_db.flush_to_disk()  # 退出兜底写盘（注册即写已在 on_frame 完成；默认 FACE_DB_PATH）
