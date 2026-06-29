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
