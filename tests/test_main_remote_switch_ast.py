# tests/test_main_remote_switch_ast.py -- main.py 远程切换注册点 AST 守护
import ast, os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MAIN_PATH = os.path.join(ROOT, "main.py")


def _src():
    with open(MAIN_PATH, "r", encoding="utf-8") as f:
        return f.read()


def test_main_registers_switch_handler():
    """main.py 必须调 register_switch_handler(_on_remote_switch)。"""
    src = _src()
    assert "register_switch_handler" in src, \
        "main.py must register switch handler"
    assert "_on_remote_switch" in src, \
        "main.py must reference _on_remote_switch"


def test_main_has_on_remote_switch_def():
    """main.py 必须定义 def _on_remote_switch(category)。"""
    src = _src()
    assert "def _on_remote_switch" in src, \
        "main.py must define _on_remote_switch(category)"


def test_on_remote_switch_uses_write_and_reset():
    """_on_remote_switch 体:有 category 分支调 _write_next_script + machine.reset。"""
    tree = ast.parse(_src(), filename=MAIN_PATH)
    fn = None
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_on_remote_switch":
            fn = node
            break
    assert fn is not None, "_on_remote_switch must be defined"
    body_src = ast.get_source_segment(_src(), fn) or ""
    assert "_write_next_script" in body_src, \
        "_on_remote_switch must call _write_next_script for script switch"
    assert "machine.reset" in body_src, \
        "_on_remote_switch must call machine.reset"


def test_on_remote_switch_handles_none_branch():
    """_on_remote_switch 体:None 分支(回菜单)调 _clear_next_script + reset。"""
    tree = ast.parse(_src(), filename=MAIN_PATH)
    fn = None
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_on_remote_switch":
            fn = node
            break
    assert fn is not None
    body_src = ast.get_source_segment(_src(), fn) or ""
    assert "_clear_next_script" in body_src, \
        "_on_remote_switch must call _clear_next_script for None (main menu) branch"
    assert "machine.reset" in body_src


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
