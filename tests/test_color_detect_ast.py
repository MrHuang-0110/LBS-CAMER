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


def test_color_db_path_is_module_level_for_on_frame_flush():
    """_COLOR_DB_PATH must be module-level because on_frame() uses it after register."""
    src = _read(APP_PATH)
    tree = ast.parse(src)
    module_names = []
    for n in tree.body:
        if isinstance(n, ast.Assign):
            for t in n.targets:
                if isinstance(t, ast.Name):
                    module_names.append(t.id)
    assert "_COLOR_DB_PATH" in module_names
    assert "_color_db.flush_to_disk(_COLOR_DB_PATH)" in src
    assert "_color_db.load_from_disk(_COLOR_DB_PATH)" in src

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


def _extract_func_src(path, func_name):
    """从 app.py 抠出指定函数源码(避免 import 触发 lvgl)。"""
    src = _read(path)
    tree = ast.parse(src)
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == func_name:
            return ast.get_source_segment(src, node)
    return None


def test_rgb_to_lab_white():
    """白色 RGB(255,255,255) → L≈100, A≈0, B≈0。"""
    src = _extract_func_src(APP_PATH, "_rgb_to_lab")
    assert src is not None, "_rgb_to_lab missing in app.py"
    ns = {}
    exec(src, ns)
    L, A, B = ns["_rgb_to_lab"](255, 255, 255)
    assert abs(L - 100) < 3, "white L should be ~100, got %s" % L
    assert abs(A) < 3, "white A should be ~0, got %s" % A
    assert abs(B) < 3, "white B should be ~0, got %s" % B


def test_rgb_to_lab_black():
    """黑色 RGB(0,0,0) → L≈0, A≈0, B≈0。"""
    src = _extract_func_src(APP_PATH, "_rgb_to_lab")
    ns = {}
    exec(src, ns)
    L, A, B = ns["_rgb_to_lab"](0, 0, 0)
    assert abs(L) < 3
    assert abs(A) < 3
    assert abs(B) < 3


def test_rgb_to_lab_red():
    """红色 RGB(255,0,0) → L≈53, A≈80(红方向), B≈67(黄方向)。"""
    src = _extract_func_src(APP_PATH, "_rgb_to_lab")
    ns = {}
    exec(src, ns)
    L, A, B = ns["_rgb_to_lab"](255, 0, 0)
    assert abs(L - 53) < 5
    assert A > 60, "red A should be strongly positive, got %s" % A
    assert B > 50, "red B should be positive, got %s" % B


def test_make_threshold_applies_plus_minus_10():
    """_make_threshold 用 ±10 容差,且裁剪到有效范围。"""
    src = _extract_func_src(APP_PATH, "_make_threshold")
    assert src is not None, "_make_threshold missing"
    ns = {}
    exec(src, ns)
    # L=95 → Lmin=85, Lmax=100(裁剪); A=5 → -5~15; B=120 → 110~127(裁剪)
    th = ns["_make_threshold"]((95, 5, 120))
    Lmin, Lmax, Amin, Amax, Bmin, Bmax = th
    assert (Lmin, Lmax) == (85, 100), "L clip fail: %s" % ((Lmin, Lmax),)
    assert (Amin, Amax) == (-5, 15)
    assert (Bmin, Bmax) == (110, 127), "B clip fail: %s" % ((Bmin, Bmax),)


def test_make_threshold_negative_a_clips():
    """A=-125 → Amin=-128(裁剪), Amax=-115。"""
    src = _extract_func_src(APP_PATH, "_make_threshold")
    ns = {}
    exec(src, ns)
    th = ns["_make_threshold"]((50, -125, 0))
    Lmin, Lmax, Amin, Amax, Bmin, Bmax = th
    assert Amin == -128
    assert Amax == -115


def test_build_ui_creates_top_bottom_preview_table():
    """_build_ui 必须建顶栏/底栏/预览/左表。"""
    src = _read(APP_PATH)
    assert "def _build_ui(" in src, "_build_ui missing"
    assert "_top_bar" in src and "_bottom_bar" in src
    assert "_preview" in src
    assert "_table" in src, "left color table missing"
    # 返回钮回调
    assert "exit_flag[0] = True" in src


def test_build_ui_has_6_thresh_cells_and_slider():
    """底栏必须有 6 阈值格 + 共享滑块。"""
    src = _read(APP_PATH)
    assert "THRESH_CELLS" in src
    assert "lv.slider" in src, "shared slider missing"
    # 选中格置绿
    assert "CARD_ACTIVE" in src


def test_preview_clickable_for_sampling():
    """预览/screen 必须可点击取色(CLICKED 事件设 pending_click)。"""
    src = _read(APP_PATH)
    assert "pending_click" in src, "pending_click sampling state missing"
    assert "EVENT.CLICKED" in src


def test_left_table_4rows_3cols():
    """左表自建 4×3 网格(非 lv.table):4 行 × 3 列,首行 L/A/B 表头。

    改自建 obj 网格因 lv.table cell 白底不可控 + 无法按行设底色(采样色)。
    断言:range(4) 行循环 + _table_cells 12 格 + 表头 ["L","A","B"]。
    """
    src = _read(APP_PATH)
    assert "range(4)" in src, "left table must build 4 rows"
    assert "_table_cells" in src, "must use _table_cells grid (not lv.table)"
    assert '["L", "A", "B"]' in src or "['L', 'A', 'B']" in src, "header row L/A/B"
    # 不应再调用 lv.table(K230 MP 绑定 cell 样式不可控);注释提及不算
    assert "lv.table(" not in src, "must not construct lv.table (cell bg uncontrollable)"


def test_on_frame_uses_find_blobs():
    """on_frame 必须用 find_blobs 检测。"""
    src = _read(APP_PATH)
    assert "find_blobs" in src


def test_on_frame_calls_host_tick():
    """on_frame 必须调 host_tick(slots)。"""
    src = _read(APP_PATH)
    assert "host_tick" in src


def test_on_frame_handles_pending_click():
    """on_frame 必须处理 _pending_click(get_pixel + RGB->LAB + 套阈值)。"""
    src = _read(APP_PATH)
    assert "_pending_click" in src
    assert "get_pixel" in src
    assert "_rgb_to_lab" in src
    assert "_make_threshold" in src


def test_on_frame_key2_register():
    """on_frame 必须处理 KEY2 注册当前阈值到 ColorDB。"""
    src = _read(APP_PATH)
    assert "try_register" in src
    assert "_color_db.register" in src or "color_db.register" in src


def test_run_has_exit_flag_loop():
    """run() 主循环必须用 exit_flag + snapshot + show OSD1 + task_handler。"""
    src = _read(APP_PATH)
    assert "def run(" in src
    assert "exit_flag" in src
    assert "snapshot" in src
    assert "LAYER_OSD1" in src
    assert "task_handler" in src


def test_on_frame_isolated_by_try_except():
    """on_frame 调用必须被 try/except 包裹。"""
    src = _read(APP_PATH)
    assert "on_frame(img)" in src
    assert "except" in src


def test_destroy_ui_restores_screen():
    """_destroy_ui 必须删 UI + 恢复 bg_opa=255。"""
    src = _read(APP_PATH)
    assert "def _destroy_ui(" in src
    assert "bg_opa(255" in src


def test_crosshair_drawn():
    """on_frame 必须画居中绿色十字。"""
    src = _read(APP_PATH)
    assert "draw_cross" in src
    compact = src.replace(" ", "")
    assert "320,240" in compact


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
