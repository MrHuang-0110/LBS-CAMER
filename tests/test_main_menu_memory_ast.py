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






def test_main_menu_keeps_lvgl_event_callbacks_alive():
    """Event callbacks passed to LVGL must be stored on self before add_event."""
    src = _read_main_menu()
    init_body = _method_src(src, "MainMenu", "__init__")
    show_body = _method_src(src, "MainMenu", "show")
    card_init_body = _method_src(src, "_CardSlot", "__init__")
    assert "self._scroll_cb = None" in init_body
    assert "self._scroll_end_cb = None" in init_body
    assert "self._scroll_cb = self._on_scroll" in show_body
    assert "self._scroll_end_cb = self._on_scroll_end" in show_body
    assert "add_event(self._scroll_cb" in show_body
    assert "add_event(self._scroll_end_cb" in show_body
    assert "self._click_cb = self._on_click_event" in card_init_body
    assert "add_event(self._click_cb" in card_init_body
