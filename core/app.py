# core/app.py — 应用状态机
#
#   APP_INIT → BOOT_SPLASH → MAIN_MENU ⇄ SCRIPT_RUNNING
#
# 全局单例 `app_state` 供各模块查询当前阶段。


class AppState:
    """应用全局状态"""

    APP_INIT = 0
    BOOT_SPLASH = 1
    MAIN_MENU = 2
    SCRIPT_RUNNING = 3

    _STATE_NAMES = {
        0: 'APP_INIT',
        1: 'BOOT_SPLASH',
        2: 'MAIN_MENU',
        3: 'SCRIPT_RUNNING',
    }

    def __init__(self):
        self._state = self.APP_INIT
        self._callbacks = {}  # state → [callback]

    # ── 状态读写 ──────────────────────────────────────

    @property
    def state(self):
        return self._state

    @state.setter
    def state(self, new_state):
        old = self._state
        self._state = new_state
        # 触发回调
        for cb in self._callbacks.get(new_state, []):
            try:
                cb(old, new_state)
            except Exception as e:
                print(f"[AppState] callback error: {e}")

    @property
    def name(self):
        return self._STATE_NAMES.get(self._state, 'UNKNOWN')

    # ── 便捷方法 ──────────────────────────────────────

    def is_menu(self):
        return self._state == self.MAIN_MENU

    def is_script_running(self):
        return self._state == self.SCRIPT_RUNNING

    def on_enter(self, state, callback):
        """注册状态进入回调"""
        self._callbacks.setdefault(state, []).append(callback)


# 全局单例
app_state = AppState()
