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
