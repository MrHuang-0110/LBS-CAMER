# tests/test_object_detect_app.py — object_detect app AST 契约(板端不可导入)
import ast
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP_PATH = os.path.join(ROOT, "scripts", "object_detect", "app.py")


def _app_src():
    with open(APP_PATH, "r", encoding="utf-8") as f:
        return f.read()


def _app_tree():
    return ast.parse(_app_src(), filename=APP_PATH)


def _func(name):
    tree = _app_tree()
    for n in tree.body:
        if isinstance(n, ast.FunctionDef) and n.name == name:
            return n
    raise AssertionError("Function %s missing in object_detect/app.py" % name)


def test_run_entrypoint_exists():
    try:
        _func("run")
    except AssertionError:
        assert False, "object_detect/app.py must define run(runtime)"


def test_on_frame_exists():
    try:
        _func("on_frame")
    except AssertionError:
        assert False, "must define on_frame(img)"


def test_on_frame_uses_cam_chn_id_2_for_detection():
    """检测须取 chn2(XGA RGBP888 AI 通道)。"""
    seg = ast.get_source_segment(_app_src(), _func("on_frame")) or ""
    assert "CAM_CHN_ID_2" in seg, "on_frame must snapshot chn=CAM_CHN_ID_2"


def test_on_frame_calls_host_tick_with_slots():
    seg = ast.get_source_segment(_app_src(), _func("on_frame")) or ""
    assert "host_tick" in seg, "on_frame must call host_tick(slots)"
    assert "slots" in seg, "on_frame must build slots list"


def test_on_frame_uses_id_registry_with_object_db_registrar():
    """KEY2 注册须走 object_db.register(registrar=)。"""
    seg = ast.get_source_segment(_app_src(), _func("on_frame")) or ""
    assert "try_register" in seg, "on_frame must call id_registry.try_register"
    assert "registrar" in seg, "try_register must pass registrar=object_db.register"


def test_on_frame_draws_center_green_crosshair():
    """on_frame 须在屏幕居中画小绿十字(VGA 中心 320,240)。"""
    seg = ast.get_source_segment(_app_src(), _func("on_frame")) or ""
    assert "draw_cross" in seg, "on_frame must draw a center crosshair"
    assert "320" in seg and "240" in seg, "crosshair must be at screen center (320, 240)"


def test_box_colors_white_unknown_and_slot_color():
    """画框对齐 face_detect:未注册白框(BOX_UNKNOWN),注册按 slot 彩色(BOX_COLORS)。"""
    src = _app_src()
    seg = ast.get_source_segment(src, _func("on_frame")) or ""
    assert "BOX_UNKNOWN" in src, "must use BOX_UNKNOWN (white) for unregistered boxes"
    assert "BOX_COLORS" in src, "must use BOX_COLORS (per-slot) for registered boxes"
    assert "_draw_color" in src, "must use _draw_color for RGB888 color tuple"
    # 不得红框
    assert "(255, 0, 0)" not in seg, "unknown box must be white, not red"


def test_on_frame_shows_id_and_english_class_name():
    """注册框须显示 ID号 + 英文类名(槽号+英文类名,不双语)。"""
    seg = ast.get_source_segment(_app_src(), _func("on_frame")) or ""
    assert "draw_string_advanced" in seg, "must draw label text"
    assert "ID" in seg, "registered box must show ID%d"
    # 类名须从 COCO_LABELS 取(英文),不得硬编码中文类名
    assert "COCO_LABELS" in _app_src() or "labels" in seg, \
        "class name must come from COCO_LABELS (english)"


def test_list_overlay_handlers_exist():
    """list 图标须绑定清除/保存浮层(对齐 face_detect/tag_detect)。"""
    for fn in ["_on_list_clicked", "_on_clear_clicked", "_on_save_clicked",
               "_on_overlay_clicked", "_on_screen_clicked", "_process_overlay_close"]:
        try:
            _func(fn)
        except AssertionError:
            assert False, "must define %s for list overlay" % fn


def test_list_button_binds_click_and_screen_closes_overlay():
    seg = ast.get_source_segment(_app_src(), _func("_build_ui")) or ""
    assert "_on_list_clicked" in seg, "_build_ui must bind _on_list_clicked to list button"
    assert "_on_screen_clicked" in seg, "_build_ui must bind _on_screen_clicked to screen"


def test_clear_clicked_clears_db_and_refreshes():
    seg = ast.get_source_segment(_app_src(), _func("_on_clear_clicked")) or ""
    assert "clear()" in seg, "_on_clear_clicked must clear db"
    assert "_refresh_count" in seg, "must refresh count after clear"
    assert "buzzer" in seg, "must beep on clear"


def test_run_loop_has_exitpoint_and_task_handler():
    seg = ast.get_source_segment(_app_src(), _func("run")) or ""
    assert "exitpoint" in seg, "run loop must call os.exitpoint()"
    assert "task_handler" in seg, "run loop must call lv.task_handler()"
    assert "_process_overlay_close" in seg, "run loop must call _process_overlay_close()"


def test_app_uses_i18n_not_hardcoded():
    """文本须走 lang.t(),不得硬编码中文。"""
    src = _app_src()
    assert "lang.t" in src or "lang.t(" in src, "must use lang.t()"
    bad = ["已注册", "清除", "保存", "物体识别"]
    for s in bad:
        assert ('"%s"' % s) not in src, "must not hardcode '%s'; use i18n" % s


def test_runner():
    failures = 0
    for name in sorted(n for n in globals() if n.startswith("test_") and n != "test_runner"):
        try:
            globals()[name]()
            print("PASS %s" % name)
        except Exception as e:
            failures += 1
            print("FAIL %s: %s" % (name, e))
    print("\n%s" % ("ALL PASS" if failures == 0 else "%d FAILED" % failures))
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    test_runner()
