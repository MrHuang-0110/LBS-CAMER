# tests/test_face_ai.py — host-side AST tests for reusable face AI helpers.
# Run with:
#   python tests/test_face_ai.py
import ast
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FACE_AI_PATH = os.path.join(ROOT, "core", "face_ai.py")


def _parse(path):
    with open(path, "r", encoding="utf-8") as f:
        return ast.parse(f.read(), filename=path)


def _function_node(tree, name):
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError("Function %s missing" % name)


def _class_node(tree, name):
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == name:
            return node
    raise AssertionError("Class %s missing" % name)


def test_face_ai_defines_detection_helpers():
    tree = _parse(FACE_AI_PATH)
    _function_node(tree, "ALIGN_UP")
    _function_node(tree, "_draw_color")
    _class_node(tree, "FaceDetectionApp")


def test_draw_color_uses_k230_abgr_tuple_order():
    """K230 draw_line/draw_rectangle on Sensor.RGB888 expects (A,B,G,R)."""
    tree = _parse(FACE_AI_PATH)
    fn = _function_node(tree, "_draw_color")
    src = ast.get_source_segment(open(FACE_AI_PATH, encoding="utf-8").read(), fn) or ""
    assert "return (0xFF, b, g, r)" in src or "return (255, b, g, r)" in src, \
        "_draw_color must return (A,B,G,R), not RGBA"


def test_face_detection_draw_result_keeps_recognition_signature():
    tree = _parse(FACE_AI_PATH)
    cls = _class_node(tree, "FaceDetectionApp")
    for node in cls.body:
        if isinstance(node, ast.FunctionDef) and node.name == "draw_result":
            arg_names = [a.arg for a in node.args.args]
            assert "recognition_results" in arg_names, \
                "draw_result must keep recognition_results=None for Phase 3 compatibility"
            defaults = node.args.defaults
            assert defaults and isinstance(defaults[-1], ast.Constant) and defaults[-1].value is None, \
                "recognition_results default must be None"
            return
    raise AssertionError("FaceDetectionApp.draw_result missing")


def test_face_ai_phase1_does_not_define_registration_app():
    src = open(FACE_AI_PATH, encoding="utf-8").read()
    assert "FaceRegistrationApp" not in src, \
        "Phase 1 core/face_ai.py should only extract detection; registration belongs to Phase 3"


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
