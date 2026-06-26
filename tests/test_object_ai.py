# tests/test_object_ai.py — ObjectDetectionApp AST 契约(板端不可导入 AIBase/Ai2d)
import ast
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
AI_PATH = os.path.join(ROOT, "core", "object_ai.py")


def _src():
    with open(AI_PATH, "r", encoding="utf-8") as f:
        return f.read()


def _tree():
    return ast.parse(_src(), filename=AI_PATH)


def _cls():
    for n in _tree().body:
        if isinstance(n, ast.ClassDef) and n.name == "ObjectDetectionApp":
            return n
    raise AssertionError("Class ObjectDetectionApp missing")


def _method(name):
    for n in _cls().body:
        if isinstance(n, ast.FunctionDef) and n.name == name:
            return n
    raise AssertionError("Method %s missing" % name)


def test_class_exists():
    try:
        _cls()
    except AssertionError:
        assert False, "object_ai.py must define ObjectDetectionApp"


def test_coco_labels_has_80_entries():
    """COCO_LABELS 必须是 80 类(person..toothbrush)。"""
    seg = ast.get_source_segment(_src(), _cls()) or ""
    assert "COCO_LABELS" in _src(), "must define COCO_LABELS"
    src = _src()
    # 至少含首尾两类标志
    assert '"person"' in src, "COCO_LABELS must contain person"
    assert '"toothbrush"' in src, "COCO_LABELS must contain toothbrush"


def test_kmodel_path_yolov8n_320():
    src = _src()
    assert "yolov8n_320.kmodel" in src, "must use yolov8n_320.kmodel"


def test_init_takes_confidence_and_nms_threshold():
    m = _method("__init__")
    args = [a.arg for a in m.args.args]
    assert "confidence_threshold" in args, "must accept confidence_threshold"
    assert "nms_threshold" in args, "must accept nms_threshold"


def test_postprocess_calls_nms():
    """postprocess 必须调 NMS(纯 Python,移植 demo)。"""
    seg = ast.get_source_segment(_src(), _method("postprocess")) or ""
    assert "nms" in seg.lower(), "postprocess must call self.nms()"


def test_nms_method_exists():
    try:
        _method("nms")
    except AssertionError:
        assert False, "must define nms(boxes, scores, thresh) method"


def test_config_preprocess_uses_resize():
    """config_preprocess 必须用 ai2d.resize(不做 letterbox,同 demo)。"""
    seg = ast.get_source_segment(_src(), _method("config_preprocess")) or ""
    assert "resize" in seg, "config_preprocess must ai2d.resize"


def test_deinit_cleans_kpu_ai2d():
    seg = ast.get_source_segment(_src(), _method("deinit")) or ""
    assert "kpu" in seg, "deinit must del kpu"
    assert "ai2d" in seg, "deinit must del ai2d"


def test_rgb888p_and_display_size_defaults():
    """默认 rgb888p_size/display_size 对齐 face_ai(1024x768/640x480)。"""
    seg = ast.get_source_segment(_src(), _method("__init__")) or ""
    assert "1024" in seg or "RGB888P_SIZE" in _src(), "rgb888p default 1024x768"
    assert "640" in seg or "DISPLAY_SIZE" in _src(), "display default 640x480"


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
