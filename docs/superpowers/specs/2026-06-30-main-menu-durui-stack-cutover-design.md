# 主菜单 init_menu 正式切换 DurUI 显示栈设计

> 创建日期:2026-06-30
> 范围:`core/app_runtime.py` 的 `init_menu` + `main.py` 的 `run_menu` 收尾固化;不动 `init_app`(脚本模式)

## 背景

DurUI probe 二分结论(2026-06-29):主菜单 GC 后卡死的元凶是显示栈。`app_runtime.init_menu` 原显示链路(配 sensor + `osd_num=2` + FULL + flush 清非活跃缓冲)与 GC 冲突;DurUI 栈(无 sensor + 无 OSD2 + DIRECT + flush 不清缓冲 + 缓冲铺不透明黑)在 probe 下验证稳定——有画面、主动 GC 不卡、mem 稳定不再单调下降。

`init_menu` 已在前几轮改为 DurUI 栈(`_init_menu_display_and_media` 无 osd_num + `_lvgl_init(DIRECT, opaque_bg=True)` + `_flush_cb` 主菜单分支不清缓冲),但正常 `main.py` → `run_menu` 流程下尚未验证有画面+不卡(上次跑该版本黑屏,疑似 BootSplash/部署/诊断交互,非显示栈本身)。

probe 覆盖了纯显示栈;但正常 `run_menu` 比 probe 多三个变量:BootSplash(开机 LOGO)、scr_act 黑底设置、host_tick(USART 握手+数据帧,主菜单需要 host)。

## 目标

把 `init_menu` 正式固化到 DurUI 显示栈,使正常 `run_menu` 流程下:
- 主菜单有画面(黑底卡片);
- 主动 GC 后不卡;
- mem 不再单调下降到耗尽;
- 保留 host_tick(主菜单需要上位机通讯);
- 保留 BootSplash 开机 LOGO。

## 非目标

- 不改 `init_app`/脚本模式(FULL + OSD2 保持不动)。
- 不改各识别脚本。
- 不预先优化 host_api 预分配(YAGNI:先看 host_tick 在新栈下是否仍掉 mem,再决定是否单独处理)。
- 不删除 `main_menu_durui_probe.py`(留作成功参照)。

## 方案

probe 已验证纯显示栈稳定。收尾在 probe 基础上逐个加回正常流程变量,隔离每个变量的影响。

### 步骤1:最小复刻验证(无 BootSplash)

`init_menu` 已是 DurUI 栈。临时给 `run_menu` 加一个常量开关 `PROBE_NO_BOOTLOGO`(默认 False,验证阶段设 True),开启时跳过 `BootSplash(...).show()`,直接进菜单。保留 host_tick、保留 scr_act 黑底设置。

验证:有画面 + GC 不卡 + host_tick 不掉 mem。

这一步隔离 BootSplash。若稳,说明 DurUI 栈 + host_tick + scr_act 黑底组合在正常流程下也可用;若掉 mem/卡,host_tick 可能是第二泄漏源,转入 host_api 预分配(另开任务)。

### 步骤2:加回 BootSplash

`PROBE_NO_BOOTLOGO=False`,恢复 BootSplash 开机 LOGO。

验证:LOGO 正常显示 + 进菜单有画面 + 不卡。BootSplash 内部用 `lv.obj`+`lv.img`,在 DIRECT 栈下须确认 LOGO 可见且清理后菜单正常。

若 BootSplash 在 DIRECT 下黑屏/异常,单独定位 BootSplash 与 DIRECT 的交互(可能需 BootSplash 也铺不透明黑底,与 `_lvgl_init` 一致)。

### 步骤3:固化 + 关诊断

全通过后:
- 移除 `PROBE_NO_BOOTLOGO` 开关(或保留为 False 常量,视实现简洁度)。
- `ui/main_menu.py` 的 `MENU_DIAG_MEM=False`、`MENU_DIAG_FORCE_GC_AT_SEQ=0`(已关)。
- `main.py` 的 `menu.diag_after_task_handler()` 保留为安全点 GC(无害),或视情况移除。
- 恢复正常启动流程。

## 组件设计

### `core/app_runtime.py`

`init_menu` 已实现 DurUI 栈(不改):
- `_init_menu_display_and_media()`:Display.init 不传 osd_num。
- `_lvgl_init(DIRECT, opaque_bg=True)`。
- `_flush_cb` 主菜单分支:`show_image(draw_buf)` 不指定 layer、不清缓冲。

无需新增改动。

### `main.py` 的 `run_menu`

新增模块级常量(验证用):

```python
PROBE_NO_BOOTLOGO = False  # 验证阶段设 True 跳过开机 LOGO,隔离 BootSplash 变量
```

`run_menu` 内 BootSplash 调用改为:

```python
if not _is_warm_boot() and not PROBE_NO_BOOTLOGO:
    BootSplash(runtime.buzzer).show()
```

其余不变:`menu.preload_icons()`、`menu.show()`、主循环 `lv.task_handler()` + `menu.diag_after_task_handler()` + `runtime.host_tick()` + `sleep_ms`。

### `ui/main_menu.py`

诊断已关(`MENU_DIAG_MEM=False`)。步骤3 视情况决定是否移除 `diag_after_task_handler` 调用。滚动视觉已 DurUI 风格(`apply_scroll_visual`)、style/callback 保活——全部保留。

### `main_menu_durui_probe.py`

保留不动,作为成功参照。若步骤1失败(probe 稳但 run_menu 不稳),对比 probe 与 run_menu 差异定位。

## 数据流

```
run_menu:
  AppRuntime.init_menu(DurUI 栈: 无 sensor + 无 OSD2 + DIRECT + 铺黑 + flush 不清缓冲)
  → lv.scr_act 设纯黑底
  → MainMenu(config,buzzer,lang).preload_icons() [首次 task_handler 前]
  → [PROBE_NO_BOOTLOGO? 跳过 : BootSplash.show()]
  → menu.show()
  → 主循环: lv.task_handler() → menu.diag_after_task_handler() → runtime.host_tick() → sleep_ms
```

## 错误处理与 K230 约束

- 首次 `lv.task_handler()` 前完成所有文件 I/O(preload_icons、BootSplash 内 open logo、fonts.load_all)。
- 不在 LVGL 回调/flush_cb 内 `gc.collect()`;`diag_after_task_handler` 只在 task_handler 返回后调。
- 主菜单不配 sensor;`Display.init` 不传 osd_num;flush 主菜单分支不指定 layer、不清缓冲。
- 主菜单保留 host_tick(USART1 TX/RX pin40/41),host_api 行为不变。
- BootSplash 在 DIRECT 下若异常,单独处理,不回退显示栈。

## 测试策略

### PC 侧源码契约

新增/保留 `tests/test_main_menu_runtime_ast.py`:
- 保留:init_menu 用 DIRECT + 无 sensor + 无 osd_num + flush 主菜单分支不清缓冲(opaque_bg 已测)。
- 新增:`main.py` 含 `PROBE_NO_BOOTLOGO` 常量,且 BootSplash 调用受其门控。

### 板端验证

- 步骤1(PROBE_NO_BOOTLOGO=True):进菜单有画面;滚动+挂机,seq=5 主动 GC 后不卡;观察 host_tick 是否导致 mem 单调下降。
- 步骤2(PROBE_NO_BOOTLOGO=False):开机 LOGO 显示 + 进菜单有画面 + 不卡。
- 步骤3:全流程稳定,诊断关闭,启动正常。

## 验收标准

- 主菜单有画面(黑底卡片)。
- 快速滚动 + 挂机 10 分钟不死机。
- 主动 GC 后能继续滚动。
- mem 不再单调下降到耗尽(允许小幅波动);若 host_tick 导致持续下降,记录并另开 host_api 预分配任务。
- BootSplash 开机 LOGO 正常显示。
- 脚本模式(face/tag/object/color/camera)不受影响(未改 init_app)。

## 实施顺序

1. 写/更新源码契约测试(RED)。
2. main.py 加 PROBE_NO_BOOTLOGO 开关 + 门控 BootSplash(GREEN)。
3. 步骤1 板端验证(无 BootSplash)。
4. 步骤2 板端验证(加回 BootSplash)。
5. 步骤3 固化 + 关诊断。
6. 若 host_tick 仍掉 mem,另开 host_api 预分配任务(不在本 spec 范围)。
