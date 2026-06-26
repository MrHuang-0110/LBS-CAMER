# tests/test_app_runtime_object.py — app_runtime object_detect 通道+图标预读契约(AST)
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


def test_channels_for_object_detect_uses_chn2_xga_rgbp888():
    """_channels_for 须为 object_detect 配 chn2 XGA RGBP888(同 face_detect AI 通道)。"""
    seg = ast.get_source_segment(_src(), _method("AppRuntime", "_channels_for")) or ""
    assert "object_detect" in seg, "_channels_for must handle object_detect"
    assert "XGA" in seg, "object_detect must use XGA detection size"
    assert "RGBP888" in seg, "object_detect must use RGBP888"
    assert "CAM_CHN_ID_2" in seg, "object_detect detection on chn2"


def test_init_app_preloads_object_icons():
    """init_app 须对 object_detect 调 preload_object_icons()。"""
    seg = ast.get_source_segment(_src(), _method("AppRuntime", "init_app")) or ""
    assert "object_detect" in seg, "init_app must branch on object_detect"
    assert "preload_object_icons" in seg, "init_app must call preload_object_icons for object_detect"


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
