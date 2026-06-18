# tests/test_framework.py — reset 框架契约测试
import ast
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RUNTIME_PATH = os.path.join(ROOT, "core", "app_runtime.py")
MAIN_PATH = os.path.join(ROOT, "main.py")


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
