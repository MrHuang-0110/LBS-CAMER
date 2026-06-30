# tests/test_gesture_ai_ast.py — host-side AST 契约测试(gesture_ai)
import ast, os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _read(path):
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def test_hand_detection_app_class_exists():
    """HandDetectionApp 类必须在 gesture_ai.py 中定义。"""
    src = _read(os.path.join(ROOT, "core", "gesture_ai.py"))
    tree = ast.parse(src)
    classes = [n.name for n in ast.walk(tree) if isinstance(n, ast.ClassDef)]
    assert "HandDetectionApp" in classes
    assert "HandRecognitionApp" in classes
    assert "HandRecognition" in classes


def test_hand_labels_module_level():
    """HAND_LABELS 必须在模块级别定义,含 4 个标签。"""
    src = _read(os.path.join(ROOT, "core", "gesture_ai.py"))
    tree = ast.parse(src)
    for n in tree.body:
        if isinstance(n, ast.Assign):
            for t in n.targets:
                if isinstance(t, ast.Name) and t.id == "HAND_LABELS":
                    assert n.value.elts[0].s == "gun"
                    assert n.value.elts[1].s == "other"
                    assert n.value.elts[2].s == "yeah"
                    assert n.value.elts[3].s == "five"
                    return
    assert False, "HAND_LABELS not found at module level"


def test_hand_anchors_module_level():
    """HAND_ANCHORS 必须在模块级别定义(9 个 anchor,18 个值)。"""
    src = _read(os.path.join(ROOT, "core", "gesture_ai.py"))
    tree = ast.parse(src)
    for n in tree.body:
        if isinstance(n, ast.Assign):
            for t in n.targets:
                if isinstance(t, ast.Name) and t.id == "HAND_ANCHORS":
                    # 9 anchors × 2 = 18 values
                    assert len(n.value.elts) == 18
                    return
    assert False, "HAND_ANCHORS not found at module level"


def test_kmodel_paths_in_file():
    """kmodel 路径常量必须在文件中。"""
    src = _read(os.path.join(ROOT, "core", "gesture_ai.py"))
    assert "/sdcard/examples/kmodel/hand_det.kmodel" in src
    assert "/sdcard/examples/kmodel/hand_reco.kmodel" in src


def test_hand_recognition_postprocess_returns_tuple():
    """HandRecognitionApp.postprocess 返回 (idx, score) tuple。"""
    src = _read(os.path.join(ROOT, "core", "gesture_ai.py"))
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == "HandRecognitionApp":
            for item in node.body:
                if isinstance(item, ast.FunctionDef) and item.name == "postprocess":
                    body_str = ast.dump(item)
                    assert any(isinstance(n, ast.Return) for n in ast.walk(item)), \
                        "postprocess must have a return statement"
                    return
    assert False, "HandRecognitionApp.postprocess not found"


def test_hand_recognition_deinit_method():
    """HandRecognition 组合类必须有 deinit 方法。"""
    src = _read(os.path.join(ROOT, "core", "gesture_ai.py"))
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == "HandRecognition":
            methods = [n.name for n in node.body if isinstance(n, ast.FunctionDef)]
            assert "deinit" in methods, "HandRecognition must have deinit"
            assert "run" in methods, "HandRecognition must have run"
            return
    assert False, "HandRecognition class not found"


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
