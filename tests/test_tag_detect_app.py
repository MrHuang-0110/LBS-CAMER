# tests/test_tag_detect_app.py — tag_detect app AST 契约(板端不可导入)
import ast
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP_PATH = os.path.join(ROOT, "scripts", "tag_detect", "app.py")
RT_PATH = os.path.join(ROOT, "core", "app_runtime.py")


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
    raise AssertionError("Function %s missing in tag_detect/app.py" % name)


def test_run_entrypoint_exists():
    try:
        _func("run")
    except AssertionError:
        assert False, "tag_detect/app.py must define run(runtime)"


def test_on_frame_calls_find_apriltags_and_find_qrcodes():
    """on_frame 须按功能调 find_apriltags 或 find_qrcodes。"""
    seg = ast.get_source_segment(_app_src(), _func("on_frame")) or ""
    assert "find_apriltags" in seg, "on_frame must call find_apriltags"
    assert "find_qrcodes" in seg, "on_frame must call find_qrcodes"
    assert "TAG36H11" in seg or "TAG36H11" in _app_src(), \
        "AprilTag must use TAG36H11 family"


def test_on_frame_uses_cam_chn_id_1_for_detection():
    """检测须取 chn1(QVGA RGB565 专用检测通道)。"""
    seg = ast.get_source_segment(_app_src(), _func("on_frame")) or ""
    assert "CAM_CHN_ID_1" in seg, "on_frame must snapshot chn=CAM_CHN_ID_1"


def test_on_frame_calls_host_tick_with_slots():
    """on_frame 须构建4槽位并调 host_tick。"""
    seg = ast.get_source_segment(_app_src(), _func("on_frame")) or ""
    assert "host_tick" in seg, "on_frame must call host_tick(slots)"
    assert "slots" in seg, "on_frame must build slots list"


def test_on_frame_uses_id_registry_with_tag_db_registrar():
    """KEY2 注册须走 tag_db.register(registrar=)。"""
    seg = ast.get_source_segment(_app_src(), _func("on_frame")) or ""
    assert "try_register" in seg, "on_frame must call id_registry.try_register"
    assert "registrar" in seg, "try_register must pass registrar=tag_db.register"


def test_run_loop_has_exitpoint_and_task_handler():
    """主循环须有 os.exitpoint + lv.task_handler(对齐模板)。"""
    seg = ast.get_source_segment(_app_src(), _func("run")) or ""
    assert "exitpoint" in seg, "run loop must call os.exitpoint()"
    assert "task_handler" in seg, "run loop must call lv.task_handler()"


def test_app_uses_i18n_not_hardcoded():
    """文本须走 lang.t(),不得硬编码中文。"""
    src = _app_src()
    assert "lang.t" in src or "lang.t(" in src, "tag_detect must use lang.t()"
    bad = ["已注册", "清除", "保存", "二维码"]
    for s in bad:
        assert ('"%s"' % s) not in src, "must not hardcode '%s'; use i18n" % s


def test_on_frame_white_unknown_and_slot_color_boxes():
    """画框对齐 face_detect:未注册白框(BOX_UNKNOWN),注册按 slot 彩色(BOX_COLORS)。

    face_detect(core/face_ai.draw_result):BOX_UNKNOWN=0xFFFFFF(白),BOX_COLORS
    按 slot 取色(1绿/2蓝/3橙/4紫),_draw_color 转 RGB888 的 (A,B,G,R)。
    旧 tag_detect 用红框(255,0,0)/统一绿框,不对齐 → 改白框+彩色。
    """
    src = _app_src()
    seg = ast.get_source_segment(src, _func("on_frame")) or ""
    # 不得用红框 (255, 0, 0)
    assert "(255, 0, 0)" not in seg, \
        "unknown box must be white (BOX_UNKNOWN), not red (255, 0, 0)"
    # 不得用统一绿框 (0, 255, 0)
    assert "(0, 255, 0)" not in seg, \
        "registered box must use per-slot BOX_COLORS, not uniform green (0, 255, 0)"
    # 须定义/使用 BOX_UNKNOWN(白) + BOX_COLORS(slot 彩色) + _draw_color
    assert "BOX_UNKNOWN" in src, "must use BOX_UNKNOWN (white) for unregistered boxes"
    assert "BOX_COLORS" in src, "must use BOX_COLORS (per-slot) for registered boxes"
    assert "_draw_color" in src, "must use _draw_color for RGB888 color tuple"


def test_list_overlay_handlers_exist():
    """list 图标须绑定清除/保存浮层(对齐 face_detect)。"""
    for fn in ["_on_list_clicked", "_on_clear_clicked", "_on_save_clicked",
               "_on_overlay_clicked", "_on_screen_clicked", "_process_overlay_close"]:
        try:
            _func(fn)
        except AssertionError:
            assert False, "tag_detect must define %s for list overlay" % fn


def test_list_button_binds_click_and_screen_closes_overlay():
    """_build_ui:list_btn 须绑 _on_list_clicked;screen 须 CLICKABLE + 绑 _on_screen_clicked。"""
    seg = ast.get_source_segment(_app_src(), _func("_build_ui")) or ""
    assert "_on_list_clicked" in seg, "_build_ui must bind _on_list_clicked to list button"
    assert "_on_screen_clicked" in seg, "_build_ui must bind _on_screen_clicked to screen"


def test_clear_clicked_clears_active_db_and_refreshes():
    """_on_clear_clicked 须清当前激活 db + 刷新计数 + 蜂鸣。"""
    seg = ast.get_source_segment(_app_src(), _func("_on_clear_clicked")) or ""
    assert "clear()" in seg, "_on_clear_clicked must clear active db"
    assert "_refresh_count" in seg, "must refresh count after clear"
    assert "buzzer" in seg, "must beep on clear"


def test_run_loop_processes_overlay_close():
    """主循环须调 _process_overlay_close()(deferred 关浮层,防 use-after-free)。"""
    seg = ast.get_source_segment(_app_src(), _func("run")) or ""
    assert "_process_overlay_close" in seg, "run loop must call _process_overlay_close()"


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
