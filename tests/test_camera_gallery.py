# tests/test_camera_gallery.py — host-side regression tests for camera gallery.
# 迁移后 camera 是模块级 run(runtime) 函数式(无 CameraApp 类)。
# Run with:
#   python tests/test_camera_gallery.py
import ast
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP_PATH = os.path.join(ROOT, "scripts", "camera", "app.py")


def _parse(path):
    with open(path, "r", encoding="utf-8") as f:
        return ast.parse(f.read(), filename=path)


def _function_node(tree, name):
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError("Function %s missing" % name)


def _module_functions(tree):
    return {
        node.name for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


def test_gallery_has_delete_reflow_helpers():
    """删除照片必须更新分组数据 + 重建列表 UI。"""
    tree = _parse(APP_PATH)
    funcs = _module_functions(tree)
    assert "_remove_photo_from_groups" in funcs, "delete must remove photo from data model"
    assert "_rebuild_gallery_ui" in funcs, "delete must rebuild gallery UI so lower rows move up"


def test_delete_handler_uses_reflow_helpers():
    """_on_delete_photo 必须调 _remove_photo_from_groups + _rebuild_gallery_ui。"""
    tree = _parse(APP_PATH)
    delete_fn = _function_node(tree, "_on_delete_photo")

    called = set()
    for node in ast.walk(delete_fn):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            called.add(node.func.id)
    assert "_remove_photo_from_groups" in called, "delete must update grouped photo data"
    assert "_rebuild_gallery_ui" in called, "delete must rebuild list positions after data changes"


def test_photo_capture_saves_as_jpg():
    """_capture_photo 必须存 .jpg(用户要求 JPEG 格式)。"""
    tree = _parse(APP_PATH)
    capture_fn = _function_node(tree, "_capture_photo")

    found_jpg = False
    found_bmp = False
    for node in ast.walk(capture_fn):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            val = node.value
            if val.endswith('.jpg') or val.endswith('.jpeg'):
                found_jpg = True
            if val.endswith('.bmp'):
                found_bmp = True

    assert found_jpg, "_capture_photo must save as .jpg — user wants JPEG format"
    assert not found_bmp, "_capture_photo must NOT save as .bmp — user wants JPEG format"


def test_gallery_does_not_decode_thumbnails():
    """图库不显示缩略图(K230 image.Image 不能读 JPG,本设计只显示文件名+日期+删除)。
    缩略图死代码(_load_thumbnail/_fit_thumb_size/_bmp_dimensions)必须已删除。"""
    src = open(APP_PATH, encoding="utf-8").read()
    for token in ("_load_thumbnail", "_fit_thumb_size", "_bmp_dimensions",
                  "_gallery_thumbs"):
        assert token not in src, "dead thumbnail code must be removed: %s" % token


def test_camera_does_not_import_image():
    """删缩略图死代码后,camera 顶层不再 import image。"""
    tree = _parse(APP_PATH)
    for node in tree.body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert alias.name != "image", "must not import image (thumbnail dead code removed)"
        elif isinstance(node, ast.ImportFrom):
            assert node.module != "image" and (
                node.module is None or not node.module.startswith("image")), \
                "must not import from image"


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
