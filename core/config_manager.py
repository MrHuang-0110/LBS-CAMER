# core/config_manager.py — 配置管理
# 加载 config/app.json 和 config/categories.json
# 运行时修改（如语言切换）后持久化回写。

import json
import os


class ConfigManager:
    """JSON 配置管理器"""

    def __init__(self, app_config_path="/sdcard/CamerAi/config/app.json",
                 categories_path="/sdcard/CamerAi/config/categories.json"):
        self._app_path = app_config_path
        self._cat_path = categories_path
        self.app = {}
        self.categories = []

    def load(self):
        """加载全部配置"""
        # app.json
        try:
            with open(self._app_path, 'r') as f:
                self.app = json.load(f)
        except Exception as e:
            print(f"[Config] load app.json failed: {e}; using defaults")
            self.app = {
                "boot_logo": "/sdcard/CamerAi/resource/logo.png",
                "boot_logo_ms": 1500,
                "lang": "zh_CN",
                "buzzer_enabled": True,
            }

        # categories.json
        try:
            with open(self._cat_path, 'r') as f:
                data = json.load(f)
            self.categories = data.get('categories', [])
            # 仅保留 enabled 的类目，按 order 排序
            self.categories = sorted(
                [c for c in self.categories if c.get('enabled', True)],
                key=lambda c: c.get('order', 999),
            )
        except Exception as e:
            print(f"[Config] load categories.json failed: {e}; using empty")
            self.categories = []

    # ── app.json 读写 ─────────────────────────────────

    def get(self, key, default=None):
        return self.app.get(key, default)

    def set(self, key, value):
        self.app[key] = value

    def save(self):
        """持久化 app.json（categories 目前只读，不写回）"""
        try:
            with open(self._app_path, 'w') as f:
                json.dump(self.app, f)
        except Exception as e:
            print(f"[Config] save app.json failed: {e}")

    # ── 类目查询 ──────────────────────────────────────

    def get_category(self, script_id):
        """按 id 查找类目"""
        for cat in self.categories:
            if cat.get('id') == script_id:
                return cat
        return None

    def get_enabled_categories(self):
        """返回所有启用的类目（已排序）"""
        return self.categories
