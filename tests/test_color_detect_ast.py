# tests/test_color_detect_ast.py — host-side AST 契约测试(color_detect)
import ast, os, json

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP_RUNTIME_PATH = os.path.join(ROOT, "core", "app_runtime.py")
ICON_CACHE_PATH = os.path.join(ROOT, "core", "icon_cache.py")
HOST_API_PATH = os.path.join(ROOT, "comm", "host_api.py")
CATEGORIES_PATH = os.path.join(ROOT, "config", "categories.json")
APP_PATH = os.path.join(ROOT, "scripts", "color_detect", "app.py")


def _read(path):
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def test_channels_for_color_detect_qvga_rgb565():
    """_channels_for 必须为 color_detect 配 chn1 QVGA RGB565(同 tag_detect)。"""
    src = _read(APP_RUNTIME_PATH)
    start = src.find("def _channels_for(")
    body = src[start:start + 1200]
    assert "color_detect" in body, "_channels_for must handle color_detect"
    assert "QVGA" in body, "color_detect must use QVGA on chn1"
    assert "RGB565" in body, "color_detect must use RGB565 on chn1"


def test_init_app_preloads_color_icons():
    """init_app 必须对 color_detect 调 preload_color_icons。"""
    src = _read(APP_RUNTIME_PATH)
    assert 'preload_color_icons' in src, "init_app must preload color icons"


def test_icon_cache_has_color_methods():
    """icon_cache 必须有 preload_color_icons + get_color_icon + _color_icons 槽。"""
    src = _read(ICON_CACHE_PATH)
    assert "def preload_color_icons" in src
    assert "def get_color_icon" in src
    assert "_color_icons" in src  # 槽字段


def test_color_detect_in_categories_enabled():
    """categories.json 必须有 color_detect 条目且 enabled。"""
    with open(CATEGORIES_PATH, encoding="utf-8") as f:
        data = json.load(f)
    cats = data.get("categories", [])
    c = [x for x in cats if x.get("id") == "color_detect"]
    assert c, "color_detect category missing"
    assert c[0].get("enabled") is True
    assert c[0].get("ui_mode") == "stream"


def test_runner():
    failures = 0
    tests = [(n, f) for n, f in sorted(globals().items())
             if n.startswith("test_") and callable(f) and n != "test_runner"]
    for name, fn in tests:
        try:
            fn(); print("PASS %s" % name)
        except Exception as e:
            failures += 1; print("FAIL %s: %s" % (name, e))
    print("\n%s" % ("ALL PASS" if failures == 0 else "%d FAILED" % failures))
    import sys
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    test_runner()
