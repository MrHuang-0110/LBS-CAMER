# core/plugin_loader.py — 脚本插件加载器
# 扫描 scripts/ 目录，按 script_id 动态加载 app.py 并实例化。

import os
import sys


SCRIPTS_PATH = "/sdcard/CamerAi/scripts/"


class PluginLoader:
    """脚本加载器 — 按 id 加载 BaseScript 子类"""

    def __init__(self):
        self._cache = {}  # script_id → module

    def load(self, script_id):
        """加载脚本模块并返回 App 实例。

        Args:
            script_id: 脚本目录名（如 "camera"）

        Returns:
            BaseScript 实例，或 None
        """
        # 命中缓存
        if script_id in self._cache:
            mod = self._cache[script_id]
            return self._instantiate(mod)

        path = SCRIPTS_PATH + script_id + "/app.py"
        try:
            # MicroPython 动态导入
            mod_name = f"scripts.{script_id}.app"
            # 先尝试标准 import
            try:
                mod = __import__(mod_name, {}, {}, [''])
            except ImportError:
                # 回退：exec 方式加载（兼容 MicroPython 路径差异）
                mod = self._exec_load(path, script_id)

            self._cache[script_id] = mod
            return self._instantiate(mod)

        except Exception as e:
            print(f"[PluginLoader] load {script_id} failed: {e}")
            return None

    def _exec_load(self, path, script_id):
        """通过 exec 从文件路径加载模块（MicroPython 兼容）"""
        import sys
        try:
            with open(path, 'r') as f:
                source = f.read()
        except Exception as e:
            raise ImportError(f"Cannot read {path}: {e}")

        # 构造一个模块命名空间
        mod = type(sys)('scripts.' + script_id + '.app')
        mod.__file__ = path
        exec(source, mod.__dict__)
        sys.modules['scripts.' + script_id + '.app'] = mod
        return mod

    def _instantiate(self, mod):
        """在模块中查找 BaseScript 子类并实例化"""
        from scripts._base import BaseScript

        for name in dir(mod):
            obj = getattr(mod, name)
            if (isinstance(obj, type)
                    and issubclass(obj, BaseScript)
                    and obj is not BaseScript):
                return obj()
        return None

    def unload(self, script_id):
        """从缓存中移除（用于强制重载）"""
        self._cache.pop(script_id, None)
