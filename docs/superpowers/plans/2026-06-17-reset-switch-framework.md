# CamerAi reset 切换框架 + 全 APP 迁移 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** CamerAi 从同进程脚本切换改为 reset 切换架构（main.py 启动器 + .next_script 标记 + app_runtime 公共 init），迁移全部 APP（face_detect/settings/camera），根治 face_detect 连跑卡死，最终无两套架构并存。

**Architecture:** main.py 读 .next_script 决定跑主菜单或目标脚本，点卡片写 .next_script + machine.reset()，脚本进程独立 init（app_runtime 封装 Display/MediaManager/sensor/LVGL/host），跑完清 .next_script + reset 回主菜单。face_detect 复用裸跑已验证的双线程 AI 结构。

**Tech Stack:** MicroPython + LVGL v8 + `_thread` + ulab.numpy + nncase_runtime + image 画图 + machine.reset + K230D BOX。

**Spec:** [docs/superpowers/specs/2026-06-17-reset-switch-framework-design.md](docs/superpowers/specs/2026-06-17-reset-switch-framework-design.md)

---

## K230 硬约束（实施前必读）

1. **坑#2**：每进程 init 后首次 task_handler 前完成文件 I/O（图标/字体/kmodel）。脚本 run() 开头加载 kmodel，在 AI 线程前。
2. **坑#10**：AI 线程不碰 LVGL（画 image.Image），每帧 gc 安全。
3. **坑#14/15**：reset 架构每进程独立 init/deinit，不再"常驻不拆"。
4. **坑#16**：AI 线程每帧 gc，对齐官方。

## image 画图 API（板端验证签名）

- `img.draw_line(x1,y1,x2,y2,color=(R,G,B,A),thickness=N)`
- `img.draw_rectangle(x,y,w,h,color=(R,G,B,A),thickness=N)`
- `img.draw_string_advanced(x,y,size,text,color=(R,G,B,A))`

## 实施顺序

- **阶段 1**：reset 框架骨架（app_runtime + main.py 启动器 + .next_script）
- **阶段 2**：face_detect 迁移（验证新框架稳定）+ 板端验收门控
- **阶段 3**：settings/camera 迁移
- **阶段 4**：删除旧架构（ScriptRunner/BaseScript/lcd），清理诊断脚本

---

## File Structure

| 文件 | 角色 | 阶段 |
|------|------|------|
| `core/app_runtime.py` | **新建** — 公共 init（Display/MediaManager/sensor/LVGL/host/字体/图标） | 1 |
| `main.py` | 重写为启动器（读 .next_script，主菜单/脚本模式分流） | 1 |
| `scripts/face_detect/app.py` | 重构为 `run(runtime)` 独立脚本 | 2 |
| `scripts/settings/app.py` | 重构为 `run(runtime)` 独立脚本 | 3 |
| `scripts/camera/app.py` | 重构为 `run(runtime)` 独立脚本 | 3 |
| `hw/lcd.py` | sensor 配置逻辑抽到 app_runtime，阶段4评估废弃 | 1/4 |
| `core/script_runner.py` | 阶段4删除 | 4 |
| `scripts/_base.py` | 阶段4评估废弃 | 4 |
| `tests/test_face_detect.py` | 更新契约（run 入口、_ai_loop 不碰 LVGL） | 2 |
| `tests/test_framework.py` | **新建** — app_runtime/main.py/.next_script 契约 | 1 |

---

## 阶段 1：reset 框架骨架

### Task 1.1: 新建 core/app_runtime.py — 公共 init 模块

**Files:**
- Create: `core/app_runtime.py`
- Create: `tests/test_framework.py`

- [ ] **Step 1: 新建 tests/test_framework.py**

```python
# tests/test_framework.py — reset 框架契约测试
import ast
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RUNTIME_PATH = os.path.join(ROOT, "core", "app_runtime.py")
MAIN_PATH = os.path.join(ROOT, "main.py")


def _parse(path):
    with open(path, "r", encoding="utf-8") as f:
        return ast.parse(f.read(), filename=path)


def _class_node(tree, name):
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == name:
            return node
    raise AssertionError("Class %s missing" % name)


def _method_names(class_node):
    return {n.name for n in class_node.body
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}


def test_app_runtime_class_exists():
    tree = _parse(RUNTIME_PATH)
    cls = _class_node(tree, "AppRuntime")
    methods = _method_names(cls)
    for m in ("init_menu", "init_app", "cleanup"):
        assert m in methods, "AppRuntime missing method: %s" % m


def test_app_runtime_init_app_takes_category():
    tree = _parse(RUNTIME_PATH)
    cls = _class_node(tree, "AppRuntime")
    found = False
    for n in cls.body:
        if isinstance(n, ast.FunctionDef) and n.name == "init_app":
            arg_names = [a.arg for a in n.args.args]
            assert "category_id" in arg_names or "category" in arg_names, \
                "init_app must take category_id param"
            found = True
    assert found, "init_app method missing"
```

- [ ] **Step 2: 跑测试确认红**

```
cd e:/LBS-Project/CanMV/CamerAi
python tests/test_framework.py
```
预期：FAIL（app_runtime.py 不存在）。

- [ ] **Step 3: 新建 core/app_runtime.py**

```python
# core/app_runtime.py — reset 框架公共 init 模块
#
# 每个 APP 进程独立 init（reset 切换架构）。封装 Display/MediaManager/
# sensor/LVGL/host/字体/图标，按 category 决定 sensor 通道配置。
#
# 对齐官方综合例程：脚本自己 init 全套，进程独立，无跨脚本状态污染。
#
# K230 硬约束：
#   - 坑#2：init 后首次 task_handler 前完成文件 I/O（字体/图标/kmodel）
#   - 坑#15：sensor 通道须 MediaManager.init() 前声明
#   - reset 架构每进程独立 init/deinit，不再"常驻不拆"

import os
from media.display import Display
from media.media import MediaManager
from media.sensor import Sensor, CAM_CHN_ID_0, CAM_CHN_ID_1, CAM_CHN_ID_2
from machine import Pin, FPIOA
import image
import lvgl as lv
import time
import uctypes

DISPLAY_WIDTH = 640
DISPLAY_HEIGHT = 480


class AppRuntime:
    """每进程独立的硬件/LVGL/host 运行时。

    main.py 按 .next_script 决定调 init_menu（主菜单）或
    init_app(category_id)（脚本模式）。脚本退出调 cleanup。
    """

    def __init__(self):
        self.width = DISPLAY_WIDTH
        self.height = DISPLAY_HEIGHT
        self.fpioa = None
        self.sensor = None
        self.display = None
        self.draw_buf_1 = None
        self.draw_buf_2 = None
        self.lv_disp = None
        self.bl = None
        self.host = None
        self.lang = None
        self.config = None
        self.buzzer = None
        self._sensor_running = False

    def _init_display_and_media(self, to_ide=False):
        self.display = Display()
        self.display.init(Display.ST7701, self.width, self.height,
                          to_ide=to_ide, osd_num=2, quality=100)
        MediaManager.init()

    def _init_backlight(self, fpioa, bl_pinx=5, bl_valid=1):
        fpioa.set_function(bl_pinx, fpioa.GPIO0 + bl_pinx)
        pull = Pin.PULL_UP if bl_valid == 0 else Pin.PULL_DOWN
        self.bl = Pin(bl_pinx, Pin.OUT, pull=pull, drive=7)
        self.bl.value(bl_valid)

    def _config_sensor(self, channels):
        """配置 sensor 通道。channels: list of (chn_id, framesize, pixformat)。
        必须在 MediaManager.init() 之前调。"""
        self.sensor = Sensor(width=1280, height=960, fps=30)
        self.sensor.reset()
        for chn_id, framesize, pixformat in channels:
            self.sensor.set_framesize(framesize, chn=chn_id)
            self.sensor.set_pixformat(pixformat, chn=chn_id)

    def _lvgl_init(self):
        self.draw_buf_1 = image.Image(self.width, self.height, image.BGRA8888)
        self.draw_buf_2 = image.Image(self.width, self.height, image.BGRA8888)
        self.draw_buf_1.clear()
        self.draw_buf_2.clear()
        self.lv_disp = lv.disp_create(self.width, self.height)
        self.lv_disp.set_flush_cb(self._flush_cb)
        self.lv_disp.set_color_format(lv.COLOR_FORMAT.ARGB8888)
        self.lv_disp.set_draw_buffers(
            self.draw_buf_1.bytearray(), self.draw_buf_2.bytearray(),
            self.draw_buf_1.size(), lv.DISP_RENDER_MODE.FULL)

    def _flush_cb(self, disp, area, px_map):
        """LVGL flush 回调（对齐官方 ai_lvgl.py disp_drv_flush_cb）。"""
        if self.draw_buf_1 is None or self.draw_buf_2 is None:
            disp.flush_ready()
            return
        if disp.flush_is_last():
            if self.draw_buf_1.virtaddr() == uctypes.addressof(
                    px_map.__dereference__()):
                self.draw_buf_2.bytearray()[:] = bytearray(0)
                self.display.show_image(self.draw_buf_1, layer=Display.LAYER_OSD2)
            else:
                self.draw_buf_1.bytearray()[:] = bytearray(0)
                self.display.show_image(self.draw_buf_2, layer=Display.LAYER_OSD2)
            time.sleep(0.01)
        disp.flush_ready()

    def init_menu(self, fpioa):
        """主菜单模式 init：Display/MediaManager/sensor(chn0)/LVGL/字体/图标/host。"""
        self.fpioa = fpioa
        self._config_sensor([(CAM_CHN_ID_0, Sensor.VGA, Sensor.RGB888)])
        self._init_display_and_media()
        self._init_backlight(fpioa)
        lv.init()
        self._lvgl_init()
        from core.font_manager import fonts
        try:
            fonts.load_all()
        except Exception as e:
            print("[Runtime] font load warning: %s" % e)
        from core.icon_cache import icon_cache
        icon_cache.preload_settings_icons()
        icon_cache.preload_camera_icons()
        icon_cache.preload_face_icons()
        self._init_services(fpioa)

    def init_app(self, category_id, fpioa):
        """脚本模式 init：按 category 配 sensor 通道 + 全套 init + sensor.run。"""
        self.fpioa = fpioa
        channels = self._channels_for(category_id)
        self._config_sensor(channels)
        self._init_display_and_media()
        self._init_backlight(fpioa)
        lv.init()
        self._lvgl_init()
        from core.font_manager import fonts
        try:
            fonts.load_all()
        except Exception as e:
            print("[Runtime] font load warning: %s" % e)
        self._init_services(fpioa)
        # sensor.run 紧贴脚本主循环（消费者就绪后才 run，避免缓冲满卡死）
        self.sensor.run()
        self._sensor_running = True

    def _channels_for(self, category_id):
        """按 category 决定 sensor 通道配置。"""
        chs = [(CAM_CHN_ID_0, Sensor.VGA, Sensor.RGB888)]
        if category_id == "face_detect":
            chs.append((CAM_CHN_ID_2, Sensor.XGA, Sensor.RGBP888))
        elif category_id == "camera":
            chs.append((CAM_CHN_ID_1, Sensor.SXGAM, Sensor.RGB565))
        return chs

    def _init_services(self, fpioa):
        from core.config_manager import ConfigManager
        from core.lang import LangManager
        from hw.buzzer import Buzzer
        from comm.host_api import HostAPI
        self.config = ConfigManager()
        self.config.load()
        self.buzzer = Buzzer(fpioa, pinx=60, pwm_ch=0, valid=0)
        self.buzzer.set_enabled(self.config.get('buzzer_enabled', True))
        self.lang = LangManager()
        self.lang.load(self.config.get('lang', 'zh_CN'))
        fpioa.set_function(40, FPIOA.UART1_TXD)
        fpioa.set_function(41, FPIOA.UART1_RXD)
        self.host = HostAPI()

    def cleanup(self):
        """脚本退出前清理（显式，虽 reset 会清）。"""
        try:
            if self._sensor_running:
                self.sensor.stop()
        except BaseException:
            pass
        try:
            if self.lv_disp is not None:
                del self.lv_disp
        except BaseException:
            pass
        try:
            del self.draw_buf_1
            del self.draw_buf_2
        except BaseException:
            pass
        try:
            lv.deinit()
        except BaseException:
            pass
        try:
            self.display.deinit()
        except BaseException:
            pass
        try:
            MediaManager.deinit()
        except BaseException:
            pass
```

- [ ] **Step 4: AST 检查 + 跑测试确认绿**

```
cd e:/LBS-Project/CanMV/CamerAi
python -c "import ast; ast.parse(open('core/app_runtime.py', encoding='utf-8').read()); print('OK')"
python tests/test_framework.py
```
预期：AST OK；测试 PASS。

- [ ] **Step 5: 提交**

```
cd e:/LBS-Project/CanMV/CamerAi
git add core/app_runtime.py tests/test_framework.py
git commit -m "feat(runtime): 新建 app_runtime 公共 init 模块 — reset 框架基座

封装 Display/MediaManager/sensor(按category配通道)/LVGL/host/字体/图标。
init_menu/init_app(category_id)/cleanup。sensor.run 紧贴脚本主循环。
flush_cb 对齐官方(time.sleep)。每进程独立 init/deinit 替代旧 lcd 常驻栈。

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 1.2: main.py 重写为启动器

**Files:**
- Modify: `main.py`
- Modify: `tests/test_framework.py`

- [ ] **Step 1: 加 main.py 启动器契约测试**

`tests/test_framework.py` 追加：

```python
def test_main_reads_next_script():
    tree = _parse(MAIN_PATH)
    src = open(MAIN_PATH, encoding="utf-8").read()
    assert "next_script" in src, "main.py must read .next_script marker"
    assert "machine.reset" in src or "reset()" in src, \
        "main.py must call machine.reset() to switch"


def test_main_has_launch_writer():
    src = open(MAIN_PATH, encoding="utf-8").read()
    assert "next_script" in src and ("wb" in src or "write" in src.lower()), \
        "main.py must write .next_script on card click"
```

- [ ] **Step 2: 跑测试确认红**

```
cd e:/LBS-Project/CanMV/CamerAi
python tests/test_framework.py
```
预期：`test_main_reads_next_script` / `test_main_has_launch_writer` FAIL。

- [ ] **Step 3: 重写 main.py 为启动器**

整个 main.py 替换为：

```python
# CamerAi — 视觉 AI 触控终端
#
# reset 切换架构：main.py 是启动器（永不被覆写），读 .next_script 决定
# 跑主菜单还是目标脚本。点卡片写 .next_script + machine.reset()，脚本
# 进程独立 init（app_runtime），跑完清 .next_script + reset 回主菜单。
#
# 对齐官方综合例程 DemoScriptRunner（machine.reset 切换），用更安全的
# .next_script 标记替代覆写 main.py。
#
# 硬件平台：正点原子 K230D BOX（ST7701 640×480）

import lvgl as lv
import time
import os
import sys
from machine import FPIOA

try:
    from machine import machine
except ImportError:
    import machine

_PROJECT_ROOT = "/sdcard/CamerAi"
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

NEXT_SCRIPT_PATH = "/sdcard/CamerAi/.next_script"


def _read_next_script():
    try:
        with open(NEXT_SCRIPT_PATH, "r") as f:
            cid = f.read().strip()
        return cid if cid else None
    except Exception:
        return None


def _write_next_script(category_id):
    try:
        with open(NEXT_SCRIPT_PATH, "w") as f:
            f.write(category_id)
    except Exception as e:
        print("[CamerAi] write .next_script failed: %s" % e)


def _clear_next_script():
    try:
        os.remove(NEXT_SCRIPT_PATH)
    except Exception:
        pass


def _load_script(category_id):
    from core.config_manager import ConfigManager
    config = ConfigManager()
    config.load()
    cat = config.get_category(category_id)
    if cat is None:
        print("[CamerAi] category not found: %s" % category_id)
        return None
    script_name = cat.get("script", category_id)
    try:
        mod = __import__("scripts.%s.app" % script_name, None, None, ["run"])
        return mod
    except Exception as e:
        print("[CamerAi] load script %s failed: %s" % (script_name, e))
        import sys as _sys
        _sys.print_exception(e)
        return None


def run_menu():
    from core.app_runtime import AppRuntime
    from ui.main_menu import MainMenu

    fpioa = FPIOA()
    runtime = AppRuntime()
    runtime.init_menu(fpioa)

    def on_card_click(category_id):
        print("[CamerAi] launch: %s" % category_id)
        runtime.buzzer.beep(ms=50)
        _write_next_script(category_id)
        machine.reset()

    menu = MainMenu(runtime.config, runtime.buzzer, runtime.lang,
                    on_card_click=on_card_click)
    menu.preload_icons()
    menu.show()
    print("[CamerAi] main menu running")
    while True:
        os.exitpoint()
        _th = lv.task_handler()
        time.sleep_ms(_th if _th > 0 else 5)


def run_script(category_id):
    from core.app_runtime import AppRuntime
    fpioa = FPIOA()
    runtime = AppRuntime()
    runtime.init_app(category_id, fpioa)
    mod = _load_script(category_id)
    if mod is not None and hasattr(mod, "run"):
        try:
            mod.run(runtime)
        except Exception as e:
            print("[CamerAi] script run error: %s" % e)
            import sys as _sys
            _sys.print_exception(e)
    else:
        print("[CamerAi] script has no run(): %s" % category_id)
    _clear_next_script()
    machine.reset()


def main():
    print("=" * 40)
    print("  CamerAi v0.2.0 (reset-switch)")
    print("=" * 40)
    next_script = _read_next_script()
    if next_script:
        print("[CamerAi] script mode: %s" % next_script)
        run_script(next_script)
    else:
        print("[CamerAi] menu mode")
        run_menu()


try:
    main()
except BaseException as e:
    print("[CamerAi] fatal: %s" % e)
    import sys
    sys.print_exception(e)
    try:
        os.remove(NEXT_SCRIPT_PATH)
    except Exception:
        pass
finally:
    print("[CamerAi] shutdown")
```

- [ ] **Step 4: AST 检查 + 跑测试确认绿**

```
cd e:/LBS-Project/CanMV/CamerAi
python -c "import ast; ast.parse(open('main.py', encoding='utf-8').read()); print('OK')"
python tests/test_framework.py
```
预期：AST OK；framework 测试全 PASS。

- [ ] **Step 5: 提交**

```
cd e:/LBS-Project/CanMV/CamerAi
git add main.py tests/test_framework.py
git commit -m "feat(main): 重写为 reset 切换启动器

读 .next_script 决定跑主菜单或目标脚本。点卡片写 .next_script +
machine.reset()。脚本进程独立 init_app + run(runtime) + 清标记 + reset。
.next_script 标记替代官方覆写 main.py(防变砖)。主循环 sleep_ms(task_handler) 对齐官方。

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## 阶段 2：face_detect 迁移（验证新框架）

### Task 2.1: face_detect 重构为 run(runtime) 独立脚本

**Files:**
- Modify: `scripts/face_detect/app.py`
- Modify: `tests/test_face_detect.py`

- [ ] **Step 1: 更新 face_detect 契约测试**

`tests/test_face_detect.py` 追加：

```python
def test_face_detect_has_run_entry():
    """face_detect 模块必须有 run(runtime) 入口（reset 框架）。"""
    tree = _parse(APP_PATH)
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == "run":
            return
    raise AssertionError("face_detect app.py must have module-level run(runtime)")


def test_face_detect_run_starts_ai_thread():
    src = open(APP_PATH, encoding="utf-8").read()
    assert "_ai_loop" in src and "start_new_thread" in src, \
        "face_detect must start AI thread with _ai_loop"


def test_face_detect_ai_loop_no_lvgl():
    tree = _parse(APP_PATH)
    cls = None
    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            cls = node
            break
    assert cls is not None, "FaceDetectApp class missing"
    method = _method_node(cls, "_ai_loop")
    for node in ast.walk(method):
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
            if node.value.id == "lv":
                raise AssertionError("_ai_loop must NOT touch lv.* (thread-safety)")
```

> 同时删过时契约：`test_face_detect_on_enter_starts_ai_thread` / `test_face_detect_on_exit_stops_ai_thread` / `test_face_detect_on_frame_collects_gc_each_frame`（on_enter/on_exit/on_frame 已不存在）。gc 契约改为守 `_ai_loop`（已有逻辑）。

- [ ] **Step 2: 跑测试确认红**

```
cd e:/LBS-Project/CanMV/CamerAi
python tests/test_face_detect.py
```
预期：新测试 FAIL（face_detect 还是 BaseScript，无 run）。

- [ ] **Step 3: 重构 scripts/face_detect/app.py 为 run(runtime) 脚本**

**迁移要点**（实施时按现有 face_detect 代码逐项处理）：

1. **删 BaseScript 继承**：`class FaceDetectApp(BaseScript)` → `class FaceDetectApp`
2. **删 on_enter/on_frame/on_exit/on_key**，逻辑并入 `run()` + 主循环
3. **构造改 `__init__(self, runtime)`**，存 `self.rt = runtime`
4. **ctx→rt 映射**：
   - `self.ctx.lcd.get_sensor()` → `self.rt.sensor`
   - `self.ctx.host` → `self.rt.host`
   - `self.ctx.lang` → `self.rt.lang`
   - `self.ctx.buzzer` → `self.rt.buzzer`
   - `self.ctx.request_exit()` → `self._exit_requested = True`
5. **`run()` 方法**（替代 on_enter + 主循环）：
   ```python
   def run(self):
       self._init_db()
       self._init_ai_models()   # 不再调 ensure_sensor_running（runtime 已 sensor.run）
       self._build_ui()
       self._ai_running = True
       _thread.start_new_thread(self._ai_loop, ())
       # 主循环
       from machine import Pin
       k2 = Pin(0, Pin.IN, Pin.PULL_UP)
       k2_last = 1
       while not self._exit_requested:
           os.exitpoint()
           th = lv.task_handler()
           try:
               cur = k2.value()
               if k2_last == 1 and cur == 0:
                   self._register_pending = True
               k2_last = cur
           except Exception:
               pass
           try:
               self.rt.host.poll_handshake()
           except Exception:
               pass
           self._tick_toast()
           time.sleep_ms(th if th > 0 else 5)
       # 退出
       self._ai_running = False
       for _ in range(100):
           if not self._ai_thread_alive:
               break
           time.sleep_ms(10)
       self._deinit_ai_models()
       self._flush_db()
       self._destroy_ui()
   ```
6. **`_ai_loop`**：复用裸跑已验证结构（snapshot chn0+chn2 + face_det.run + 识别 + 注册检查 + _draw_overlay + show_image + UART + gc）。不碰 lv。`sensor = self.rt.sensor`。
7. **`_draw_overlay`**：画到 img_0（chn0 帧），十字架+人脸框+ID，坐标映射 chn2 1024×768 → img 640×480
8. **`_do_register(frame)`**：接收 frame 参数（不持久化 _current_frame_data 跨帧）
9. **返回按钮回调**：`self._exit_requested = True`（替代 ctx.request_exit）
10. **保留不变**：FaceDetApp/FaceRegistrationApp 类、_search_face、face_db 调用、UART 协议、_build_ui/_build_top_bar/_build_bottom_bar、弹窗/toast 逻辑
11. **模块末尾**：
    ```python
    def run(runtime):
        app = FaceDetectApp(runtime)
        app.run()
    ```

- [ ] **Step 4: AST 检查 + 跑测试确认绿**

```
cd e:/LBS-Project/CanMV/CamerAi
python -c "import ast; ast.parse(open('scripts/face_detect/app.py', encoding='utf-8').read()); print('OK')"
python tests/test_face_detect.py
```
预期：AST OK；契约测试 PASS。

- [ ] **Step 5: 提交**

```
cd e:/LBS-Project/CanMV/CamerAi
git add scripts/face_detect/app.py tests/test_face_detect.py
git commit -m "refactor(face_detect): 迁移到 reset 框架 run(runtime) 独立脚本

BaseScript/on_enter/on_frame/on_exit → run(runtime)+主循环。
双线程: AI线程(snapshot+NPU+画叠加+show+gc+UART,不碰LVGL) + 主线程
(task_handler+K2+握手+toast+退出)。sensor.run 由 runtime.init_app 完成。
复用裸跑已验证稳定结构。ctx.* → rt.*。

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 2.2: face_detect 板端验收（用户手动，门控）

- [ ] **Step 1: 部署 + 验收 1（主菜单）**

部署 `main.py` + `core/app_runtime.py` + `scripts/face_detect/app.py` → SD 卡。上电 → 主菜单正常显示。

- [ ] **Step 2: 验收 2（face_detect 连跑 5 分钟，核心）**

点 face_detect → reset → 进 face_detect → **静置 5 分钟不卡死**。串口看 `[FD] fc=N mem=M` 心跳推进、mem 稳定。卡死 → 贴日志回 Phase 1。

- [ ] **Step 3: 验收 3（功能回归）**

对脸看十字架+人脸框+ID；K2 注册 4 脸；保存；退出重进读到 4 脸；UART 上送。

- [ ] **Step 4: 验收 4（反复进出）**

退出 → reset → 主菜单 → 再进 face_detect → 反复 3 次稳定。

> **门控**：验收 2-4 全过才进阶段 3。不稳定则停止回 systematic-debugging。

---

## 阶段 3：settings/camera 迁移

### Task 3.1: settings 迁移为 run(runtime)

**Files:**
- Modify: `scripts/settings/app.py`

- [ ] **Step 1: 重构 settings/app.py**

settings 是 page 模式纯 LVGL UI（语言/蜂鸣/关于），无 sensor。迁移要点：
1. `class SettingsApp(BaseScript)` → `class SettingsApp`
2. `__init__(self)` → `__init__(self, runtime)`，`self.rt = runtime`
3. `on_enter(ctx)` 逻辑 → `run()` 入口（_build_ui）
4. `on_exit` → run 末尾 `_destroy_ui`
5. `self.ctx.lang/config/buzzer` → `self.rt.lang/config/buzzer`
6. 返回按钮 → `self._exit_requested = True`
7. `run()`：_build_ui + 主循环（task_handler + 退出检测）→ _destroy_ui
8. 模块末尾 `def run(runtime): SettingsApp(runtime).run()`

> 现有方法（_build_ui/_build_left_row/_select_item/_render_right/_render_language/_on_lang/_set_lang/_render_about/_refresh_texts/_destroy_ui）逻辑不变，只改 ctx→rt + 生命周期入口。

- [ ] **Step 2: AST 检查 + 板端验收**

```
cd e:/LBS-Project/CanMV/CamerAi
python -c "import ast; ast.parse(open('scripts/settings/app.py', encoding='utf-8').read()); print('OK')"
```
板端：点 settings → reset → 进设置 → 改语言 → 退出回主菜单 → 语言生效。

- [ ] **Step 3: 提交**

```
cd e:/LBS-Project/CanMV/CamerAi
git add scripts/settings/app.py
git commit -m "refactor(settings): 迁移到 reset 框架 run(runtime)

BaseScript/on_enter/on_exit → run(runtime)。纯 LVGL UI 无 sensor。
ctx.* → rt.*。返回按钮设退出标志。

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 3.2: camera 迁移为 run(runtime)

**Files:**
- Modify: `scripts/camera/app.py`

- [ ] **Step 1: 重构 camera/app.py**

camera 是 stream 模式（snapshot+show_image+拍照/录像/图库）。迁移要点：
1. `class CameraApp(BaseScript)` → `class CameraApp`
2. `__init__(self)` → `__init__(self, runtime)`，`self.rt = runtime`
3. `on_enter/on_frame/on_exit` → `run()` 入口 + 主循环（task_handler + K2 + snapshot/show_image + 拍照/录像/图库逻辑）
4. `self.ctx.lcd.get_sensor()` → `self.rt.sensor`
5. `self.ctx.lcd.capture_chn` → `CAM_CHN_ID_1`（runtime 为 camera 配了 chn1）
6. 删 `_init_camera` 的 `ensure_sensor_running`（runtime.init_app 已 sensor.run）
7. `self.ctx.host/lang/buzzer` → `self.rt.*`
8. 返回按钮 → `self._exit_requested = True`
9. `run()`：_build_ui + 主循环 → 停推帧 + _destroy_ui
10. 模块末尾 `def run(runtime): CameraApp(runtime).run()`

> 现有方法（_init_camera/_stop_camera/_build_ui/_build_top_bar/_build_preview_area/_build_bottom_bar/_on_mode_toggle/_on_shutter/_capture_photo/_flash_feedback/图库/录像...）逻辑不变，只改生命周期入口 + ctx→rt + sensor 来源。

- [ ] **Step 2: AST 检查 + 板端验收**

```
cd e:/LBS-Project/CanMV/CamerAi
python -c "import ast; ast.parse(open('scripts/camera/app.py', encoding='utf-8').read()); print('OK')"
```
板端：点 camera → reset → 进相机 → 预览/拍照/录像/图库正常 → 退出回主菜单。

- [ ] **Step 3: 提交**

```
cd e:/LBS-Project/CanMV/CamerAi
git add scripts/camera/app.py
git commit -m "refactor(camera): 迁移到 reset 框架 run(runtime)

BaseScript/on_enter/on_frame/on_exit → run(runtime)+主循环。
sensor.run 由 runtime.init_app。chn0 预览/chn1 拍照。ctx.* → rt.*。

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## 阶段 4：删除旧架构 + 清理

### Task 4.1: 删除 ScriptRunner / BaseScript / lcd 旧架构

**Files:**
- Delete: `core/script_runner.py`
- Evaluate: `scripts/_base.py`, `hw/lcd.py`

- [ ] **Step 1: 确认无引用**

```
cd e:/LBS-Project/CanMV/CamerAi
grep -rn "ScriptRunner\|from scripts._base\|BaseScript\|runner.launch\|runner.tick\|from hw.lcd\|import lcd\|LCD(" scripts/ core/ main.py ui/ 2>/dev/null
```
预期：无业务代码引用。

- [ ] **Step 2: 删 core/script_runner.py**

```
cd e:/LBS-Project/CanMV/CamerAi
git rm core/script_runner.py
```

- [ ] **Step 3: 评估 _base.py / lcd.py**

根据 Step 1 grep 结果：
- `_base.py` 无引用 → `git rm scripts/_base.py`；有引用（如 BackBar）→ 保留共享部分
- `lcd.py` 无引用 → `git rm hw/lcd.py`；MainMenu 等用其常量 → 保留常量

- [ ] **Step 4: AST + 全量测试**

```
cd e:/LBS-Project/CanMV/CamerAi
python -c "import ast; ast.parse(open('main.py',encoding='utf-8').read()); print('main OK')"
python tests/test_framework.py
python tests/test_face_detect.py
```
预期：全 PASS。

- [ ] **Step 5: 提交**

```
cd e:/LBS-Project/CanMV/CamerAi
git add -A
git commit -m "chore: 删除旧同进程架构 — ScriptRunner/BaseScript/lcd

reset 框架全 APP 迁移完成,无两套并存。删 core/script_runner.py。
按引用评估删 scripts/_base.py / hw/lcd.py。所有 APP 走 reset+run(runtime)。

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 4.2: 清理诊断脚本 + 更新文档

**Files:**
- Delete: `test_face_baseline.py`, `test_face_baseline_camerai_sensor.py`, `test_face_baseline_with_menu_sim.py`
- Modify: K230 坑记忆 + 项目记录

- [ ] **Step 1: 删诊断脚本**

```
cd e:/LBS-Project/CanMV/CamerAi
git rm test_face_baseline.py test_face_baseline_camerai_sensor.py test_face_baseline_with_menu_sim.py
```

- [ ] **Step 2: 更新 K230 坑记忆**

更新 `C:\Users\24160\.claude\projects\e--LBS-Project\memory\camerai-k230-pitfalls.md`：
- 坑#14/15 标注"reset 架构下每进程独立 init，常驻前提已变"
- 新增坑#17："同进程脚本切换+常驻显示栈与 K230 硬件状态管理冲突，sensor.run 后须有消费者持续 snapshot 否则缓冲满 DMA 卡死；正解是 reset 切换（每进程独立 init），对齐官方综合例程"

- [ ] **Step 3: 提交**

```
cd e:/LBS-Project/CanMV/CamerAi
git add -A
git commit -m "docs: 清理诊断脚本 + 更新 K230 坑记忆(reset 架构)

删 test_face_baseline*.py。更新坑#14/15(reset 前提)、新增坑#17(同进程冲突)。

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## 自检（Plan Self-Review）

- ✅ **Spec coverage**：架构（Task 1.1/1.2）、app_runtime（1.1）、main.py（1.2）、face_detect（2.1）、settings/camera 迁移 + 删旧（3.1/3.2/4.1）、测试（各 Task + 板端 2.2/3.1/3.2）、K230 约束（实现遵守）。
- ✅ **No placeholders**：Task 1.1/1.2 完整代码。Task 2.1/3.1/3.2 给迁移要点 + run() 骨架（迁移任务本质是改造现有代码，要点明确：ctx→rt、生命周期入口、sensor 来源）。Task 4 给 grep 确认命令。
- ✅ **Type consistency**：`AppRuntime`(init_menu/init_app/cleanup)、`run(runtime)` 入口、`self.rt`、`_ai_loop`/`_draw_overlay`/`_do_register`、category_id 串与 categories.json 一致。
- ⚠️ **迁移 Task 粒度**：2.1/3.1/3.2 是大迁移，给骨架+要点。建议 subagent 执行时给完整 spec + 现有代码整体迁移。
