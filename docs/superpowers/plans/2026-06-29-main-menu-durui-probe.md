# DurUI Probe Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a standalone `main_menu_durui_probe.py` that runs the existing `MainMenu` UI on a verbatim DurUI display stack, to bisect whether the GC-after-crash comes from the display stack or the menu UI.

**Architecture:** Single self-contained file. Copy DurUI's `LCD`, `Touch`, buzzer, `lvgl_init` (DIRECT + opaque-black buffers), `lvgl_flush_cb` (single-layer `show_image`, no clear, no layer), and 3ms main loop verbatim. Reuse `ui.main_menu.MainMenu` + `core.config_manager` + `core.lang`. Add a `_diag_tick` that prints mem each second and runs proactive `gc.collect()` at seq 5, called only after `lv.task_handler()` returns. No sensor, no OSD2, no reset framework.

**Tech Stack:** MicroPython on K230, LVGL Python bindings, CanMV `media.display`/`media.media`/`image`, `machine.{FPIOA,Pin,PWM,TOUCH}`. Host-side AST contract tests via `python` (no pytest installed).

---

## File Structure

- Create: `main_menu_durui_probe.py` — self-contained probe entry. Owns LCD/Touch/buzzer/lvgl init, main loop, GC diagnostic, and MainMenu construction. Verbatim DurUI display stack.
- Create: `tests/test_main_menu_durui_probe_ast.py` — host-side AST contracts proving the file replicates the DurUI display path and reuses MainMenu. Avoids importing lvgl.
- Reference only: `ui/main_menu.py`, `core/config_manager.py`, `core/lang.py` — reused as-is, not modified.

Note on `config_manager.ConfigManager`: its constructor hardcodes `/sdcard/CamerAi/config/...` paths, so on the board the probe reads the real config. On PC it would fail file I/O but the probe file is never executed on PC (only AST-tested).

---

### Task 1: Add Failing AST Contract Tests

**Files:**
- Create: `tests/test_main_menu_durui_probe_ast.py`
- Test: `tests/test_main_menu_durui_probe_ast.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_main_menu_durui_probe_ast.py`:

```python
# tests/test_main_menu_durui_probe_ast.py — host-side AST contracts for the DurUI probe
import ast
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROBE_PATH = os.path.join(ROOT, "main_menu_durui_probe.py")


def _src():
    with open(PROBE_PATH, "r", encoding="utf-8") as f:
        return f.read()


def _class_node(tree, name):
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == name:
            return node
    raise AssertionError("class %s missing" % name)


def _method_src(src, class_name, method_name):
    tree = ast.parse(src)
    cls = _class_node(tree, class_name)
    for node in cls.body:
        if isinstance(node, ast.FunctionDef) and node.name == method_name:
            return ast.get_source_segment(src, node)
    raise AssertionError("%s.%s missing" % (class_name, method_name))


def test_probe_reuses_main_menu():
    """Probe must reuse the existing MainMenu UI, not reimplement cards."""
    src = _src()
    assert "from ui.main_menu import MainMenu" in src
    assert "MainMenu(" in src


def test_lcd_display_init_has_no_osd_num():
    """LCD must init display without osd_num (DurUI single-layer path)."""
    src = _src()
    init_body = _method_src(src, "LCD", "__init__")
    assert "Display.ST7701" in init_body
    assert "osd_num" not in init_body
    assert "MediaManager.init" in init_body


def test_lcd_lvgl_init_uses_direct_and_opaque_black():
    """lvgl_init must use DIRECT and prime buffers with opaque black."""
    body = _method_src(_src(), "LCD", "lvgl_init")
    assert "lv.DISP_RENDER_MODE.DIRECT" in body
    assert "draw_rectangle" in body
    assert "(0, 0, 0)" in body
    assert "fill=True" in body


def test_flush_cb_single_layer_no_clear_no_layer():
    """flush_cb must show_image the matching buffer without layer= or bytearray(0)."""
    body = _method_src(_src(), "LCD", "lvgl_flush_cb")
    assert "self.display.show_image(self.draw_buf_1)" in body
    assert "self.display.show_image(self.draw_buf_2)" in body
    assert "layer=" not in body
    assert "bytearray(0)" not in body
    assert "disp.flush_ready()" in body


def test_main_loop_calls_task_handler_then_diag_then_sleep():
    """Main loop: task_handler, then diag (with proactive gc), then sleep_ms."""
    src = _src()
    assert "def main(" in src
    assert "lv.task_handler()" in src
    assert "_diag_tick" in src
    assert "gc.collect" in src
    assert "time.sleep_ms" in src
    # No sensor, no osd_num anywhere
    assert "_config_sensor" not in src
    assert "sensor.run" not in src
    assert "LAYER_OSD2" not in src


def test_diag_runs_proactive_gc_after_task_handler_pattern():
    """_diag_tick must do proactive gc.collect at seq 5."""
    body = _method_src(_src(), "_ProbeState", "_diag_tick") if "class _ProbeState" in _src() else _src()
    assert "gc.collect" in body
    assert "proactive gc begin" in body
    assert "proactive gc end" in body
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
python -c "import importlib.util,sys; s=importlib.util.spec_from_file_location('t','tests/test_main_menu_durui_probe_ast.py'); m=importlib.util.module_from_spec(s); s.loader.exec_module(m); f=0;
import os
for n in sorted(x for x in dir(m) if x.startswith('test_')):
    try: getattr(m,n)(); print('PASS',n)
    except AssertionError as e: f+=1; print('FAIL',n,e)
sys.exit(1 if f else 0)"
```

Expected: FAIL — `main_menu_durui_probe.py` does not exist (FileNotFoundError or parse error on empty read). All six tests fail because the probe file is missing.

- [ ] **Step 3: Commit the failing tests**

```bash
git add tests/test_main_menu_durui_probe_ast.py
git commit -m "test(durui_probe): add AST contracts for display-stack replication"
```

---

### Task 2: Implement the Probe Display Stack and Main Loop

**Files:**
- Create: `main_menu_durui_probe.py`
- Test: `tests/test_main_menu_durui_probe_ast.py`

- [ ] **Step 1: Write the probe file**

Create `main_menu_durui_probe.py` with this content. This is a verbatim-faithful copy of DurUI's display stack (LCD, Touch, buzzer, flush_cb, DIRECT + opaque-black buffers, 3ms loop) plus reuse of the existing MainMenu.

```python
# main_menu_durui_probe.py — DurUI 显示栈最小复刻对照实验
#
# 目的:用 DurUI 原样的显示栈,跑我们现有 ui.main_menu.MainMenu 卡片 UI,
# 看主动 GC 后是否还卡。二分定位:
#   不卡 → 显示栈是元凶(app_runtime.init_menu 显示链路)
#   仍卡 → MainMenu UI 本身是元凶,与显示栈无关
#
# 不配 sensor、不申请 OSD2、不走 reset 框架、不动各识别脚本。
# 仅用于板端实验,跑完还原 /sdcard/main.py。

import gc
import os
import time

import lvgl as lv
import uctypes

from media.display import Display
from media.media import MediaManager
import image

from machine import FPIOA
from machine import Pin
from machine import PWM
from machine import TOUCH


DISPLAY_WIDTH = 640
DISPLAY_HEIGHT = 480
MENU_LV_TASK_SLEEP_MS = 3  # 与 DurUI 主循环一致

# 蜂鸣器:Pin60 → PWM0(对齐 DurUI)
BUZZER_PIN = 60
BUZZER_PWM_CH = 0
BUZZER_FREQ_HZ = 4000
BUZZER_DUTY_ON = 50
BUZZER_DUTY_OFF = 100
_buzzer_pwm = None


class LCD:
    """逐行照搬 DurUI.LCD:Display.ST7701 + MediaManager + 背光 + DIRECT 双缓冲。"""

    def __init__(self, width=DISPLAY_WIDTH, height=DISPLAY_HEIGHT, to_ide=False, fpioa=None, bl_pinx=5, bl_valid=1):
        self._width = width
        self._height = height
        self._to_ide = to_ide
        self.display = Display()
        self.display.init(Display.ST7701, width, height, to_ide=to_ide, quality=100)
        MediaManager.init()

        fpioa.set_function(bl_pinx, fpioa.GPIO0 + bl_pinx)
        pull = Pin.PULL_UP if bl_valid == 0 else Pin.PULL_DOWN
        self.bl = Pin(bl_pinx, Pin.OUT, pull=pull, drive=7)
        self.bl_valid = bl_valid
        self.on()

    def on(self):
        self.bl.value(self.bl_valid)

    def lvgl_flush_cb(self, disp, area, px_map):
        if disp.flush_is_last():
            if self.draw_buf_1.virtaddr() == uctypes.addressof(px_map.__dereference__()):
                self.display.show_image(self.draw_buf_1)
            else:
                self.display.show_image(self.draw_buf_2)
        disp.flush_ready()

    def lvgl_init(self, width, height):
        self.draw_buf_1 = image.Image(width, height, image.BGRA8888)
        self.draw_buf_2 = image.Image(width, height, image.BGRA8888)
        # 先铺纯黑像素,避免缓冲初始值异常(DIRECT 无下层画面时必须不透明)。
        for _fb in (self.draw_buf_1, self.draw_buf_2):
            _fb.draw_rectangle(0, 0, width, height, color=(0, 0, 0), thickness=1, fill=True)
        self.disp = lv.disp_create(width, height)
        self.disp.set_flush_cb(self.lvgl_flush_cb)
        self.disp.set_draw_buffers(
            self.draw_buf_1.bytearray(),
            self.draw_buf_2.bytearray(),
            self.draw_buf_1.size(),
            lv.DISP_RENDER_MODE.DIRECT,
        )


class Touch:
    """逐行照搬 DurUI.Touch:TOUCH(0) + lv indev pointer + read_cb。"""

    def __init__(self):
        self.touch = TOUCH(0)

    def __del__(self):
        del self.touch

    def lvgl_read_cb(self, indev, data):
        x, y, state = 0, 0, lv.INDEV_STATE.RELEASED
        tp = self.touch.read(1)
        if len(tp):
            x, y, event = tp[0].x, tp[0].y, tp[0].event
            if event in (TOUCH.EVENT_DOWN, TOUCH.EVENT_MOVE):
                state = lv.INDEV_STATE.PRESSED
        data.point = lv.point_t({'x': x, 'y': y})
        data.state = state

    def lvgl_init(self):
        self.indev = lv.indev_create()
        self.indev.set_type(lv.INDEV_TYPE.POINTER)
        self.indev.set_read_cb(self.lvgl_read_cb)


def _buzzer_init(fpioa):
    global _buzzer_pwm
    try:
        fpioa.set_function(BUZZER_PIN, fpioa.PWM0 + BUZZER_PWM_CH)
        _buzzer_pwm = PWM(BUZZER_PWM_CH, BUZZER_FREQ_HZ, BUZZER_DUTY_OFF, enable=True)
    except BaseException as e:
        _buzzer_pwm = None
        print("[probe] buzzer init fail:", e)


def _buzzer_beep(ms=80):
    if _buzzer_pwm is None:
        return
    try:
        _buzzer_pwm.duty(BUZZER_DUTY_ON)
        time.sleep_ms(ms)
        _buzzer_pwm.duty(BUZZER_DUTY_OFF)
    except BaseException:
        pass


class _ProbeState:
    """板端诊断状态:每秒打印 mem,seq==5 主动 GC(仅在 task_handler 返回后)。"""

    def __init__(self):
        self.last_ms = 0
        self.seq = 0

    def _diag_tick(self):
        try:
            now = time.ticks_ms()
            if self.last_ms == 0:
                self.last_ms = now
                return
            if time.ticks_diff(now, self.last_ms) < 1000:
                return
            self.seq += 1
            try:
                mem = gc.mem_free()
            except Exception:
                mem = -1
            print("[probe-diag] seq=%d mem=%d" % (self.seq, mem))
            if self.seq == 5:
                print("[probe-diag] proactive gc begin")
                gc.collect()
                print("[probe-diag] proactive gc end mem=%d" % gc.mem_free())
            self.last_ms = now
        except Exception as e:
            print("[probe-diag] failed: %s" % e)


def _on_card_click(category_id):
    """本实验不进脚本(不触碰 reset 框架),仅打印。"""
    print("[probe] card click:", category_id)


def main():
    print("[probe] === DurUI display-stack probe start ===")
    fpioa = FPIOA()

    lcd = LCD(DISPLAY_WIDTH, DISPLAY_HEIGHT, to_ide=False, fpioa=fpioa, bl_pinx=5, bl_valid=1)
    touch = Touch()
    _buzzer_init(fpioa)

    lv.init()
    lcd.lvgl_init(DISPLAY_WIDTH, DISPLAY_HEIGHT)
    touch.lvgl_init()

    # 字体:MainMenu 内部用 core.font_manager.fonts;首次 task_handler 前加载。
    try:
        from core.font_manager import fonts
        fonts.load_all()
    except Exception as e:
        print("[probe] font load warning:", e)

    # 配置/语言:复用 core,均不依赖 sensor。
    from core.config_manager import ConfigManager
    from core.lang import LangManager
    config = ConfigManager()
    config.load()
    lang = LangManager()
    lang.load(config.get('lang', 'zh_CN'))

    from ui.main_menu import MainMenu
    menu = MainMenu(config, _buzzer_pwm if False else _BuzzerShim(), lang, on_card_click=_on_card_click)

    # preload_icons 必须在首次 task_handler 前(文件 I/O 安全窗口)。
    menu.preload_icons()
    menu.show()

    _buzzer_beep(80)
    print("[probe] main menu running")

    state = _ProbeState()
    while True:
        os.exitpoint()
        lv.task_handler()
        state._diag_tick()
        time.sleep_ms(MENU_LV_TASK_SLEEP_MS)


class _BuzzerShim:
    """MainMenu 期望 buzzer.beep(ms=...);对齐 DurUI PWM 行为。"""

    def beep(self, ms=50):
        _buzzer_beep(ms)

    def set_enabled(self, enabled):
        pass


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Fix the MainMenu buzzer wiring**

The probe passes `_BuzzerShim()` as `buzzer`. The `_buzzer_pwm if False else _BuzzerShim()` expression is intentionally simple but slightly awkward. Replace the MainMenu construction line to make intent clear.

In `main_menu_durui_probe.py`, replace:

```python
    menu = MainMenu(config, _buzzer_pwm if False else _BuzzerShim(), lang, on_card_click=_on_card_click)
```

with:

```python
    buzzer = _BuzzerShim()
    menu = MainMenu(config, buzzer, lang, on_card_click=_on_card_click)
```

- [ ] **Step 3: Run tests to verify they pass**

Run:

```bash
python -c "import importlib.util,sys; s=importlib.util.spec_from_file_location('t','tests/test_main_menu_durui_probe_ast.py'); m=importlib.util.module_from_spec(s); s.loader.exec_module(m); f=0;
for n in sorted(x for x in dir(m) if x.startswith('test_')):
    try: getattr(m,n)(); print('PASS',n)
    except AssertionError as e: f+=1; print('FAIL',n,e)
sys.exit(1 if f else 0)"
```

Expected: all six tests PASS.

- [ ] **Step 4: Compile-check the probe**

Run:

```bash
python -m compileall main_menu_durui_probe.py
```

Expected: `Compiling 'main_menu_durui_probe.py'...` with no error. (Cross-compiles on CPython; K230 MicroPython syntax is identical for the constructs used.)

- [ ] **Step 5: Commit the probe**

```bash
git add main_menu_durui_probe.py
git commit -m "feat(durui_probe): verbatim DurUI display stack running existing MainMenu"
```

---

### Task 3: Board Bisect Validation

**Files:**
- No code changes; board deployment and observation only.

- [ ] **Step 1: Ensure MainMenu diagnostics are off for the probe run**

Open `ui/main_menu.py`. Confirm these constants are:

```python
MENU_DIAG_MEM = False
MENU_DIAG_FORCE_GC_AT_SEQ = 0
```

If they are `True` / `5` from earlier experiments, set them back to `False` / `0`. The probe uses its own `_diag_tick`, so MainMenu's internal diagnostics must be off to avoid duplicate prints.

Do not commit this if it only reverts a temporary diagnostic state already intended to be off. If a commit is needed:

```bash
git add ui/main_menu.py
git commit -m "chore(main_menu): disable diagnostics for durui probe run"
```

- [ ] **Step 2: Deploy files to the board**

Copy these two files to the SD card:

```text
/sdcard/CamerAi/main_menu_durui_probe.py
/sdcard/CamerAi/ui/main_menu.py   (with MENU_DIAG_MEM=False)
```

Also ensure `/sdcard/CamerAi/core/config_manager.py`, `/sdcard/CamerAi/core/lang.py`, `/sdcard/CamerAi/core/font_manager.py`, `/sdcard/CamerAi/ui/theme.py`, `/sdcard/CamerAi/config/categories.json`, and `/sdcard/CamerAi/resource/icons/` are present (they are the reused dependencies; normally already on the board).

- [ ] **Step 3: Run the probe instead of the normal main**

Temporarily make the board execute the probe. Safest method: back up and replace `/sdcard/main.py` with a one-line launcher:

```python
import main_menu_durui_probe
main_menu_durui_probe.main()
```

Save the original `/sdcard/main.py` first (e.g. copy to `/sdcard/main_real.py`) so it can be restored after the experiment.

- [ ] **Step 4: Observe: does the menu show a picture?**

Power on. Expected: black background with card menu visible (DIRECT + opaque-black buffers). If still black screen, record it — that itself is a finding (the menu UI draws but nothing reaches the panel under DIRECT on this stack).

- [ ] **Step 5: Observe: does GC at seq 5 crash?**

Scroll and idle until the serial shows:

```text
[probe-diag] seq=5 ...
[probe-diag] proactive gc begin
[probe-diag] proactive gc end mem=...
```

Then continue scrolling.

Record one of two outcomes:

- **Outcome A (not crashed):** `gc end` prints, scrolling continues, no hang. → Display stack is the culprit. Next step: switch `app_runtime.init_menu` to this DurUI stack.
- **Outcome B (still crashed):** `gc end` prints (or hangs inside begin), then hangs/black screen. → MainMenu UI is the culprit, display stack is irrelevant. Next step: hunt allocation sources inside MainMenu.

- [ ] **Step 6: Restore the normal main.py**

After recording the outcome, restore the original board `/sdcard/main.py`:

```text
copy /sdcard/main_real.py back to /sdcard/main.py
```

- [ ] **Step 7: Record the bisect conclusion**

Append to `项目记录.md` a section `## 2026-06-29 DurUI probe bisect result` with:

```text
- 菜单是否有画面:<是/否>
- seq=5 proactive gc end 后:<继续滚动/卡死>
- 二分结论:<Outcome A 显示栈元凶 / Outcome B MainMenu UI 元凶>
- 下一步方向:<据此决定>
```

Commit:

```bash
git add 项目记录.md
git commit -m "docs(durui_probe): record bisect conclusion"
```

---

## Self-Review

- Spec coverage: spec sections covered — verbatim DurUI stack (LCD/Touch/buzzer/lvgl_init DIRECT+opaque black/flush_cb single-layer no clear/3ms loop) in Task 2; reuse MainMenu + config/lang in Task 2; GC diagnostic after task_handler in Task 2 `_diag_tick`; AST contract tests in Task 1; board bisect in Task 3. No spec section left without a task.
- Placeholder scan: no TBD/TODO; all code blocks are complete and concrete. The board-side `/sdcard/main.py` swap is given as exact content.
- Type consistency: `LCD.lvgl_flush_cb`, `LCD.lvgl_init`, `Touch.lvgl_init`, `_ProbeState._diag_tick`, `_BuzzerShim.beep` — names match between tests and implementation. `_diag_tick` test falls back to whole-source scan if `_ProbeState` not found, but the implementation defines `_ProbeState` so the method-source path is used.
