# tests/test_body_ai_ast.py — host-side AST 契约测试(body_ai)
import ast, os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _read(path):
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def test_body_ai_classes_exist():
    """PersonDetectionApp / PersonRecognitionApp / PersonRecognition 类必须定义。"""
    src = _read(os.path.join(ROOT, "core", "body_ai.py"))
    tree = ast.parse(src)
    classes = [n.name for n in ast.walk(tree) if isinstance(n, ast.ClassDef)]
    assert "PersonDetectionApp" in classes
    assert "PersonRecognitionApp" in classes
    assert "PersonRecognition" in classes


def test_person_labels_module_level():
    """PERSON_LABELS 必须在模块级别定义,含 1 个标签 'person'。"""
    src = _read(os.path.join(ROOT, "core", "body_ai.py"))
    tree = ast.parse(src)
    for n in tree.body:
        if isinstance(n, ast.Assign):
            for t in n.targets:
                if isinstance(t, ast.Name) and t.id == "PERSON_LABELS":
                    assert n.value.elts[0].s == "person"
                    return
    assert False, "PERSON_LABELS not found at module level"


def test_person_anchors_module_level():
    """PERSON_ANCHORS 必须在模块级别定义(9 个 anchor,18 个值)。"""
    src = _read(os.path.join(ROOT, "core", "body_ai.py"))
    tree = ast.parse(src)
    for n in tree.body:
        if isinstance(n, ast.Assign):
            for t in n.targets:
                if isinstance(t, ast.Name) and t.id == "PERSON_ANCHORS":
                    assert len(n.value.elts) == 18
                    return
    assert False, "PERSON_ANCHORS not found at module level"


def test_kmodel_paths_in_file():
    """kmodel 路径常量必须在文件中。"""
    src = _read(os.path.join(ROOT, "core", "body_ai.py"))
    assert "/sdcard/examples/kmodel/person_detect_yolov5n.kmodel" in src
    assert "/sdcard/examples/kmodel/recognition.kmodel" in src


def test_person_recognition_postprocess_returns():
    """PersonRecognitionApp.postprocess 必须有 return 语句(返回特征向量)。"""
    src = _read(os.path.join(ROOT, "core", "body_ai.py"))
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == "PersonRecognitionApp":
            for item in node.body:
                if isinstance(item, ast.FunctionDef) and item.name == "postprocess":
                    assert any(isinstance(n, ast.Return) for n in ast.walk(item)), \
                        "postprocess must have a return statement"
                    return
    assert False, "PersonRecognitionApp.postprocess not found"


def test_person_recognition_composite_methods():
    """PersonRecognition 组合类必须有 deinit + run 方法。"""
    src = _read(os.path.join(ROOT, "core", "body_ai.py"))
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == "PersonRecognition":
            methods = [n.name for n in node.body if isinstance(n, ast.FunctionDef)]
            assert "deinit" in methods, "PersonRecognition must have deinit"
            assert "run" in methods, "PersonRecognition must have run"
            return
    assert False, "PersonRecognition class not found"


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
