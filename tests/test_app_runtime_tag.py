# tests/test_app_runtime_tag.py — app_runtime tag_detect 通道+图标预读契约(AST)
import ast
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RT_PATH = os.path.join(ROOT, "core", "app_runtime.py")


def _src():
    with open(RT_PATH, "r", encoding="utf-8") as f:
        return f.read()


def _method(cls_name, name):
    tree = ast.parse(_src(), filename=RT_PATH)
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == cls_name:
            for n in node.body:
                if isinstance(n, ast.FunctionDef) and n.name == name:
                    return n
    raise AssertionError("%s.%s missing" % (cls_name, name))


def test_channels_for_tag_detect_uses_qvga_rgb565():
    """_channels_for 须为 tag_detect 配 chn1 QVGA RGB565。"""
    seg = ast.get_source_segment(_src(), _method("AppRuntime", "_channels_for")) or ""
    assert "tag_detect" in seg, "_channels_for must handle tag_detect"
    assert "QVGA" in seg, "tag_detect must use QVGA detection"
    assert "RGB565" in seg, "tag_detect must use RGB565"
    assert "CAM_CHN_ID_1" in seg, "tag_detect detection on chn1"


def test_init_app_preloads_tag_icons():
    """init_app 须对 tag_detect 调 preload_tag_icons()。"""
    seg = ast.get_source_segment(_src(), _method("AppRuntime", "init_app")) or ""
    assert "tag_detect" in seg, "init_app must branch on tag_detect"
    assert "preload_tag_icons" in seg, "init_app must call preload_tag_icons for tag_detect"


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
