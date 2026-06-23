# 通用脚本模板（基础框架 + AI on_frame 插件槽）实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 做一个稳定的基础框架脚本模板 `_template`（顶栏+空底栏+摄像头画面+触摸返回），作为后续 AI 脚本复制起点，从结构上排除基础框架被 AI 影响的问题。

**Architecture:** 模板 `run(runtime)` 单线程主循环内置 `on_frame(img)` 钩子（默认空实现）；AI 脚本复制模板只填 `on_frame`，异常 try/except 隔离不杀循环。走 init_app 统一路径，sensor 单通道 chn0，render mode 按 ui_mode 区分（stream→PARTIAL，退路 FULL）。

**Tech Stack:** MicroPython / K230 CanMV / LVGL / media(Display+Sensor+MediaManager) / reset 框架（main.py + app_runtime.py）。Host 测试用 AST 解析（board 模块在 Windows host 不可导入，沿用 tests/ 现有 AST 风格）。

**参考设计:** `docs/superpowers/specs/2026-06-23-script-template-design.md`

**测试运行方式（所有 host 测试统一）:** `python tests/<file>.py`，期望输出 `ALL PASS`。

---

## 文件结构

| 文件 | 责任 | 操作 |
|------|------|------|
| `core/app_runtime.py` | `_lvgl_init` 加 render_mode 参数；`init_app` 按 ui_mode 选 render_mode；`_channels_for` 加 `_template` 单通道 | 修改 |
| `main.py` | `run_script` 加 `runtime.cleanup()`（跳过 face_detect） | 修改 |
| `scripts/_template/app.py` | 模板主体：`run(runtime)` + `on_frame` 钩子 + `_build_ui` + `_destroy_ui` | 新建 |
| `scripts/_template/__init__.py` | 包标识（空文件） | 新建 |
| `scripts/_template/manifest.json` | 模板元数据 | 新建 |
| `config/categories.json` | 注册 `_template` category | 修改 |
| `tests/test_template.py` | 模板 + 框架改动的 AST 测试 | 新建 |

---

## Task 1: `_lvgl_init` 加 render_mode 参数

**Files:**
- Modify: `core/app_runtime.py`（`_lvgl_init` 方法，约 72-82 行）
- Test: `tests/test_template.py`（新建）

- [ ] **Step 1: 新建测试文件，写第一个失败测试**

创建 `tests/test_template.py`：

```python
# tests/test_template.py — host-side AST tests for script template + framework changes.
# Run with:
#   python tests/test_template.py
import ast
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP_RUNTIME_PATH = os.path.join(ROOT, "core", "app_runtime.py")
MAIN_PATH = os.path.join(ROOT, "main.py")
TEMPLATE_APP_PATH = os.path.join(ROOT, "scripts", "_template", "app.py")
TEMPLATE_MANIFEST_PATH = os.path.join(ROOT, "scripts", "_template", "manifest.json")
CATEGORIES_PATH = os.path.join(ROOT, "config", "categories.json")


def _parse(path):
    with open(path, "r", encoding="utf-8") as f:
        return ast.parse(f.read(), filename=path)


def _method_node(cls_node, name):
    for n in cls_node.body:
        if isinstance(n, ast.FunctionDef) and n.name == name:
            return n
    raise AssertionError("Method %s missing" % name)


def test_lvgl_init_takes_render_mode_param():
    """_lvgl_init must accept a render_mode parameter (default FULL)."""
    tree = _parse(APP_RUNTIME_PATH)
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == "AppRuntime":
            init = _method_node(node, "_lvgl_init")
            arg_names = [a.arg for a in init.args.args]
            assert "render_mode" in arg_names, \
                "_lvgl_init must take render_mode param"
            # default must be FULL
            defaults = init.args.defaults
            assert defaults, "_lvgl_init render_mode must have default FULL"
            break
    else:
        raise AssertionError("AppRuntime class missing")


def test_runner():
    failures = 0
    tests = [(name, fn) for name, fn in sorted(globals().items())
             if name.startswith("test_") and callable(fn) and name != "test_runner"]
    for name, fn in tests:
        try:
            fn()
            print("PASS %s" % name)
        except Exception as e:
            failures += 1
            print("FAIL %s: %s" % (name, e))
    print("\n%s" % ("ALL PASS" if failures == 0 else "%d FAILED" % failures))
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    test_runner()
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python tests/test_template.py`
Expected: FAIL `test_lvgl_init_takes_render_mode_param` ("_lvgl_init must take render_mode param")

- [ ] **Step 3: 改 `_lvgl_init` 加 render_mode 参数**

修改 `core/app_runtime.py` 的 `_lvgl_init`（当前签名 `def _lvgl_init(self):`），改为：

```python
    def _lvgl_init(self, render_mode=lv.DISP_RENDER_MODE.FULL):
        self.draw_buf_1 = image.Image(self.width, self.height, image.BGRA8888)
        self.draw_buf_2 = image.Image(self.width, self.height, image.BGRA8888)
        self.draw_buf_1.clear()
        self.draw_buf_2.clear()
        self.lv_disp = lv.disp_create(self.width, self.height)
        self.lv_disp.set_flush_cb(self._flush_cb)
        self.lv_disp.set_color_format(lv.COLOR_FORMAT.ARGB8888)
        self.lv_disp.set_draw_buffers(
            self.draw_buf_1.bytearray(), self.draw_buf_2.bytearray(),
            self.draw_buf_1.size(), render_mode)
```

（仅改签名行 + `set_draw_buffers` 的最后一个实参由 `lv.DISP_RENDER_MODE.FULL` 改为 `render_mode`，其余不变。）

- [ ] **Step 4: 运行测试确认通过**

Run: `python tests/test_template.py`
Expected: PASS `test_lvgl_init_takes_render_mode_param` → ALL PASS

- [ ] **Step 5: 确认旧测试不被破坏**

Run: `python tests/test_framework.py`
Expected: ALL PASS（`_lvgl_init` 加默认参数不影响 init_menu 调用）

- [ ] **Step 6: 提交**

```bash
git add core/app_runtime.py tests/test_template.py
git commit -m "feat(app_runtime): _lvgl_init accepts render_mode param (default FULL)"
```

---

## Task 2: `init_app` 按 ui_mode 选 render_mode

**Files:**
- Modify: `core/app_runtime.py`（`init_app` 方法，约 128-157 行）
- Test: `tests/test_template.py`

- [ ] **Step 1: 写失败测试**

在 `tests/test_template.py` 的 `test_runner` 函数**之前**追加：

```python
def test_init_app_passes_render_mode_for_stream():
    """init_app must select PARTIAL for stream ui_mode, FULL otherwise."""
    src = open(APP_RUNTIME_PATH, encoding="utf-8").read()
    # init_app 必须根据 ui_mode 决定 render_mode 并传给 _lvgl_init
    assert "DISP_RENDER_MODE.PARTIAL" in src, \
        "init_app must use PARTIAL for stream ui_mode"
    assert "_lvgl_init(" in src, \
        "init_app must call _lvgl_init with computed render_mode"
    # 必须从 ui_mode 判定（ConfigManager 查 category 的 ui_mode）
    assert "ui_mode" in src, "init_app must read ui_mode to decide render_mode"
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python tests/test_template.py`
Expected: FAIL `test_init_app_passes_render_mode_for_stream`

- [ ] **Step 3: 改 `init_app` 按 ui_mode 选 render_mode**

修改 `core/app_runtime.py` 的 `init_app`。当前调用 `self._lvgl_init()`（无参）。改为在调用前计算 render_mode：

在 `init_app` 内、`lv.init()` 之前插入 ui_mode 查询与 render_mode 计算。定位 `init_app` 中这两行：

```python
        lv.init()
        self._lvgl_init()
```

替换为：

```python
        # render mode 按 ui_mode：stream 用 PARTIAL（顶底栏静态+预览透明，只刷脏区，
        # 避开 FULL 整屏 DMA 与 OSD1 推帧竞争）；menu/page 用 FULL（全屏重绘）。
        # PARTIAL 板端若 flush_cb 异常，退路：此处改回 FULL（单线程下仍稳）。
        from core.config_manager import ConfigManager as _CM
        _cat = _CM().get_category(category_id)
        _ui_mode = _cat.get("ui_mode", "") if _cat else ""
        _render_mode = (lv.DISP_RENDER_MODE.PARTIAL
                        if _ui_mode == "stream"
                        else lv.DISP_RENDER_MODE.FULL)
        lv.init()
        self._lvgl_init(_render_mode)
```

> 注：`ConfigManager` 在 `init_app` 内本地 import（与 `_init_services` 内 import 风格一致）。`get_category` 已存在（config_manager.py:66），返回 dict 或 None。ConfigManager 构造 + load：为避免重复 load，`_CM()` 不调 load 会怎样？——查 config_manager：`__init__` 不自动 load，`get_category` 遍历 `self.categories`（未 load 时为空列表 → 返回 None → `_ui_mode=""` → FULL）。
>
> **修正**：必须 load。改用：
> ```python
>         from core.config_manager import ConfigManager as _CM
>         _cm = _CM()
>         _cm.load()
>         _cat = _cm.get_category(category_id)
>         _ui_mode = _cat.get("ui_mode", "") if _cat else ""
>         _render_mode = (lv.DISP_RENDER_MODE.PARTIAL
>                         if _ui_mode == "stream"
>                         else lv.DISP_RENDER_MODE.FULL)
> ```
> init 阶段（task_handler 前）文件 I/O 安全（坑#2）。

- [ ] **Step 4: 运行测试确认通过**

Run: `python tests/test_template.py`
Expected: ALL PASS

- [ ] **Step 5: 确认旧测试不破坏**

Run: `python tests/test_framework.py && python tests/test_face_register.py`
Expected: ALL PASS

- [ ] **Step 6: 提交**

```bash
git add core/app_runtime.py tests/test_template.py
git commit -m "feat(app_runtime): init_app selects render_mode by ui_mode (stream=PARTIAL)"
```

---

## Task 3: `_channels_for` 加 `_template` 单通道

**Files:**
- Modify: `core/app_runtime.py`（`_channels_for` 方法，约 159-166 行）
- Test: `tests/test_template.py`

- [ ] **Step 1: 写失败测试**

在 `tests/test_template.py` 追加：

```python
def test_channels_for_template_single_channel():
    """_channels_for must return single chn0 for _template (no extra channels)."""
    src = open(APP_RUNTIME_PATH, encoding="utf-8").read()
    # _channels_for 必须识别 _template 且不附加额外通道
    assert '"_template"' in src or "'_template'" in src, \
        "_channels_for must handle _template category"
    # 找 _channels_for 函数体，确认 _template 分支不 append
    start = src.find("def _channels_for(")
    assert start != -1
    body = src[start:start + 600]
    # _template 分支应是 pass（单通道，复用默认 chn0）
    assert "_template" in body, "_channels_for body must contain _template branch"
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python tests/test_template.py`
Expected: FAIL `test_channels_for_template_single_channel`

- [ ] **Step 3: 改 `_channels_for` 加 `_template` 分支**

修改 `core/app_runtime.py` 的 `_channels_for`，当前：

```python
    def _channels_for(self, category_id):
        """按 category 决定 sensor 通道配置。"""
        chs = [(CAM_CHN_ID_0, Sensor.VGA, Sensor.RGB888)]
        if category_id == "face_detect":
            chs.append((CAM_CHN_ID_2, Sensor.XGA, Sensor.RGBP888))
        elif category_id == "camera":
            chs.append((CAM_CHN_ID_1, Sensor.SXGAM, Sensor.RGB565))
        return chs
```

改为（加 `_template` 单通道分支）：

```python
    def _channels_for(self, category_id):
        """按 category 决定 sensor 通道配置。"""
        chs = [(CAM_CHN_ID_0, Sensor.VGA, Sensor.RGB888)]
        if category_id == "face_detect":
            chs.append((CAM_CHN_ID_2, Sensor.XGA, Sensor.RGBP888))
        elif category_id == "camera":
            chs.append((CAM_CHN_ID_1, Sensor.SXGAM, Sensor.RGB565))
        elif category_id == "_template":
            pass  # 模板纯显示，单通道 chn0（复用默认）
        return chs
```

- [ ] **Step 4: 运行测试确认通过**

Run: `python tests/test_template.py`
Expected: ALL PASS

- [ ] **Step 5: 提交**

```bash
git add core/app_runtime.py tests/test_template.py
git commit -m "feat(app_runtime): _channels_for handles _template (single chn0)"
```

---

## Task 4: `main.py run_script` 加 cleanup（跳过 face_detect）

**Files:**
- Modify: `main.py`（`run_script` 函数，约 101-130 行）
- Test: `tests/test_template.py`

- [ ] **Step 1: 写失败测试**

在 `tests/test_template.py` 追加：

```python
def test_main_run_script_calls_cleanup_skipping_face_detect():
    """run_script must call runtime.cleanup() for non-face_detect scripts."""
    src = open(MAIN_PATH, encoding="utf-8").read()
    # 必须有 cleanup 调用
    assert "runtime.cleanup()" in src, \
        "run_script must call runtime.cleanup() after mod.run()"
    # face_detect 分支保留（搁置，不调 cleanup）
    assert 'category_id == "face_detect"' in src, \
        "face_detect special branch must be preserved"
    # cleanup 必须有条件跳过 face_detect
    assert ('category_id != "face_detect"' in src
            or 'category_id == "face_detect"' in src), \
        "cleanup must be skipped for face_detect"
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python tests/test_template.py`
Expected: FAIL `test_main_run_script_calls_cleanup_skipping_face_detect`（"runtime.cleanup()" 不在 main.py）

- [ ] **Step 3: 改 `main.py run_script` 加 cleanup**

修改 `main.py` 的 `run_script`。当前末尾：

```python
    if mod is not None and hasattr(mod, "run"):
        try:
            print("[CamerAi] calling mod.run(runtime)...")
            mod.run(runtime)
        except Exception as e:
            print("[CamerAi] script run error: %s" % e)
            import sys as _sys
            _sys.print_exception(e)
    else:
        print("[CamerAi] script has no run(): %s" % category_id)
    _clear_next_script()
    machine.reset()
```

改为（在 `_clear_next_script()` 前加条件 cleanup）：

```python
    if mod is not None and hasattr(mod, "run"):
        try:
            print("[CamerAi] calling mod.run(runtime)...")
            mod.run(runtime)
        except Exception as e:
            print("[CamerAi] script run error: %s" % e)
            import sys as _sys
            _sys.print_exception(e)
    else:
        print("[CamerAi] script has no run(): %s" % category_id)
    # 统一 deinit：非 face_detect 脚本由 runtime.cleanup() 释放硬件
    # （face_detect 搁置，自管 media，不调 cleanup 避免冲突）
    if category_id != "face_detect":
        try:
            runtime.cleanup()
        except Exception as e:
            print("[CamerAi] cleanup error: %s" % e)
    _clear_next_script()
    machine.reset()
```

- [ ] **Step 4: 运行测试确认通过**

Run: `python tests/test_template.py`
Expected: ALL PASS

- [ ] **Step 5: 确认旧测试不破坏**

Run: `python tests/test_face_register.py`
Expected: `test_main_face_detect_skips_init_app_sets_fpioa` PASS（验 face_detect 分支保留）。
> **已知预期失败（非本 Task 引入）**：`test_face_detect_ai_thread_calls_try_register`、`test_face_detect_run_inits_id_registry` 会 FAIL——这是 face_detect app.py 早前回退到 Step 5 纯净版（去掉 Step 7 id_registry 接入）所致，face_detect 已搁置。这两个失败与 Task 4 无关，只要它们在改动前后失败集相同即可。

- [ ] **Step 6: 提交**

```bash
git add main.py tests/test_template.py
git commit -m "feat(main): run_script calls runtime.cleanup() for non-face_detect scripts"
```

---

## Task 5: 新建 `_template` 包与 manifest

**Files:**
- Create: `scripts/_template/__init__.py`
- Create: `scripts/_template/manifest.json`
- Test: `tests/test_template.py`

- [ ] **Step 1: 写失败测试**

在 `tests/test_template.py` 追加：

```python
def test_template_manifest_exists():
    """scripts/_template/manifest.json must exist with id=_template, ui_mode=stream."""
    import json
    with open(TEMPLATE_MANIFEST_PATH, encoding="utf-8") as f:
        m = json.load(f)
    assert m.get("id") == "_template", "manifest id must be _template"
    assert m.get("ui_mode") == "stream", "manifest ui_mode must be stream"
    assert m.get("enabled", True) is True, "manifest must be enabled"


def test_template_package_init_exists():
    """scripts/_template/__init__.py must exist (package marker)."""
    init_path = os.path.join(ROOT, "scripts", "_template", "__init__.py")
    assert os.path.exists(init_path), "_template package __init__.py missing"
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python tests/test_template.py`
Expected: FAIL `test_template_manifest_exists`、`test_template_package_init_exists`

- [ ] **Step 3: 创建 `scripts/_template/__init__.py`（空包标识）**

```python
# scripts/_template/__init__.py — 基础框架模板包标识
```

- [ ] **Step 4: 创建 `scripts/_template/manifest.json`**

```json
{
  "id": "_template",
  "version": "1.0.0",
  "name_key": "category.template",
  "desc_key": "category.template_desc",
  "entry_icon": "/sdcard/CamerAi/resource/icons/menu_icon/camera.png",
  "icon_dir": "/sdcard/CamerAi/resource/icons/camera_icon/",
  "models": [],
  "ui_mode": "stream",
  "enabled": true,
  "order": 99
}
```

> 注：`entry_icon` 复用 camera 图标占位（模板无专属图标）。`order: 99` 排末尾。

- [ ] **Step 5: 运行测试确认通过**

Run: `python tests/test_template.py`
Expected: ALL PASS

- [ ] **Step 6: 提交**

```bash
git add scripts/_template/__init__.py scripts/_template/manifest.json tests/test_template.py
git commit -m "feat(template): add _template package + manifest"
```

---

## Task 6: 注册 `_template` 到 categories.json

**Files:**
- Modify: `config/categories.json`
- Test: `tests/test_template.py`

- [ ] **Step 1: 写失败测试**

在 `tests/test_template.py` 追加：

```python
def test_categories_json_has_template():
    """config/categories.json must register _template category with stream ui_mode."""
    import json
    with open(CATEGORIES_PATH, encoding="utf-8") as f:
        data = json.load(f)
    cats = data.get("categories", [])
    template = [c for c in cats if c.get("id") == "_template"]
    assert template, "categories.json must have _template entry"
    t = template[0]
    assert t.get("script") == "_template", "script field must be _template"
    assert t.get("ui_mode") == "stream", "ui_mode must be stream"
    assert t.get("enabled", True) is True, "must be enabled"
    assert "icon" in t, "must have icon field"
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python tests/test_template.py`
Expected: FAIL `test_categories_json_has_template`

- [ ] **Step 3: 改 `config/categories.json` 加 `_template` 项**

在 `categories` 数组末尾（`image_classify` 项之后、闭合 `]` 之前）追加。当前末尾：

```json
    {
      "id": "image_classify",
      "script": "image_classify",
      "icon": "/sdcard/CamerAi/resource/icons/menu_icon/image_classify.png",
      "name_key": "category.image_classify",
      "desc_key": "category.image_classify_desc",
      "ui_mode": "stream",
      "enabled": true,
      "order": 11
    }
  ]
}
```

改为（`image_classify` 项后加逗号，追加 `_template`）：

```json
    {
      "id": "image_classify",
      "script": "image_classify",
      "icon": "/sdcard/CamerAi/resource/icons/menu_icon/image_classify.png",
      "name_key": "category.image_classify",
      "desc_key": "category.image_classify_desc",
      "ui_mode": "stream",
      "enabled": true,
      "order": 11
    },
    {
      "id": "_template",
      "script": "_template",
      "icon": "/sdcard/CamerAi/resource/icons/menu_icon/camera.png",
      "name_key": "category.template",
      "desc_key": "category.template_desc",
      "ui_mode": "stream",
      "enabled": true,
      "order": 99
    }
  ]
}
```

- [ ] **Step 4: 运行测试确认通过**

Run: `python tests/test_template.py`
Expected: ALL PASS

- [ ] **Step 5: 提交**

```bash
git add config/categories.json tests/test_template.py
git commit -m "feat(config): register _template category in categories.json"
```

---

## Task 7: 模板 app.py — `on_frame` 钩子 + `run` 主循环骨架

**Files:**
- Create: `scripts/_template/app.py`
- Test: `tests/test_template.py`

- [ ] **Step 1: 写失败测试**

在 `tests/test_template.py` 追加：

```python
def _template_tree():
    return _parse(TEMPLATE_APP_PATH)


def test_template_has_run_entry():
    """模板必须有 run(runtime) 入口（reset 框架要求）。"""
    tree = _template_tree()
    run_fn = None
    for n in tree.body:
        if isinstance(n, ast.FunctionDef) and n.name == "run":
            run_fn = n
            break
    assert run_fn is not None, "run(runtime) entry missing"
    assert "runtime" in [a.arg for a in run_fn.args.args], "run must take runtime"


def test_template_has_on_frame_hook():
    """模板必须有 on_frame(img) 钩子（AI 插槽，默认空实现）。"""
    tree = _template_tree()
    found = False
    for n in tree.body:
        if isinstance(n, ast.FunctionDef) and n.name == "on_frame":
            found = True
            assert "img" in [a.arg for a in n.args.args], "on_frame must take img"
            break
    assert found, "on_frame(img) hook missing"


def test_template_run_has_exit_flag_loop():
    """run() 主循环必须用 exit_flag 检测退出（触摸回调设标志）。"""
    src = open(TEMPLATE_APP_PATH, encoding="utf-8").read()
    assert "exit_flag" in src, "run must use exit_flag for exit detection"
    assert "while" in src, "run must have main loop"
    # 主循环必须调 snapshot + show_image(OSD1) + task_handler
    assert "snapshot" in src, "run must snapshot sensor"
    assert "LAYER_OSD1" in src, "run must show_image to OSD1"
    assert "task_handler" in src, "run must call lv.task_handler()"


def test_template_on_frame_isolated_by_try_except():
    """on_frame 调用必须被 try/except 包裹（AI 异常不杀循环）。"""
    src = open(TEMPLATE_APP_PATH, encoding="utf-8").read()
    assert "on_frame(img)" in src or "on_frame( img)" in src, \
        "run must call on_frame(img)"
    assert "except" in src, "on_frame call must be in try/except"
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python tests/test_template.py`
Expected: FAIL 上述 4 个测试（app.py 不存在）

- [ ] **Step 3: 创建 `scripts/_template/app.py`**

```python
# scripts/_template/app.py — 基础框架模板（顶栏+空底栏+摄像头+触摸返回）
#
# 作为后续所有 AI 脚本的复制起点。核心结构：
#   run(runtime) 单线程主循环内置 on_frame(img) 钩子（默认空实现）。
#   AI 脚本复制本模板后只填 on_frame，不碰骨架；on_frame 异常 try/except
#   隔离不杀循环 → 基础框架不被 AI 影响。
#
# 单线程主循环（snapshot→on_frame→show_image→task_handler 串行）从结构上
# 消除 face_detect 的双线程双写者 display DMA 竞争。
#
# 设计文档：docs/superpowers/specs/2026-06-23-script-template-design.md

import os
import time
import lvgl as lv
from media.display import Display
from media.sensor import CAM_CHN_ID_0
from core.icon_cache import icon_cache
from core.font_manager import fonts

# ── 布局常量（对齐 camera/app.py 尺寸）──────────────────────
BAR_H = 52              # 顶/底栏高度
PREVIEW_Y = BAR_H       # 预览区起始 Y
PREVIEW_H = 376         # 480 - BAR_H * 2
BAR_BG = 0x1A1A1A       # 栏背景色
TITLE_TEXT = "基础框架"  # 硬编码标题（不依赖 manifest/lang，隔离变量）

# ── 模块级 UI 对象引用（_build_ui 建造，_destroy_ui 清理）──
_screen = None
_top_bar = None
_bottom_bar = None
_preview = None


def on_frame(img):
    """AI 钩子 — 模板空实现。

    后续 AI 脚本复制本模板后填此函数：拿 img 做检测/识别，把结果画到 img 上。
    异常由 run() 主循环 try/except 接住，不杀基础框架循环。
    """
    pass


def _build_ui(runtime, exit_flag):
    """构建顶栏(返回钮+标题) + 空底栏 + 透明预览区。

    返回钮 CLICKED 回调设 exit_flag[0]=True（只设标志，不做重操作）。
    """
    global _screen, _top_bar, _bottom_bar, _preview
    screen = lv.scr_act()
    # 屏幕透明：让 OSD1 摄像头画面透出；顶底栏自带不透明背景
    screen.set_style_bg_opa(0, 0)
    _screen = screen

    # ── 顶栏：返回钮(左) + 标题(中) ──
    _top_bar = lv.obj(screen)
    _top_bar.set_size(lv.pct(100), BAR_H)
    _top_bar.set_pos(0, 0)
    _top_bar.set_style_bg_color(lv.color_hex(BAR_BG), 0)
    _top_bar.set_style_bg_opa(255, 0)
    _top_bar.set_style_border_width(0, 0)
    _top_bar.set_style_pad_all(0, 0)
    _top_bar.set_style_radius(0, 0)
    _top_bar.clear_flag(lv.obj.FLAG.SCROLLABLE)

    # 返回钮（48×48 透明点击区 + back 图标）
    btn = lv.obj(_top_bar)
    btn.set_size(48, 48)
    btn.align(lv.ALIGN.LEFT_MID, 2, 0)
    btn.set_style_bg_opa(0, 0)
    btn.set_style_border_width(0, 0)
    btn.set_style_shadow_width(0, 0)
    btn.set_style_outline_width(0, 0)
    btn.set_style_outline_opa(0, 0)
    btn.set_style_pad_all(0, 0)
    btn.clear_flag(lv.obj.FLAG.SCROLLABLE)
    btn.add_flag(lv.obj.FLAG.CLICKABLE)

    icon_data, icon_dsc = icon_cache.get_back_icon()
    if icon_dsc is not None and icon_data is not None:
        import struct
        w = h = 64
        if len(icon_data) >= 24:
            w = struct.unpack('>I', icon_data[16:20])[0]
            h = struct.unpack('>I', icon_data[20:24])[0]
        target = int(48 * 0.85)
        zoom = int(min(target / w, target / h) * 256) if w > 0 and h > 0 else 256
        zoom = max(8, min(zoom, 256))
        icon_img = lv.img(btn)
        icon_img.set_src(icon_dsc)
        icon_img.set_zoom(zoom)
        icon_img.center()
    else:
        lbl = lv.label(btn)
        lbl.set_text("<")
        lbl.center()

    def _on_back(e):
        if e.get_code() == lv.EVENT.CLICKED:
            exit_flag[0] = True
    btn.add_event(_on_back, lv.EVENT.CLICKED, None)

    # 标题居中
    title = lv.label(_top_bar)
    title.set_text(TITLE_TEXT)
    title.align(lv.ALIGN.CENTER, 0, 0)
    from ui.theme import make_back_bar_text_style
    title.add_style(make_back_bar_text_style(fonts.body), 0)

    # ── 预览区：透明，透出 OSD1 摄像头画面 ──
    _preview = lv.obj(screen)
    _preview.set_size(lv.pct(100), PREVIEW_H)
    _preview.set_pos(0, PREVIEW_Y)
    _preview.set_style_bg_opa(0, 0)
    _preview.set_style_border_width(0, 0)
    _preview.set_style_pad_all(0, 0)
    _preview.set_style_radius(0, 0)
    _preview.clear_flag(lv.obj.FLAG.SCROLLABLE)
    _preview.clear_flag(lv.obj.FLAG.CLICKABLE)

    # ── 底栏：纯空栏（无按钮，只验证渲染）──
    _bottom_bar = lv.obj(screen)
    _bottom_bar.set_size(lv.pct(100), BAR_H)
    _bottom_bar.set_pos(0, PREVIEW_Y + PREVIEW_H)
    _bottom_bar.set_style_bg_color(lv.color_hex(BAR_BG), 0)
    _bottom_bar.set_style_bg_opa(255, 0)
    _bottom_bar.set_style_border_width(0, 0)
    _bottom_bar.set_style_pad_all(0, 0)
    _bottom_bar.set_style_radius(0, 0)
    _bottom_bar.clear_flag(lv.obj.FLAG.SCROLLABLE)


def _destroy_ui():
    """删顶栏/底栏/预览区 LVGL 对象 + 恢复屏幕不透明。
    不碰 runtime 持有的硬件（由 main.py runtime.cleanup() 统一 deinit）。
    """
    global _screen, _top_bar, _bottom_bar, _preview
    for obj in (_top_bar, _bottom_bar, _preview):
        if obj is not None:
            try:
                obj.delete()
            except Exception:
                pass
    _top_bar = None
    _bottom_bar = None
    _preview = None
    # 恢复屏幕不透明背景（主菜单需要）
    try:
        from ui.theme import Colors
        scr = lv.scr_act()
        scr.set_style_bg_color(lv.color_hex(Colors.BG), 0)
        scr.set_style_bg_opa(255, 0)
    except Exception:
        pass
    _screen = None


def run(runtime):
    """模板主入口（reset 框架调 mod.run(runtime)）。

    单线程主循环：snapshot → on_frame(try/except) → show_image(OSD1) → task_handler。
    触摸返回钮设 exit_flag → 循环退出 → _destroy_ui → main.py cleanup+reset 回菜单。
    """
    exit_flag = [False]
    _build_ui(runtime, exit_flag)
    fc = 0
    while not exit_flag[0]:
        os.exitpoint()
        img = runtime.sensor.snapshot(chn=CAM_CHN_ID_0)
        try:
            on_frame(img)
        except Exception as e:
            print("[template] on_frame error: %s" % e)
        Display.show_image(img, 0, 0, Display.LAYER_OSD1)
        time.sleep_ms(lv.task_handler())
        fc += 1
        if fc % 30 == 0:
            print("[template] fc=%d" % fc)
    _destroy_ui()
```

- [ ] **Step 4: 运行测试确认通过**

Run: `python tests/test_template.py`
Expected: ALL PASS

- [ ] **Step 5: 确认全部 host 测试不破坏**

Run: `python tests/test_framework.py && python tests/test_face_detect.py && python tests/test_face_register.py`
Expected: `test_framework.py`、`test_face_detect.py` 均 ALL PASS。
> **已知预期失败（非本 Task 引入）**：`test_face_register.py` 中 `test_face_detect_ai_thread_calls_try_register`、`test_face_detect_run_inits_id_registry` 会 FAIL——face_detect app.py 早前回退到 Step 5 纯净版（去掉 Step 7 id_registry 接入）所致，face_detect 已搁置。这两个失败与 Task 7 无关，只要改动前后失败集相同即可。

- [ ] **Step 6: 提交**

```bash
git add scripts/_template/app.py tests/test_template.py
git commit -m "feat(template): baseline app with on_frame hook + single-thread run loop"
```

---

## Task 8: 模板 UI 构建与退出清理验证测试

**Files:**
- Test: `tests/test_template.py`

> 此 Task 纯测试加固（验证 Task 7 的 UI/退出逻辑结构），不改实现。

- [ ] **Step 1: 写 UI/退出结构测试**

在 `tests/test_template.py` 追加：

```python
def test_template_build_ui_creates_top_and_bottom_bar():
    """_build_ui 必须创建顶栏+底栏+预览区，返回钮挂 CLICKED 回调。"""
    src = open(TEMPLATE_APP_PATH, encoding="utf-8").read()
    assert "def _build_ui(" in src, "_build_ui missing"
    assert "_top_bar" in src, "must create _top_bar"
    assert "_bottom_bar" in src, "must create _bottom_bar"
    assert "_preview" in src, "must create _preview"
    # 返回钮回调
    assert "EVENT.CLICKED" in src, "back button must bind CLICKED"
    assert "exit_flag[0] = True" in src, "back callback must set exit_flag"


def test_template_destroy_ui_restores_screen_opacity():
    """_destroy_ui 必须删 UI 对象 + 恢复屏幕 bg_opa=255，且不调 runtime.cleanup()。"""
    src = open(TEMPLATE_APP_PATH, encoding="utf-8").read()
    assert "def _destroy_ui(" in src, "_destroy_ui missing"
    assert "bg_opa(255" in src or "bg_opa(255, 0)" in src, \
        "_destroy_ui must restore screen opacity to 255 for menu"
    # 必须不在 _destroy_ui 函数体内【调用】runtime.cleanup（职责交给 main.py）。
    # 用 AST 检查真正的调用节点，避免误中 docstring/注释里的 "runtime.cleanup" 字样。
    tree = _parse(TEMPLATE_APP_PATH)
    destroy_fn = None
    for n in tree.body:
        if isinstance(n, ast.FunctionDef) and n.name == "_destroy_ui":
            destroy_fn = n
            break
    assert destroy_fn is not None, "_destroy_ui function missing"
    for node in ast.walk(destroy_fn):
        if isinstance(node, ast.Call):
            func = node.func
            # 形如 runtime.cleanup() → Attribute(attr='cleanup', value=Name(id='runtime'))
            if (isinstance(func, ast.Attribute) and func.attr == "cleanup"
                    and isinstance(func.value, ast.Name) and func.value.id == "runtime"):
                raise AssertionError(
                    "_destroy_ui must NOT call runtime.cleanup() (main.py's job)")


def test_template_uses_runtime_sensor_not_self_init():
    """模板必须用 runtime.sensor（init_app 已配），不自己 media_init。"""
    src = open(TEMPLATE_APP_PATH, encoding="utf-8").read()
    assert "runtime.sensor.snapshot" in src, "must use runtime.sensor.snapshot"
    # 不应自己 init sensor/media（init_app 已做）
    assert "MediaManager.init" not in src, "must not self-init MediaManager"
    assert "sensor.reset()" not in src, "must not self-reset sensor"


def test_template_title_hardcoded():
    """标题硬编码中性文字（不依赖 manifest/lang，隔离变量）。"""
    src = open(TEMPLATE_APP_PATH, encoding="utf-8").read()
    assert "基础框架" in src, "title must be hardcoded neutral text"
```

- [ ] **Step 2: 运行测试确认通过**

Run: `python tests/test_template.py`
Expected: ALL PASS（Task 7 实现已满足）

- [ ] **Step 3: 提交**

```bash
git add tests/test_template.py
git commit -m "test(template): add UI build/destroy structure tests"
```

---

## Task 9: 板端部署清单与验收（非 host 任务）

**Files:** 无（部署 + 板端验收）

> 此 Task 是板端验证，host 测试无法覆盖。需用户在 K230 板上执行。

- [ ] **Step 1: 部署文件到 /sdcard/CamerAi/**

将以下文件同步到板子（覆盖）：
- `core/app_runtime.py`
- `main.py`
- `config/categories.json`
- `scripts/_template/__init__.py`、`scripts/_template/manifest.json`、`scripts/_template/app.py`

- [ ] **Step 2: 硬断电重启**

拔电源等 5 秒以上，重新上电（彻底释放前几轮崩溃残留的 sensor/DMA wedged 状态）。

- [ ] **Step 3: 验收 1 — 能进**

从主菜单点 `_template` 卡片，确认稳定进入（看到顶栏"基础框架"+摄像头画面+空底栏）。
连续 5 次硬断电后点进，5 次都成功。✅ / ❌

- [ ] **Step 4: 验收 2 — 画面稳**

观察串口日志，确认连续打印 `fc=` 到 `fc≥300` 不卡死（对比 face_detect fc~20-35）。
目测画面流畅。✅ / ❌

- [ ] **Step 5: 验收 3 — 触摸返回**

点顶栏返回钮，确认稳定退出回主菜单。连续 5 次进出循环不卡。✅ / ❌

- [ ] **Step 6: 验收 4 — 退出干净**

退出后再次从菜单点进，确认不需硬断电即可再次进入（不 wedged）。✅ / ❌

- [ ] **Step 7: 验收 5 — PARTIAL flush_cb 检查**

观察顶底栏渲染是否正常（无花屏/错位/不刷新）。
- 若正常 ✅：PARTIAL + 现有 flush_cb 可用，保留。
- 若异常 ❌：执行退路 —— 改 `core/app_runtime.py` 的 `init_app`，把 `_render_mode` 强制改为 `lv.DISP_RENDER_MODE.FULL`（单线程下仍稳），重新部署+硬断电+复验。

- [ ] **Step 8: 验收 6 — AI 异常隔离验证**

临时编辑板子上 `scripts/_template/app.py` 的 `on_frame`，改为：
```python
def on_frame(img):
    raise Exception("test isolation")
```
重新进入模板，确认：串口持续打印 `[template] on_frame error: test isolation`，但**画面继续刷新、循环不中断**（fc 继续涨）。验证后改回 `pass`。
✅ / ❌

- [ ] **Step 9: 记录验收结果**

在 `项目记录.md` 追加模板板端验收结果（通过项/失败项/是否用了 FULL 退路）。提交。

```bash
git add 项目记录.md
git commit -m "docs: record _template board acceptance results"
```

---

## Self-Review

**1. Spec 覆盖检查:**
- §3 on_frame 钩子 → Task 7 ✓
- §4.1 顶栏+空底栏+透明预览+OSD1 → Task 7 ✓
- §4.1 sensor 单通道 chn0 → Task 3 ✓
- §4.1 PARTIAL(stream) → Task 2 ✓
- §4.1 触摸返回 exit_flag → Task 7 ✓
- §4.1 帧计数 fc%30 → Task 7 ✓
- §5 init_app 统一路径 → Task 2/3 ✓
- §5 main.py cleanup → Task 4 ✓
- §6 run/_build_ui/_destroy_ui 结构 → Task 7/8 ✓
- §7.1 _lvgl_init render_mode → Task 1 ✓
- §7.2 _channels_for _template → Task 3 ✓
- §7.3 main.py cleanup 跳过 face_detect → Task 4 ✓
- §7.4 注册 category(manifest+categories.json) → Task 5/6 ✓
- §7.5 PARTIAL flush_cb 板端验证退路 → Task 9 Step 7 ✓
- §8 验收标准 1-6 → Task 9 Step 3-8 ✓
- §4.2 不做项(不碰 face_detect/camera/settings) → 计划全程未触碰 face_detect app.py/camera/settings ✓
- face_detect 搁置分支保留 → Task 4 测试 `test_main_run_script_calls_cleanup_skipping_face_detect` 验证 + test_face_register.py 复验 ✓

**2. 占位符扫描:** 无 TBD/TODO。Task 9 是板端人工验收（ inherently 非代码），步骤明确具体（改 on_frame raise、观察 fc、改 FULL 退路），非占位。

**3. 类型/命名一致性:**
- `on_frame(img)` — Task 7 定义，Task 8 测试引用一致 ✓
- `exit_flag` / `exit_flag[0]` — Task 7 定义，Task 8 测试一致 ✓
- `_build_ui(runtime, exit_flag)` / `_destroy_ui()` — Task 7 定义，Task 8 测试一致 ✓
- `_lvgl_init(render_mode=...)` — Task 1 定义，Task 2 调用 `_lvgl_init(_render_mode)` 一致 ✓
- `_channels_for` `_template` 分支 — Task 3 一致 ✓
- `runtime.cleanup()` — Task 4 定义，Task 8 测试断言 _destroy_ui 不调它 ✓

无问题。
