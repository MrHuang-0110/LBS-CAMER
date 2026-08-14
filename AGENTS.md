# CamerAi — K230D 视觉 AI 触控终端（MicroPython）

正点原子 K230D BOX（ST7701 640×480）视觉 AI 相机固件：MicroPython + LVGL + `media` 库。主机侧 UART 通信 + 板端 LVGL 触控。

> 详细架构/坑位/约定见 `CLAUDE.md`（本文件是快速索引，读源码前先看它）。

## 关键事实（勿遗漏）

- **reset 切换架构**：`main.py` 是启动器，**永不被覆写**。读 `/sdcard/CamerAi/.next_script` 决定进主菜单还是目标脚本；切脚本 = 写 `.next_script` + `machine.reset()`。每个脚本进程独立 `init`，无跨脚本状态。
- **板端路径**：部署到 `/sdcard/CamerAi/`；本机无模拟器，板端行为靠 `项目记录.md` 验收记录。
- **仓库只入库板端运行代码**：`tests/`、`docs/`、`tools/`、`CLAUDE.md`、`项目记录.md`、`demo/` 均在 `.gitignore` 中（本机存在但不入 git）。改测试/文档不会进版本库。

## 命令

```bash
# 单测：host 侧逐文件跑，无 pytest
python tests/test_xxx.py          # 逐项 PASS/FAIL，结尾 ALL PASS
python tests/test_host_api.py     # 常用回归
python tests/test_framework.py

# 语法检查（板端模块 host 侧不可 import，用 py_compile）
python -m py_compile <file>
```

## 架构边界

- `main.py` — 启动器/脚本分发；`.next_script`、`.warm_boot`（热启动跳 LOGO）标记。
- `core/` — `app_runtime.py`（每进程 init：Display/MediaManager/sensor 通道/LVGL FULL/字体/图标/`host_tick`）、`config_manager.py`、`lang.py`（`t("key")` i18n）、各 AI 模块（`face_ai/gesture_ai/object_ai/body_ai/object_classify_ai/...`）、`db_store.py`、`event_bus.py`。
- `scripts/<category>/app.py` — 功能脚本，统一 `run(runtime)` + 单线程主循环；`scripts/_template/` 是复制起点；`scripts/_base.py` 是旧架构基类（弃用中）。
- `ui/` — `main_menu.py`、`boot_splash.py`、`theme.py`。
- `comm/host_api.py` — UART1 二进制协议（帧格式/类型码见 `通讯协议.txt`，类型 0x01~0x13；`TYPE_MODE_SWITCH=0xFF`）。
- `hw/` — `lcd.py`、`buzzer.py`、`touch.py` 板级驱动。
- `config/` + `resource/` — categories.json/app.json + i18n。

## 约定与硬约束

- 注释与提交信息用**中文**；每轮按 spec → plan → TDD（Red→Green→commit）推进；完成后把实施记录追加进 `项目记录.md`。
- 每次更改（含测试/文档/记录）验证后 `git add` + `commit` + `push`，不留未推送改动。
- **脚本只提供 `run(runtime)`**；切脚本一律 `.next_script` + `machine.reset()`；`core/__init__.py` 不得 eager-import 旧架构模块（ScriptRunner/PluginLoader/ui.back_bar）。
- **K230 硬约束（坑）**：① 首次 `lv.task_handler()` 前完成文件 I/O（字体/图标/kmodel，坑#2）；② sensor 通道须在 `MediaManager.init()` 前声明（坑#15）；③ `open()` 前先 `os.stat()` 预检查（坑#18：ENOENT 异常污染 FATFS → GC 后卡死）；④ 主循环里 `os.exitpoint()` 供 IDE 中断。
- **单线程主循环**：snapshot→on_frame→show_image→task_handler 串行，禁止双写者竞争 display DMA；渲染用 `DISP_RENDER_MODE.FULL`（禁用 PARTIAL）；on_frame 用 try/except 隔离，AI 异常不杀循环。
- **资源路径注意单复数**：实际用 `resource/font/`（单数，字体 bin）与 `resource/icons/`（复数，图标）；`resource/fonts/`、`resource/icon/` 是遗留空目录勿用。
- 布局常量：`BAR_H=52`、`PREVIEW_Y=52`、`PREVIEW_H=376`、`BAR_BG=0x1A1A1A`、屏幕背景纯黑 `0x000000`。
- 测试风格：host 侧多用 **AST 契约断言**（板端模块不可 import）；纯逻辑（帧解析/DB 持久化）直接 import；AST 文本断言会扫到注释，注释勿含被断言关键词。

## 新增脚本清单

1. `config/categories.json`（order/enabled）
2. i18n（`resource/i18n/`）
3. `host_api.CATEGORY_TYPE`
4. `app_runtime._channels_for`（AI 类另配 chn2 XGA RGBP888）
5. `resource/icons/<cat>_icon/`
