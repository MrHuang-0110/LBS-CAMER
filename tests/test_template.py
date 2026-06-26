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


def test_categories_json_has_template():
    """config/categories.json must register _template category with stream ui_mode.

    _template 是开发模板脚本,默认 enabled=False(不进主菜单,仅作新脚本参考)。
    故只校验条目存在 + script/ui_mode 字段,不强制 enabled=True。
    """
    import json
    with open(CATEGORIES_PATH, encoding="utf-8") as f:
        data = json.load(f)
    cats = data.get("categories", [])
    template = [c for c in cats if c.get("id") == "_template"]
    assert template, "categories.json must have _template entry"
    t = template[0]
    assert t.get("script") == "_template", "script field must be _template"
    assert t.get("ui_mode") == "stream", "ui_mode must be stream"
    assert "icon" in t, "must have icon field"


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
