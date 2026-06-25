# tests/test_host_tick_wiring.py — 主菜单+各脚本主循环必须调 host_tick
import ast
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _src(rel):
    with open(os.path.join(ROOT, rel), "r", encoding="utf-8") as f:
        return f.read()


def _run_fn_body(src, fn_name="run"):
    """返回 run() 函数源码段（从 def run 到下一个顶层 def 之前）。"""
    start = src.find("def %s(" % fn_name)
    assert start != -1, "%s missing" % fn_name
    nxt = src.find("\ndef ", start + 1)
    return src[start:nxt if nxt != -1 else len(src)]


def test_main_run_menu_calls_host_tick():
    src = _src("main.py")
    start = src.find("def run_menu(")
    assert start != -1, "run_menu missing"
    body = src[start:src.find("\ndef ", start + 1)]
    assert "host_tick" in body, "run_menu loop must call runtime.host_tick()"


def test_template_run_calls_host_tick():
    body = _run_fn_body(_src("scripts/_template/app.py"))
    assert "host_tick" in body, "_template run loop must call runtime.host_tick()"


def test_camera_run_calls_host_tick():
    body = _run_fn_body(_src("scripts/camera/app.py"))
    assert "host_tick" in body, "camera run loop must call runtime.host_tick()"


def test_settings_run_calls_host_tick():
    body = _run_fn_body(_src("scripts/settings/app.py"))
    assert "host_tick" in body, "settings run loop must call runtime.host_tick()"


def test_runner():
    failures = 0
    for name in sorted(n for n in globals() if n.startswith("test_") and n != "test_runner"):
        try:
            globals()[name]()
            print("PASS %s" % name)
        except Exception as e:
            failures += 1
            print("FAIL %s: %s" % (name, e))
    print("\n%s" % ("ALL PASS" if failures == 0 else "%d FAILED" % failures))
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    test_runner()
