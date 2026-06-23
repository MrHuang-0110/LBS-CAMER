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
