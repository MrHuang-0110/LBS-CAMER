# tests/test_framework.py — reset 框架契约测试
import ast
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RUNTIME_PATH = os.path.join(ROOT, "core", "app_runtime.py")
MAIN_PATH = os.path.join(ROOT, "main.py")
ICON_CACHE_PATH = os.path.join(ROOT, "core", "icon_cache.py")


def _parse(path):
    with open(path, "r", encoding="utf-8") as f:
        return ast.parse(f.read(), filename=path)


def _class_node(tree, name):
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == name:
            return node
    raise AssertionError("Class %s missing" % name)


def _method_names(class_node):
    return {n.name for n in class_node.body
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}


def test_app_runtime_class_exists():
    tree = _parse(RUNTIME_PATH)
    cls = _class_node(tree, "AppRuntime")
    methods = _method_names(cls)
    for m in ("init_menu", "init_app", "cleanup"):
        assert m in methods, "AppRuntime missing method: %s" % m


def test_app_runtime_init_app_takes_category():
    tree = _parse(RUNTIME_PATH)
    cls = _class_node(tree, "AppRuntime")
    found = False
    for n in cls.body:
        if isinstance(n, ast.FunctionDef) and n.name == "init_app":
            arg_names = [a.arg for a in n.args.args]
            assert "category_id" in arg_names or "category" in arg_names, \
                "init_app must take category_id param"
            found = True
    assert found, "init_app method missing"


def test_main_reads_next_script():
    tree = _parse(MAIN_PATH)
    src = open(MAIN_PATH, encoding="utf-8").read()
    assert "next_script" in src, "main.py must read .next_script marker"
    assert "machine.reset" in src or "reset()" in src, \
        "main.py must call machine.reset() to switch"


def test_main_has_launch_writer():
    src = open(MAIN_PATH, encoding="utf-8").read()
    assert "next_script" in src and ("wb" in src or "write" in src.lower()), \
        "main.py must write .next_script on card click"


def test_icon_cache_has_preload_back_icon():
    """icon_cache 必须有独立 preload_back_icon() 方法（供 init_app 预读返回钮图标）。

    根因：_back_icon 原本只在 preload_settings_icons() 里预读，而该方法只在
    init_menu 调用。走 init_app 的脚本（模板/settings）顶栏返回钮用
    get_back_icon() 拿不到图标。需独立 preload_back_icon() 供 init_app 调。
    """
    src = open(ICON_CACHE_PATH, encoding="utf-8").read()
    assert "def preload_back_icon(" in src, \
        "icon_cache must have standalone preload_back_icon() method"


def test_init_app_preloads_back_icon():
    """init_app 必须调 preload_back_icon()（脚本顶栏返回钮需要图标）。"""
    src = open(RUNTIME_PATH, encoding="utf-8").read()
    init_start = src.find("def init_app(")
    assert init_start != -1, "init_app missing"
    init_body = src[init_start:]
    assert "preload_back_icon" in init_body, \
        "init_app must call icon_cache.preload_back_icon() for top bar back button"


def test_init_app_preloads_camera_icons():
    """init_app 必须为 camera 预读 camera 图标。

    根因:camera 走 init_app,顶栏返回/底栏图库/模式钮用 get_camera_icon(),
    而 preload_camera_icons() 原本只在 init_menu 调 → camera 进程拿不到图标。
    """
    src = open(RUNTIME_PATH, encoding="utf-8").read()
    init_start = src.find("def init_app(")
    assert init_start != -1, "init_app missing"
    init_body = src[init_start:]
    assert "preload_camera_icons" in init_body, \
        "init_app must preload camera icons for camera top/bottom bar"


def test_init_app_uses_full_render_mode():
    """init_app 必须用 FULL 渲染模式,不得用 PARTIAL。

    根因:flush_cb 每次清零非活跃缓冲,只在 FULL(整屏重绘)下安全;PARTIAL 只
    刷脏区,清零会抹掉持久 UI(顶栏等)——见 hw/lcd.py 注释。camera 拍照闪光
    触发脏区后 PARTIAL+清零导致顶底栏消失;face_detect 每帧画框同理会崩。
    单线程下 FULL 无 OSD1/OSD2 DMA 竞争,稳。对齐官方 ai_lvgl.py + hw/lcd.py。
    """
    src = open(RUNTIME_PATH, encoding="utf-8").read()
    assert "DISP_RENDER_MODE.PARTIAL" not in src, \
        "must not use PARTIAL (clears inactive buffer, wipes persistent UI on dynamic redraw)"
    init_start = src.find("def init_app(")
    assert init_start != -1, "init_app missing"
    init_body = src[init_start:]
    assert "_lvgl_init" in init_body, "init_app must call _lvgl_init"
    assert "DISP_RENDER_MODE.FULL" in init_body, \
        "init_app must pass FULL render mode to _lvgl_init"



def test_main_does_not_special_case_face_detect_init():
    """face_detect Phase 1 uses normal runtime.init_app path, not naked self-init."""
    src = open(MAIN_PATH, encoding="utf-8").read()
    assert 'category_id == "face_detect"' not in src and "category_id == 'face_detect'" not in src, \
        "main.py must not skip init_app for face_detect"
    assert "runtime.init_app(category_id, fpioa)" in src, \
        "run_script must initialize scripts through runtime.init_app(category_id, fpioa)"


def test_main_cleanup_does_not_skip_face_detect():
    src = open(MAIN_PATH, encoding="utf-8").read()
    assert 'category_id != "face_detect"' not in src and "category_id != 'face_detect'" not in src, \
        "runtime.cleanup() must not skip face_detect after template migration"
    assert "runtime.cleanup()" in src, "run_script must call runtime.cleanup()"


def test_app_runtime_face_detect_uses_only_chn0_and_chn2():
    src = open(RUNTIME_PATH, encoding="utf-8").read()
    start = src.find("def _channels_for")
    assert start != -1, "_channels_for missing"
    body = src[start:]
    face_pos = body.find('category_id == "face_detect"')
    assert face_pos != -1, "_channels_for must special-case face_detect channel setup"
    face_block = body[face_pos:body.find("elif", face_pos) if body.find("elif", face_pos) != -1 else len(body)]
    assert "CAM_CHN_ID_2" in face_block, "face_detect must declare chn2 before MediaManager.init"
    assert "CAM_CHN_ID_1" not in face_block, "Phase 1 face_detect must not allocate unused chn1"


def test_core_init_has_no_legacy_side_effect_imports():
    """import core.app_runtime must not load old ScriptRunner/UI modules.

    根因:Python import core.app_runtime 会先执行 core/__init__.py。旧 __init__
    eager-import ScriptRunner/PluginLoader 等旧同进程架构模块,板端因此继续导入
    ui.back_bar 并 fatal。reset 架构包初始化必须无副作用。
    """
    core_init_path = os.path.join(ROOT, "core", "__init__.py")
    src = open(core_init_path, encoding="utf-8").read()
    forbidden = [
        "ScriptRunner",
        "PluginLoader",
        "core.script_runner",
        "core.plugin_loader",
        "ui.back_bar",
    ]
    found = [token for token in forbidden if token in src]
    assert not found, "core/__init__.py must not eager-import legacy modules: %s" % found


def test_app_runtime_stores_category_id_in_init_menu():
    """init_menu 必须存 self.category_id='main_menu'（host_tick 用）。"""
    src = open(RUNTIME_PATH, encoding="utf-8").read()
    start = src.find("def init_menu(")
    assert start != -1, "init_menu missing"
    body = src[start:src.find("def ", start + 1)]
    assert "category_id" in body and "main_menu" in body, \
        "init_menu must set self.category_id = 'main_menu'"


def test_app_runtime_stores_category_id_in_init_app():
    """init_app 必须存 self.category_id=category_id（host_tick 用）。"""
    src = open(RUNTIME_PATH, encoding="utf-8").read()
    start = src.find("def init_app(")
    assert start != -1, "init_app missing"
    body = src[start:src.find("def ", start + 1)]
    assert "self.category_id" in body, \
        "init_app must store self.category_id = category_id"


def test_app_runtime_has_host_tick_method():
    """AppRuntime 必须有 host_tick(slots=None) 方法（每帧握手+推送）。"""
    src = open(RUNTIME_PATH, encoding="utf-8").read()
    tree = _parse(RUNTIME_PATH)
    cls = _class_node(tree, "AppRuntime")
    methods = _method_names(cls)
    assert "host_tick" in methods, "AppRuntime must have host_tick method"
    start = src.find("def host_tick(")
    seg = src[start:src.find("def ", start + 1)]
    assert "self.host" in seg and "tick" in seg, \
        "host_tick must call self.host.tick(...)"


if __name__ == "__main__":
    failures = 0
    for name in sorted(n for n in dir() if n.startswith("test_")):
        try:
            globals()[name]()
            print(f"PASS {name}")
        except AssertionError as e:
            failures += 1
            print(f"FAIL {name}: {e}")
        except Exception as e2:
            failures += 1
            print(f"FAIL {name}: {e2}")
    print(f"\n{'ALL PASS' if failures == 0 else str(failures) + ' FAILED'}")
    sys.exit(1 if failures else 0)
