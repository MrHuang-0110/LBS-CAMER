# 通用脚本模板设计（基础框架 + AI 插件槽）

- **日期**: 2026-06-23
- **状态**: 设计已确认，待写实施计划
- **作者**: brainstorming 产出

## 1. 背景与动机

CamerAi 采用 reset 切换架构（main.py 启动器 + 每进程独立 init）。当前存在两个问题：

### 1.1 架构不匹配导致 camera/settings 启动不了

- `scripts/camera/app.py`、`scripts/settings/app.py` 是为**旧的同进程常驻架构**写的：`BaseScript` 子类 + `on_enter/on_frame/on_exit` 生命周期 + 依赖 `ctx.lcd`（`ctx.lcd.get_sensor()`/`ensure_sensor_running()`/`clear_framebuffers()`）。
- 但 reset 框架的 `runtime`（`AppRuntime`）**没有 `lcd` 属性**，main.py 调 `mod.run(runtime)`，而这两个 app.py **没有 `run()` 函数** → "script has no run()" → 直接退出。
- main.py 从不实例化 `ScriptRunner`/`ScriptContext`，所以旧生命周期从未被调用，是孤儿代码。
- 只有 `face_detect/app.py` 适配了 reset 框架（有 `run(runtime)`），能进入。

### 1.2 face_detect 帧循环卡死（基础框架与 AI 耦合）

- face_detect fc~20-35 卡死，根因：`DISP_RENDER_MODE.FULL` 下主线程每帧整屏 DMA 刷 OSD2 + AI 线程每帧 `show_image(OSD1)`，**双线程双写者抢 display DMA**。
- 06-18 的"稳定"是靠砍掉 buzzer/touch/fonts/services 堆出来的，根因未除，Step 7 加回 GPIO 接入后复发。
- **本质问题**：AI（NPU 推理/kmodel/内存压力/AI 线程）和基础框架（启动/退出/摄像/display/触摸）混在一个 app.py、一个进程循环里，AI 出问题直接冲垮基础框架。

## 2. 目标

做一个稳定的**基础框架脚本模板**，作为后续所有 AI 脚本（含将来重写的 face_detect）的复制起点。从结构上排除基础框架问题，满足：

1. 后续扩展新脚本（加 AI）**方便**。
2. 基础框架（启动/退出/摄像/顶底栏/触摸）**不被 AI 影响**。

## 3. 核心设计：基础框架 + AI 插件槽（on_frame 钩子）

### 3.1 思想

模板（基础框架）定义一个清晰的"每帧钩子" `on_frame(img)`。基础框架负责：启动（init_app）、顶底栏 UI、摄像头取帧推 OSD1、触摸返回、退出清理。**AI 脚本不重写这些骨架，只实现 `on_frame(img)`**——拿到帧做检测/识别，把结果画到 img 上或返回数据，基础框架负责 `show_image`。

```
基础框架 run(runtime) 主循环（稳定，不变）:
    while not exit_flag:
        os.exitpoint()
        img = sensor.snapshot(chn=chn0)
        try:
            on_frame(img)          # ← AI 挂载点（模板默认空实现）
        except Exception as e:
            print("[frame] on_frame error: %s" % e)   # AI 异常不杀循环
        Display.show_image(img, 0, 0, Display.LAYER_OSD1)
        time.sleep_ms(lv.task_handler())
        fc += 1
        if fc % 30 == 0:
            print("[template] fc=%d" % fc)
```

### 3.2 为什么这个结构满足两个目标

- **基础框架不被 AI 影响**：AI 的 `on_frame` 抛异常被 try/except 接住、跳过该帧、继续循环。AI 卡死自己的推理，基础框架的取帧/推帧/触摸照样转。骨架代码固定，AI 碰不到。
- **扩展方便**：新 AI 脚本 = 复制模板 app.py + 填 `on_frame`。基础框架骨架零改动。
- **单线程结构避开双线程竞争**：模板纯摄像头无 AI，单线程主循环（snapshot→on_frame→show_image→task_handler 串行），display 只有一个写者（主循环），**从结构上消除 face_detect 的双线程双写者竞争**。

### 3.3 render mode：PARTIAL 为可选优化，非硬依赖

- 单线程结构已避开 face_detect 的双线程竞争，PARTIAL 不再是必须。
- **先上 PARTIAL 试**（init_app 对 stream category 用 PARTIAL）：顶底栏静态、预览区透明，LVGL 只在按钮点击瞬间刷小脏区，OSD2 DMA 传输量从"每帧整屏"降到"偶发小块"。
- **板端若 PARTIAL 的 flush_cb 异常，退回 FULL**：单线程下 FULL 也比 face_detect 的双线程 FULL 稳得多（只有一个写者）。
- PARTIAL 的 `_flush_cb` 兼容性是**实施时需板端验证的唯一高风险点**，有明确退路（退 FULL）。

## 4. 范围

### 4.1 做

- 新建 category `_template`：`scripts/_template/{app.py, manifest.json}` + `config/categories.json` 加项 + 主菜单自动生成卡片。
- `run(runtime)` 入口：顶栏（返回钮 + 硬编码标题"基础框架"）+ 空底栏 + 透明预览区 + OSD1 摄像头画面 + 空 `on_frame` 钩子。
- sensor 单通道 chn0 VGA/RGB888（走 init_app）。
- LVGL `DISP_RENDER_MODE.PARTIAL`（stream category），menu/page 保持 FULL。
- 触摸返回：点返回钮 → 回调设标志 → 主循环退出 → `run()` 末尾删 UI → main.py 调 `runtime.cleanup()` → reset 回菜单。
- 帧计数降频打印（`fc % 30 == 0`）。

### 4.2 不做

- 不碰 face_detect（搁置，其 main.py 特殊分支保留）。
- 不碰 camera/settings 旧代码（孤儿，以后再说）。
- 不加任何 AI/NPU/kmodel（模板 `on_frame` 空实现）。
- 底栏无业务按钮（纯空栏，只验证渲染）。
- 不抽象基类（YAGNI，等第二个 stream 脚本再说）。
- 不做拍照/录像/图库。

## 5. 架构与调用链

```
main.py main()
  └─ _read_next_script() → "_template"
  └─ run_script("_template")
       ├─ runtime = AppRuntime()
       ├─ runtime.init_app("_template", fpioa)    ← 框架统一路径（非 face_detect 走此分支）
       │    ├─ Display.init(osd_num=2)
       │    ├─ _config_sensor([(chn0, VGA, RGB888)])      ← 模板单通道
       │    ├─ MediaManager.init()
       │    ├─ _init_backlight
       │    ├─ lv.init() + _lvgl_init(render_mode=PARTIAL)  ← ui_mode=stream → PARTIAL
       │    ├─ _init_touch()                               ← 模板需要触摸
       │    ├─ fonts.load_all()
       │    ├─ _init_services(buzzer 等)
       │    └─ sensor.run()
       ├─ _load_script("_template") → scripts._template.app
       ├─ mod.run(runtime)        ← 模板主循环（含 on_frame 钩子）
       ├─ if category_id != "face_detect": runtime.cleanup()  ← 新增：统一 deinit（face_detect 跳过）
       ├─ _clear_next_script()
       └─ machine.reset()         ← 回主菜单
```
> 注：第 5 节调用链为简化示意，cleanup 的精确条件（跳过 face_detect）以第 7.3 节为准。

### 5.1 关键架构决策

1. **模板走 init_app 统一路径**：main.py 的 `run_script` 对 `_template` 正常调 `runtime.init_app`。face_detect 的特殊分支（跳过 init_app 走裸跑）保留不动（face_detect 搁置）。
2. **render mode 按 ui_mode 区分**：`_lvgl_init` 加 `render_mode` 参数。`init_app` 内部用 ConfigManager 查 category 的 `ui_mode`：`stream` → PARTIAL，其他 → FULL。`init_menu` 调 `_lvgl_init()` 默认 FULL。
3. **sensor 通道按 category**：`_channels_for` 加 `elif category_id == "_template": pass`（单通道 chn0，复用默认）。
4. **media 生命周期由 runtime 管**：模板 `run()` 不自己 media_init/lvgl_init，直接用 `runtime.sensor`/`runtime.display`/`runtime.lv_disp`。退出时硬件 deinit 交给 main.py 统一调 `runtime.cleanup()`。

### 5.2 单写者分析（为什么结构上不卡）

- **OSD1**：模板主循环每帧 `runtime.sensor.snapshot(chn=chn0)` → `Display.show_image(img, layer=OSD1)`。单一写者（主循环），无 AI 线程。
- **OSD2**：PARTIAL 模式 `task_handler()` 只在有脏区时刷小块。顶底栏静态，预览区 `bg_opa=0` 透明不刷。退 FULL 时也是单写者（无 AI 线程并发）。
- **对比 face_detect**：FULL + AI 线程并发 OSD1 → 双写者持续竞争。模板单线程 → 竞争消除。

## 6. 模板 app.py 内部结构

### 6.1 布局常量（对齐 camera/app.py 尺寸）

```python
BAR_H = 52              # 顶/底栏高度
PREVIEW_Y = BAR_H       # 预览区起始 Y
PREVIEW_H = 376         # 480 - BAR_H*2
BAR_BG = 0x1A1A1A       # 栏背景色
```

### 6.2 run(runtime) 结构

```python
def run(runtime):
    exit_flag = [False]          # 闭包可变容器，供触摸回调设
    _build_ui(runtime, exit_flag)    # 顶栏(返回钮+标题) + 空底栏 + 透明预览区
    fc = 0
    while not exit_flag[0]:
        os.exitpoint()
        img = runtime.sensor.snapshot(chn=CAM_CHN_ID_0)
        try:
            on_frame(img)        # AI 钩子（模板空实现）
        except Exception as e:
            print("[frame] on_frame error: %s" % e)
        Display.show_image(img, 0, 0, Display.LAYER_OSD1)
        time.sleep_ms(lv.task_handler())
        fc += 1
        if fc % 30 == 0:
            print("[template] fc=%d" % fc)
    _destroy_ui()
    # 硬件 deinit 由 main.py 调 runtime.cleanup()，run() 只清自己的 UI 对象


def on_frame(img):
    """AI 钩子 — 模板空实现。后续 AI 脚本复制模板后填此函数。
    异常由主循环 try/except 接住，不杀基础框架循环。"""
    pass
```

### 6.3 UI 构建 `_build_ui(runtime, exit_flag)`

- 在 `lv.scr_act()` 上建（用 `runtime.lv_disp` 已配好的 display）。
- 屏幕背景 `bg_opa=0`（让 OSD1 摄像头画面透出）。
- **顶栏**：`lv.obj` 全宽×BAR_H，`BAR_BG` 底。左边返回钮（48×48 透明点击区 + back 图标，从 `icon_cache.get_back_icon()`）。中间标题"基础框架"（硬编码，`fonts.body`）。
- **返回钮回调**：`EVENT.CLICKED` → `exit_flag[0] = True`（只设标志，不做重操作）。
- **预览区**：`lv.obj` 全宽×PREVIEW_H，`bg_opa=0` 透明，透出 OSD1。
- **底栏**：`lv.obj` 全宽×BAR_H，`BAR_BG` 底，**无任何按钮**（纯空栏）。

### 6.4 退出清理 `_destroy_ui()`

- 删顶栏/底栏/预览区 LVGL 对象（各包 try/except）。
- 恢复屏幕 `bg_opa=255`（主菜单需不透明背景）。
- **不碰 runtime 持有的硬件**（sensor/display/LVGL/MediaManager），那些由 main.py 调 `runtime.cleanup()` 统一 deinit。职责清晰：谁 init 谁 deinit。

### 6.5 触摸依赖

`runtime.touch`（init_app 的 `_init_touch` 已创建并注册 indev）。`task_handler()` 处理触摸事件 → 触发返回钮 CLICKED → 设 `exit_flag`。模板不直接碰 touch 对象。

## 7. 框架改动点

### 7.1 `app_runtime._lvgl_init` 加 render_mode 参数

```python
def _lvgl_init(self, render_mode=lv.DISP_RENDER_MODE.FULL):
    ...
    self.lv_disp.set_draw_buffers(
        self.draw_buf_1.bytearray(), self.draw_buf_2.bytearray(),
        self.draw_buf_1.size(), render_mode)
```
- `init_menu` 调 `_lvgl_init()`（默认 FULL）。
- `init_app` 调 `_lvgl_init(render_mode)`，render_mode 由 category 的 `ui_mode` 决定（ConfigManager 查）：`stream` → PARTIAL，否则 FULL。

### 7.2 `app_runtime._channels_for` 加模板通道

```python
def _channels_for(self, category_id):
    chs = [(CAM_CHN_ID_0, Sensor.VGA, Sensor.RGB888)]
    if category_id == "face_detect":
        chs.append((CAM_CHN_ID_2, Sensor.XGA, Sensor.RGBP888))
    elif category_id == "camera":
        chs.append((CAM_CHN_ID_1, Sensor.SXGAM, Sensor.RGB565))
    elif category_id == "_template":     # 模板纯显示单通道
        pass
    return chs
```

### 7.3 `main.py run_script` 加 cleanup + 保留 face_detect 分支

```python
def run_script(category_id):
    ...
    runtime = AppRuntime()
    if category_id == "face_detect":   # face_detect 搁置分支保留
        runtime.fpioa = fpioa
    else:
        runtime.init_app(category_id, fpioa)
    ...
    if mod is not None and hasattr(mod, "run"):
        try:
            mod.run(runtime)
        except Exception as e:
            ...
    if category_id != "face_detect":   # face_detect 自己管 media，不调 cleanup
        runtime.cleanup()              # 新增：统一 deinit
    _clear_next_script()
    machine.reset()
```

### 7.4 注册 _template category

- `config/categories.json` 加一项：`id="_template"`, `script="_template"`, `icon`（占位，可复用 camera 图标路径）, `enabled=true`, `order`（排末尾或合适位置）, `ui_mode="stream"`。
  > 字段名以现有 categories.json 实际结构为准（实施时核对 config_manager 读取的字段：id/script/icon/enabled/order 等）。
- `scripts/_template/manifest.json`：`id="_template"`, `ui_mode="stream"`, `models=[]`, `enabled=true`（对齐 camera/face_detect 的 manifest 结构）。
- 主菜单 `get_enabled_categories()` 自动读到 → 生成卡片，无需改 main_menu.py。

### 7.5 PARTIAL flush_cb 兼容性（高风险点，板端验证）

- 现有 `_flush_cb` 用 `flush_is_last()` + 整缓冲 `show_image(OSD2)`。PARTIAL 模式下 `px_map` 是局部脏区，地址比较逻辑可能不适用。
- **实施时板端验证**：PARTIAL + 现有 `_flush_cb` 能否正常显示。
- **退路**：若异常，`init_app` 对 stream 退回 FULL（单线程下 FULL 也稳，因只有一个写者）。或单独适配 PARTIAL 的 flush_cb。

## 8. 验收标准

1. **能进**：连续 5 次硬断电后从主菜单点模板卡片，5 次都成功进入（非"偶尔进得去"）。
2. **画面稳**：摄像头画面持续刷新，连续跑 `fc ≥ 300` 不卡死（对比 face_detect fc~20-35，数量级提升）。
3. **触摸返回**：点顶栏返回钮，稳定退出回主菜单，连续 5 次进出循环不卡。
4. **退出干净**：退出后能再次从菜单点进（不 wedged），不需每次硬断电。
5. **帧率可接受**：画面目测流畅（不强求量化 fps，30fps 下 fc≥300≈10 秒，肉眼可确认稳定）。
6. **AI 隔离验证**（推荐）：临时在 `on_frame` 填一个会抛异常的实现（如 `raise Exception("test")`），确认异常被 try/except 接住、循环不中断、画面继续刷新——这是证明"基础框架不被 AI 影响"核心设计目标的关键验证。

## 9. 后续扩展路径（AI 脚本如何基于模板）

新 AI 脚本（如重写 face_detect）流程：
1. 复制 `scripts/_template/app.py` → `scripts/<new_ai>/app.py`。
2. `config/categories.json` 加新 category 项 + `manifest.json`。
3. 填 `on_frame(img)`：加载 kmodel（在 `run()` 主线程、AI 推理前，坑#18/#19）、做检测/识别、把结果画到 `img` 上。
4. 若 NPU 推理重，`on_frame` 内可起 AI 线程——但那是 AI 脚本自己的事，基础框架骨架不变。
5. AI 异常由主循环 try/except 隔离，不杀基础框架。

**关键**：基础框架骨架（run 主循环/顶底栏/触摸退出/退出清理）是固定的、验证过的，AI 脚本只动 `on_frame`，碰不到骨架 → 基础框架不被 AI 影响。

## 10. 不确定性与风险

| 风险 | 影响 | 对策 |
|------|------|------|
| PARTIAL flush_cb 不兼容 | OSD2 显示异常 | 退 FULL（单线程下稳）；或适配 flush_cb |
| init_app 全套（含 touch）引入污染 | 模板卡死 | 模板无 AI/无 NPU，资源压力小；逐项验证 touch/buzzer 是否影响，若影响则模板 init_app 内按需跳过 |
| cleanup 与 face_detect 冲突 | face_detect 退出异常 | cleanup 仅对非 face_detect 调用（face_detect 搁置，自己管 media） |
| 模板 category 图标缺失 | 卡片无图标 | 复用 camera 图标占位 |

## 11. 与现有记忆的关联

- 关联 [[camerai-face-detect-stable-config]]：face_detect 的卡死根因（FULL 双写竞争）与本设计的结构解法（单线程 + on_frame 隔离）。
- 关联 [[camerai-reset-switch-arch]]：模板走 reset 框架的 init_app 统一路径。
- 关联 [[camerai-k230-pitfalls]]：坑#2（文件 I/O 时序）、#18（kmodel 主线程加载）、#19（mobile kmodel）在后续 AI 扩展时适用，模板阶段无 AI 不触发。
