# tests/test_settings.py — host-side AST tests for settings run(runtime) refactor.
# Run with:
#   python tests/test_settings.py
import ast
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP_PATH = os.path.join(ROOT, "scripts", "settings", "app.py")


def _parse(path):
    with open(path, "r", encoding="utf-8") as f:
        return ast.parse(f.read(), filename=path)


def _function_node(tree, name):
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError("Function %s missing" % name)


def test_settings_has_run_entry():
    """settings 必须有 run(runtime) 入口（reset 框架要求）。"""
    tree = _parse(APP_PATH)
    run_fn = _function_node(tree, "run")
    arg_names = [a.arg for a in run_fn.args.args]
    assert "runtime" in arg_names, "run(runtime) entry is required by reset framework"


def test_settings_run_uses_exit_flag_loop():
    """run() 主循环必须用 exit_flag 检测退出 + task_handler（page 型纯 UI）。"""
    src = open(APP_PATH, encoding="utf-8").read()
    assert "exit_flag" in src, "run must use exit_flag for exit detection"
    assert "while" in src, "run must have main loop"
    assert "task_handler" in src, "run must call lv.task_handler()"


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
