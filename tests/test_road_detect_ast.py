# tests/test_road_detect_ast.py — host-side AST 契约测试(road_detect)
import ast, os, json

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP_RUNTIME_PATH = os.path.join(ROOT, "core", "app_runtime.py")
ICON_CACHE_PATH = os.path.join(ROOT, "core", "icon_cache.py")
HOST_API_PATH = os.path.join(ROOT, "comm", "host_api.py")
CATEGORIES_PATH = os.path.join(ROOT, "config", "categories.json")


def _read(path):
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def test_road_detect_in_categories_enabled():
    """categories.json 必须有 road_detect 条目且 enabled。"""
    with open(CATEGORIES_PATH, encoding="utf-8") as f:
        data = json.load(f)
    cats = data.get("categories", [])
    c = [x for x in cats if x.get("id") == "road_detect"]
    assert len(c) == 1, "road_detect must be in categories.json"
    assert c[0].get("enabled", False), "road_detect must be enabled"
    assert c[0]["script"] == "road_detect"
    assert c[0]["name_key"] == "category.road_detect"


def test_type_road_detect_in_host_api():
    """host_api 必须有 TYPE_ROAD_DETECT = 0x07。"""
    src = _read(HOST_API_PATH)
    assert "TYPE_ROAD_DETECT" in src
    assert "0x07" in src


def test_road_detect_in_category_type_map():
    """CATEGORY_TYPE 必须包含 'road_detect'。"""
    src = _read(HOST_API_PATH)
    assert '"road_detect":' in src
    assert "TYPE_ROAD_DETECT" in src.split('"road_detect":')[1][:80]


def test_channels_for_road_detect_qvga_rgb565():
    """_channels_for 必须为 road_detect 配 chn1 QVGA RGB565(同 color_detect)。"""
    src = _read(APP_RUNTIME_PATH)
    start = src.find("def _channels_for(")
    body = src[start:start + 1200]
    assert "road_detect" in body, "_channels_for must handle road_detect"
    assert "QVGA" in body.split('"road_detect"')[1][:200], "road_detect must use QVGA"
    assert "RGB565" in body.split('"road_detect"')[1][:200], "road_detect must use RGB565"


def test_preload_road_icons_in_init_app():
    """init_app 必须对 road_detect 调 preload_road_icons。"""
    src = _read(APP_RUNTIME_PATH)
    assert '"road_detect"' in src
    assert 'preload_road_icons' in src


def test_icon_cache_has_road_methods():
    """icon_cache 必须有 preload_road_icons + get_road_icon + _road_icons 槽。"""
    src = _read(ICON_CACHE_PATH)
    assert "def preload_road_icons" in src
    assert "def get_road_icon" in src
    assert "_road_icons" in src


def test_road_db_path_is_module_level():
    """_ROAD_DB_PATH 必须在模块级别定义。"""
    app_path = os.path.join(ROOT, "scripts", "road_detect", "app.py")
    src = _read(app_path)
    tree = ast.parse(src)
    module_names = []
    for n in tree.body:
        if isinstance(n, ast.Assign):
            for t in n.targets:
                if isinstance(t, ast.Name):
                    module_names.append(t.id)
    assert "_ROAD_DB_PATH" in module_names


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
