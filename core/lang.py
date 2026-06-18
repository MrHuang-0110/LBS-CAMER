# core/lang.py — 多语言管理
# 加载 /resource/i18n/{lang}.json，提供 t("key") 翻译接口。
# 分层键：t("category.camera")、t("common.back") 等。

import json


class LangManager:
    """i18n 翻译管理器"""

    I18N_PATH = "/sdcard/CamerAi/resource/i18n/"

    def __init__(self):
        self._lang = "zh_CN"
        self._data = {}        # lang → parsed JSON
        self._flat = {}        # "key.sub" → text  (当前语言展平)

    def load(self, lang=None):
        """加载语言包。若不指定则用当前 lang。"""
        if lang is not None:
            self._lang = lang

        path = self.I18N_PATH + self._lang + ".json"
        try:
            with open(path, 'r') as f:
                self._data[self._lang] = json.load(f)
        except Exception as e:
            print(f"[Lang] load {path} failed: {e}")
            # 回退到硬编码英文最小集
            self._data[self._lang] = {
                "common": {"back": "Back", "app_name": "CamerAi"},
            }

        # 展平嵌套 JSON 为 "section.key" 形式
        self._flatten()

    def _flatten(self):
        """将嵌套结构展平为一层键"""
        self._flat.clear()
        data = self._data.get(self._lang, {})

        def _walk(obj, prefix):
            for k, v in obj.items():
                full_key = f"{prefix}.{k}" if prefix else k
                if isinstance(v, dict):
                    _walk(v, full_key)
                else:
                    self._flat[full_key] = v

        _walk(data, "")

    # ── 翻译接口 ──────────────────────────────────────

    def t(self, key, *args):
        """获取翻译文本。支持格式化参数。

        Usage:
            lang.t("category.camera")          → "相机"
            lang.t("common.back")              → "返回"
        """
        text = self._flat.get(key)
        if text is None:
            return key  # 返回键名作为兜底
        if args:
            try:
                return text % args
            except Exception:
                return text
        return text

    # ── 语言切换 ──────────────────────────────────────

    @property
    def lang(self):
        return self._lang

    def switch(self, new_lang):
        """切换到新语言并重新加载"""
        if new_lang != self._lang:
            self.load(new_lang)

    @property
    def available_langs(self):
        return ["zh_CN", "en_US"]
