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


def test_settings_no_basescript():
    """改造后不得残留旧 BaseScript 架构。"""
    src = open(APP_PATH, encoding="utf-8").read()
    for token in ("BaseScript", "on_enter", "on_exit", "SCRIPT_ID",
                  "SELF_MANAGED_TOP_BAR", "class SettingsApp"):
        assert token not in src, "old architecture token must be removed: %s" % token


def test_settings_uses_runtime_not_ctx():
    """ctx.X 必须改为 runtime.X（不留 ctx 引用）。"""
    src = open(APP_PATH, encoding="utf-8").read()
    assert "runtime.lang" in src, "must use runtime.lang"
    assert "runtime.config" in src, "must use runtime.config"
    # 不应再有 self.ctx 或裸 ctx.lang/ctx.config（注释里的 ctx 不算：用行内检查）
    for line in src.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        assert "self.ctx" not in line, "self.ctx must be removed: %s" % line
        assert "ctx.lang" not in line and "ctx.config" not in line, \
            "ctx.lang/ctx.config must be runtime.* : %s" % line


def test_settings_has_top_bar_back_button():
    """必须有顶栏返回钮（CLICKED 设 exit_flag）。"""
    src = open(APP_PATH, encoding="utf-8").read()
    assert "_build_top_bar" in src, "must have _build_top_bar"
    assert "EVENT.CLICKED" in src, "back button must bind CLICKED"
    assert "exit_flag[0] = True" in src, "back callback must set exit_flag"


def test_settings_title_from_lang():
    """顶栏标题必须取 lang（非硬编码）。"""
    src = open(APP_PATH, encoding="utf-8").read()
    assert "category.settings" in src, "title must come from lang.t('category.settings')"


def test_settings_does_not_self_init_media():
    """走 init_app，不自 init media/sensor。"""
    src = open(APP_PATH, encoding="utf-8").read()
    assert "MediaManager.init" not in src, "must not self-init MediaManager"
    assert "sensor.reset" not in src, "must not self-reset sensor"


def test_settings_keeps_language_and_about_business():
    """语言切换 + 关于业务必须保留。"""
    src = open(APP_PATH, encoding="utf-8").read()
    assert "_set_lang" in src, "language switch function must remain"
    assert ".switch(" in src, "lang.switch must remain"
    assert ".save()" in src, "config.save must remain"
    assert "event_bus.emit" in src, "event_bus.emit must remain"
    assert "_render_about" in src, "about render must remain"


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
