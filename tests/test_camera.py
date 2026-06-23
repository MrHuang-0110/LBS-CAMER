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


def test_camera_top_bar_back_button():
    """顶栏有返回钮 + CLICKED 设 exit_flag(GALLERY 态调 _leave_gallery)。"""
    src = open(APP_PATH, encoding="utf-8").read()
    assert "_build_top_bar" in src, "must have _build_top_bar"
    assert "EVENT.CLICKED" in src, "back button must bind CLICKED"
    assert "exit_flag[0] = True" in src, "back callback must set exit_flag"
    assert "_leave_gallery" in src, "back from gallery must call _leave_gallery"


def test_camera_state_machine():
    """状态机四状态常量 + 快门/模式回调存在。"""
    src = open(APP_PATH, encoding="utf-8").read()
    for token in ("STATE_PHOTO", "STATE_VIDEO", "STATE_RECORDING", "STATE_GALLERY"):
        assert token in src, "state constant missing: %s" % token
    tree = _parse(APP_PATH)
    funcs = {n.name for n in tree.body
             if isinstance(n, ast.FunctionDef)}
    for fn in ("_on_shutter", "_on_mode_toggle", "_refresh_shutter", "_refresh_mode_icon"):
        assert fn in funcs, "state machine function missing: %s" % fn


def test_camera_capture_uses_chn1():
    """拍照用 chn1(SXGAM/RGB565 支持 jpg save)。"""
    src = open(APP_PATH, encoding="utf-8").read()
    assert "CAM_CHN_ID_1" in src, "capture must use chn1 (SXGAM/RGB565 for jpg save)"


def test_camera_destroy_ui_restores_opacity():
    """_destroy_ui 必须恢复屏幕不透明(相机运行期 bg_opa=0)。"""
    tree = _parse(APP_PATH)
    destroy_fn = _function_node(tree, "_destroy_ui")
    src_segment = ast.unparse(destroy_fn)
    assert "set_style_bg_opa(255" in src_segment or "bg_opa(255" in src_segment, \
        "_destroy_ui must restore screen opacity to 255 for main menu"


def test_camera_no_self_init_media():
    """走 init_app,不自 init media/sensor(由 runtime 管理)。"""
    src = open(APP_PATH, encoding="utf-8").read()
    assert "MediaManager.init" not in src, "must not self-init MediaManager"
    assert "sensor.reset" not in src, "must not self-reset sensor"
    assert "sensor.run" not in src, "must not self-run sensor (init_app does it)"


def test_camera_business_preserved():
    """核心业务函数保留(拍照/录像/图库)。"""
    tree = _parse(APP_PATH)
    funcs = {n.name for n in tree.body
             if isinstance(n, ast.FunctionDef)}
    for fn in ("_capture_photo", "_start_recording", "_stop_recording",
               "_update_timer", "_enter_gallery", "_leave_gallery",
               "_on_delete_photo", "_group_photos_by_date", "_build_gallery_ui"):
        assert fn in funcs, "business function must remain: %s" % fn


def test_camera_no_on_frame_hook():
    """camera 是叶子 APP,不挂 on_frame 钩子(非 AI 基座)。"""
    src = open(APP_PATH, encoding="utf-8").read()
    # on_frame 是 _template 的 AI 钩子,camera 不应有顶层 def on_frame
    assert "def on_frame" not in src, "camera must NOT have on_frame hook (leaf app, not AI base)"


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
