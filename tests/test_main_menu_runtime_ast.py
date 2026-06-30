# tests/test_main_menu_runtime_ast.py — host-side AST contracts for main menu runtime display path
import ast
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RT_PATH = os.path.join(ROOT, "core", "app_runtime.py")


def _src():
    with open(RT_PATH, "r", encoding="utf-8") as f:
        return f.read()


def _method_src(name):
    src = _src()
    tree = ast.parse(src, filename=RT_PATH)
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == "AppRuntime":
            for item in node.body:
                if isinstance(item, ast.FunctionDef) and item.name == name:
                    return ast.get_source_segment(src, item)
    raise AssertionError("AppRuntime.%s missing" % name)


def test_init_menu_uses_durui_style_display_without_sensor():
    """Main menu should not configure sensor/OSD2; it is LCD-only like DurUI."""
    body = _method_src("init_menu")
    assert "_config_sensor" not in body
    assert "_init_menu_display_and_media" in body
    assert "lv.DISP_RENDER_MODE.DIRECT" in body


def test_menu_display_init_does_not_request_osd2():
    """Menu display init must not pass osd_num=2, unlike script mode."""
    body = _method_src("_init_menu_display_and_media")
    assert "Display.ST7701" in body
    assert "osd_num" not in body
    assert "MediaManager.init" in body


def test_flush_cb_has_menu_direct_branch_without_layer_or_clear():
    """Menu flush branch must show draw buffers directly and scripts keep OSD2 path."""
    body = _method_src("_flush_cb")
    assert 'self.category_id == "main_menu"' in body
    assert "self.display.show_image(self.draw_buf_1)" in body
    assert "self.display.show_image(self.draw_buf_2)" in body
    assert "layer=Display.LAYER_OSD2" in body
    assert "bytearray(0)" in body
    main_menu_pos = body.index('self.category_id == "main_menu"')
    first_layer_pos = body.index("layer=Display.LAYER_OSD2")
    first_clear_pos = body.index("bytearray(0)")
    direct_1_pos = body.index("self.display.show_image(self.draw_buf_1)")
    direct_2_pos = body.index("self.display.show_image(self.draw_buf_2)")
    assert main_menu_pos < direct_1_pos < first_clear_pos
    assert main_menu_pos < direct_2_pos < first_layer_pos


def test_lvgl_init_supports_opaque_black_buffers_for_menu():
    """Menu must prime buffers with opaque black (alpha=255), not clear (alpha=0)."""
    body = _method_src("_lvgl_init")
    assert "opaque_bg" in body
    assert "draw_rectangle" in body
    assert "(0, 0, 0)" in body
    assert "fill=True" in body


def test_init_menu_primes_opaque_buffers():
    """init_menu must request opaque black buffers so DIRECT menu is visible."""
    body = _method_src("init_menu")
    assert "opaque_bg=True" in body
