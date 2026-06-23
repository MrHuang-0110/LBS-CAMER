# tests/test_template.py — host-side AST tests for script template + framework changes.
# Run with:
#   python tests/test_template.py
import ast
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP_RUNTIME_PATH = os.path.join(ROOT, "core", "app_runtime.py")
MAIN_PATH = os.path.join(ROOT, "main.py")
TEMPLATE_APP_PATH = os.path.join(ROOT, "scripts", "_template", "app.py")
TEMPLATE_MANIFEST_PATH = os.path.join(ROOT, "scripts", "_template", "manifest.json")
CATEGORIES_PATH = os.path.join(ROOT, "config", "categories.json")


def _parse(path):
    with open(path, "r", encoding="utf-8") as f:
        return ast.parse(f.read(), filename=path)


def _method_node(cls_node, name):
    for n in cls_node.body:
        if isinstance(n, ast.FunctionDef) and n.name == name:
            return n
    raise AssertionError("Method %s missing" % name)


def test_lvgl_init_takes_render_mode_param():
    """_lvgl_init must accept a render_mode parameter (default FULL)."""
    tree = _parse(APP_RUNTIME_PATH)
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == "AppRuntime":
            init = _method_node(node, "_lvgl_init")
            arg_names = [a.arg for a in init.args.args]
            assert "render_mode" in arg_names, \
                "_lvgl_init must take render_mode param"
            # default must be FULL
            defaults = init.args.defaults
            assert defaults, "_lvgl_init render_mode must have default FULL"
            break
    else:
        raise AssertionError("AppRuntime class missing")


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
