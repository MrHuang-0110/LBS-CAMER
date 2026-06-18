# tests/test_camera_gallery.py — host-side regression tests for camera gallery bugs.
# Run with either:
#   python tests/test_camera_gallery.py      (standalone, no deps)
#   python -m pytest tests/test_camera_gallery.py
import ast
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP_PATH = os.path.join(ROOT, "scripts", "camera", "app.py")


def _camera_app_tree():
    with open(APP_PATH, "r", encoding="utf-8") as f:
        return ast.parse(f.read(), filename=APP_PATH)


def _top_level_imports(tree):
    names = set()
    for node in tree.body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.asname or alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module.split(".")[0])
    return names


def _camera_app_class(tree):
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == "CameraApp":
            return node
    raise AssertionError("CameraApp class missing")


def _method_names(class_node):
    return {
        node.name for node in class_node.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


def _method_node(class_node, name):
    for node in class_node.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"{name} missing")


def test_thumbnail_loader_decodes_jpg_via_image_module():
    """_load_thumbnail must use image.Image() to decode JPG files into
    a temp BMP, then feed BMP bytes to LVGL's built-in decoder.
    LVGL on K230 lacks LV_USE_JPEG, cannot auto-decode JPG."""
    tree = _camera_app_tree()
    load_method = _method_node(_camera_app_class(tree), "_load_thumbnail")

    attr_calls = []
    name_calls = []
    for node in ast.walk(load_method):
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Attribute):
                attr_calls.append(node.func.attr)
            elif isinstance(node.func, ast.Name):
                name_calls.append(node.func.id)

    # Must use image.Image() for JPG decode
    assert "Image" in attr_calls, (
        "_load_thumbnail must call image.Image() to decode JPG photos — "
        "LVGL on K230 lacks LV_USE_JPEG, cannot auto-decode JPG"
    )
    # Must save temp BMP for LVGL decoder
    assert "save" in attr_calls, (
        "_load_thumbnail must call img.save(tmp_bmp) to produce BMP bytes "
        "that LVGL's built-in BMP decoder can render"
    )
    # Must read file bytes (for BMP direct or temp BMP)
    assert "read" in attr_calls, (
        "_load_thumbnail must use read() to get raw BMP bytes"
    )
    # Must use open() for file I/O
    assert "open" in name_calls, (
        "_load_thumbnail must use open() to read BMP/JPG files"
    )
    # Must NOT use to_rgb888() — temp BMP path, not raw pixels
    assert "to_rgb888" not in attr_calls, (
        "_load_thumbnail must NOT call to_rgb888() — "
        "uses temp BMP path instead of raw pixel header"
    )
    # Must NOT use resize() — K230 image.Image has no resize method
    assert "resize" not in attr_calls, (
        "_load_thumbnail must not call resize() — "
        "K230 MicroPython image.Image has no such method; "
        "LVGL lv.img.set_size() handles display scaling"
    )


def test_camera_app_imports_image_module():
    """camera app MUST import 'image' — _load_thumbnail uses image.Image()
    to decode JPG photos into raw RGB888 pixels for LVGL display, since
    LVGL on K230 lacks LV_USE_JPEG built-in decoder."""
    tree = _camera_app_tree()
    imports = _top_level_imports(tree)
    assert "image" in imports or "_image_lib" in imports, (
        "camera app must import image module — "
        "_load_thumbnail uses image.Image() + to_rgb888() "
        "to decode JPG → raw pixels for lv.img_dsc_t"
    )


def test_camera_app_defines_delete_reflow_helper():
    tree = _camera_app_tree()
    methods = _method_names(_camera_app_class(tree))
    assert "_remove_photo_from_groups" in methods, "delete must remove photo from data model"
    assert "_rebuild_gallery_ui" in methods, "delete must rebuild gallery UI so lower rows move up"


def test_delete_handler_uses_reflow_helper_and_rebuilds_ui():
    tree = _camera_app_tree()
    class_node = _camera_app_class(tree)
    delete_method = None
    for node in class_node.body:
        if isinstance(node, ast.FunctionDef) and node.name == "_on_delete_photo":
            delete_method = node
            break
    assert delete_method is not None, "_on_delete_photo missing"

    called = set()
    for node in ast.walk(delete_method):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if isinstance(node.func.value, ast.Name) and node.func.value.id == "self":
                called.add(node.func.attr)
    assert "_remove_photo_from_groups" in called, "delete must update grouped photo data"
    assert "_rebuild_gallery_ui" in called, "delete must rebuild list positions after data changes"


def test_photo_capture_saves_as_jpg():
    """_capture_photo must save as .jpg because user wants JPEG format.
    LVGL on K230 lacks LV_USE_JPEG, so gallery thumbnails decode JPG
    via image.Image() into raw pixels for display."""
    tree = _camera_app_tree()
    capture_method = _method_node(_camera_app_class(tree), "_capture_photo")

    found_jpg = False
    found_bmp = False

    for node in ast.walk(capture_method):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            val = node.value
            if val.endswith('.jpg') or val.endswith('.jpeg'):
                found_jpg = True
            if val.endswith('.bmp'):
                found_bmp = True

    assert found_jpg, (
        "_capture_photo must save as .jpg — "
        "user wants JPEG format for photos"
    )
    assert not found_bmp, (
        "_capture_photo must NOT save as .bmp — "
        "user wants JPEG format, not BMP"
    )


if __name__ == "__main__":
    failures = 0
    for name in sorted(n for n in dir() if n.startswith("test_")):
        try:
            globals()[name]()
            print(f"PASS {name}")
        except AssertionError as e:
            failures += 1
            print(f"FAIL {name}: {e}")
    print(f"\n{'ALL PASS' if failures == 0 else str(failures) + ' FAILED'}")
    sys.exit(1 if failures else 0)
