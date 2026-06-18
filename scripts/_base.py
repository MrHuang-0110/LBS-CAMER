# scripts/_base.py — 脚本基类
#
# 所有功能脚本必须继承 BaseScript。
# ScriptRunner 通过 on_enter → [on_frame × N] → on_exit 生命周期调度。
#
# 项目计划 §3.5.6

class BaseScript:
    """脚本基类 — ScriptRunner 通过此类调度所有脚本"""

    SCRIPT_ID = ""  # 子类必须覆写
    SELF_MANAGED_TOP_BAR = False  # True = 脚本自管顶栏，Runner 不挂载 BackBar

    def __init__(self):
        self.ctx = None
        self._running = False

    # ── 生命周期 ──────────────────────────────────────

    def on_enter(self, ctx):
        """进入脚本时调用 — 初始化硬件/模型/UI

        Args:
            ctx: ScriptContext 实例
        """
        self.ctx = ctx
        self._running = True

    def on_frame(self):
        """每帧调用 — 仅 stream 类型脚本需要实现

        相机/AI 脚本在此处理帧逻辑。
        Runner 会在 while running 中循环调用。
        """
        import os
        os.exitpoint()  # IDE 中断检测

    def on_exit(self):
        """退出脚本时调用 — 停止 Sensor、释放模型、删除 UI

        子类覆写时必须调用 super().on_exit()。
        """
        self._running = False

    def on_key(self, key):
        """硬件按键回调（可选覆写）"""
        pass

    # ── 便捷方法 ──────────────────────────────────────

    @property
    def running(self):
        return self._running

    def request_exit(self):
        """请求返回主菜单"""
        if self.ctx:
            self.ctx.request_exit()
