# tests/test_gesture_detect_ast.py -- host-side AST 契约测试(gesture_detect)
import ast, os, json

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP_RUNTIME_PATH = os.path.join(ROOT, "core", "app_runtime.py")
ICON_CACHE_PATH = os.path.join(ROOT, "core", "icon_cache.py")
HOST_API_PATH = os.path.join(ROOT, "comm", "host_api.py")


def _read(path):
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def test_gesture_detect_in_category_type_map():
    """CATEGORY_TYPE 必须包含 'gesture_detect': TYPE_GESTURE_DETECT。"""
    src = _read(HOST_API_PATH)
    assert '"gesture_detect":' in src
    after = src.split('"gesture_detect":')[1][:80]
    assert "TYPE_GESTURE_DETECT" in after


def test_channels_for_gesture_detect():
    """_channels_for 的 gesture_detect 分支 append chn2 XGA RGBP888。"""
    src = _read(APP_RUNTIME_PATH)
    start = src.find("def _channels_for(")
    body = src[start:start + 1500]
    assert "gesture_detect" in body, "_channels_for must handle gesture_detect"
    after = body.split('"gesture_detect"')[1][:300]
    assert "append" in after, "gesture_detect should append AI channel"
    assert "CAM_CHN_ID_2" in after, "gesture_detect must use CAM_CHN_ID_2 for AI"
    assert "XGA" in after, "gesture_detect AI channel must use XGA framesize"
    assert "RGBP888" in after, "gesture_detect AI channel must use RGBP888 pixformat"


def test_preload_gesture_icons_in_init_app():
    """init_app 必须对 gesture_detect 调 preload_gesture_icons。"""
    src = _read(APP_RUNTIME_PATH)
    assert '"gesture_detect"' in src
    assert 'preload_gesture_icons' in src


def test_icon_cache_has_gesture_methods():
    """icon_cache 必须有 preload_gesture_icons + get_gesture_icon + _gesture_icons 槽。"""
    src = _read(ICON_CACHE_PATH)
    assert "def preload_gesture_icons" in src
    assert "def get_gesture_icon" in src
    assert "_gesture_icons" in src


def test_app_imports_gesture_ai():
    """app.py 必须导入 gesture_ai 的 HandRecognition 类。"""
    app_path = os.path.join(ROOT, "scripts", "gesture_detect", "app.py")
    src = _read(app_path)
    assert "gesture_ai" in src, "app must import from gesture_ai"
    assert "HandRecognition" in src, "app must import HandRecognition"


def test_on_frame_uses_registrar():
    """app.py on_frame 必须使用 try_register(..., registrar=gesture_db.register)。"""
    app_path = os.path.join(ROOT, "scripts", "gesture_detect", "app.py")
    src = _read(app_path)
    assert "registrar" in src, "app must use registrar pattern for K2 registration"


def test_has_host_tick():
    """app.py on_frame 必须有 host_tick 调用。"""
    app_path = os.path.join(ROOT, "scripts", "gesture_detect", "app.py")
    src = _read(app_path)
    assert "host_tick" in src, "app must call host_tick for protocol 0x08"


def test_has_draw_cross():
    """app.py on_frame 必须有 draw_cross 调用(居中绿色十字)。"""
    app_path = os.path.join(ROOT, "scripts", "gesture_detect", "app.py")
    src = _read(app_path)
    assert "draw_cross" in src, "app must call draw_cross for center crosshair"


def test_runner():
    import sys
    mod = sys.modules[__name__]
    fails = 0
    for n in sorted(dir(mod)):
        if n.startswith("test_") and callable(getattr(mod, n)) and n != "test_runner":
            try:
                getattr(mod, n)()
                print("  PASS %s" % n)
            except Exception as e:
                print("  FAIL %s: %s" % (n, e))
                fails += 1
    assert fails == 0, "%d tests failed" % fails


if __name__ == "__main__":
    test_runner()
