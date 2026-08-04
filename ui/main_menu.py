# ui/main_menu.py — 主菜单：深灰卡片纵向滚动选择器
#
# 全屏 640×480，无顶栏，纯黑背景。
# 上下滑动浏览类目，松手后自动吸附居中卡片并放大高亮。
#
# 视觉规范（项目计划 §3.2）：
#   屏幕背景：纯黑 0x000000
#   卡片容器：612×144px, 圆角 14px, 深灰 0x222222
#   卡片内边距：左右 6px, 上下 0
#   无边框，无内阴影，圆角裁剪生效
#   左图标：115×115，水平居中于卡片宽度 1/4 处（距左缘约 96px）
#   右文字：纯白 0xFFFFFF，距图标 16px，垂直居中
#   选中态：scale 1.0, opacity 100%
#   非选中：scale 0.80, opacity 60%
#   吸附：ease_out 250ms
#
# 数据来源：config/categories.json → 动态生成卡片

import lvgl as lv
import time
import gc
from ui.theme import Colors, make_bg_style
from core.font_manager import fonts


# ── 卡片几何参数 ──────────────────────────────────────
# 屏幕 640×480。所有卡片统一高度、固定纵向位置（y 不随选中变化），
# 选中态只改变宽度与水平位置 → 纵向滚动时无需逐帧重排，更流畅。
#   吸附(选中)卡：满宽 612，水平居中
#   未吸附卡    ：宽度减小(CARD_W_NORMAL)，右边缘对齐选中卡右边缘（靠右）
CARD_W = 612                     # 吸附卡宽度
CARD_W_NORMAL = 540              # 未吸附卡宽度（减小靠右）
CARD_H = 144                     # 卡片统一高度
CARD_RADIUS = 14
CARD_GAP = 8                     # 上下间距（调小）

VERT_MARGIN = 10    # 竖向边距（上下）
HORZ_MARGIN = 14    # 横向边距（左右）

# 图标参数
ICON_SIZE = 100                  # 图标容器（100×100）
ICON_LEFT_PAD = 97               # align offset: CARD_W/4 - ICON_SIZE/2 - pad_left = 153 - 50 - 6 = 97
ICON_TEXT_GAP = 16               # 图文间距
ICON_ZOOM_MAX = 256              # 图标最大缩放（256=100%，不放大于原始尺寸）
ICON_ZOOM_MIN = 48               # 图标最小缩放

OPA_NORMAL = 153    # 60% of 255
OPA_SELECTED = 255   # 100%

SCROLL_SNAP_TIME = 250  # 吸附动画时长 (ms)
SCROLL_SNAP_DELAY = 150  # 触摸释放后延迟开始吸附 (ms)
GEOM_ANIM_TIME = 280     # 卡片宽窄/位置过渡动画时长(ms),ease_out 顺滑

# Board diagnostic: prints once per second while menu is alive. Keep False for normal builds.
MENU_DIAG_MEM = False
MENU_DIAG_FORCE_GC_AT_SEQ = 0  # 0=disabled; positive value runs gc at that printed seq.

# Avoid Python lv.anim_t custom callbacks on K230 scroll path; they allocate and can pin closures.
USE_PY_SCROLL_ANIM = False

ZOOM_SELECTED = 256
ZOOM_NORMAL = 205  # 80% visual scale, replacing width shrink animation.
CARD_SHIFT_X = CARD_W - CARD_W_NORMAL


class MainMenu:
    """纵向卡片滑动主菜单"""

    def __init__(self, config, buzzer, lang, on_card_click=None):
        """
        Args:
            config: ConfigManager 实例
            buzzer: Buzzer 实例
            lang: LangManager 实例（用于翻译卡片文字）
            on_card_click: func(script_id) — 点击启动回调
        """
        self.config = config
        self.buzzer = buzzer
        self.lang = lang
        self.on_card_click = on_card_click

        self._screen = None         # 全屏容器
        self._scroll = None         # 纵向滚动区
        self._spacer = None         # 底部撑开 spacer
        self._cards = []            # CardSlot 列表
        self._selected_index = -1
        self._icon_cache = {}       # {cat_id: bytes} 预读图标,常驻内存
        self._bg_style = None       # lv.style_t 必须保活，GC 后 C 侧仍引用
        self._scroll_cb = None      # LVGL 事件回调必须保活，防 GC 后 C 侧悬空
        self._scroll_end_cb = None
        self._scroll_press_cb = None
        self._snap_pending_at = 0   # 延迟吸附到期时刻(ticks_ms),0=无待执行吸附
        self._diag_last_ms = 0
        self._diag_seq = 0

    # ── 公开 API ──────────────────────────────────────

    def preload_icons(self):
        """在显示刷新循环启动前,把全部图标读入内存。

        ⚠️ K230 平台约束:一旦 lv.task_handler() 刷屏循环开始运行
        (splash 阶段及之后),SD/FATFS 文件读取会与显示 flush
        (display.show_image → MediaManager/DMA)抢占共享资源,
        导致 open()/read() 永久死锁(现象:黑屏 + 串口无输出)。

        因此所有图标文件 I/O 必须在 splash 之前(首次 task_handler 之前)
        这个已验证安全的窗口内完成,字节常驻 self._icon_cache,
        主菜单构建期间不再触碰文件系统。

        必须在 main() 中、首次 lv.task_handler() 之前调用。
        """
        cats = self.config.get_enabled_categories()
        print(f"[MainMenu] preload_icons: {len(cats)} categories")
        for cat in cats:
            cat_id = cat.get('id', '')
            path = cat.get('icon', '')
            if not path:
                continue
            try:
                with open(path, 'rb') as f:
                    # bytearray(非 bytes):img_dsc_t.data 须指向不会被 GC 移动的
                    # 连续缓冲(K230 LVGL 绑定注意事项)。bytes 也常驻但 bytearray 更稳妥。
                    self._icon_cache[cat_id] = bytearray(f.read())
                print(f"  [preload] {cat_id} OK ({len(self._icon_cache[cat_id])} bytes)")
            except Exception as e:
                print(f"  [preload] {cat_id} FAILED: {e}")
        print(f"[MainMenu] preload_icons done, {len(self._icon_cache)} cached")

    def show(self):
        """构建并显示主菜单 UI"""
        print("[MainMenu] show() called")
        if self._screen is not None:
            self._screen.clear_flag(lv.obj.FLAG.HIDDEN)
            self._screen.set_style_opa(255, 0)  # 恢复 hide() 中设的 opa=0
            print("[MainMenu] revealed hidden screen (cleared HIDDEN, restored opa=255)")
            return

        print("[MainMenu] getting active screen...")
        screen = lv.scr_act()
        scr_w = screen.get_width()
        scr_h = screen.get_height()
        print(f"[MainMenu] screen={scr_w}x{scr_h}")

        # ── 全屏容器 ──
        print("[MainMenu] creating container...")
        self._screen = lv.obj(screen)
        self._screen.set_size(scr_w, scr_h)
        self._screen.center()
        self._bg_style = make_bg_style()
        self._screen.add_style(self._bg_style, 0)
        self._screen.clear_flag(lv.obj.FLAG.SCROLLABLE)
        # 确保主菜单在最顶层（覆盖残留的 splash 等）
        self._screen.move_foreground()
        print("[MainMenu] container created")

        # ── 纵向滚动区 ──
        print("[MainMenu] creating scroll area...")
        self._scroll = lv.obj(self._screen)
        self._scroll.set_size(scr_w, scr_h)
        self._scroll.center()
        self._scroll.set_style_bg_opa(0, 0)
        self._scroll.set_style_border_width(0, 0)
        self._scroll.set_style_pad_all(0, 0)
        self._scroll.set_style_radius(0, 0)
        self._scroll.add_flag(lv.obj.FLAG.SCROLLABLE)
        self._scroll.set_scroll_dir(lv.DIR.VER)
        self._scroll.set_scrollbar_mode(lv.SCROLLBAR_MODE.OFF)
        print("[MainMenu] scroll area created")

        # ── 逐张创建卡片（图标已预读入 self._icon_cache，构建期零文件 I/O）──
        categories = self.config.get_enabled_categories()
        print(f"[MainMenu] categories count: {len(categories)}")
        if not categories:
            self._show_empty()
            print("[MainMenu] show_empty done")
            return

        # ⚠️ 这里不要调用 gc.collect()/gc.mem_free()。menu.show() 由 splash 完成
        # 回调触发,运行在 splash.show() 调用栈内;此处 gc 触发 lv 终结器/获取堆锁
        # 会与显示路径交互出问题(历史上在此卡死)。内存交给主循环自然回收即可。
        print("[MainMenu] building cards")

        # 首尾卡也要能吸附居中：内容区上下各留半屏空白。
        # 居中偏移 = (视口高 - 卡高) / 2，首卡 y 放在该偏移处时
        # scroll_y=0 即让首卡正好居中；尾卡下方再补等高 spacer。
        center_offset = (scr_h - CARD_H) // 2
        pad_top = center_offset

        # 卡片水平居中：屏幕中心
        card_center_x = scr_w // 2

        # 设置滚动内容区大小
        self._scroll.set_size(scr_w, scr_h)
        # 每个卡片绝对定位在滚动区内

        for i, cat in enumerate(categories):
            cat_id = cat.get('id', str(i))
            print(f"[MainMenu] card #{i}: id={cat_id}")

            # ── 步骤 A：取本卡片图标（已在 splash 之前预读到内存）──
            # 此处绝不做文件 I/O：菜单阶段 task_handler 刷屏循环已活跃,
            # open() 会与显示 flush 抢资源死锁(见 preload_icons 说明)。
            icon_data = self._icon_cache.get(cat_id)
            if icon_data is None and cat.get('icon'):
                print(f"  [icon #{i}] not in cache (preload missing?) — placeholder")

            # ── 步骤 B：创建卡片 LVGL 对象 ──
            try:
                card = _CardSlot(
                    parent=self._scroll,
                    index=i,
                    category=cat,
                    y=pad_top + i * (CARD_H + CARD_GAP),
                    center_x=card_center_x,
                    buzzer=self.buzzer,
                    lang=self.lang,
                    icon_data=icon_data,
                    on_click=lambda idx=i: self._on_card_clicked(idx),
                )
                self._cards.append(card)
                print(f"  [Card #{i}] created OK")
            except Exception as e:
                print(f"  [Card #{i}] FAILED: {e}")

            # ── 步骤 C：仅释放循环局部引用 ──
            # ⚠️ 不在循环里调用 task_handler()/gc.collect():menu.show() 运行在
            # splash.show() 调用栈内,在此刷屏或回收都可能扰动显示/DMA 路径而卡死。
            # 全部卡片建完后由 main 主循环统一渲染。
            # 字节已由 self._icon_cache 与卡片 self._icon_data 保活。
            icon_data = None

        print(f"[MainMenu] all {len(self._cards)} cards created")

        # 底部 spacer：撑开内容区，使最后一张卡也能滚动到居中位置。
        # 高度 = 居中偏移，无背景、不可点。
        if self._cards:
            last = self._cards[-1]
            spacer = lv.obj(self._scroll)
            spacer.set_size(1, center_offset)
            spacer.set_pos(0, last.y + CARD_H + CARD_GAP)
            spacer.set_style_bg_opa(0, 0)
            spacer.set_style_border_width(0, 0)
            spacer.clear_flag(lv.obj.FLAG.SCROLLABLE)
            spacer.clear_flag(lv.obj.FLAG.CLICKABLE)
            self._spacer = spacer

        # ── 滚动事件：吸附 + 选中联动 ──
        print("[MainMenu] setting up scroll events...")
        self._scroll_cb = self._on_scroll
        self._scroll_end_cb = self._on_scroll_end
        self._scroll_press_cb = self._on_pressed
        self._scroll.add_event(self._scroll_cb, lv.EVENT.SCROLL, None)
        self._scroll.add_event(self._scroll_end_cb, lv.EVENT.SCROLL_END, None)
        self._scroll.add_event(self._scroll_press_cb, lv.EVENT.PRESSED, None)

        # 初始选中第一张并吸附居中（_scroll_to 内部会调用 _update_selection）
        print("[MainMenu] initial scroll_to(0)...")
        self._scroll_to(0, animate=False)
        print("[MainMenu] show() complete")

    def hide(self):
        """隐藏主菜单（进入脚本前调用）"""
        print("[MainMenu] hide() called")
        if self._screen is not None:
            print("[MainMenu]   screen exists, adding HIDDEN + opa=0")
            self._screen.add_flag(lv.obj.FLAG.HIDDEN)
            # K230 防御：万一 FLAG.HIDDEN 未阻止渲染，style_opa=0 确保不可见
            self._screen.set_style_opa(0, 0)
        else:
            print("[MainMenu]   WARN: _screen is None!")

    def refresh_texts(self):
        """语言切换后刷新所有卡片文字"""
        for card in self._cards:
            card.refresh_text()

    # ── 滚动回调 ──────────────────────────────────────

    def _on_scroll(self, event):
        """滚动中：按卡片距视口中心的距离更新视觉，不创建 Python 动画。

        滚动中清除待执行吸附：_cancel_scroll_anim 终止动画会再触发
        SCROLL_END 重设 pending,此处清掉避免拖动中被 tick 抢走吸附。
        """
        self._snap_pending_at = 0
        self._apply_scroll_visuals(update_selection=True)
        self._diag_mem_tick("scroll")

    def _on_scroll_end(self, event):
        """滚动结束 → 延迟吸附；视觉仍由距离驱动。

        连续滑动时若松手立即吸附,动画会与下一次手指拖动竞争
        (SCROLL 事件高频触发拖慢 task_handler → 不跟手)。改为延迟
        SCROLL_SNAP_DELAY 后由主循环 tick() 执行吸附,期间手指再次
        按下/滚动会取消 pending,手势完全接管。
        """
        self._schedule_snap()
        self._apply_scroll_visuals(update_selection=True)
        self._diag_mem_tick("scroll_end")

    def _schedule_snap(self):
        """调度延迟吸附:记录到期时刻,由主循环 menu.tick() 驱动执行。"""
        self._snap_pending_at = time.ticks_ms() + SCROLL_SNAP_DELAY

    def tick(self):
        """主循环驱动:延迟吸附到期检查。须在 lv.task_handler() 后调用。"""
        if self._snap_pending_at == 0 or not self._cards:
            return
        if time.ticks_diff(time.ticks_ms(), self._snap_pending_at) >= 0:
            self._snap_pending_at = 0
            self._snap_to_nearest()

    def _on_pressed(self, event):
        """手指按下:取消待执行吸附 + 停止进行中的吸附动画,手势完全接管。"""
        self._snap_pending_at = 0
        self._cancel_scroll_anim()

    def _cancel_scroll_anim(self):
        """停止 LVGL 正在运行的 scroll 动画(ANIM.OFF 落位到当前值即终止)。"""
        try:
            self._scroll.scroll_to_y(self._scroll.get_scroll_y(), lv.ANIM.OFF)
        except Exception:
            pass

    # ── 吸附逻辑 ──────────────────────────────────────

    def _apply_scroll_visuals(self, update_selection=False):
        """DurUI 风格滚动视觉：只更新已有对象，避免滚动时分配 Python 回调。"""
        if not self._cards:
            return
        center_y = self._scroll.get_scroll_y() + self._scroll.get_height() // 2
        nearest_idx = self._find_nearest(center_y)
        step = CARD_H + CARD_GAP
        # 优化:只处理最近 3 张卡片(选中 + 前后各 1),其余保持非选中态,
        # 减少每帧遍历全部卡片的 LVGL style 设定开销(K230 单核性能有限)。
        for i, card in enumerate(self._cards):
            if abs(i - nearest_idx) <= 1:
                card_cy = card.y + CARD_H // 2
                dist = abs(card_cy - center_y)
                t = dist / float(step)
                if t > 1.0:
                    t = 1.0
                card.apply_scroll_visual(t)
            else:
                card.apply_scroll_visual(1.0)
        if update_selection:
            self._selected_index = nearest_idx

    def _diag_mem_tick(self, reason):
        """板端诊断：每秒打印 mem；不在 LVGL 事件回调内主动 GC。"""
        if not MENU_DIAG_MEM:
            return
        try:
            now = time.ticks_ms()
            if self._diag_last_ms == 0:
                self._diag_last_ms = now
                return
            if time.ticks_diff(now, self._diag_last_ms) < 1000:
                return
            self._diag_seq += 1
            mem = gc.mem_free()
            print("[MainMenu-diag] seq=%d reason=%s selected=%d mem=%d" % (
                self._diag_seq, reason, self._selected_index, mem))
            self._diag_last_ms = now
        except Exception as e:
            print("[MainMenu-diag] failed: %s" % e)

    def diag_after_task_handler(self):
        """主循环安全点诊断：lv.task_handler() 返回后才允许主动 GC。"""
        if not MENU_DIAG_MEM:
            return
        if MENU_DIAG_FORCE_GC_AT_SEQ <= 0:
            return
        if self._diag_seq != MENU_DIAG_FORCE_GC_AT_SEQ:
            return
        # 只触发一次，避免每轮主循环重复 GC。
        self._diag_seq += 1
        print("[MainMenu-diag] proactive gc begin")
        gc.collect()
        print("[MainMenu-diag] proactive gc end mem=%d" % gc.mem_free())

    def _update_snap(self):
        """滚动中:实时跟踪最近卡片,带迟滞防边界抖动,选中态变化用动画过渡。

        慢速滚动停在两张卡交界附近时,微小抖动会让 nearest_idx 在 N/N+1 间
        反复跳 → 同一张卡弹出缩回重复 + 蜂鸣嘀嘀嘀。加迟滞:已选中某卡时,
        center_y 必须越过该卡中心超过半个卡片步进(+迟滞量)才切换到相邻卡。
        快速滑动 center_y 变化大,正常越过阈值切换;慢速抖动在阈值内不切。
        滚动中不蜂鸣(beep=False),蜂鸣只在松手吸附时响一次。
        """
        if not self._cards:
            return
        center_y = self._scroll.get_scroll_y() + self._scroll.get_height() // 2
        nearest_idx = self._find_nearest(center_y)
        if nearest_idx == self._selected_index:
            return
        # 迟滞:当前选中卡中心 + 半步进阈值内不切换
        cur = self._cards[self._selected_index]
        cur_cy = cur.y + CARD_H // 2
        step = CARD_H + CARD_GAP
        hyst = step // 4  # 迟滞量(1/4 步进)
        if abs(center_y - cur_cy) < step // 2 + hyst:
            return  # 未越过当前卡半步进+迟滞,保持当前选中
        self._update_selection(nearest_idx, animate=True)

    def _snap_to_nearest(self):
        """吸附到最近卡片"""
        if not self._cards:
            return
        center_y = self._scroll.get_scroll_y() + self._scroll.get_height() // 2
        idx = self._find_nearest(center_y)
        self._scroll_to(idx)

    def _find_nearest(self, center_y):
        """找到 y 坐标最近的卡片索引"""
        best_idx = 0
        best_dist = float('inf')
        for i, card in enumerate(self._cards):
            card_cy = card.y + CARD_H // 2
            dist = abs(card_cy - center_y)
            if dist < best_dist:
                best_dist = dist
                best_idx = i
        return best_idx

    def _scroll_to(self, idx, animate=True):
        """滚动使指定卡片居中；不再创建 Python 几何动画。"""
        if idx < 0 or idx >= len(self._cards):
            return
        card = self._cards[idx]
        target_scroll_y = card.y - (self._scroll.get_height() - CARD_H) // 2
        target_scroll_y = max(0, target_scroll_y)
        cur_y = self._scroll.get_scroll_y()
        # 距离过近直接落位:避免 0 位移动画 + 避免连续吸附抖动
        if abs(target_scroll_y - cur_y) <= 2:
            anim = lv.ANIM.OFF
        else:
            anim = lv.ANIM.ON if animate else lv.ANIM.OFF
        self._scroll.scroll_to_y(target_scroll_y, anim)
        self._selected_index = idx
        self._apply_scroll_visuals(update_selection=False)

    def _update_selection(self, idx, animate=True):
        """更新选中索引；视觉由 _apply_scroll_visuals 根据距离统一计算。"""
        if idx < 0 or idx >= len(self._cards):
            return
        self._selected_index = idx
        self._apply_scroll_visuals(update_selection=False)

    # ── 点击 ──────────────────────────────────────────

    def _on_card_clicked(self, idx):
        """点击卡片:仅居中(选中)卡可点击启动,非居中卡点击无反应。

        非居中卡只能通过手指滚动来选中吸附 → 居中后才能点击启动。
        """
        if not self._cards:
            return

        # 非居中卡:完全无反应(不吸附、不启动),只能滚动选中
        if idx != self._selected_index:
            return

        # 点击居中卡 → 启动脚本
        if self.buzzer is not None:
            self.buzzer.beep(ms=50)
        # 压下动画
        card = self._cards[idx]
        card.press_animation()

        cat = card.category
        if self.on_card_click is not None:
            self.on_card_click(cat.get('script', cat.get('id')))

    # ── 空状态 ────────────────────────────────────────

    def _show_empty(self):
        """没有可用类目时的占位"""
        label = lv.label(self._screen)
        label.set_text("No apps available")
        label.center()
        label.set_style_text_color(lv.color_hex(Colors.TEXT), 0)


# ═══════════════════════════════════════════════════════
# 卡片组件
# ═══════════════════════════════════════════════════════

class _CardSlot:
    """单张卡片 — 左图标 + 中间功能名"""

    def __init__(self, parent, index, category, y, center_x, buzzer,
                 lang, icon_data=None, on_click=None):
        print(f"  [Card #{index}] __init__ start")
        self.index = index
        self.category = category
        self.y = y
        self.center_x = center_x
        self.buzzer = buzzer
        self.lang = lang
        self.on_click = on_click
        self._selected = None
        self._last_visual_key = None
        self._click_cb = None

        # ── 卡片容器 ──
        print(f"  [Card #{index}] creating obj...")
        self.obj = lv.obj(parent)
        self.obj.set_size(CARD_W, CARD_H)
        self.obj.set_pos(center_x - CARD_W // 2, y)
        self.obj.set_style_bg_color(lv.color_hex(Colors.CARD), 0)
        self.obj.set_style_bg_opa(255, 0)
        self.obj.set_style_radius(CARD_RADIUS, 0)
        self.obj.set_style_border_width(0, 0)
        self.obj.set_style_pad_hor(6, 0)          # 左右内边距 6px
        self.obj.set_style_pad_ver(0, 0)           # 上下内边距 0
        self.obj.set_style_clip_corner(True, 0)    # 圆角裁剪生效
        self.obj.clear_flag(lv.obj.FLAG.SCROLLABLE)
        self.obj.add_flag(lv.obj.FLAG.CLICKABLE)
        print(f"  [Card #{index}] obj created")

        # ── 图标（左）──
        # icon_data 由 MainMenu.show() 预读取后传入（已解耦文件 I/O），
        # 如果为 None 表示文件不存在或读取失败，显示占位块。
        self.icon_img = None
        if icon_data:
            try:
                print(f"  [Card #{index}] creating img_dsc ({len(icon_data)} bytes)...")
                # ⚠️ icon_dsc 与 icon_data 必须挂到 self 上保活:
                # lv.img.set_src 只保存底层 C 结构指针,LVGL 重绘时仍会
                # 解引用该 dsc 及其指向的字节缓冲。若它们是局部变量,
                # __init__ 返回(及循环末尾 gc.collect())后被回收,
                # 重绘即访问已释放内存 → 硬崩溃。
                self._icon_data = icon_data
                self._icon_dsc = lv.img_dsc_t({
                    'data_size': len(icon_data),
                    'data': icon_data,
                })
                print(f"  [Card #{index}] img_dsc_t created")
                self.icon_img = lv.img(self.obj)
                self.icon_img.set_src(self._icon_dsc)
                print(f"  [Card #{index}] img set_src done")
                # 自动缩放图标以适应容器，保持比例（从 PNG 二进制头解析尺寸）
                self._auto_scale_icon(icon_data)
                self.icon_img.set_size(ICON_SIZE, ICON_SIZE)
                self.icon_img.align(lv.ALIGN.LEFT_MID,
                                    ICON_LEFT_PAD, 0)
                print(f"  [Card #{index}] icon done")
            except Exception as e2:
                print(f"  [Card #{index}] icon create failed: {e2}")
                self._make_placeholder_icon()
        else:
            print(f"  [Card #{index}] no icon data, making placeholder")
            self._make_placeholder_icon()

        # ── 文字（中）──
        self.text_label = lv.label(self.obj)
        name_key = category.get('name_key', category.get('id', ''))
        translated = self.lang.t(name_key) if self.lang else name_key
        # 字体可能加载失败(返回 None)：仅在非 None 时设置,否则用内置字体兜底,
        # 绝不把 None 传给 set_style_text_font(会崩)。
        _font = fonts.title
        if _font is not None:
            self.text_label.set_style_text_font(_font, 0)
        else:
            print(f"  [Card #{index}] WARN: title font is None, using builtin")
        self.text_label.set_style_text_color(lv.color_hex(Colors.TEXT), 0)
        self.text_label.set_text(translated)
        # 确保 label 有足够宽度显示文字
        self.text_label.set_width(CARD_W - ICON_LEFT_PAD - ICON_SIZE - ICON_TEXT_GAP - 20)
        if self.icon_img:
            self.text_label.align_to(self.icon_img,
                                     lv.ALIGN.OUT_RIGHT_MID,
                                     ICON_TEXT_GAP, 0)
        else:
            self.text_label.align(lv.ALIGN.LEFT_MID,
                                  ICON_LEFT_PAD, 0)

        # ── 点击事件 ──
        self._click_cb = self._on_click_event
        self.obj.add_event(self._click_cb, lv.EVENT.CLICKED, None)

        # 初始状态：远离中心的非选中视觉
        self.apply_scroll_visual(1.0)

    def _make_placeholder_icon(self):
        """图标缺失时的占位色块"""
        placeholder = lv.obj(self.obj)
        placeholder.set_size(ICON_SIZE, ICON_SIZE)
        placeholder.align(lv.ALIGN.LEFT_MID, ICON_LEFT_PAD, 0)
        placeholder.set_style_bg_color(lv.color_hex(Colors.CARD_SEL), 0)
        placeholder.set_style_radius(8, 0)
        placeholder.set_style_border_width(0, 0)
        self.icon_img = placeholder

    def _auto_scale_icon(self, png_data):
        """自动缩放图标以适应 ICON_SIZE×ICON_SIZE 容器，保持比例。

        从 PNG 二进制头解析图像尺寸（offset 16-23，big-endian）。
        LVGL v8 zoom 参数：256 = 100%，512 = 200%。
        缩放系数限制在 ICON_ZOOM_MIN(48) ~ ICON_ZOOM_MAX(256) 之间。
        """
        try:
            # PNG 格式：8 字节签名 | 4 字节数据长度 | 4 字节 'IHDR'
            #          | 4 字节 width (big-endian) | 4 字节 height (big-endian)
            if len(png_data) < 24:
                return
            # 使用 struct 解包 width/height
            import struct
            img_w = struct.unpack('>I', png_data[16:20])[0]
            img_h = struct.unpack('>I', png_data[20:24])[0]
            if img_w <= 0 or img_h <= 0:
                return

            # 计算适应容器的缩放系数，取宽高中较小的比例
            scale_w = int(ICON_SIZE / img_w * 256)
            scale_h = int(ICON_SIZE / img_h * 256)
            zoom = min(scale_w, scale_h)

            # 限制缩放范围
            if zoom > ICON_ZOOM_MAX:
                zoom = ICON_ZOOM_MAX
            elif zoom < ICON_ZOOM_MIN:
                zoom = ICON_ZOOM_MIN

            self.icon_img.set_zoom(zoom)
        except Exception:
            pass  # 缩放失败不阻塞，使用默认尺寸

    def _on_click_event(self, event):
        if event.get_code() == lv.EVENT.CLICKED:
            if self.on_click is not None:
                self.on_click()

    # ── 视觉状态 ──────────────────────────────────────

    def set_visual_state(self, selected, animate=True):
        """兼容旧调用：选中=中心视觉，非选中=远离中心视觉。"""
        self._selected = selected
        self.apply_scroll_visual(0.0 if selected else 1.0)

    def apply_scroll_visual(self, t):
        """按距中心归一化值更新视觉；t=0 选中，t=1 非选中。"""
        if self.obj is None:
            return
        if t < 0.0:
            t = 0.0
        elif t > 1.0:
            t = 1.0

        zoom = int(ZOOM_SELECTED - t * (ZOOM_SELECTED - ZOOM_NORMAL))
        opa = int(OPA_SELECTED - t * (OPA_SELECTED - OPA_NORMAL))
        dx = int(t * CARD_SHIFT_X)
        x = self.center_x - CARD_W // 2 + dx

        # Quantize to avoid repeatedly writing identical style values on tiny scroll jitter.
        key = (zoom, opa, x)
        if key == self._last_visual_key:
            return
        self._last_visual_key = key

        try:
            self.obj.set_style_transform_pivot_x(CARD_W // 2, 0)
            self.obj.set_style_transform_pivot_y(CARD_H // 2, 0)
            self.obj.set_style_transform_zoom(zoom, 0)
        except Exception:
            # Fallback keeps the menu usable if transform_zoom is unavailable.
            width = int(CARD_W_NORMAL + (1.0 - t) * (CARD_W - CARD_W_NORMAL))
            self.obj.set_size(width, CARD_H)
            x = self.center_x + CARD_W // 2 - width
        self.obj.set_style_opa(opa, 0)
        self.obj.set_pos(x, self.y)

    def press_animation(self):
        """点击反馈：瞬时轻微收缩，避免创建 Python 自定义动画回调。"""
        if self.obj is None:
            return
        try:
            w = int(CARD_W * 0.95)
            x = self.center_x - w // 2
            self.obj.set_size(w, CARD_H)
            self.obj.set_pos(x, self.y)
            self.obj.set_size(CARD_W, CARD_H)
            self.obj.set_pos(self.center_x - CARD_W // 2, self.y)
        except Exception:
            pass

    def refresh_text(self):
        """刷新卡片文字（语言切换后调用）"""
        # 字体加载暂时移除：未创建 label,直接跳过。
        if self.text_label is None:
            return
        name_key = self.category.get('name_key', self.category.get('id', ''))
        translated = self.lang.t(name_key) if self.lang else name_key
        self.text_label.set_text(translated)
