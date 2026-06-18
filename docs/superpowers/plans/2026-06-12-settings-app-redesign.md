# 设置页重构实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将设置页从 Tab 式改为 iOS 风格左右分栏列表，修复返回按钮图标不显示和中文字体部分缺失问题。

**Architecture:** 重写 `scripts/settings/app.py` 为左右分栏布局（35%:65%）；修改 `ui/back_bar.py` 返回按钮从文本改为图片图标；补全字体字符集并重新生成字体 bin。

**Tech Stack:** MicroPython + LVGL v8 + K230D BOX (640×480)

---

## 文件结构

| 文件 | 操作 | 职责 |
|------|------|------|
| `scripts/settings/app.py` | 重写 | 左右分栏布局：左栏功能列表 + 右栏内容区 |
| `ui/back_bar.py` | 修改 | 返回按钮从文本 `←` 改为图片图标 |
| `resource/i18n/zh_CN.json` | 修改 | 补充左右分栏所需的新 i18n 键 |
| `resource/i18n/en_US.json` | 修改 | 同步补充英文键 |
| `tools/ui_chars.txt` | 修改 | 补全缺失中文字符 |
| `resource/font/font_body_18.bin` | 重新生成 | 补全字形 |
| `resource/font/font_title_50.bin` | 重新生成 | 补全字形 |
| `resource/font/font_caption_14.bin` | 重新生成 | 补全字形 |

---

### Task 1: 补全 i18n 键 + 字符集，重新生成字体

**Files:**
- Modify: `resource/i18n/zh_CN.json`
- Modify: `resource/i18n/en_US.json`
- Modify: `tools/ui_chars.txt`

新布局需要额外的 i18n 键：右栏语言的当前语言指示文字。关于页的硬编码值（K230D BOX、ST7701 640×480、MicroPython + LVGL、LBS Team）虽不含中文，但 `ui_chars.txt` 应补全 i18n 中可能缺失的字。

- [ ] **Step 1: 补全 zh_CN.json**

在 `settings` 键下补充右栏语言选择区需要的文字键：

```json
{
  "category": {
    "settings": "设置",
    "settings_desc": "语言、系统关于",
    "camera": "相机",
    "camera_desc": "实时预览拍摄",
    "face_detect": "人脸识别",
    "face_detect_desc": "人脸检测与识别",
    "tag_detect": "标签识别",
    "tag_detect_desc": "二维码与标签码",
    "object_detect": "物体识别",
    "object_detect_desc": "检测画面中的物体",
    "color_detect": "颜色识别",
    "color_detect_desc": "识别色块与主色",
    "road_detect": "道路识别",
    "road_detect_desc": "车道线与循线",
    "gesture_detect": "手势识别",
    "gesture_detect_desc": "识别手部手势",
    "body_detect": "人体识别",
    "body_detect_desc": "人体检测与姿态",
    "object_classify": "物体分类",
    "object_classify_desc": "对物体进行分类",
    "image_classify": "图像分类",
    "image_classify_desc": "对图像场景分类"
  },
  "settings": {
    "tab_language": "语言",
    "tab_language_desc": "切换界面显示语言",
    "tab_about": "关于",
    "tab_about_desc": "系统版本与设备信息",
    "lang_zh": "中文",
    "lang_en": "English",
    "lang_current": "当前",
    "about_app_name": "应用名称",
    "about_version": "版本",
    "about_platform": "平台",
    "about_display": "显示",
    "about_sdk": "固件",
    "about_author": "作者",
    "about_app_name_val": "CamerAi",
    "about_version_val": "v0.1.0",
    "about_platform_val": "K230D BOX",
    "about_display_val": "ST7701 640x480",
    "about_sdk_val": "MicroPython+LVGL",
    "about_author_val": "LBS Team"
  },
  "common": {
    "back": "返回",
    "app_name": "CamerAi"
  }
}
```

注意：`about_display_val` 中 `640x480` 用小写 x 而非 × ，避免 Unicode 乘号字形缺失。

- [ ] **Step 2: 补全 en_US.json**

同步补充英文键，键集必须与 zh_CN.json 一致：

```json
{
  "category": {
    "settings": "Settings",
    "settings_desc": "Language & About",
    "camera": "Camera",
    "camera_desc": "Live preview",
    "face_detect": "Face",
    "face_detect_desc": "Face detection",
    "tag_detect": "Tag",
    "tag_detect_desc": "QR & tag codes",
    "object_detect": "Object",
    "object_detect_desc": "Detect objects",
    "color_detect": "Color",
    "color_detect_desc": "Color detection",
    "road_detect": "Road",
    "road_detect_desc": "Lane & line follow",
    "gesture_detect": "Gesture",
    "gesture_detect_desc": "Hand gesture",
    "body_detect": "Body",
    "body_detect_desc": "Body detection",
    "object_classify": "Obj Classify",
    "object_classify_desc": "Object classification",
    "image_classify": "Img Classify",
    "image_classify_desc": "Image classification"
  },
  "settings": {
    "tab_language": "Language",
    "tab_language_desc": "Switch UI language",
    "tab_about": "About",
    "tab_about_desc": "Version & device info",
    "lang_zh": "中文",
    "lang_en": "English",
    "lang_current": "Current",
    "about_app_name": "App Name",
    "about_version": "Version",
    "about_platform": "Platform",
    "about_display": "Display",
    "about_sdk": "Firmware",
    "about_author": "Author",
    "about_app_name_val": "CamerAi",
    "about_version_val": "v0.1.0",
    "about_platform_val": "K230D BOX",
    "about_display_val": "ST7701 640x480",
    "about_sdk_val": "MicroPython+LVGL",
    "about_author_val": "LBS Team"
  },
  "common": {
    "back": "Back",
    "app_name": "CamerAi"
  }
}
```

- [ ] **Step 3: 补全 ui_chars.txt**

在 `# ── 设置页 ──` 段落下追加缺失字符：

```
# ── 设置页 ──
关于
版本
设备
存储
亮度
音量
蓝牙
WiFi
重启
关机
恢复出厂
语言
切换
当前
固件
显示
作者
名称
应用
```

新增：语言、切换、当前、固件、显示、作者、名称、应用

- [ ] **Step 4: 重新生成字体 bin**

```bash
python tools/build_fonts.py --run
```

预期：3 个 bin 文件重新生成，字形数量比之前多。

- [ ] **Step 5: 验证 JSON 键集一致**

```bash
python -c "import json; zh=json.load(open('resource/i18n/zh_CN.json')); en=json.load(open('resource/i18n/en_US.json')); assert set(_flatten(zh)) == set(_flatten(en)), 'KEY MISMATCH'"
```

其中 `_flatten` 是递归展平嵌套 dict 键的函数。如果不想写脚本，用肉眼对比即可——两个 JSON 结构相同，键名逐一对应。

- [ ] **Step 6: Commit**

```bash
git add resource/i18n/zh_CN.json resource/i18n/en_US.json tools/ui_chars.txt resource/font/font_body_18.bin resource/font/font_title_50.bin resource/font/font_caption_14.bin
git commit -m "feat(settings): 补全 i18n 键 + 字体字符集，重新生成字体 bin"
```

---

### Task 2: 修改 BackBar 返回按钮为图片图标

**Files:**
- Modify: `ui/back_bar.py`

当前返回按钮用文本 `"←"`，在 demo 字体（ASCII only）中无此字形。改为加载 `back.png` 图片。

- [ ] **Step 1: 修改 BackBar 构造函数，增加 icon_path 参数**

在 `__init__` 中新增 `icon_path` 参数，默认为 `None`（向后兼容）：

```python
class BackBar:
    """统一返回栏 — 由 ScriptRunner 自动挂载到所有脚本"""

    HEIGHT = 40
    BTN_WIDTH = 80
    STATUS_WIDTH = 40
    DEFAULT_ICON = "/sdcard/CamerAi/resource/icons/settings_icon/back.png"

    def __init__(self, title, on_back=None, icon_path=None):
        self.title = title
        self.on_back = on_back
        self.icon_path = icon_path or self.DEFAULT_ICON
        self._bar = None
        self._btn = None
        self._label = None
        self._icon_data = None
        self._icon_dsc = None
```

- [ ] **Step 2: 修改 show() 方法，用图片替代文本按钮**

将 `show()` 中创建按钮的逻辑从文本标签改为图片。完整 `show()` 方法：

```python
def show(self):
    """创建并显示返回栏（置于屏幕最上层）"""
    if self._bar is not None:
        return

    screen = lv.scr_act()
    screen_width = screen.get_width()

    # ── 背景条 ──
    self._bar = lv.obj(screen)
    self._bar.set_size(lv.pct(100), self.HEIGHT)
    self._bar.align(lv.ALIGN.TOP_MID, 0, 0)
    bar_style = make_back_bar_style()
    self._bar.add_style(bar_style, 0)
    self._bar.clear_flag(lv.obj.FLAG.SCROLLABLE)
    self._bar.move_foreground()

    # ── 返回按钮（左，图片图标）──
    self._btn = lv.btn(self._bar)
    self._btn.set_size(self.HEIGHT, self.HEIGHT)  # 40×40 方形按钮
    self._btn.align(lv.ALIGN.LEFT_MID, 0, 0)
    self._btn.set_style_bg_opa(0, 0)
    self._btn.set_style_border_width(0, 0)
    self._btn.set_style_radius(0, 0)
    self._btn.set_style_pad_all(4, 0)  # 内边距让图标小一圈

    # 加载返回箭头图标
    icon_loaded = False
    try:
        with open(self.icon_path, 'rb') as f:
            self._icon_data = f.read()
        if self._icon_data:
            self._icon_dsc = lv.img_dsc_t({
                'data_size': len(self._icon_data),
                'data': self._icon_data,
            })
            icon_img = lv.img(self._btn)
            icon_img.set_src(self._icon_dsc)
            # back.png 是 64×64，缩放到按钮内 32×32
            zoom = int(32 / 64 * 256)
            icon_img.set_zoom(zoom)
            icon_img.center()
            icon_loaded = True
    except Exception as e:
        print(f"[BackBar] icon load failed: {e}")

    if not icon_loaded:
        # 回退：用 ASCII 文字 "<"
        btn_label = lv.label(self._btn)
        btn_text_style = make_back_bar_text_style(fonts.body)
        btn_label.add_style(btn_text_style, 0)
        btn_label.set_text("<")
        btn_label.center()

    if self.on_back is not None:
        self._btn.add_event(
            lambda e: self.on_back() if e.get_code() == lv.EVENT.CLICKED else None,
            lv.EVENT.CLICKED, None,
        )

    # ── 标题（居中）──
    self._label = lv.label(self._bar)
    self._label.set_text(self.title)
    self._label.align(lv.ALIGN.CENTER, 0, 0)
    title_style = make_back_bar_text_style(fonts.body)
    self._label.add_style(title_style, 0)

    # ── 状态区（右）──
    status = lv.obj(self._bar)
    status.set_size(self.STATUS_WIDTH, self.HEIGHT)
    status.align(lv.ALIGN.RIGHT_MID, 0, 0)
    status.set_style_bg_opa(0, 0)
    status.set_style_border_width(0, 0)
    status.clear_flag(lv.obj.FLAG.SCROLLABLE)
```

- [ ] **Step 3: 修改 hide() 方法，清理图标数据**

在 `hide()` 中增加对 `_icon_data` 和 `_icon_dsc` 的清理：

```python
def hide(self):
    """移除返回栏"""
    if self._bar is not None:
        try:
            self._bar.delete()
        except Exception:
            pass
        self._bar = None
        self._btn = None
        self._label = None
        self._icon_data = None
        self._icon_dsc = None
```

- [ ] **Step 4: 在文件顶部添加 font_manager 导入**

在 `ui/back_bar.py` 顶部 import 区域添加：

```python
from core.font_manager import fonts
```

- [ ] **Step 5: Commit**

```bash
git add ui/back_bar.py
git commit -m "fix(back_bar): 返回按钮从文本箭头改为图片图标"
```

---

### Task 3: 重写 settings/app.py 为左右分栏布局

**Files:**
- Rewrite: `scripts/settings/app.py`

这是核心任务。将 Tab 式布局改为左栏功能列表 + 右栏内容区的 iOS 风格分栏。

- [ ] **Step 1: 重写 SettingsApp 完整代码**

```python
# scripts/settings/app.py — 设置页（左右分栏布局）
#
# 左栏 35%：功能列表（图标 + 名称 + 箭头）
# 右栏 65%：内容区（语言切换 / 关于信息）
# ui_mode = "page"，LVGL 全程管理，无相机参与。

import struct
import lvgl as lv
from scripts._base import BaseScript
from core.event_bus import event_bus
from ui.theme import Colors, make_back_bar_text_style
from core.font_manager import fonts


# ── 布局参数 ──────────────────────────────────────
BAR_H = 40          # 返回栏高度
LEFT_W = 224        # 左栏宽度 (640 * 35%)
RIGHT_W = 416       # 右栏宽度 (640 * 65%)
ROW_H = 56          # 左栏行高
ICON_SIZE = 36      # 左栏图标显示尺寸
ICON_PAD_LEFT = 12  # 图标左边距
ICON_TEXT_GAP = 12  # 图标与文字间距
ARROW_PAD_RIGHT = 8 # 箭头右边距
CONTENT_PAD = 8     # 右栏内边距
LANG_ROW_H = 48     # 语言行高
ABOUT_ROW_H = 44    # 关于行高


class SettingsApp(BaseScript):
    SCRIPT_ID = "settings"

    def __init__(self):
        super().__init__()
        self._screen = None
        self._left_panel = None
        self._right_panel = None
        self._rows = []        # [(btn_obj, icon_dsc, icon_data), ...]
        self._active_item = "language"
        self._icon_cache = {}  # item_id → (icon_data, icon_dsc)

    # ── 生命周期 ──────────────────────────────────────

    def on_enter(self, ctx):
        super().on_enter(ctx)
        self._preload_icons()
        self._build_ui()

    def on_exit(self):
        self._destroy_ui()
        super().on_exit()

    # ── 图标预加载 ──────────────────────────────────────

    def _preload_icons(self):
        """预加载设置页图标到内存"""
        icons = {
            "language": "/sdcard/CamerAi/resource/icons/settings_icon/Language.png",
            "about":    "/sdcard/CamerAi/resource/icons/settings_icon/about.png",
        }
        for item_id, path in icons.items():
            try:
                with open(path, 'rb') as f:
                    data = f.read()
                dsc = lv.img_dsc_t({
                    'data_size': len(data),
                    'data': data,
                })
                self._icon_cache[item_id] = (data, dsc)
            except Exception as e:
                print(f"[Settings] icon load failed ({item_id}): {e}")

    # ── UI 构建 ──────────────────────────────────────

    def _build_ui(self):
        ctx = self.ctx
        lang = ctx.lang

        screen = lv.scr_act()
        screen.set_style_bg_color(lv.color_hex(Colors.BG), 0)
        screen.set_style_bg_opa(255, 0)
        self._screen = screen

        scr_w = screen.get_width()
        scr_h = screen.get_height()

        # ── 左栏（功能列表）──
        self._left_panel = lv.obj(screen)
        self._left_panel.set_size(LEFT_W, scr_h - BAR_H)
        self._left_panel.set_pos(0, BAR_H)
        self._left_panel.set_style_bg_color(lv.color_hex(0x1A1A1A), 0)
        self._left_panel.set_style_bg_opa(255, 0)
        self._left_panel.set_style_border_width(0, 0)
        self._left_panel.set_style_pad_all(0, 0)
        self._left_panel.set_style_radius(0, 0)
        self._left_panel.clear_flag(lv.obj.FLAG.SCROLLABLE)

        # ── 右栏（内容区）──
        self._right_panel = lv.obj(screen)
        self._right_panel.set_size(RIGHT_W, scr_h - BAR_H - CONTENT_PAD * 2)
        self._right_panel.set_pos(LEFT_W, BAR_H + CONTENT_PAD)
        self._right_panel.set_style_bg_color(lv.color_hex(0x222222), 0)
        self._right_panel.set_style_bg_opa(255, 0)
        self._right_panel.set_style_border_width(0, 0)
        self._right_panel.set_style_pad_all(CONTENT_PAD, 0)
        self._right_panel.set_style_radius(14, 0)
        self._right_panel.clear_flag(lv.obj.FLAG.SCROLLABLE)

        # ── 构建左栏行 ──
        items = [
            ("language", "settings.tab_language"),
            ("about",    "settings.tab_about"),
        ]
        for i, (item_id, name_key) in enumerate(items):
            self._build_left_row(i, item_id, name_key)

        # ── 渲染右栏默认内容 ──
        self._render_right(self._active_item)

    def _build_left_row(self, index, item_id, name_key):
        """构建左栏单行：图标 + 名称 + 箭头"""
        lang = self.ctx.lang
        y = index * ROW_H
        active = (item_id == self._active_item)

        # 行容器（可点击）
        row = lv.btn(self._left_panel)
        row.set_size(LEFT_W, ROW_H)
        row.set_pos(0, y)
        row.set_style_bg_color(
            lv.color_hex(0x2A2A2A if active else 0x1A1A1A), 0)
        row.set_style_bg_opa(255, 0)
        row.set_style_border_width(0, 0)
        row.set_style_radius(0, 0)
        row.set_style_pad_all(0, 0)

        # 图标
        icon_data, icon_dsc = self._icon_cache.get(item_id, (None, None))
        if icon_dsc is not None:
            icon_img = lv.img(row)
            icon_img.set_src(icon_dsc)
            # 128×128 → 36×36
            zoom = int(ICON_SIZE / 128 * 256)
            icon_img.set_zoom(zoom)
            icon_img.set_size(ICON_SIZE, ICON_SIZE)
            icon_img.align(lv.ALIGN.LEFT_MID, ICON_PAD_LEFT, 0)

        # 功能名
        name_label = lv.label(row)
        name_label.set_text(lang.t(name_key))
        name_label.align(lv.ALIGN.LEFT_MID,
                         ICON_PAD_LEFT + ICON_SIZE + ICON_TEXT_GAP, 0)
        name_style = make_back_bar_text_style(fonts.body)
        name_label.add_style(name_style, 0)

        # 右侧箭头
        arrow_label = lv.label(row)
        arrow_label.set_text(">")
        arrow_label.align(lv.ALIGN.RIGHT_MID, -ARROW_PAD_RIGHT, 0)
        arrow_style = make_back_bar_text_style(fonts.body)
        arrow_label.add_style(arrow_style, 0)
        arrow_label.set_style_text_color(lv.color_hex(Colors.TEXT_DIM), 0)

        # 点击事件
        def _on_row(e, iid=item_id):
            if e.get_code() == lv.EVENT.CLICKED:
                self._select_item(iid)
        row.add_event(_on_row, lv.EVENT.CLICKED, None)

        self._rows.append((row, item_id))

    def _select_item(self, item_id):
        """切换左栏选中项 + 刷新右栏"""
        if item_id == self._active_item:
            return
        self._active_item = item_id

        # 刷新左栏高亮
        for row, iid in self._rows:
            active = (iid == item_id)
            row.set_style_bg_color(
                lv.color_hex(0x2A2A2A if active else 0x1A1A1A), 0)

        # 刷新右栏
        self._right_panel.clean()
        self._render_right(item_id)

    def _render_right(self, item_id):
        """渲染右栏内容"""
        if item_id == "language":
            self._render_language()
        elif item_id == "about":
            self._render_about()

    # ── 语言内容 ──────────────────────────────────────

    def _render_language(self):
        ctx = self.ctx
        lang = ctx.lang
        container = self._right_panel

        langs = [
            ("zh_CN", "settings.lang_zh"),
            ("en_US", "settings.lang_en"),
        ]
        for i, (code, key) in enumerate(langs):
            active = (lang.lang == code)
            row = lv.btn(container)
            row.set_size(lv.pct(100), LANG_ROW_H)
            row.align(lv.ALIGN.TOP_MID, 0, i * (LANG_ROW_H + 6))
            row.set_style_bg_color(
                lv.color_hex(0x2A5298 if active else 0x2A2A2A), 0)
            row.set_style_bg_opa(255, 0)
            row.set_style_radius(10, 0)
            row.set_style_border_width(0, 0)

            lbl = lv.label(row)
            lbl.set_text(lang.t(key))
            lbl.align(lv.ALIGN.LEFT_MID, 12, 0)
            lbl.add_style(make_back_bar_text_style(fonts.body), 0)

            # 当前语言高亮标记（文字）
            if active:
                cur_lbl = lv.label(row)
                cur_lbl.set_text(lang.t("settings.lang_current"))
                cur_lbl.align(lv.ALIGN.RIGHT_MID, -12, 0)
                cur_lbl.add_style(make_back_bar_text_style(fonts.body), 0)
                cur_lbl.set_style_text_color(
                    lv.color_hex(Colors.ACCENT), 0)

            def _on_lang(e, c=code):
                if e.get_code() == lv.EVENT.CLICKED:
                    self._set_lang(c)
            row.add_event(_on_lang, lv.EVENT.CLICKED, None)

    def _set_lang(self, code):
        ctx = self.ctx
        if ctx.lang.lang == code:
            return
        ctx.lang.switch(code)
        ctx.config.set('lang', code)
        ctx.config.save()
        event_bus.emit('lang_changed')
        # 刷新自身
        self._refresh_texts()
        self._right_panel.clean()
        self._render_tab(self._active_item)

    # ── 关于内容 ──────────────────────────────────────

    def _render_about(self):
        ctx = self.ctx
        lang = ctx.lang
        container = self._right_panel

        rows = [
            ("settings.about_app_name", lang.t("settings.about_app_name_val")),
            ("settings.about_version",  lang.t("settings.about_version_val")),
            ("settings.about_platform", lang.t("settings.about_platform_val")),
            ("settings.about_display",  lang.t("settings.about_display_val")),
            ("settings.about_sdk",      lang.t("settings.about_sdk_val")),
            ("settings.about_author",   lang.t("settings.about_author_val")),
        ]
        for i, (key, val) in enumerate(rows):
            row = lv.obj(container)
            row.set_size(lv.pct(100), ABOUT_ROW_H)
            row.align(lv.ALIGN.TOP_MID, 0, i * (ABOUT_ROW_H + 4))
            row.set_style_bg_color(lv.color_hex(0x1A1A1A), 0)
            row.set_style_bg_opa(255, 0)
            row.set_style_border_width(0, 0)
            row.set_style_pad_hor(12, 0)
            row.set_style_pad_ver(0, 0)
            row.clear_flag(lv.obj.FLAG.SCROLLABLE)

            lbl_key = lv.label(row)
            lbl_key.set_text(lang.t(key))
            lbl_key.align(lv.ALIGN.LEFT_MID, 0, 0)
            lbl_key.add_style(make_back_bar_text_style(fonts.body), 0)
            lbl_key.set_style_text_color(lv.color_hex(Colors.TEXT_DIM), 0)

            lbl_val = lv.label(row)
            lbl_val.set_text(val)
            lbl_val.align(lv.ALIGN.RIGHT_MID, 0, 0)
            lbl_val.add_style(make_back_bar_text_style(fonts.body), 0)

    # ── 文字刷新 ──────────────────────────────────────

    def _refresh_texts(self):
        """语言切换后刷新左栏文字"""
        lang = self.ctx.lang
        keys = {"language": "settings.tab_language", "about": "settings.tab_about"}
        for row, iid in self._rows:
            # 找到行中的 name label（第二个子对象，第一个是图标）
            # LVGL v8 没有方便的 child 遍历，此处简单重建左栏
            pass
        # 简化：重建整个左栏
        self._left_panel.clean()
        self._rows = []
        items = [
            ("language", "settings.tab_language"),
            ("about",    "settings.tab_about"),
        ]
        for i, (item_id, name_key) in enumerate(items):
            self._build_left_row(i, item_id, name_key)

    # 兼容旧引用
    def _render_tab(self, tab_id):
        self._render_right(tab_id)

    # ── 销毁 ──────────────────────────────────────

    def _destroy_ui(self):
        for obj in (self._left_panel, self._right_panel):
            if obj is not None:
                try:
                    obj.delete()
                except Exception:
                    pass
        self._left_panel = None
        self._right_panel = None
        self._rows = []
        self._icon_cache = {}
        self._screen = None
```

- [ ] **Step 2: 删除不再需要的旧 Tab 相关代码**

确认新文件中不包含任何 `_tab_bar`、`_tab_btns`、`_tab_content`、`_switch_tab`、`_style_tab_btn` 等旧 Tab 式变量和方法。上面的完整代码已全部替换。

- [ ] **Step 3: Commit**

```bash
git add scripts/settings/app.py
git commit -m "feat(settings): 重写为左右分栏布局（iOS 风格）"
```

---

### Task 4: 板端验证

**Files:** 无代码变更

- [ ] **Step 1: 确认需拷贝到板端的文件列表**

```
scripts/settings/app.py
ui/back_bar.py
resource/i18n/zh_CN.json
resource/i18n/en_US.json
resource/font/font_body_18.bin
resource/font/font_title_50.bin
resource/font/font_caption_14.bin
```

图标文件（`settings_icon/back.png`、`Language.png`、`about.png`）已在板端，无需重新拷贝。

- [ ] **Step 2: 板端验证清单**

1. 从主菜单点击"设置"卡片 → 进入设置页
2. 返回栏：左侧显示 back.png 图标，标题"设置"
3. 左栏：显示"语言"行（带图标）和"关于"行（带图标），默认选中"语言"
4. 右栏：显示中文/English 两行，当前语言高亮
5. 点击"English" → 语言切换 → 左栏标题刷新为英文、右栏刷新
6. 点击"关于"行 → 右栏切换为设备信息列表
7. 点击返回栏 → 回到主菜单
8. 中文字符全部正常显示（无方块/空白）

- [ ] **Step 3: 修复板端问题（如有）**

根据验证结果修复发现的问题，提交修复 commit。

---

## 自审

1. **Spec 覆盖**：布局重设计 ✅（Task 3）、返回按钮图标 ✅（Task 2）、中文字体缺失 ✅（Task 1）
2. **占位符扫描**：无 TBD/TODO
3. **类型一致性**：`BackBar.__init__` 新增 `icon_path` 参数有默认值，`ScriptRunner` 中调用 `BackBar(title, on_back=...)` 无需改动（向后兼容）
