# tests/test_object_classify_detect_ast.py -- host-side AST 契约测试(object_classify)
import ast, os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP_RUNTIME_PATH = os.path.join(ROOT, "core", "app_runtime.py")
ICON_CACHE_PATH = os.path.join(ROOT, "core", "icon_cache.py")
HOST_API_PATH = os.path.join(ROOT, "comm", "host_api.py")


def _read(path):
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def test_object_classify_in_category_type_map():
    """CATEGORY_TYPE 必须包含 'object_classify': TYPE_OBJECT_CLASSIFY。"""
    src = _read(HOST_API_PATH)
    assert '"object_classify":' in src
    after = src.split('"object_classify":')[1][:80]
    assert "TYPE_OBJECT_CLASSIFY" in after


def test_channels_for_object_classify():
    """_channels_for 的 object_classify 分支 append chn2 XGA RGBP888。"""
    src = _read(APP_RUNTIME_PATH)
    start = src.find("def _channels_for(")
    body = src[start:start + 2200]
    assert "object_classify" in body, "_channels_for must handle object_classify"
    after = body.split('"object_classify"')[1][:300]
    assert "append" in after, "object_classify should append AI channel"
    assert "CAM_CHN_ID_2" in after, "object_classify must use CAM_CHN_ID_2 for AI"
    assert "XGA" in after, "object_classify AI channel must use XGA framesize"
    assert "RGBP888" in after, "object_classify AI channel must use RGBP888 pixformat"


def test_preload_object_classify_icons_in_init_app():
    """init_app 必须对 object_classify 调 preload_object_classify_icons。"""
    src = _read(APP_RUNTIME_PATH)
    assert '"object_classify"' in src
    assert 'preload_object_classify_icons' in src


def test_icon_cache_has_object_classify_methods():
    """icon_cache 必须有 preload_object_classify_icons + get_object_classify_icon + 槽。"""
    src = _read(ICON_CACHE_PATH)
    assert "def preload_object_classify_icons" in src
    assert "def get_object_classify_icon" in src
    assert "_object_classify_icons" in src


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
