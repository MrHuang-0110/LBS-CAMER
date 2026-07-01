# tests/test_body_detect_ast.py -- host-side AST 契约测试(body_detect)
import ast, os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP_RUNTIME_PATH = os.path.join(ROOT, "core", "app_runtime.py")
ICON_CACHE_PATH = os.path.join(ROOT, "core", "icon_cache.py")
HOST_API_PATH = os.path.join(ROOT, "comm", "host_api.py")


def _read(path):
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def test_body_detect_in_category_type_map():
    """CATEGORY_TYPE 必须包含 'body_detect': TYPE_BODY_DETECT。"""
    src = _read(HOST_API_PATH)
    assert '"body_detect":' in src
    after = src.split('"body_detect":')[1][:80]
    assert "TYPE_BODY_DETECT" in after


def test_channels_for_body_detect():
    """_channels_for 的 body_detect 分支 append chn2 XGA RGBP888。"""
    src = _read(APP_RUNTIME_PATH)
    start = src.find("def _channels_for(")
    body = src[start:start + 1800]
    assert "body_detect" in body, "_channels_for must handle body_detect"
    after = body.split('"body_detect"')[1][:300]
    assert "append" in after, "body_detect should append AI channel"
    assert "CAM_CHN_ID_2" in after, "body_detect must use CAM_CHN_ID_2 for AI"
    assert "XGA" in after, "body_detect AI channel must use XGA framesize"
    assert "RGBP888" in after, "body_detect AI channel must use RGBP888 pixformat"


def test_preload_body_icons_in_init_app():
    """init_app 必须对 body_detect 调 preload_body_icons。"""
    src = _read(APP_RUNTIME_PATH)
    assert '"body_detect"' in src
    assert 'preload_body_icons' in src


def test_icon_cache_has_body_methods():
    """icon_cache 必须有 preload_body_icons + get_body_icon + _body_icons 槽。"""
    src = _read(ICON_CACHE_PATH)
    assert "def preload_body_icons" in src
    assert "def get_body_icon" in src
    assert "_body_icons" in src


def test_app_imports_body_ai():
    """app.py 必须导入 body_ai 的 PersonRecognition 类。"""
    app_path = os.path.join(ROOT, "scripts", "body_detect", "app.py")
    src = _read(app_path)
    assert "body_ai" in src, "app must import from body_ai"
    assert "PersonRecognition" in src, "app must import PersonRecognition"


def test_on_frame_uses_registrar():
    """app.py on_frame 必须使用 try_register(..., registrar=body_db.register)。"""
    app_path = os.path.join(ROOT, "scripts", "body_detect", "app.py")
    src = _read(app_path)
    assert "registrar" in src, "app must use registrar pattern for K2 registration"


def test_has_host_tick():
    """app.py on_frame 必须有 host_tick 调用。"""
    app_path = os.path.join(ROOT, "scripts", "body_detect", "app.py")
    src = _read(app_path)
    assert "host_tick" in src, "app must call host_tick for protocol 0x09"


def test_has_draw_cross():
    """app.py on_frame 必须有 draw_cross 调用(居中绿色十字)。"""
    app_path = os.path.join(ROOT, "scripts", "body_detect", "app.py")
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
