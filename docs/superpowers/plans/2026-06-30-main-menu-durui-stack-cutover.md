# Main Menu DurUI Stack Cutover Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Finalize `app_runtime.init_menu` on the DurUI display stack so the normal `run_menu` flow shows the menu, survives proactive GC, and keeps host_tick — validated one variable at a time.

**Architecture:** `init_menu` is already on the DurUI stack (no sensor, no OSD2, DIRECT, opaque-black buffers, flush without layer/clear). This plan adds a `PROBE_NO_BOOTLOGO` switch in `main.py` to isolate BootSplash during board validation, then walks steps: (1) no-BootSplash + host_tick, (2) BootSplash restored, (3) finalize + diagnostics off. `init_app` (script mode) is untouched.

**Tech Stack:** MicroPython on K230, LVGL Python bindings, CanMV media stack. Host-side AST contract tests run via `python` inline runner (pytest NOT installed; use Bash heredoc runner).

---

## File Structure

- Modify: `main.py` — add `PROBE_NO_BOOTLOGO` constant and gate the `BootSplash(...).show()` call in `run_menu`. Reverted to `False` after step 2.
- Reference only: `core/app_runtime.py` — `init_menu` already on DurUI stack; no changes in this plan. `ui/main_menu.py` — diagnostics already off; no changes in this plan.
- Create: `tests/test_main_menu_cutover_ast.py` — AST contracts: `PROBE_NO_BOOTLOGO` exists and gates BootSplash; `init_menu` still on DurUI stack (re-assert, guards against regression).

---

### Task 1: Add Failing AST Contract Tests

**Files:**
- Create: `tests/test_main_menu_cutover_ast.py`
- Test: `tests/test_main_menu_cutover_ast.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_main_menu_cutover_ast.py`:

```python
# tests/test_main_menu_cutover_ast.py — host-side AST contracts for DurUI stack cutover
import ast
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MAIN_PATH = os.path.join(ROOT, "main.py")
RT_PATH = os.path.join(ROOT, "core", "app_runtime.py")


def _read(path):
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def _method_src(src, cls_name, name, filename="<src>"):
    tree = ast.parse(src, filename=filename)
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == cls_name:
            for item in node.body:
                if isinstance(item, ast.FunctionDef) and item.name == name:
                    return ast.get_source_segment(src, item)
    raise AssertionError("%s.%s missing" % (cls_name, name))


def test_main_has_probe_no_bootlogo_constant():
    main_src = _read(MAIN_PATH)
    assert "PROBE_NO_BOOTLOGO" in main_src


def test_run_menu_gates_bootsplash_with_probe_no_bootlogo():
    main_src = _read(MAIN_PATH)
    assert "def run_menu(" in main_src
    start = main_src.index("def run_menu(")
    body = main_src[start:start + 1600]
    assert "BootSplash" in body
    assert "PROBE_NO_BOOTLOGO" in body


def test_init_menu_still_on_durui_stack():
    rt_src = _read(RT_PATH)
    init_body = _method_src(rt_src, "AppRuntime", "init_menu", RT_PATH)
    assert "_config_sensor" not in init_body
    assert "_init_menu_display_and_media" in init_body
    assert "lv.DISP_RENDER_MODE.DIRECT" in init_body
    assert "opaque_bg=True" in init_body


def test_init_app_unchanged_full_osd2():
    """Script mode must keep FULL + osd_num=2 (regression guard)."""
    rt_src = _read(RT_PATH)
    init_app_body = _method_src(rt_src, "AppRuntime", "init_app", RT_PATH)
    assert "osd_num=2" in init_app_body
    assert "lv.DISP_RENDER_MODE.FULL" in init_app_body
    assert "_config_sensor" in init_app_body
```

- [ ] **Step 2: Run tests to verify they fail**

Run (Bash heredoc — Git Bash supports it):

```bash
python - <<'EOF'
import importlib.util, sys
spec = importlib.util.spec_from_file_location('t', 'tests/test_main_menu_cutover_ast.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
fails = 0
for n in sorted(x for x in dir(m) if x.startswith('test_')):
    try:
        getattr(m, n)(); print('PASS', n)
    except AssertionError as e:
        fails += 1; print('FAIL', n, e)
sys.exit(1 if fails else 0)
EOF
```

Expected: `test_main_has_probe_no_bootlogo_constant` and `test_run_menu_gates_bootsplash_with_probe_no_bootlogo` FAIL (no `PROBE_NO_BOOTLOGO` yet). `test_init_menu_still_on_durui_stack` and `test_init_app_unchanged_full_osd2` may PASS already (guards); that's fine — they are regression guards, not new behavior.

- [ ] **Step 3: Commit the failing tests**

```bash
git add tests/test_main_menu_cutover_ast.py
git commit -m "test(cutover): add AST contracts for DurUI stack cutover"
```

---

### Task 2: Add PROBE_NO_BOOTLOGO Switch and Gate BootSplash

**Files:**
- Modify: `main.py:22-25` (add constant near WARM_BOOT_PATH)
- Modify: `main.py:127-129` (gate BootSplash call)
- Test: `tests/test_main_menu_cutover_ast.py`

- [ ] **Step 1: Add the constant**

In `main.py`, after the `WARM_BOOT_PATH = ...` line, add:

```python

# 验证开关：True 跳过开机 LOGO，用于 DurUI 栈收尾阶段隔离 BootSplash 变量。
# 验证完成后恢复 False（步骤2 起恢复开机 LOGO）。
PROBE_NO_BOOTLOGO = False
```

- [ ] **Step 2: Gate the BootSplash call**

In `main.py` `run_menu`, find:

```python
    if not _is_warm_boot():
        # BootSplash 内部 open logo 在首次 task_handler 前，安全；阻塞显示后清理
        BootSplash(runtime.buzzer).show()
```

Replace with:

```python
    if not _is_warm_boot() and not PROBE_NO_BOOTLOGO:
        # BootSplash 内部 open logo 在首次 task_handler 前，安全；阻塞显示后清理
        BootSplash(runtime.buzzer).show()
```

- [ ] **Step 3: Run tests to verify they pass**

Run:

```bash
python - <<'EOF'
import importlib.util, sys
spec = importlib.util.spec_from_file_location('t', 'tests/test_main_menu_cutover_ast.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
fails = 0
for n in sorted(x for x in dir(m) if x.startswith('test_')):
    try:
        getattr(m, n)(); print('PASS', n)
    except AssertionError as e:
        fails += 1; print('FAIL', n, e)
sys.exit(1 if fails else 0)
EOF
```

Expected: all four tests PASS.

- [ ] **Step 4: Compile-check**

Run:

```bash
python -m compileall main.py
```

Expected: `Compiling 'main.py'...` with no error.

- [ ] **Step 5: Commit**

```bash
git add main.py
git commit -m "feat(main): add PROBE_NO_BOOTLOGO switch to isolate BootSplash"
```

---

### Task 3: Step 1 Board Validation — No BootSplash

**Files:**
- No code changes; board deployment + observation. Temporarily set the switch True on the deployed copy only.

- [ ] **Step 1: Confirm diagnostics off in ui/main_menu.py**

Open `ui/main_menu.py`. Confirm:

```python
MENU_DIAG_MEM = False
MENU_DIAG_FORCE_GC_AT_SEQ = 0
```

If different, set to these values. (The probe's own diagnostics are not used here; run_menu uses `menu.diag_after_task_handler`, which early-returns when `MENU_DIAG_MEM=False`. For this board step we want the menu's natural behavior, no proactive GC trigger. Re-enable diagnostics only if you want to re-observe GC behavior — see Step 5 optional.)

- [ ] **Step 2: Temporarily enable PROBE_NO_BOOTLOGO on the board-deployed copy**

For the board run only, edit the deployed `/sdcard/CamerAi/main.py` so that:

```python
PROBE_NO_BOOTLOGO = True
```

(Do NOT commit this True state. It is a board-only temporary setting.)

- [ ] **Step 3: Deploy files**

Copy to the SD card:

```text
/sdcard/CamerAi/main.py        (with PROBE_NO_BOOTLOGO=True)
/sdcard/CamerAi/core/app_runtime.py
/sdcard/CamerAi/ui/main_menu.py
```

(`app_runtime.py` and `main_menu.py` carry the DurUI-stack + scroll-visual changes from prior work; ensure they are the current versions.)

- [ ] **Step 4: Boot and observe — picture?**

Power on. Expected: no LOGO, then black background with card menu visible.

Record: picture visible? (yes/no)

- [ ] **Step 5: Observe — GC crash? (optional proactive GC)**

If you want to re-confirm GC safety in this flow, temporarily set in the deployed `ui/main_menu.py`:

```python
MENU_DIAG_MEM = True
MENU_DIAG_FORCE_GC_AT_SEQ = 5
```

Scroll and idle until `[MainMenu-diag] seq=5 ... proactive gc begin / proactive gc end`. Then continue scrolling.

Record: after `gc end` — continues scrolling / crashes.

(If you enabled diagnostics, set them back to `False`/`0` on the board copy afterward.)

- [ ] **Step 6: Observe — host_tick mem behavior**

With the menu idle and scrolling for ~2 minutes, watch `gc.mem_free` behavior. Since diagnostics may be off, optionally re-enable `MENU_DIAG_MEM=True` (no force-GC) just to print mem each second.

Record: does mem drift down monotonically while host_tick runs, or stay roughly flat?

- **Decision point:**
  - Picture visible + GC safe + mem roughly flat → proceed to Task 4.
  - Picture missing → BootSplash is NOT the black-screen cause; the DurUI stack under run_menu differs from probe. Compare run_menu vs probe (scr_act black underlay, host_tick). Record finding.
  - mem drifts down monotonically with host_tick → host_tick is a second leak source. Record and note: open a separate host_api preallocation task (out of this plan's scope) before finalizing.

- [ ] **Step 7: Record step-1 result**

Append to `项目记录.md`:

```text
## 2026-06-30 DurUI 栈收尾 步骤1(无 BootSplash)
- 画面:<有/无>
- seq=5 主动 GC 后:<继续滚动/卡死>
- host_tick mem:<大致平稳/单调下降>
- 结论:<进步骤2 / 需定位 host_tick / 需对比 probe>
```

No commit needed yet (board observation only).

---

### Task 4: Step 2 Board Validation — BootSplash Restored

**Files:**
- No code changes; board deployment + observation.

- [ ] **Step 1: Restore PROBE_NO_BOOTLOGO=False on the board**

Edit the deployed `/sdcard/CamerAi/main.py`:

```python
PROBE_NO_BOOTLOGO = False
```

- [ ] **Step 2: Deploy**

Copy `/sdcard/CamerAi/main.py` to the board.

- [ ] **Step 3: Boot and observe — LOGO + menu**

Power on. Expected: boot LOGO shows, then menu with black background and cards visible.

Record: LOGO visible? menu visible?

- [ ] **Step 4: Observe — GC crash + host_tick**

Scroll and idle ~2 minutes (optionally re-enable `MENU_DIAG_MEM=True` to watch mem + proactive GC at seq 5).

Record: after GC, continues scrolling / crashes; mem flat / drifting.

- **Decision point:**
  - LOGO + menu visible + GC safe → proceed to Task 5 (finalize).
  - LOGO black screen or menu broken under DIRECT → BootSplash interacts badly with DIRECT. Record finding; BootSplash may need an opaque-black underlay (separate fix). Do NOT finalize with broken BootSplash.

- [ ] **Step 5: Record step-2 result**

Append to `项目记录.md`:

```text
## 2026-06-30 DurUI 栈收尾 步骤2(加回 BootSplash)
- 开机 LOGO:<有/无>
- 菜单画面:<有/无>
- seq=5 主动 GC 后:<继续滚动/卡死>
- 结论:<进步骤3固化 / BootSplash 需单独修>
```

---

### Task 5: Finalize — Diagnostics Off, Confirm Switch False

**Files:**
- Modify: `main.py` (confirm `PROBE_NO_BOOTLOGO = False`)
- Verify: `ui/main_menu.py` diagnostics off
- Test: `tests/test_main_menu_cutover_ast.py`

- [ ] **Step 1: Confirm PROBE_NO_BOOTLOGO is False in source**

In `main.py`, confirm:

```python
PROBE_NO_BOOTLOGO = False
```

(Task 2 already set this; just verify it was not left True.)

- [ ] **Step 2: Confirm ui/main_menu.py diagnostics off**

In `ui/main_menu.py`, confirm:

```python
MENU_DIAG_MEM = False
MENU_DIAG_FORCE_GC_AT_SEQ = 0
```

- [ ] **Step 3: Run all main-menu contract tests**

Run:

```bash
python - <<'EOF'
import importlib.util, sys
paths = [
    'tests/test_main_menu_cutover_ast.py',
    'tests/test_main_menu_runtime_ast.py',
    'tests/test_main_menu_memory_ast.py',
    'tests/test_main_menu_durui_probe_ast.py',
]
fails = 0
for p in paths:
    spec = importlib.util.spec_from_file_location('t', p)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    for n in sorted(x for x in dir(m) if x.startswith('test_')):
        try:
            getattr(m, n)(); print('PASS', p, n)
        except AssertionError as e:
            fails += 1; print('FAIL', p, n, e)
sys.exit(1 if fails else 0)
EOF
```

Expected: all tests PASS across all four files.

- [ ] **Step 4: Compile-check the whole set**

Run:

```bash
python -m compileall main.py core/app_runtime.py ui/main_menu.py main_menu_durui_probe.py
```

Expected: all compile, no error.

- [ ] **Step 5: Deploy final files to board**

Copy to the SD card (final, production state):

```text
/sdcard/CamerAi/main.py               (PROBE_NO_BOOTLOGO=False)
/sdcard/CamerAi/core/app_runtime.py
/sdcard/CamerAi/ui/main_menu.py       (MENU_DIAG_MEM=False)
```

- [ ] **Step 6: Final board smoke test**

Power on. Expected: LOGO → menu visible, scrollable, host_tick running, no crash over normal use. Launch one script (e.g. face_detect), let it reset back to menu, confirm menu still works.

Record: pass/fail.

- [ ] **Step 7: Record final result and commit**

Append to `项目记录.md`:

```text
## 2026-06-30 DurUI 栈收尾 步骤3(固化)
- 主菜单有画面 + 滚动 + GC 安全:<是/否>
- 开机 LOGO 正常:<是/否>
- 进脚本 reset 回菜单正常:<是/否>
- 脚本模式未受影响(init_app 不变):<是>
- 最终状态:PROBE_NO_BOOTLOGO=False, MENU_DIAG_MEM=False
```

Commit (only if any source changed during finalization; otherwise just the doc):

```bash
git add 项目记录.md
git commit -m "docs(cutover): finalize DurUI stack cutover for main menu"
```

---

## Self-Review

- Spec coverage: spec steps 1/2/3 map to Tasks 3/4/5. `init_menu` DurUI stack asserted in Task 1 (`test_init_menu_still_on_durui_stack`) — no source change needed (already implemented). `PROBE_NO_BOOTLOGO` + BootSplash gating in Task 2. host_tick behavior observed in Task 3 Step 6 (decision point references separate host_api task if leak found). BootSplash restoration in Task 4. Finalization + diagnostics off in Task 5. No spec section left without a task.
- Placeholder scan: no TBD/TODO; board steps give exact switch values and exact file lists; decision points are concrete branches, not "handle appropriately".
- Type consistency: `PROBE_NO_BOOTLOGO` name matches across Task 1 test, Task 2 implementation, Tasks 3-5 board steps. `MENU_DIAG_MEM`/`MENU_DIAG_FORCE_GC_AT_SEQ` names match `ui/main_menu.py`. `init_menu`/`init_app`/`_init_menu_display_and_media`/`opaque_bg` match `core/app_runtime.py` (verified in earlier tasks).
