# core/font_manager.py — 字体加载与缓存
#
# Phase 0–1：直接加载 /resource/font/ 下的 demo 字体 bin（ASCII only）。
# Phase 2：使用思源黑体子集（lv_font_conv 生成），含中文字形。
#          运行 tools/build_fonts.py 生成后，自动切换为 Phase 2 字体。
#
# UI 层通过 fonts.title / fonts.body / fonts.caption 引用，禁止硬编码路径。

import lvgl as lv


FONT_PATH = "A:/sdcard/CamerAi/resource/font/"
# K230 板端：lv.font_load() 的 "A:" 前缀映射到文件系统根目录 "/"，
# 其后必须跟【从根开始的绝对路径】。对齐 demo/123:
#   lv.font_load("A:" + "/sdcard/CanMV Sample/" + "Fonts/x.bin")
#   → "A:/sdcard/CanMV Sample/Fonts/x.bin" → 实际访问 /sdcard/CanMV Sample/...
# 所以本项目字体在 /sdcard/CamerAi/resource/font/ → 前缀须是
# "A:/sdcard/CamerAi/resource/font/"（含 /sdcard，曾因漏掉它导致
# lv.font_load 去找 /CamerAi/... 失败返回 None → label 不渲染文字）。

# A: 路径对应的真实文件系统路径（去掉 "A:" 前缀即可），用于 open() 探测。
FONT_FS_PATH = FONT_PATH[2:]  # → "/sdcard/CamerAi/resource/font/"

# ── Phase 2 正式字体映射（lv_font_conv 生成，含 CJK 字形）──
PHASE2_FONT_MAP = {
    "title":   "font_title_50.bin",     # 50px 思源黑体子集（卡片名称，约图标高度一半）
    "body":    "font_body_18.bin",      # 18px 思源黑体子集
    "caption": "font_caption_14.bin",   # 14px 思源黑体子集
}

# demo 字体文件名映射（Phase 0–1 临时方案，ASCII only）
DEMO_FONT_MAP = {
    "title":   "lv_font_normal_bold_size25_bpp4.bin",   # 20px → 临时用 25px demo 字体
    "body":    "lv_font_normal_size20_bpp4.bin",         # 18px → 临时用 20px
    "caption": "lv_font_normal_size20_bpp4.bin",         # 14px → 临时用 20px（缩小显示）
}


class FontManager:
    """全局字体管理器"""

    def __init__(self):
        self._fonts = {}
        self._loaded = False

    def load_all(self):
        """加载全部预设字体。

        Phase 2 字体优先（含中文），不存在时自动回退到 demo 字体。
        """
        if self._loaded:
            return

        # 探测 Phase 2 字体是否齐全（逐个检查，不依赖 dict 顺序）。
        # ⚠️ MicroPython 的 dict 不保证插入顺序,不能用 values()[0] 取"第一个"。
        # 改为：只要每个 Phase2 文件都能 open 成功,才用 Phase2;任一缺失则回退。
        use_phase2 = True
        for level, filename in PHASE2_FONT_MAP.items():
            fs_path = FONT_FS_PATH + filename
            try:
                with open(fs_path, "rb") as _f:
                    pass
                print(f"[Font] probe OK: {fs_path}")
            except Exception as e:
                print(f"[Font] probe MISSING: {fs_path} ({e})")
                use_phase2 = False

        font_map = PHASE2_FONT_MAP if use_phase2 else DEMO_FONT_MAP
        phase_label = "Phase2" if use_phase2 else "Demo"
        print(f"[Font] using {phase_label} fonts")

        for level, filename in font_map.items():
            path = FONT_PATH + filename
            try:
                print(f"[Font]   loading {level}: {path}")
                f = lv.font_load(path)
                if f is None or f == 0:
                    print(f"[Font]   {level}: {filename} load returned None/0 — font unusable")
                    self._fonts[level] = None
                else:
                    self._fonts[level] = f
                    print(f"[Font]   {level}: {filename} OK (id={id(f) if hasattr(f, '__hash__') else hex(f) if isinstance(f, int) else 'ptr'})")
            except Exception as e:
                print(f"[Font] load {path} failed: {e}")
                self._fonts[level] = None

        self._loaded = True

    # ── 字体引用 ──────────────────────────────────────

    @property
    def title(self):
        """20px 标题字体 → 主菜单卡片名称、设置页标题"""
        return self._fonts.get("title")

    @property
    def body(self):
        """18px 正文字体 → 返回栏、设置项"""
        return self._fonts.get("body")

    @property
    def caption(self):
        """14px 辅助字体 → 描述、状态文字"""
        return self._fonts.get("caption")

    def get(self, level="body"):
        """通过名称获取字体"""
        return self._fonts.get(level)

    @property
    def is_loaded(self):
        return self._loaded


# 全局单例
fonts = FontManager()
