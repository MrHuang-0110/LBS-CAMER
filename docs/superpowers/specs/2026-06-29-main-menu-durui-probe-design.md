# DurUI 显示栈最小复刻对照实验设计

> 创建日期:2026-06-29
> 范围:新增独立实验入口 `main_menu_durui_probe.py`,不修改 reset 框架/`main.py`/`app_runtime.py`

## 背景

主菜单在滚动或挂机后 `gc.mem_free()` 持续下降,触发自动 GC 或主动 GC 后下一轮 `lv.task_handler()` 卡死。已连续排除以下方向,均未解决:

1. 去掉滚动 Python `lv.anim_t` 自定义动画
2. 主动 GC 移到 `lv.task_handler()` 返回后
3. `lv.style_t` 保活
4. LVGL event callback 保活
5. 主菜单显示链路对齐 DurUI(DIRECT + 无 sensor + 无 OSD2 + flush 不清缓冲) —— GC 后仍卡,且 DIRECT 引入黑屏

按 systematic-debugging,3+ 次局部修复失败需质疑架构、做干净二分。前提已确认:DurUI 不卡死版本跑在**同一块 K230 + 同一块 LCD**上。

## 目标

做一次最小复刻对照实验:用 DurUI 原样的显示栈,跑我们现有的 `MainMenu` 卡片 UI,看主动 GC 后是否还卡。

- 若不卡 → 问题在显示栈,`app_runtime.init_menu` 的显示链路是元凶,后续把主菜单换成这套栈。
- 若还卡 → 问题在 `MainMenu` UI 本身,显示栈不是元凶,回到 UI 内部找分配源。

这是干净的二分定位,不再逐项猜差异。

## 非目标

- 不改 `main.py`、`core/app_runtime.py`、`hw/lcd.py`、`hw/touch.py`。
- 不改 reset 切换架构,不改各识别脚本。
- 不实现开机 LOGO、子应用返回、`scr_load` 重建、USART 握手——这些是 DurUI 全貌,与本实验无关。
- 不追求"优化"或"对齐参数";复刻保真优先,逐行照搬 DurUI 显示栈。

## 架构

新增单文件 `main_menu_durui_probe.py`,自包含硬件初始化 + 主循环,无外部框架依赖:

```
FPIOA
 → LCD 类(逐行照搬 DurUI:Display.ST7701 + MediaManager.init + 背光)
 → Touch 类(逐行照搬 DurUI:TOUCH(0) + lv.indev + read_cb)
 → buzzer(逐行照搬 DurUI:PWM pin60)
 → lvgl_init(DIRECT + 两块 BGRA8888 draw_buf + 铺不透明黑 + flush_cb 单层 show_image 不指定 layer、不清缓冲)
 → 构造 MainMenu(复用 core/config_manager + core/lang)
 → preload_icons(沿用 ui/main_menu.MainMenu.preload_icons)
 → menu.show()
 → 主循环: while True: lv.task_handler(); diag(); time.sleep_ms(MENU_LV_TASK_SLEEP_MS)
```

不配 sensor。不申请 OSD2(`Display.init` 不传 `osd_num`)。不用 `app_runtime`。

## 组件设计

### `main_menu_durui_probe.py`(新增,板端根目录)

职责:自包含 DurUI 显示栈 + 复用现有 MainMenu UI。

照搬 DurUI 的以下结构(逐行,不"对齐参数"):

- `class LCD`: `__init__`(`Display().init(Display.ST7701, w, h, to_ide, quality=100)` + `MediaManager.init()` + 背光 Pin)、`lvgl_init`(两块 `image.Image(w,h,BGRA8888)` + 铺不透明黑 `draw_rectangle(color=(0,0,0),fill=True)` + `lv.disp_create` + `set_flush_cb` + `set_draw_buffers(..., DIRECT)`)、`lvgl_flush_cb`(`if disp.flush_is_last(): if buf1.virtaddr()==... show_image(buf1) else show_image(buf2); disp.flush_ready()`),无 layer、无清缓冲、无 sleep。
- `class Touch`: `__init__(TOUCH(0))`、`lvgl_read_cb`、`lvgl_init`(indev pointer + read_cb)。
- `_buzzer_init` / `_buzzer_beep`: PWM0 pin60。
- 主循环:`while True: lv.task_handler(); _diag_tick(); time.sleep_ms(MENU_LV_TASK_SLEEP_MS)`,`MENU_LV_TASK_SLEEP_MS=3`。

复用现有:

- `from ui.main_menu import MainMenu`。
- `from core.config_manager import ConfigManager`,`config.load()`。
- `from core.lang import LangManager`,`lang.load(config.get('lang','zh_CN'))`。
- `menu = MainMenu(config, buzzer, lang, on_card_click=_on_card_click)`。
- `menu.preload_icons()` 必须在首次 `lv.task_handler()` 前(文件 I/O 安全窗口)。
- `menu.show()`。
- `on_card_click` 回调:本实验只打印 + 持续 GC 诊断,**不 `machine.reset` 进脚本**(避免触碰 reset 框架)。

### 诊断(`main_menu_durui_probe.py` 内)

`_diag_tick()`:

- 每秒打印 `seq / selected / gc.mem_free()`。
- `seq==5` 时主动 `gc.collect()`,打印 begin/end。
- 主动 GC 在主循环 `lv.task_handler()` 返回后调用,不在任何 LVGL 回调内。

这是与现有 `MainMenu._diag_mem_tick` 等价的行为,但放在 probe 主循环里,确保复刻栈下 GC 时机与 DurUI 主循环一致。

### `MainMenu` 复用注意

- `MainMenu` 内部已有 `apply_scroll_visual`(距离驱动)、event callback 保活、style 保活、`MENU_DIAG_MEM` 诊断开关。probe 复用时 `MainMenu` 自身的 `_diag_mem_tick` 会因 `MENU_DIAG_MEM` 状态而启用/关闭。probe 主循环的 `_diag_tick` 独立于 `MainMenu`,两者不冲突(probe 用自己的 seq)。
- 为避免双诊断重复打印,probe 部署时将 `ui/main_menu.py` 的 `MENU_DIAG_MEM` 置 `False`,只用 probe 自己的 `_diag_tick`。

## 数据流

```
TOUCH(0) → lv indev → LVGL 事件 → MainMenu._on_scroll → apply_scroll_visual(已有对象 zoom/opa/pos)
                                                       ↓
主循环: lv.task_handler() → flush_cb → show_image(draw_buf, 无layer) → LCD
       ↓
       _diag_tick(): seq==5 gc.collect() → 打印 end → 下一轮 lv.task_handler()
```

## 错误处理与 K230 约束

- 首次 `lv.task_handler()` 前完成所有文件 I/O(`preload_icons`、字体加载由 MainMenu 触发的 fonts.load_all)。
- 不在 LVGL 回调/flush_cb 内 `gc.collect()`。
- 不配 sensor。
- `Display.init` 不传 `osd_num`,单层显示。
- flush_cb 不指定 layer、不清非活跃缓冲。
- 复刻照搬,不"优化"任何参数。

## 测试策略

板端是唯一真实环境。PC 侧无法跑 LVGL。本实验做轻量源码契约测试 `tests/test_main_menu_durui_probe_ast.py`:

- 文件存在且含 `class LCD`、`class Touch`、`lvgl_init`、`lvgl_flush_cb`。
- `lvgl_init` 用 `lv.DISP_RENDER_MODE.DIRECT` + `draw_rectangle` + `(0, 0, 0)` + `fill=True`(铺不透明黑)。
- `lvgl_flush_cb` 用 `show_image(self.draw_buf_1)` / `show_image(self.draw_buf_2)`,不含 `layer=`、不含 `bytearray(0)`。
- `Display.init(...)` 调用不含 `osd_num`。
- 主循环含 `lv.task_handler()` + `gc.collect()`(在 task_handler 之后) + `time.sleep_ms`。
- 复用 `from ui.main_menu import MainMenu`。

板端验证步骤:

1. 部署 `main_menu_durui_probe.py` + 现有 `ui/main_menu.py`(`MENU_DIAG_MEM=False`)。
2. 临时把 `/sdcard/main.py` 内容替换为 `import main_menu_durui_probe` 调用入口(或直接运行 probe 的 `main()`),跑 probe。
3. 看菜单是否**有画面**(DIRECT + 铺黑应显示黑底卡片)。
4. 滚动 + 挂机,看 seq=5 `gc end` 后是否继续滚动。

## 验收标准

二分结论之一成立:

- **不卡**:probe 下主动 GC 后能继续滚动,菜单有画面。→ 显示栈是元凶,`init_menu` 换用 DurUI 栈。
- **仍卡**:probe 下 `gc end` 后卡死。→ `MainMenu` UI 是元凶,显示栈无关。

任一结论即实验完成。实验完成后,根据结论决定下一步(不在本 spec 范围)。

## 实施顺序

1. 写源码契约测试(RED)。
2. 照搬 DurUI 显示栈 + 复用 MainMenu,实现 `main_menu_durui_probe.py`。
3. 契约测试 GREEN + compileall。
4. 板端部署 + 验证,记录二分结论。
