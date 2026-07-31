# CamerAi — K230D 视觉 AI 触控终端（MicroPython）

正点原子 K230D BOX（ST7701 640×480）视觉 AI 相机固件：MicroPython + LVGL + media 库。
**reset 切换架构**：`main.py` 是启动器（永不被覆写），读 `/sdcard/CamerAi/.next_script` 决定跑主菜单还是目标脚本；
切脚本 = 写 `.next_script` + `machine.reset()`，每个脚本进程独立 init，无跨脚本状态污染。

## Project

- 平台：K230D (CanMV) MicroPython；LVGL；`media.sensor/display/MediaManager`；主机侧 PikaScript
- 启动器：`main.py` — `main()` → `_read_next_script()` → `run_menu()` 或 `run_script(cat)`；`_on_remote_switch` 处理主机远程切脚本
- 脚本进程：`core/app_runtime.py` `AppRuntime.init_app(cat, fpioa)` → `scripts/<cat>/app.py` `run(runtime)`；退出 `runtime.cleanup()` + reset
- 主机通信：UART1 二进制协议（`comm/host_api.py`，帧格式/类型码见 `通讯协议.txt`，类型 0x01~0x13）

## Commands

- 单测（host 侧逐文件跑，无 pytest）：`python tests/test_xxx.py` → 逐项 PASS/FAIL + `ALL PASS`
- 语法检查：`python -m py_compile <file>`
- 常用回归：`python tests/test_host_api.py`；`python tests/test_framework.py`
- 板端部署：文件拷到板 `/sdcard/CamerAi/`；本机无模拟器，板端行为靠 项目记录.md 的验收记录

## Architecture

- `main.py` — 启动器/脚本分发；`.next_script`（切脚本）、`.warm_boot`（热启动跳 LOGO）标记
- `core/` — `app_runtime.py`（每进程 init：Display/MediaManager/sensor 通道/LVGL FULL/字体/图标/`host_tick`）、`config_manager.py`（app.json+categories.json）、`icon_cache.py`、`font_manager.py`、`lang.py`（`t("key")` i18n）、各 AI 模块（`face_ai/face_db/gesture_ai/object_ai/body_ai/road_db/...`）、`db_store.py`、`id_registry.py`、`event_bus.py`
- `ui/` — `main_menu.py`（深灰卡片滚动选择器）、`boot_splash.py`、`back_bar.py`、`theme.py`
- `scripts/<category>/app.py` — 功能脚本，统一 `run(runtime)` + 单线程主循环；`_template/` 是复制起点骨架；`_base.py` 是旧架构基类（弃用中）
- `comm/host_api.py` — UART1 协议栈：握手、帧解析、`CATEGORY_TYPE` 映射、模式切换命令（`TYPE_MODE_SWITCH=0xFF`）
- `hw/` — `lcd.py`、`buzzer.py`、`touch.py` 板级驱动
- `config/` + `resource/` — categories.json/app.json + icons/fonts/i18n
- `tests/` — host 侧 AST/纯 Python 契约测试（板端模块不可导入）；`docs/superpowers/specs|plans/` + `项目记录.md` — 设计/计划/实施记录

## Conventions

- 注释与提交信息用中文；每轮功能按 spec → plan → TDD（Red→Green→commit）推进，完成后把实施记录追加进 `项目记录.md`
- **维护同步**：后续维护中遇到的 bug、坑、根因与解法及时追加记录到 `项目记录.md`（按日期分节，含现象/根因/解法/验证，用于团队同步）；每次更改（含测试、文档、记录）完成并验证后 `git add` + `git commit` + `git push` 到远端，不留未推送改动
- **reset 框架**：脚本只提供 `run(runtime)`；切脚本一律 `.next_script` + `machine.reset()`；`core/__init__.py` 不得 eager-import 旧架构模块（ScriptRunner/PluginLoader/ui.back_bar）
- **K230 硬约束**：① 首次 `lv.task_handler()` 前完成文件 I/O（字体/图标/kmodel，坑#2）；② sensor 通道须在 `MediaManager.init()` 前声明（坑#15）；③ `open()` 前先 `os.stat()` 预检查（坑#18：ENOENT 异常污染 FATFS → GC 后卡死）；④ 主循环里 `os.exitpoint()` 供 IDE 中断
- **单线程主循环**：snapshot→on_frame→show_image→task_handler 串行，禁止双写者竞争 display DMA；渲染用 `DISP_RENDER_MODE.FULL`，禁用 PARTIAL；on_frame 用 try/except 隔离，AI 异常不杀循环
- 新增脚本清单：`config/categories.json`（order/enabled）+ i18n（`resource/i18n/`）+ `host_api.CATEGORY_TYPE` + `app_runtime._channels_for`（AI 类另配 chn2 XGA RGBP888）+ `resource/icons/<cat>_icon/`
- 布局常量：BAR_H=52、PREVIEW_Y=52、PREVIEW_H=376、BAR_BG=0x1A1A1A、屏幕背景纯黑 0x000000
- 测试风格：host 侧多用 AST 契约断言（板端模块不可导入）；纯逻辑（帧解析/DB 持久化）直接 import 测试；注意 AST 文本断言会扫到注释，注释勿含被断言关键词

## Notes

（预留）
