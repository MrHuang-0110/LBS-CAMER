# scripts/image_classify/app.py — 图像分类(预览脚手架)。
#
# 复用 _template 单线程主循环 + road_detect 的 _DETECTION_ENABLED 预览模式。
# chn0 VGA RGB888 显示。本轮不跑 AI、不持久化:只顶栏(返回钮+i18n标题)
# + 透明预览 + 空底栏,每帧 host_tick(None) 推协议 0x13 心跳(40B 全零)。
#
# 后续完善时:改 _DETECTION_ENABLED=True,在 app_runtime._channels_for 的
# image_classify 分支附加 chn2 AI 通道,在 on_frame 检测分支填 slots。

import os
import sys
import time
import lvgl as lv
from media.display import Display
from media.sensor import CAM_CHN_ID_0
from core.icon_cache import icon_cache
from core.font_manager import fonts
from core.status_hud import status_text
from core.diagnostics import read_temperature

BAR_H = 52
PREVIEW_Y = BAR_H
PREVIEW_H = 376
BAR_BG = 0x1A1A1A

# 图像分类暂为单摄像源预览(不跑AI、空底栏)。
# 后续完善时改 True:恢复 chn2 AI 通道(_channels_for)+ on_frame 检测分支。
_DETECTION_ENABLED = False

_RUNTIME = None
_status_label = None  # 顶栏状态小字(帧率/温度/目标数,2026-08-13)
_screen = None
_top_bar = None
_bottom_bar = None
_preview = None


def _build_ui(runtime, exit_flag):
    """顶栏(返回钮 + i18n 标题) + 透明预览 + 空底栏。

    返回钮 CLICKED 回调设 exit_flag[0]=True(只设标志,不做重操作)。
    返回钮用通用 get_back_icon()(init_app 已无条件预读),无需 image_classify 专属图标。
    """
    global _screen, _top_bar, _bottom_bar, _preview, _status_label
    screen = lv.scr_act()
    # 屏幕透明:让 OSD1 摄像头画面透出;顶底栏自带不透明背景
    screen.set_style_bg_opa(0, 0)
    screen.add_flag(lv.obj.FLAG.CLICKABLE)
    _screen = screen

    # ── 顶栏:返回钮(左) + 标题(中) ──
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

    icon_data, icon_dsc = icon_cache.get_back_icon()
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
    title.set_text(runtime.lang.t("category.image_classify"))
    title.align(lv.ALIGN.CENTER, 0, 0)
    from ui.theme import make_back_bar_text_style
    title.add_style(make_back_bar_text_style(fonts.body), 0)

    # 顶栏右侧状态小字(2026-08-13):帧率/温度/目标数,通用单位行
    _status_label = lv.label(_top_bar)
    _status_label.set_text("--fps --C -")
    _status_label.align(lv.ALIGN.RIGHT_MID, -8, 0)
    _status_label.add_style(make_back_bar_text_style(fonts.body), 0)

    # ── 预览区:透明,透出 OSD1 摄像头画面 ──
    _preview = lv.obj(screen)
    _preview.set_size(lv.pct(100), PREVIEW_H)
    _preview.set_pos(0, PREVIEW_Y)
    _preview.set_style_bg_opa(0, 0)
    _preview.set_style_border_width(0, 0)
    _preview.set_style_pad_all(0, 0)
    _preview.set_style_radius(0, 0)
    _preview.clear_flag(lv.obj.FLAG.SCROLLABLE)
    _preview.clear_flag(lv.obj.FLAG.CLICKABLE)

    # ── 底栏:纯空栏(占位,后续 AI 加按钮时填) ──
    _bottom_bar = lv.obj(screen)
    _bottom_bar.set_size(lv.pct(100), BAR_H)
    _bottom_bar.set_pos(0, PREVIEW_Y + PREVIEW_H)
    _bottom_bar.set_style_bg_color(lv.color_hex(BAR_BG), 0)
    _bottom_bar.set_style_bg_opa(255, 0)
    _bottom_bar.set_style_border_width(0, 0)
    _bottom_bar.set_style_pad_all(0, 0)
    _bottom_bar.set_style_radius(0, 0)
    _bottom_bar.clear_flag(lv.obj.FLAG.SCROLLABLE)


def on_frame(img):
    """单摄像源预览(暂不跑AI)。后续完善时改 _DETECTION_ENABLED=True 启用检测分支。"""
    if _RUNTIME is None:
        return
    if not _DETECTION_ENABLED:
        # 预览模式:仅显示 chn0(主循环 show_image),不检测。协议心跳用空 slots。
        if _RUNTIME.host is not None:
            _RUNTIME.host_tick(None)
        return
    # --- 以下为检测分支(_DETECTION_ENABLED=True 时启用)---
    # 后续:img_det = _RUNTIME.sensor.snapshot(chn=CAM_CHN_ID_2) 跑 AI,
    # 填 slots = [(id,x,y,w,h,conf), ...],再 _RUNTIME.host_tick(slots)。
    if _RUNTIME.host is not None:
        _RUNTIME.host_tick(None)


def _destroy_ui():
    """删顶栏/底栏/预览区 LVGL 对象 + 恢复屏幕不透明。"""
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
    """主入口(reset 框架调 mod.run(runtime))。

    单线程主循环:snapshot → on_frame(try/except) → show_image(OSD1) → task_handler。
    触摸返回钮设 exit_flag → 循环退出 → _destroy_ui → main.py cleanup+reset 回菜单。
    """
    global _RUNTIME, _hud_t0
    _RUNTIME = runtime
    _hud_t0 = time.ticks_ms()  # 状态栏 fps 窗口起点(2026-08-13)
    exit_flag = [False]
    _build_ui(runtime, exit_flag)
    fc = 0
    try:
        while not exit_flag[0]:
            os.exitpoint()
            img = runtime.sensor.snapshot(chn=CAM_CHN_ID_0)
            try:
                on_frame(img)
            except Exception as e:
                print("[image_classify] on_frame error: %s" % e)
                try:
                    sys.print_exception(e)
                except Exception:
                    pass
            Display.show_image(img, 0, 0, Display.LAYER_OSD1)
            time.sleep_ms(lv.task_handler())
            fc += 1
            # 顶栏状态小字(~1s 一次):帧率/温度/目标数(2026-08-13)
            if _status_label is not None and fc % 30 == 0:
                _hud_el = time.ticks_diff(time.ticks_ms(), _hud_t0)
                _hud_fps = 30 * 1000 // _hud_el if _hud_el > 0 else 0
                _hud_t0 = time.ticks_ms()
                _status_label.set_text(status_text(_RUNTIME.lang, _hud_fps,
                                                   read_temperature(), 0))
            if fc % 30 == 0:
                print("[image_classify] fc=%d" % fc)
    finally:
        _destroy_ui()
        _RUNTIME = None
