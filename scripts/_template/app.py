# scripts/_template/app.py — 基础框架模板（顶栏+空底栏+摄像头+触摸返回）
#
# 作为后续所有 AI 脚本的复制起点。核心结构：
#   run(runtime) 单线程主循环内置 on_frame(img) 钩子（默认空实现）。
#   AI 脚本复制本模板后只填 on_frame，不碰骨架；on_frame 异常 try/except
#   隔离不杀循环 → 基础框架不被 AI 影响。
#
# 单线程主循环（snapshot→on_frame→show_image→task_handler 串行）从结构上
# 消除 face_detect 的双线程双写者 display DMA 竞争。
#
# 设计文档：docs/superpowers/specs/2026-06-23-script-template-design.md

import os
import time
import lvgl as lv
from media.display import Display
from media.sensor import CAM_CHN_ID_0
from core.icon_cache import icon_cache
from core.font_manager import fonts

# ── 布局常量（对齐 camera/app.py 尺寸）──────────────────────
BAR_H = 52              # 顶/底栏高度
PREVIEW_Y = BAR_H       # 预览区起始 Y
PREVIEW_H = 376         # 480 - BAR_H * 2
BAR_BG = 0x1A1A1A       # 栏背景色
TITLE_TEXT = "基础框架"  # 硬编码标题（不依赖 manifest/lang，隔离变量）

# ── 模块级 UI 对象引用（_build_ui 建造，_destroy_ui 清理）──
_screen = None
_top_bar = None
_bottom_bar = None
_preview = None


def on_frame(img):
    """AI 钩子 — 模板空实现。

    后续 AI 脚本复制本模板后填此函数：拿 img 做检测/识别，把结果画到 img 上。
    异常由 run() 主循环 try/except 接住，不杀基础框架循环。
    """
    pass


def _build_ui(runtime, exit_flag):
    """构建顶栏(返回钮+标题) + 空底栏 + 透明预览区。

    返回钮 CLICKED 回调设 exit_flag[0]=True（只设标志，不做重操作）。
    """
    global _screen, _top_bar, _bottom_bar, _preview
    screen = lv.scr_act()
    # 屏幕透明：让 OSD1 摄像头画面透出；顶底栏自带不透明背景
    screen.set_style_bg_opa(0, 0)
    _screen = screen

    # ── 顶栏：返回钮(左) + 标题(中) ──
    _top_bar = lv.obj(screen)
    _top_bar.set_size(lv.pct(100), BAR_H)
    _top_bar.set_pos(0, 0)
    _top_bar.set_style_bg_color(lv.color_hex(BAR_BG), 0)
    _top_bar.set_style_bg_opa(255, 0)
    _top_bar.set_style_border_width(0, 0)
    _top_bar.set_style_pad_all(0, 0)
    _top_bar.set_style_radius(0, 0)
    _top_bar.clear_flag(lv.obj.FLAG.SCROLLABLE)

    # 返回钮（48×48 透明点击区 + back 图标）
    btn = lv.obj(_top_bar)
    btn.set_size(48, 48)
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
        target = int(48 * 0.85)
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
            exit_flag[0] = True
    btn.add_event(_on_back, lv.EVENT.CLICKED, None)

    # 标题居中
    title = lv.label(_top_bar)
    title.set_text(TITLE_TEXT)
    title.align(lv.ALIGN.CENTER, 0, 0)
    from ui.theme import make_back_bar_text_style
    title.add_style(make_back_bar_text_style(fonts.body), 0)

    # ── 预览区：透明，透出 OSD1 摄像头画面 ──
    _preview = lv.obj(screen)
    _preview.set_size(lv.pct(100), PREVIEW_H)
    _preview.set_pos(0, PREVIEW_Y)
    _preview.set_style_bg_opa(0, 0)
    _preview.set_style_border_width(0, 0)
    _preview.set_style_pad_all(0, 0)
    _preview.set_style_radius(0, 0)
    _preview.clear_flag(lv.obj.FLAG.SCROLLABLE)
    _preview.clear_flag(lv.obj.FLAG.CLICKABLE)

    # ── 底栏：纯空栏（无按钮，只验证渲染）──
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
    """删顶栏/底栏/预览区 LVGL 对象 + 恢复屏幕不透明。
    不碰 runtime 持有的硬件（由 main.py runtime.cleanup() 统一 deinit）。
    """
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
    # 恢复屏幕不透明背景（主菜单需要）
    try:
        from ui.theme import Colors
        scr = lv.scr_act()
        scr.set_style_bg_color(lv.color_hex(Colors.BG), 0)
        scr.set_style_bg_opa(255, 0)
    except Exception:
        pass
    _screen = None


def run(runtime):
    """模板主入口（reset 框架调 mod.run(runtime)）。

    单线程主循环：snapshot → on_frame(try/except) → show_image(OSD1) → task_handler。
    触摸返回钮设 exit_flag → 循环退出 → _destroy_ui → main.py cleanup+reset 回菜单。
    """
    exit_flag = [False]
    _build_ui(runtime, exit_flag)
    fc = 0
    while not exit_flag[0]:
        os.exitpoint()
        img = runtime.sensor.snapshot(chn=CAM_CHN_ID_0)
        try:
            on_frame(img)
        except Exception as e:
            print("[template] on_frame error: %s" % e)
        Display.show_image(img, 0, 0, Display.LAYER_OSD1)
        time.sleep_ms(lv.task_handler())
        fc += 1
        if fc % 30 == 0:
            print("[template] fc=%d" % fc)
    _destroy_ui()
