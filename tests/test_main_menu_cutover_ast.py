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


def test_read_next_script_uses_stat_precheck():
    """_read_next_script must os.stat before open to avoid ENOENT pollution (pitfall #18 variant).

    Root cause of main-menu GC-then-freeze (2026-06-30): boot-time open() of a
    non-existent .next_script raised ENOENT, polluting K230 FATFS/global state,
    so later Display/MediaManager/LVGL froze after GC. Fix: os.stat precheck,
    return None without open when absent. This test guards the fix.
    """
    main_src = _read(MAIN_PATH)
    body = _method_src(main_src, None, "_read_next_script", MAIN_PATH) if False else None
    # _read_next_script is a module-level function, not in a class; locate it directly.
    import ast
    tree = ast.parse(main_src, filename=MAIN_PATH)
    fn = None
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == "_read_next_script":
            fn = ast.get_source_segment(main_src, node)
    assert fn is not None, "_read_next_script missing"
    assert "os.stat(NEXT_SCRIPT_PATH)" in fn
    stat_idx = fn.index("os.stat(NEXT_SCRIPT_PATH)")
    open_idx = fn.index("open(NEXT_SCRIPT_PATH")
    assert stat_idx < open_idx, "os.stat must precede open() in _read_next_script"
    # The open() must be guarded so it only runs after stat succeeds.
    assert "return None" in fn[:open_idx]


def test_main_has_no_probe_experiment_switches():
    """Experiment switches PROBE_* must be removed after cutover (regression guard)."""
    main_src = _read(MAIN_PATH)
    for sw in ("PROBE_NO_BOOTLOGO", "PROBE_NO_HOSTTICK", "PROBE_INIT", "PROBE_DIRECT"):
        assert sw not in main_src, "leftover experiment switch %s" % sw


def test_init_menu_still_on_durui_stack():
    rt_src = _read(RT_PATH)
    init_body = _method_src(rt_src, "AppRuntime", "init_menu", RT_PATH)
    assert "_config_sensor" not in init_body
    assert "_init_menu_display_and_media" in init_body
    assert "lv.DISP_RENDER_MODE.DIRECT" in init_body
    assert "opaque_bg=True" in init_body


def test_init_menu_uses_probe_style_touch():
    """Menu must use probe-style touch (direct TOUCH(0) + bare read_cb), not hw/touch.

    probe (GC-safe) constructs TOUCH(0) directly and has a bare read_cb without
    try/except. hw/touch.Touch delays TOUCH(0) to hw_init() and wraps read in
    try/except. run_menu crashed after GC while probe did not; aligning the
    menu's touch to probe isolates this variable. Script mode keeps hw/touch.
    """
    rt_src = _read(RT_PATH)
    init_body = _method_src(rt_src, "AppRuntime", "init_menu", RT_PATH)
    assert "_init_menu_touch" in init_body
    menu_touch_body = _method_src(rt_src, "AppRuntime", "_init_menu_touch", RT_PATH)
    assert "TOUCH(0)" in menu_touch_body
    assert "lv.indev_create" in menu_touch_body
    # must NOT import or use hw/touch.Touch for the menu path
    assert "hw.touch" not in menu_touch_body
    assert "Touch(" not in menu_touch_body


def test_init_app_unchanged_full_osd2():
    """Script mode must keep FULL + osd_num=2 (regression guard)."""
    rt_src = _read(RT_PATH)
    init_app_body = _method_src(rt_src, "AppRuntime", "init_app", RT_PATH)
    assert "osd_num=2" in init_app_body
    assert "lv.DISP_RENDER_MODE.FULL" in init_app_body
    assert "_config_sensor" in init_app_body


def test_lvgl_init_skips_color_format_when_opaque_bg():
    """Menu DIRECT path must NOT set_color_format (aligns with DurUI probe that shows a picture).

    set_color_format(ARGB8888) mismatches the BGRA8888 draw buffers and
    black-screens under DIRECT. DurUI's working probe omits it; the menu path
    must omit it too. The set_color_format CALL must be nested inside an
    `if not opaque_bg:` block (use AST so comments don't confuse the check).
    """
    rt_src = _read(RT_PATH)
    tree = ast.parse(rt_src, filename=RT_PATH)
    fn = None
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == "AppRuntime":
            for item in node.body:
                if isinstance(item, ast.FunctionDef) and item.name == "_lvgl_init":
                    fn = item
    assert fn is not None, "AppRuntime._lvgl_init missing"

    def find_call(node):
        for child in ast.walk(node):
            if isinstance(child, ast.Call):
                f = child.func
                if isinstance(f, ast.Attribute) and f.attr == "set_color_format":
                    return child
        return None

    cf_call = find_call(fn)
    assert cf_call is not None, "set_color_format call must exist for script path"
    # The call must be nested inside an If whose test is `not opaque_bg`.
    parent_ifs = []
    for ifn in ast.walk(fn):
        if isinstance(ifn, ast.If):
            for child in ast.walk(ifn):
                if child is cf_call:
                    parent_ifs.append(ifn)
    assert parent_ifs, "set_color_format must be inside an `if not opaque_bg` block"
    gates = [i for i in parent_ifs
             if isinstance(i.test, ast.UnaryOp) and isinstance(i.test.op, ast.Not)
             and isinstance(i.test.operand, ast.Name) and i.test.operand.id == "opaque_bg"]
    assert gates, "set_color_format must be gated by `if not opaque_bg`"
