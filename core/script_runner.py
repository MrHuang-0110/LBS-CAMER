# core/script_runner.py — 脚本运行框架
#
# 统一管理脚本进入/运行/返回的生命周期。
# 负责 stream/page 模式标记、资源回收、异常兜底。
#
# 显示架构（参考官方 ai_lvgl.py）：
#   OSD1 — 相机帧（sensor.snapshot() → Display.show_image(LAYER_OSD1)）
#   OSD2 — LVGL  UI（flush 回调 → Display.show_image(LAYER_OSD2)）
#   Display + MediaManager 全程不重建，LVGL 全程运行。
#
# ⚠️ 重要：launch() 由 LVGL 事件回调触发（在 lv.task_handler() 内部），
# 因此 launch() 绝不能阻塞式调用 lv.task_handler()，否则会死锁。
# 运行循环改为 tick 驱动：main 主循环每帧调用 runner.tick()，
# runner.tick() 负责 on_frame / 退出检测 / UI 刷新。

from media.display import Display
from media.media import MediaManager
import lvgl as lv
import gc
import os
import time

from core.app import app_state, AppState
from core.event_bus import event_bus
from ui.back_bar import BackBar

# ── 显示模式标记 ──────────────────────────────────────
MODE_LVGL = 0      # LVGL 主菜单 / 纯 UI
MODE_CAMERA = 1    # Sensor 直连显示（相机/AI）


class ScriptContext:
    """传入脚本的上下文对象"""

    def __init__(self, lcd, touch, buzzer, lang, config, host):
        self.lcd = lcd
        self.touch = touch
        self.buzzer = buzzer
        self.lang = lang
        self.config = config
        self.host = host
        self._exit_requested = False

    def request_exit(self):
        """脚本内调用：请求返回主菜单"""
        self._exit_requested = True

    @property
    def exit_requested(self):
        return self._exit_requested

    # 便捷资源路径
    @property
    def resource_path(self):
        return "/sdcard/CamerAi/resource/"


class ScriptRunner:
    """脚本生命周期调度器"""

    def __init__(self, lcd, touch, buzzer, lang, config, host):
        self.lcd = lcd
        self.touch = touch
        self.buzzer = buzzer
        self.lang = lang
        self.config = config
        self.host = host

        self._current_mode = MODE_LVGL
        self._running = False
        self._script = None
        self._ctx = None
        self._back_bar = None
        self._ui_mode = None

        self._current_script_id = None

        # K2 物理按键（GPIO0，上拉，下降沿检测 → 分发给 app.on_key('K2')）
        from machine import Pin
        self._k2_pin = Pin(0, Pin.IN, Pin.PULL_UP)
        self._k2_last = 1  # 上拉默认高电平

        # 语言切换时刷新返回栏标题
        def _on_lang_changed():
            if self._back_bar is not None and self._current_script_id is not None:
                sid = self._current_script_id
                name_key = self.config.get_category(sid).get('name_key', sid)
                self._back_bar.set_title(self.lang.t(name_key))
        self._on_lang_changed = _on_lang_changed
        event_bus.on('lang_changed', _on_lang_changed)

    # ── 公开 API ──────────────────────────────────────

    @property
    def running(self):
        """是否有脚本正在运行"""
        return self._running

    def launch(self, script_id):
        """启动脚本（非阻塞）

        步骤：蜂鸣 → 加载 → 切换显示模式 → 挂载返回栏 → on_enter
        运行循环由 main 主循环每帧调用 tick() 驱动，不再在此阻塞。

        ⚠️ 此方法从 LVGL 事件回调（lv.task_handler 内部）调用，
        绝不能内嵌 lv.task_handler()，否则死锁。
        """
        from core.plugin_loader import PluginLoader

        # 1. 蜂鸣反馈
        self.buzzer.beep(ms=50)

        # 2. 加载脚本
        loader = PluginLoader()
        script = loader.load(script_id)
        if script is None:
            print(f"[Runner] load {script_id} failed")
            return

        self._script = script
        self._current_script_id = script_id

        # 2.5 隐藏主菜单容器
        # ⚠️ 主菜单是 scr_act 上的全屏不透明容器，不隐藏会一直挂在屏上。
        # page 模式脚本建不透明全屏 UI 盖住它，看不出问题；
        # 相机 stream 模式预览区透明，未隐藏的主菜单会挡死下层 OSD1 画面。
        print("[Runner] emitting runner_launched...")
        event_bus.emit('runner_launched')
        print("[Runner] runner_launched emitted")

        # 3. 构建上下文
        self._ctx = ScriptContext(
            self.lcd, self.touch, self.buzzer,
            self.lang, self.config, self.host,
        )

        # 4. 切换显示模式
        ui_mode = self.config.get_category(script_id).get('ui_mode', 'stream')
        self._ui_mode = ui_mode
        self._switch_mode_for(ui_mode)

        # 5. 挂载统一返回栏（脚本可声明自管顶栏跳过）
        self_managed = getattr(script, 'SELF_MANAGED_TOP_BAR', False)
        if not self_managed:
            self._back_bar = BackBar(
                self.lang.t(self.config.get_category(script_id).get(
                    'name_key', script_id)),
                on_back=lambda: self._ctx.request_exit(),
            )
            self._back_bar.show()
        else:
            self._back_bar = None

        # 6. 调用 on_enter
        try:
            script.on_enter(self._ctx)
        except Exception as e:
            print(f"[Runner] on_enter error: {e}")
            self.exit()
            return

        # 7. 标记运行中
        self._running = True
        app_state.state = AppState.SCRIPT_RUNNING

    def tick(self):
        """每帧调用 — 由 main 主循环驱动

        ⚠️ 不调用 lv.task_handler()，由 main 主循环统一调用。
        """
        if not self._running:
            return

        os.exitpoint()  # IDE 中断检测

        # K2 按键轮询（下降沿检测 → 分发给脚本）
        try:
            k2_cur = self._k2_pin.value()
            if self._k2_last == 1 and k2_cur == 0:
                if self._script is not None and hasattr(self._script, 'on_key'):
                    self._script.on_key('K2')
            self._k2_last = k2_cur
        except Exception:
            pass

        # 握手状态机轮询（所有 stream 模式脚本都需要）
        if self._ctx is not None and self._ctx.host is not None:
            try:
                self._ctx.host.poll_handshake()
            except Exception:
                pass

        # 退出检测
        if self._ctx and self._ctx.exit_requested:
            self.exit()
            return

        # stream 模式：驱动脚本的 on_frame
        if self._ui_mode == 'stream' and hasattr(self._script, 'on_frame'):
            try:
                self._script.on_frame()
            except Exception as e:
                print(f"[Runner] on_frame error: {e}")
                self.exit()
                return

    def exit(self):
        """退出脚本 → 回收资源 → 回到主菜单"""
        script = self._script
        ctx = self._ctx

        # 1. 脚本清理
        if script is not None:
            try:
                script.on_exit()
            except Exception as e:
                print(f"[Runner] on_exit error: {e}")

        # 2. 统一返回栏移除
        if self._back_bar is not None:
            try:
                self._back_bar.hide()
                del self._back_bar
                self._back_bar = None
            except Exception as e:
                print(f"[Runner] back_bar cleanup error: {e}")

        # 3. 恢复 LVGL 模式
        self._switch_to_lvgl_mode()

        # 4. GC 回收
        gc.collect()

        # 5. 状态回 MAIN_MENU
        app_state.state = AppState.MAIN_MENU
        self._running = False
        self._ui_mode = None
        self._current_script_id = None

        # 6. 蜂鸣反馈（可选）
        self.buzzer.beep(ms=50)

        # 7. 通知主菜单恢复
        event_bus.emit('runner_exited')

    def request_exit(self):
        """由外部（如返回栏）触发退出"""
        if self._ctx is not None:
            self._ctx.request_exit()

    # ── 显示模式切换 ──────────────────────────────────

    def _switch_mode_for(self, ui_mode):
        """根据脚本 ui_mode 切换显示"""
        if ui_mode == 'stream':
            self._switch_to_camera_mode()
        else:
            # page 模式继续用 LVGL，仅标记
            self._current_mode = MODE_LVGL

    def _switch_to_camera_mode(self):
        """标记进入相机模式

        显示栈全程不拆（Display.init 一次、MediaManager.init 一次）。
        相机脚本的 on_enter 只做 Sensor 初始化 + run()，
        on_frame 通过 sensor.snapshot() + Display.show_image(LAYER_OSD1)
        推送相机帧，LVGL 继续在 OSD2 层渲染。

        参考官方 ai_lvgl.py 的 LVGL+相机共存架构。
        """
        if self._current_mode == MODE_CAMERA:
            return

        # 显示栈保持运行，不拆。仅标记模式。
        self._current_mode = MODE_CAMERA

    def _switch_to_lvgl_mode(self):
        """标记回到纯 LVGL 模式

        相机脚本的 on_exit 已 sensor.stop() 清理传感器。
        LVGL 从未停止（OSD2 层持续渲染），无需重建任何显示栈。
        """
        if self._current_mode == MODE_LVGL:
            return
        self._current_mode = MODE_LVGL
