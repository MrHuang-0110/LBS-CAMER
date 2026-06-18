# 设置 APP 实施计划

> **设计依据**：docs/superpowers/specs/2026-06-12-settings-app-design.md  
> **日期**：2026-06-12  
> **预计步骤**：8 步，每步独立可验证

---

## Step 1 — i18n 补键（zh_CN + en_US）

**文件**：`resource/i18n/zh_CN.json`、`resource/i18n/en_US.json`

在 `settings` 段补充 6 个「关于」区 label 键：

```
settings.about_product  产品名称 / Product
settings.about_version  版本 / Version
settings.about_model    设备型号 / Model
settings.about_canmv    CanMV 版本 / CanMV
settings.about_memory   可用内存 / Free RAM
settings.about_storage  存储剩余 / Storage
```

**验证**：`python -c "import json; json.load(open('resource/i18n/zh_CN.json'))"` 两个文件均无报错

---

## Step 2 — config/app.json 加 version 字段

**文件**：`config/app.json`

新增 `"version": "v0.1.0"`。

**验证**：`python -c "import json; d=json.load(open('config/app.json')); assert d['version']=='v0.1.0'"`

---

## Step 3 — BackBar 加 set_title 方法

**文件**：`ui/back_bar.py`

在 `BackBar` 类末尾新增：

```python
def set_title(self, text):
    """更新返回栏标题文字（语言切换后调用）"""
    if self._label is not None:
        self._label.set_text(text)
```

**验证**：`python -m py_compile ui/back_bar.py`

---

## Step 4 — ScriptRunner 监听 lang_changed

**文件**：`core/script_runner.py`

在 `launch()` 挂完 `BackBar` 之后注册一次性监听（用 lambda 捕获 `self`），语言变化时更新返回栏标题：

```python
# 在挂载 back_bar 之后添加
from core.event_bus import event_bus
def _on_lang_changed():
    if self._back_bar is not None:
        cat = self.config.get_category(script_id)
        self._back_bar.set_title(self.lang.t(cat.get('name_key', script_id)))
event_bus.on('lang_changed', _on_lang_changed)
self._lang_changed_cb = _on_lang_changed   # 保存引用，exit 时 off
```

在 `exit()` 里清理监听：

```python
if hasattr(self, '_lang_changed_cb'):
    event_bus.off('lang_changed', self._lang_changed_cb)
    self._lang_changed_cb = None
```

**验证**：`python -m py_compile core/script_runner.py`

---

## Step 5 — main.py 监听 lang_changed

**文件**：`main.py`

在 `menu` 构建完成之后（`menu.preload_icons()` 之前或之后均可）注册：

```python
event_bus.on('lang_changed', lambda: menu.refresh_texts())
```

**验证**：`python -m py_compile main.py`

---

## Step 6 — manifest.json

**文件**：`scripts/settings/manifest.json`（新建）

对齐 camera manifest 格式：

```json
{
  "id": "settings",
  "version": "1.0.0",
  "name_key": "category.settings",
  "desc_key": "category.settings_desc",
  "entry_icon": "/sdcard/CamerAi/resource/icons/menu_icon/setting.png",
  "icon_dir": "/sdcard/CamerAi/resource/icons/settings_icon/",
  "models": [],
  "ui_mode": "page",
  "enabled": true,
  "order": 1
}
```

**验证**：`python -c "import json; json.load(open('scripts/settings/manifest.json'))"`

---

## Step 7 — SettingsApp 主体（scripts/settings/app.py）

**文件**：`scripts/settings/app.py`（新建）

结构如下（伪代码级，编码时展开）：

```
SettingsApp(BaseScript)
  SCRIPT_ID = "settings"

  on_enter(ctx):
    self.ctx = ctx（调 super）
    self._root = 根容器（lv.obj，y=40，640×440，纯黑底，纵向滚动）
    _build_language_section()   # 语言区块
    _build_about_section()      # 关于区块
    self._root.move_foreground()

  _build_language_section():
    区块标题 label（lang.t("settings.tab_language")，TEXT_DIM）→ 挂 self._lbl_lang_title
    两张语言卡（280×72，#222222，圆角 14）
      - 卡内文字：lang.t("settings.lang_zh") / lang.t("settings.lang_en") → 挂 self._lbl_zh / self._lbl_en
      - 点击事件 → _on_lang_select("zh_CN") / _on_lang_select("en_US")
    _update_lang_cards()   # 按当前语言设初始发光边框

  _build_about_section():
    区块标题 label → 挂 self._lbl_about_title
    关于卡（592×(6*44)，#222222，圆角 14）
      - 6 行，每行：左 label（TEXT_DIM）+ 右 value（TEXT）
      - 左 label 键：about_product/version/model/canmv/memory/storage → 挂 self._about_labels[]
      - 右 value：静态读取（os.uname, gc.mem_free, statvfs，各自 try/except）

  _on_lang_select(new_lang):
    若 new_lang == 当前语言 → return
    ctx.buzzer.beep(50)
    ctx.lang.switch(new_lang)
    ctx.config.set("lang", new_lang) + ctx.config.save()
    _refresh_texts()        # 增量刷新本页
    _update_lang_cards()    # 更新发光边框
    event_bus.emit('lang_changed')

  _refresh_texts():
    用 ctx.lang.t() 逐一 set_text 所有挂在 self 上的翻译 label
    （self._lbl_lang_title, self._lbl_about_title, self._lbl_zh, self._lbl_en, self._about_labels[]）

  _update_lang_cards():
    当前语言卡：3px GLOW 边框 + shadow
    另一张：无边框

  on_exit():
    try: self._root.delete()
    except: pass
    把所有 label 引用置 None
    super().on_exit()
```

**验证**：`python -m py_compile scripts/settings/app.py`（语法检查）

---

## Step 8 — 文案键完整性检查 + 提交

手工或脚本核对 `app.py` 里所有 `lang.t("...")` 的键在两个 i18n JSON 里均存在，然后一次性提交所有改动：

```
git add resource/i18n/ config/app.json ui/back_bar.py \
        core/script_runner.py main.py \
        scripts/settings/
git commit -m "feat: 设置APP — 语言切换+关于区 (Phase 2 首期)"
```

---

## 依赖顺序说明

```
Step 1-2（数据层）→ Step 3-5（基础设施改动）→ Step 6-7（APP 本体）→ Step 8（验证+提交）
```

Step 3-5 互相独立，可并行。Step 7 依赖 Step 1-5 全部完成（需要 i18n 键、back_bar.set_title、event_bus 订阅就位）。

---

## 板端验证顺序（Step 8 后）

按设计文档 §6.2 清单逐项验证，优先验证：
1. 进入设置页不黑屏（基本可达）
2. 语言切换立即生效（核心功能）
3. 返回主菜单卡片文字已更新（跨页事件链路）
