# Main Menu Scroll Memory Stability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stabilize the K230 main menu so scrolling no longer steadily consumes memory or crashes when GC runs.

**Architecture:** First add host-side source contract tests that fail against the current Python custom LVGL animation path. Then add a board-visible diagnostic/safe-mode path and replace scroll visual updates with DurUI-style distance-driven updates on existing LVGL objects. Display render mode and flush behavior remain unchanged unless this plan's animation hypothesis fails on board.

**Tech Stack:** MicroPython on K230, LVGL Python bindings, pytest host-side AST/source tests, existing `ui/main_menu.py` menu implementation.

---

## File Structure

- Modify: `ui/main_menu.py`
  - Owns main menu UI, card construction, scroll callbacks, snap selection, and card visual state.
  - Add diagnostic constants and memory logging helpers.
  - Remove rolling dependence on Python `lv.anim_t` custom callbacks for scroll visual state.
  - Add DurUI-style `apply_scroll_visual(t)` on `_CardSlot`.
- Create: `tests/test_main_menu_memory_ast.py`
  - Host-side source contract tests that avoid importing `lvgl`.
  - Guards against reintroducing `set_custom_exec_cb` into scroll visual state.
  - Verifies distance-driven visual update API exists.
- Reference only: `core/app_runtime.py`
  - No change in this plan. `FULL + OSD2` stays unchanged for the first fix pass.

---

### Task 1: Add Failing Source Contract Tests

**Files:**
- Create: `tests/test_main_menu_memory_ast.py`
- Test: `tests/test_main_menu_memory_ast.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_main_menu_memory_ast.py` with this content:

```python
# tests/test_main_menu_memory_ast.py — host-side source contracts for main menu memory stability
import ast
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MAIN_MENU_PATH = os.path.join(ROOT, "ui", "main_menu.py")


def _read_main_menu():
    with open(MAIN_MENU_PATH, "r", encoding="utf-8") as f:
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


def test_scroll_visual_api_exists():
    """Cards must expose distance-driven visual update used during scroll."""
    src = _read_main_menu()
    assert "def apply_scroll_visual" in src
    body = _method_src(src, "_CardSlot", "apply_scroll_visual")
    assert "set_style_transform_zoom" in body
    assert "set_x" in body or "set_pos" in body
    assert "set_style_opa" in body


def test_set_visual_state_does_not_start_python_anim():
    """Selection state must not create Python custom LVGL animations while scrolling."""
    src = _read_main_menu()
    body = _method_src(src, "_CardSlot", "set_visual_state")
    assert "_animate_geometry" not in body
    assert "lv.anim_t" not in body
    assert "set_custom_exec_cb" not in body


def test_scroll_callback_uses_distance_visuals():
    """Scroll path must update existing card objects instead of starting animations."""
    src = _read_main_menu()
    scroll_body = _method_src(src, "MainMenu", "_on_scroll")
    assert "_apply_scroll_visuals" in scroll_body
    assert "_update_snap" not in scroll_body
    visuals_body = _method_src(src, "MainMenu", "_apply_scroll_visuals")
    assert "apply_scroll_visual" in visuals_body
    assert "gc.collect" not in visuals_body
    assert "lv.anim_t" not in visuals_body


def test_python_custom_animation_not_used_for_scroll_geometry():
    """The old geometry animation implementation must not remain active in scroll visuals."""
    src = _read_main_menu()
    assert "set_custom_exec_cb(_anim_cb)" not in src
    assert "def _animate_geometry" not in src
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
pytest tests/test_main_menu_memory_ast.py -q
```

Expected: FAIL. The first failure should mention `def apply_scroll_visual` missing, and later assertions should point to `_animate_geometry` / `set_custom_exec_cb` still present.

- [ ] **Step 3: Commit the failing tests**

```bash
git add tests/test_main_menu_memory_ast.py
git commit -m "test(main_menu): capture scroll memory stability contracts"
```

---

### Task 2: Add Diagnostics and Remove Scroll Animation Allocation

**Files:**
- Modify: `ui/main_menu.py:49-78`
- Modify: `ui/main_menu.py:257-350`
- Modify: `ui/main_menu.py:390-663`
- Test: `tests/test_main_menu_memory_ast.py`

- [ ] **Step 1: Add constants and diagnostic state**

In `ui/main_menu.py`, near the existing constants after `GEOM_ANIM_TIME = 280`, add:

```python
# Board diagnostic: prints once per second while menu is alive. Keep False for normal builds.
MENU_DIAG_MEM = False
MENU_DIAG_FORCE_GC_AT_SEQ = 0  # 0=disabled; positive value runs gc at that printed seq.

# Avoid Python lv.anim_t custom callbacks on K230 scroll path; they allocate and can pin closures.
USE_PY_SCROLL_ANIM = False

ZOOM_SELECTED = 256
ZOOM_NORMAL = 205  # 80% visual scale, replacing width shrink animation.
CARD_SHIFT_X = CARD_W - CARD_W_NORMAL
```

In `MainMenu.__init__`, after `self._icon_cache = {}`, add:

```python
        self._diag_last_ms = 0
        self._diag_seq = 0
```

- [ ] **Step 2: Replace scroll callback with distance visual update**

Replace `MainMenu._on_scroll()` with:

```python
    def _on_scroll(self, event):
        """滚动中：按卡片距视口中心的距离更新视觉，不创建 Python 动画。"""
        self._is_scrolling = True
        self._apply_scroll_visuals(update_selection=True)
        self._diag_mem_tick("scroll")
```

Add this method inside `MainMenu`, immediately before `_update_snap()`:

```python
    def _apply_scroll_visuals(self, update_selection=False):
        """DurUI 风格滚动视觉：已有对象 set zoom/x/opa，避免 lv.anim_t 闭包分配。"""
        if not self._cards:
            return
        center_y = self._scroll.get_scroll_y() + self._scroll.get_height() // 2
        nearest_idx = self._find_nearest(center_y)
        step = CARD_H + CARD_GAP
        for card in self._cards:
            card_cy = card.y + CARD_H // 2
            dist = abs(card_cy - center_y)
            t = dist / float(step)
            if t > 1.0:
                t = 1.0
            card.apply_scroll_visual(t)
        if update_selection:
            self._selected_index = nearest_idx
```

Add this method inside `MainMenu`, after `_apply_scroll_visuals()`:

```python
    def _diag_mem_tick(self, reason):
        """板端诊断：每秒打印 mem；主动 GC 只在 task_handler 返回后的调用点开启。"""
        if not MENU_DIAG_MEM:
            return
        try:
            now = time.ticks_ms()
            if self._diag_last_ms == 0:
                self._diag_last_ms = now
                return
            if time.ticks_diff(now, self._diag_last_ms) < 1000:
                return
            self._diag_seq += 1
            mem = gc.mem_free()
            print("[MainMenu-diag] seq=%d reason=%s selected=%d mem=%d" % (
                self._diag_seq, reason, self._selected_index, mem))
            if MENU_DIAG_FORCE_GC_AT_SEQ > 0 and self._diag_seq == MENU_DIAG_FORCE_GC_AT_SEQ:
                print("[MainMenu-diag] proactive gc begin")
                gc.collect()
                print("[MainMenu-diag] proactive gc end mem=%d" % gc.mem_free())
            self._diag_last_ms = now
        except Exception as e:
            print("[MainMenu-diag] failed: %s" % e)
```

- [ ] **Step 3: Update snap and selection to use instant state**

Replace `_on_scroll_end()` with:

```python
    def _on_scroll_end(self, event):
        """滚动结束 → 吸附到最近卡片；视觉仍由距离驱动。"""
        self._is_scrolling = False
        self._snap_to_nearest()
        self._apply_scroll_visuals(update_selection=True)
        self._diag_mem_tick("scroll_end")
```

Replace `_scroll_to()` with:

```python
    def _scroll_to(self, idx, animate=True):
        """滚动使指定卡片居中；不再创建 Python 几何动画。"""
        if idx < 0 or idx >= len(self._cards):
            return
        card = self._cards[idx]
        target_scroll_y = card.y - (self._scroll.get_height() - CARD_H) // 2
        anim = lv.ANIM.ON if animate else lv.ANIM.OFF
        self._scroll.scroll_to_y(max(0, target_scroll_y), anim)
        self._selected_index = idx
        self._apply_scroll_visuals(update_selection=False)
```

Replace `_update_selection()` with:

```python
    def _update_selection(self, idx, animate=True):
        """更新选中索引；视觉由 _apply_scroll_visuals 根据距离统一计算。"""
        if idx < 0 or idx >= len(self._cards):
            return
        self._selected_index = idx
        self._apply_scroll_visuals(update_selection=False)
```

- [ ] **Step 4: Replace `_CardSlot` animation state with distance-driven visual state**

In `_CardSlot.__init__`, replace:

```python
        self._selected = None    # None=未初始化，确保首次 set_visual_state 必应用几何
        self._geom_anim = None   # 当前几何动画(防重叠)
        self._anim_token = 0     # 几何动画版本号:新动画+1,旧 cb 检测过期即 return
```

with:

```python
        self._selected = None
        self._last_visual_key = None
```

Replace the initial state call near the end of `__init__`:

```python
        self.set_visual_state(False)
```

with:

```python
        self.apply_scroll_visual(1.0)
```

- [ ] **Step 5: Replace visual methods in `_CardSlot`**

Replace `set_visual_state`, `_stop_geom_anim`, `_animate_geometry`, and `_apply_geometry` with this code:

```python
    def set_visual_state(self, selected, animate=True):
        """兼容旧调用：选中=中心视觉，非选中=远离中心视觉。"""
        self._selected = selected
        self.apply_scroll_visual(0.0 if selected else 1.0)

    def apply_scroll_visual(self, t):
        """按距中心归一化值更新视觉；t=0 选中，t=1 非选中。"""
        if self.obj is None:
            return
        if t < 0.0:
            t = 0.0
        elif t > 1.0:
            t = 1.0

        zoom = int(ZOOM_SELECTED - t * (ZOOM_SELECTED - ZOOM_NORMAL))
        opa = int(OPA_SELECTED - t * (OPA_SELECTED - OPA_NORMAL))
        dx = int(t * CARD_SHIFT_X)
        x = self.center_x - CARD_W // 2 + dx

        # Quantize to avoid repeatedly writing identical style values on tiny scroll jitter.
        key = (zoom, opa, x)
        if key == self._last_visual_key:
            return
        self._last_visual_key = key

        try:
            self.obj.set_style_transform_pivot_x(CARD_W // 2, 0)
            self.obj.set_style_transform_pivot_y(CARD_H // 2, 0)
            self.obj.set_style_transform_zoom(zoom, 0)
        except Exception:
            # Fallback keeps the menu usable if transform_zoom is unavailable.
            width = int(CARD_W_NORMAL + (1.0 - t) * (CARD_W - CARD_W_NORMAL))
            self.obj.set_size(width, CARD_H)
            x = self.center_x + CARD_W // 2 - width
        self.obj.set_style_opa(opa, 0)
        self.obj.set_pos(x, self.y)
```

- [ ] **Step 6: Remove scroll custom animation code**

Delete the old `_stop_geom_anim()` and `_animate_geometry()` definitions completely. Keep `press_animation()` unchanged for now because it only runs on click, not continuously while scrolling.

- [ ] **Step 7: Run tests to verify they pass**

Run:

```bash
pytest tests/test_main_menu_memory_ast.py -q
```

Expected: PASS.

- [ ] **Step 8: Run related host tests**

Run:

```bash
pytest tests/test_framework.py tests/test_icon_cache.py tests/test_host_api.py tests/test_main_menu_memory_ast.py -q
```

Expected: PASS. If unrelated tests fail because the host environment lacks K230 modules, record the exact failure and continue to board validation.

- [ ] **Step 9: Commit the implementation**

```bash
git add ui/main_menu.py tests/test_main_menu_memory_ast.py
git commit -m "fix(main_menu): avoid Python scroll animations"
```

---

### Task 3: Board Diagnostic Validation

**Files:**
- Modify: `ui/main_menu.py:49-78` only if enabling diagnostics for board run
- No new tests

- [ ] **Step 1: Enable diagnostic logging for the board run**

Temporarily set these constants in `ui/main_menu.py`:

```python
MENU_DIAG_MEM = True
MENU_DIAG_FORCE_GC_AT_SEQ = 5
```

Do not commit this diagnostic-enabled state unless the project convention is to keep board diagnostics on.

- [ ] **Step 2: Deploy to board**

Copy the updated project files to the SD card using the project's normal deployment process. Ensure at minimum these files are updated on board:

```text
/sdcard/main.py
/sdcard/ui/main_menu.py
/sdcard/core/app_runtime.py
/sdcard/hw/lcd.py
/sdcard/config/categories.json
```

Expected: board boots into the main menu.

- [ ] **Step 3: Static idle validation**

Do not touch the screen for 5 minutes.

Expected serial pattern:

```text
[MainMenu-diag] seq=1 reason=... selected=0 mem=<number>
[MainMenu-diag] seq=2 reason=... selected=0 mem=<number>
```

Expected result: mem may fluctuate, but it must not monotonically drop toward zero, and seq 5 proactive GC must print both begin and end lines.

- [ ] **Step 4: Slow scroll validation**

Slowly scroll up and down for 5 minutes.

Expected result: menu remains responsive; mem fluctuates or stabilizes; no hard hang; selected index changes normally.

- [ ] **Step 5: Fast scroll validation**

Rapidly scroll through the full menu for 10 minutes.

Expected result: no deadlock, no black screen, no serial stall, no monotonic mem exhaustion.

- [ ] **Step 6: Launch/return validation**

Tap the centered card to launch one script, then let reset return to menu.

Expected result: `.next_script` is written, board resets into script, script can return/reset to menu, and the main menu still scrolls.

- [ ] **Step 7: Disable diagnostic logging after validation**

Set constants back to:

```python
MENU_DIAG_MEM = False
MENU_DIAG_FORCE_GC_AT_SEQ = 0
```

Run the focused test again:

```bash
pytest tests/test_main_menu_memory_ast.py -q
```

Expected: PASS.

- [ ] **Step 8: Commit diagnostic default state if changed**

```bash
git add ui/main_menu.py
git commit -m "chore(main_menu): keep memory diagnostics disabled by default"
```

Only run this commit if the diagnostic constants changed after the implementation commit.

---

### Task 4: Failure Branch for Display Flush Hypothesis

Use this task only if Task 3 still shows monotonic memory drop or GC hang. Do not run it if Task 3 passes.

**Files:**
- Modify: `core/app_runtime.py:72-99`
- Test: board-only single-variable validation

- [ ] **Step 1: Record the failed animation result**

Create a short note in the implementation log or commit message body with these exact fields:

```text
Animation-free menu validation failed:
- idle mem behavior:
- slow scroll mem behavior:
- fast scroll mem behavior:
- proactive gc seq result:
- last serial line before hang:
```

- [ ] **Step 2: Test display mode as a single variable**

Temporarily change only menu init in `core/app_runtime.py` from:

```python
        self._lvgl_init()
```

to:

```python
        self._lvgl_init(lv.DISP_RENDER_MODE.DIRECT)
```

This is inside `init_menu()` only. Do not change `init_app()`.

- [ ] **Step 3: Board-test the display mode variable**

Repeat Task 3 Steps 3-5.

Expected: either DIRECT stabilizes the menu, or behavior remains unchanged. Record which one happened.

- [ ] **Step 4: Revert the temporary display mode test unless it clearly fixes the issue**

If DIRECT does not clearly fix the issue, restore:

```python
        self._lvgl_init()
```

If DIRECT clearly fixes the issue, stop and write a new design/spec for making main-menu-only render mode safe without breaking OSD2 script overlays.

---

## Self-Review

- Spec coverage: covered root-cause validation, DurUI-style scroll visual update, no reset/script changes, PC source tests, board validation, and display flush fallback.
- Placeholder scan: no TBD/TODO placeholders remain; failure branch fields are concrete log fields, not unspecified implementation work.
- Type consistency: method names used by tests match implementation tasks: `_apply_scroll_visuals`, `_diag_mem_tick`, `_CardSlot.apply_scroll_visual`, `_CardSlot.set_visual_state`.
