# 设置 APP 设计文档（scripts/settings/）

> **日期**：2026-06-12
> **状态**：设计已确认，待实施
> **所属阶段**：Phase 2（设置与语言）
> **平台**：正点原子 K230D BOX · MicroPython + LVGL v8 · 横屏 640×480

---

## 1. 目标与范围

实现 CamerAi 主菜单「设置」类目对应的 APP。首版范围（用户确认 = A 项，聚焦）：

- **语言切换**：中文 ⇄ English，点击即时整页刷新并持久化
- **关于**：纯展示，含基础信息 + 运行信息

**明确不做（YAGNI）**：蜂鸣器开关、背光亮度、恢复默认设置、多级页面。这些等真正需要时再加。

---

## 2. 整体架构

设置 APP 是一个 `ui_mode: "page"` 脚本，继承 `BaseScript`，由 `ScriptRunner` 调度生命周期。

**page 模式运行特征**（区别于相机的 stream 模式）：
- 全程在 LVGL 显示模式下运行，**不切相机模式、不 deinit LVGL**
- `ScriptRunner` 在 `on_enter` **之前**自动挂载顶部 40px 返回栏（`BackBar`）
- 运行循环每 5ms 调一次 `lv.task_handler()`，不调用 `on_frame`
- page 模式**不隐藏主菜单**：主菜单 LVGL 对象仍在底层 → 设置页根容器必须不透明地盖住它

### 2.1 文件清单

| 文件 | 动作 | 说明 |
|------|------|------|
| `scripts/settings/app.py` | 新建 | `SettingsApp(BaseScript)`，`on_enter` 构建 UI，`on_exit` 销毁 |
| `scripts/settings/manifest.json` | 新建 | 对齐 camera manifest 格式，`ui_mode: "page"` |
| `config/app.json` | 改 | 新增 `"version": "v0.1.0"` 字段 |
| `core/script_runner.py` | 小改 | 监听 `lang_changed` 事件 → 更新返回栏标题 |
| `ui/back_bar.py` | 小改 | 新增 `set_title(text)` 方法 |
| `main.py` | 小改 | 监听 `lang_changed` 事件 → `menu.refresh_texts()` |
| `resource/i18n/zh_CN.json` | 小改 | 补「关于」区文案键 |
| `resource/i18n/en_US.json` | 小改 | 补「关于」区文案键 |

### 2.2 职责边界

- **`SettingsApp`**：只管自己的内容区 UI（y=40 以下的 640×440）和设置逻辑。不碰返回栏（runner 管），不直接碰主菜单（通过事件解耦）。
- **语言切换广播**：通过 `event_bus.emit('lang_changed')`，返回栏与主菜单各自订阅响应，互不直接依赖。`event_bus` 是全局单例，无需经 `ScriptContext` 传递。

---

## 3. 界面布局

返回栏占顶部 40px（runner 挂载，标题 = `lang.t("category.settings")`，← 返回）。
设置内容区根容器定位 **y=40、尺寸 640×440**，纯黑底 `#000000`，纵向可滚动，物理上不与返回栏重叠（规避 z-order 陷阱）。

```
┌──────────────────────────────────────────────┐  ← 返回栏 (runner, 40px)
│  ←            设置                             │
├──────────────────────────────────────────────┤  ← 设置内容区 (640×440, 纯黑, 纵滑)
│                                                │
│   语言 / Language                              │  ← 区块标题 (TEXT_DIM #9E9E9E, body)
│   ┌────────────────────┐ ┌──────────────────┐ │
│   │   中文        ✓     │ │   English        │ │  ← 语言卡 (#222222, 选中=发光边框)
│   └────────────────────┘ └──────────────────┘ │
│                                                │
│   关于 / About                                 │  ← 区块标题
│   ┌──────────────────────────────────────────┐│
│   │  产品名称              CamerAi            ││  ← 信息行 (label 左 + value 右)
│   │  版本                  v0.1.0             ││
│   │  设备型号              K230D BOX          ││
│   │  CanMV 版本            <os.uname>         ││
│   │  可用内存              <mem_free> KB      ││
│   │  存储剩余              <statvfs> MB       ││
│   └──────────────────────────────────────────┘│
│                                                │
└──────────────────────────────────────────────┘
```

### 3.1 几何参数（初定，实现时可微调）

- 区块左右边距 24px；区块标题与卡片间距 8px；区块之间间距 24px
- **语言卡**：两张并排，各约 280×72，圆角 14px，底色 `#222222`；卡内文字纯白居中，选中卡带 ✓
- **关于卡**：单张通栏卡（约 592 宽，高度自适应），圆角 14px，底色 `#222222`；内部 6 行，每行左 label（`TEXT_DIM #9E9E9E`）+ 右 value（`TEXT #FFFFFF`），行高约 44px
- 字体：区块标题与卡内文字均用 `fonts.body`，全部走 `font=None` 判空兜底

### 3.2 选中态（发光边框，用户明确要求保留）

当前语言卡加淡蓝发光边框：`GLOW #6DB8FF` 3px border + shadow（复用主菜单选中卡样式规范）。非选中卡无边框。

---

## 4. 数据流与交互

### 4.1 进入设置页

1. 主菜单点「设置」→ `runner.launch("settings")` → 蜂鸣 → 加载 `SettingsApp`
2. runner 挂返回栏（标题 `lang.t("category.settings")`），不切显示模式
3. `on_enter(ctx)`：从 `ctx.lang` / `ctx.config` 读当前语言与版本，构建内容区 UI；把所有需翻译的 label 引用挂到 `self`（**方案 2 增量刷新**，与主菜单 `refresh_texts()` 模式一致）

### 4.2 语言切换（点中文 / English 卡）

1. 蜂鸣 `beep(50)`
2. 若点的就是当前语言 → 无操作返回
3. 否则：
   - `ctx.lang.switch(new_lang)`（重载 i18n JSON）
   - `ctx.config.set("lang", new_lang)` + `ctx.config.save()`（持久化）
4. **本页增量刷新**：遍历 self 上的 label 引用，用新 `lang.t()` 重设文字；更新两张语言卡的选中发光边框（移到新语言卡）
5. `event_bus.emit('lang_changed')` 广播

### 4.3 事件响应（解耦刷新）

- **返回栏**：`ScriptRunner` 监听 `lang_changed` → 若 `self._back_bar` 存在，调用新增的 `BackBar.set_title()` 用新语言更新标题
- **主菜单**：`main.py` 监听 `lang_changed` → `menu.refresh_texts()`。无论在设置页当场还是返回后，主菜单卡片文字都是新语言

> **现状补充**：当前 [main.py](../../../main.py) 的 `runner_exited` 回调只 `menu.show()`，不刷新文字。本设计通过 `lang_changed` 事件解决，无需依赖退出回调。

### 4.4 退出设置页

1. 点返回栏 ← → `ctx.request_exit()` → 运行循环 break
2. `on_exit()`：`delete()` 内容区根容器（连带所有子控件），清空 self 上的 label 引用置 `None`
3. runner 移除返回栏 → `gc.collect()` → 回主菜单 → `runner_exited` → `menu.show()`

### 4.5 关于区动态值（on_enter 时读一次，静态展示）

| 行项 | 来源 | 兜底 |
|------|------|------|
| 产品名称 | `lang.t("common.app_name")` → `CamerAi` | — |
| 版本 | `config.get("version", "v0.1.0")` | `v0.1.0` |
| 设备型号 | 固定字符串 `K230D BOX` | — |
| CanMV 版本 | `os.uname().release`（或 `.version`） | `Unknown` |
| 可用内存 | `gc.mem_free() // 1024` KB | `--` |
| 存储剩余 | `os.statvfs("/sdcard")`：`f_bavail * f_frsize // (1024*1024)` MB | `--` |

---

## 5. 错误处理与边界情况

K230 + MicroPython 环境脆弱，目标：任一步失败都不崩、不卡死。

- **(a) 字体兜底**：`fonts.body` 可能为 `None`。所有 `set_style_text_font` 前判空，`None` 则跳过（用 LVGL 内置字体）。与主菜单 / theme 现有模式一致。
- **(b) 关于区取值容错**：`os.uname()` / `os.statvfs()` / `gc.mem_free()` 各自独立 `try/except`，任一失败该行显示兜底值，不影响其他行、不抛上层。
- **(c) 语言切换容错**：`lang.switch()` 内部已有 load 失败兜底（回退英文最小集）。`config.save()` 失败只打印日志不崩（切换已在内存生效，仅未持久化）。
- **(d) 退出清理容错**：`on_exit` 的 `delete()` 包 `try/except`；label 引用统一置 `None`，避免悬空指针。退出时**不**额外 `gc.collect()`（runner 的 `exit()` 已统一回收；历史经验表明某些调用栈里手动 gc 会与显示路径冲突）。
- **(e) 文件 I/O 安全性**：设置页关于区取值全部来自内存 API，无文件读取、无图标加载。语言切换时由 `lang.switch()` 读 i18n JSON——发生在 task_handler 已稳定运行、用户主动触发的时刻，小 JSON 读取视为安全（与主菜单大图标 + DMA 抢占场景不同）。设置页**不涉及**主菜单那套"splash 前预读图标"约束。
- **(f) 重复进出**：每次 `on_enter` 重建、`on_exit` 彻底销毁，无残留状态；self 引用每次进入重新赋值。

---

## 6. 测试与验证

板端 MicroPython + LVGL，无 PC 端单测框架（LVGL/media 模块仅板上存在）。以板端手动清单为主 + PC 端静态检查。

### 6.1 PC 端（提交前）

- `python -m py_compile scripts/settings/app.py`（纯语法，不 import 板端模块）
- JSON 合法性：`config/app.json` + 两个 i18n JSON 用 `json.load` 验证可解析
- 文案键完整性：核对 `app.py` 用到的所有 `lang.t("...")` 键在 zh_CN / en_US 均存在

### 6.2 板端手动验证清单

1. 主菜单点「设置」→ 正常进入，返回栏标题「设置」，内容区显示语言区 + 关于区
2. 关于区 6 行信息正常显示（版本/型号/CanMV/内存/存储均有真实值，非兜底）
3. 当前语言卡有淡蓝发光边框，另一张无
4. 点「English」→ 蜂鸣 → 区块标题/卡片文字/关于 label/返回栏标题**全部立刻变英文**，发光边框移到 English 卡
5. 再点「中文」→ 全部变回中文
6. 点返回栏 ← → 回主菜单 → **主菜单卡片文字是切换后的语言**
7. 重新进设置页 → 语言选中态与上次一致（已持久化）
8. 反复进出设置页 5 次 → 无黑屏、无卡死、无内存崩溃
9. 内容区可纵向滑动（若内容超出 440px）

### 6.3 关键回归点

- 切换语言后返回主菜单不能黑屏（验证 `lang_changed` + `refresh_texts` 未引入主菜单 LVGL 异常）

---

## 7. i18n 文案键补充

`settings` 段已有：`tab_language` / `tab_about` / `lang_zh` / `lang_en`。需新增「关于」区行项 label 键（value 多为动态值或固定串，不入 i18n）：

| 键 | zh_CN | en_US |
|----|-------|-------|
| `settings.about_product` | 产品名称 | Product |
| `settings.about_version` | 版本 | Version |
| `settings.about_model` | 设备型号 | Model |
| `settings.about_canmv` | CanMV 版本 | CanMV |
| `settings.about_memory` | 可用内存 | Free RAM |
| `settings.about_storage` | 存储剩余 | Storage |

> 区块标题复用现有键：语言区用 `settings.tab_language`，关于区用 `settings.tab_about`。
