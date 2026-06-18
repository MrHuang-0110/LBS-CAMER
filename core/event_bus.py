# core/event_bus.py — 简易事件总线
# 用于模块间松耦合通信（如：语言切换通知主菜单刷新）。


class EventBus:
    """发布/订阅事件总线"""

    def __init__(self):
        self._handlers = {}  # event_name → [callback]

    def on(self, event, callback):
        """注册事件监听"""
        self._handlers.setdefault(event, []).append(callback)

    def off(self, event, callback):
        """移除事件监听"""
        handlers = self._handlers.get(event, [])
        if callback in handlers:
            handlers.remove(callback)

    def emit(self, event, *args, **kwargs):
        """触发事件（同步）"""
        for cb in self._handlers.get(event, []):
            try:
                cb(*args, **kwargs)
            except Exception as e:
                print(f"[EventBus] handler error for '{event}': {e}")

    def clear(self, event=None):
        """清除事件（不指定则全部清除）"""
        if event is None:
            self._handlers.clear()
        else:
            self._handlers.pop(event, None)


# 全局单例
event_bus = EventBus()
