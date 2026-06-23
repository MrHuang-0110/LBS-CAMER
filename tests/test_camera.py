# tests/test_camera.py — host-side AST tests for camera run(runtime) refactor.
# Run with:
#   python tests/test_camera.py
import ast
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP_PATH = os.path.join(ROOT, "scripts", "camera", "app.py")


def _parse(path):
    with open(path, "r", encoding="utf-8") as f:
        return ast.parse(f.read(), filename=path)


def _function_node(tree, name):
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError("Function %s missing" % name)


def test_camera_has_run_entry():
    """camera 必须有 run(runtime) 入口(reset 框架要求)。"""
    tree = _parse(APP_PATH)
    run_fn = _function_node(tree, "run")
    arg_names = [a.arg for a in run_fn.args.args]
    assert "runtime" in arg_names, "run(runtime) entry is required by reset framework"


def test_camera_run_uses_exit_flag_loop():
    """run() 主循环必须用 exit_flag 检测退出 + task_handler。"""
    src = open(APP_PATH, encoding="utf-8").read()
    assert "exit_flag" in src, "run must use exit_flag for exit detection"
    assert "while" in src, "run must have main loop"
    assert "task_handler" in src, "run must call lv.task_handler()"


def test_camera_no_basescript():
    """改造后不得残留旧 BaseScript 架构。"""
    src = open(APP_PATH, encoding="utf-8").read()
    for token in ("BaseScript", "on_enter", "on_exit", "SCRIPT_ID",
                  "SELF_MANAGED_TOP_BAR", "class CameraApp"):
        assert token not in src, "old architecture token must be removed: %s" % token


def test_camera_no_ctx_lcd():
    """不得依赖旧架构的 ctx.lcd 共享 sensor。"""
    src = open(APP_PATH, encoding="utf-8").read()
    for token in ("ctx.lcd", "get_sensor", "ensure_sensor_running",
                  "clear_framebuffers", "capture_chn"):
        assert token not in src, "old ctx.lcd shared-sensor token must be removed: %s" % token


def test_camera_uses_runtime_sensor():
    """预览必须用 runtime.sensor.snapshot(chn=CAM_CHN_ID_0) 推 OSD1。"""
    src = open(APP_PATH, encoding="utf-8").read()
    assert "runtime.sensor.snapshot" in src, "must use runtime.sensor.snapshot for preview"
    assert "CAM_CHN_ID_0" in src, "preview must use chn0"
    assert "LAYER_OSD1" in src, "preview must push to OSD1"


def test_camera_no_self_ctx():
    """不得残留 self.ctx(self._xxx 改为模块级状态)。"""
    src = open(APP_PATH, encoding="utf-8").read()
    assert "self.ctx" not in src, "self.ctx must be removed (module-level state)"
    assert "self.ctx" not in src


def test_camera_uses_runtime_lang():
    """语言取自 runtime.lang(非 ctx.lang)。"""
    src = open(APP_PATH, encoding="utf-8").read()
    assert "runtime.lang" in src, "must use runtime.lang"


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
