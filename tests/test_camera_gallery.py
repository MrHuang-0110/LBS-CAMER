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


def test_delete_handler_enqueues_and_beeps():
    """_on_delete_photo(CLICKED 回调)只入队 + 蜂鸣,不删文件/不重建 UI。

    reflow(os.remove + _remove_photo_from_groups + _rebuild_gallery_ui)由
    _process_pending_deletes 在主循环执行(见 test_process_pending_deletes_*)。
    根因:回调内删 _gallery_list(被删按钮祖先)= use-after-free 死机。
    """
    tree = _parse(APP_PATH)
    delete_fn = _function_node(tree, "_on_delete_photo")

    called = set()
    for node in ast.walk(delete_fn):
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name):
                called.add(node.func.id)
            elif isinstance(node.func, ast.Attribute):
                called.add(node.func.attr)
    assert "append" in called, "_on_delete_photo must enqueue photo (deferred)"
    assert "beep" in called, "_on_delete_photo must beep feedback"
    assert "remove" not in called, "_on_delete_photo must NOT os.remove (deferred)"
    assert "_rebuild_gallery_ui" not in called, \
        "_on_delete_photo must NOT rebuild UI (deferred)"


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


def _called_names(fn_node):
    """收集函数体内调用的函数名(Name 调用 + Attribute 属性调用)。"""
    names = set()
    for node in ast.walk(fn_node):
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name):
                names.add(node.func.id)
            elif isinstance(node.func, ast.Attribute):
                names.add(node.func.attr)
    return names


def test_delete_deferred_out_of_callback():
    """_on_delete_photo 不得在事件回调内直接删文件/重建 UI。

    根因:删除按钮(del_btn)是 _gallery_list 的子孙。在 CLICKED 回调内调
    _rebuild_gallery_ui() 会删除 _gallery_list(事件派发控件的祖先)→ LVGL
    use-after-free → 板端死机重启(C 级故障,不可被 try/except 捕获)。
    必须入队,由主循环 _process_pending_deletes 处理(对齐白闪 deferred 模式)。
    """
    tree = _parse(APP_PATH)
    fn = _function_node(tree, "_on_delete_photo")
    called = _called_names(fn)
    assert "remove" not in called, \
        "_on_delete_photo must NOT call os.remove directly (defer to main loop)"
    assert "_rebuild_gallery_ui" not in called, \
        "_on_delete_photo must NOT call _rebuild_gallery_ui directly (defer to main loop)"
    assert "_remove_photo_from_groups" not in called, \
        "_on_delete_photo must NOT call _remove_photo_from_groups directly (defer to main loop)"


def test_delete_has_pending_queue_and_processor():
    """必须有 _pending_deletes 队列 + _process_pending_deletes 处理器。"""
    src = open(APP_PATH, encoding="utf-8").read()
    assert "_pending_deletes" in src, "must have _pending_deletes queue"
    tree = _parse(APP_PATH)
    funcs = _module_functions(tree)
    assert "_process_pending_deletes" in funcs, \
        "must have _process_pending_deletes processor (deferred delete handler)"


def test_process_pending_deletes_does_remove_and_rebuild():
    """_process_pending_deletes 必须 os.remove + _remove_photo_from_groups + _rebuild_gallery_ui。"""
    tree = _parse(APP_PATH)
    fn = _function_node(tree, "_process_pending_deletes")
    called = _called_names(fn)
    assert "remove" in called, "_process_pending_deletes must call os.remove"
    assert "_remove_photo_from_groups" in called, \
        "_process_pending_deletes must call _remove_photo_from_groups"
    assert "_rebuild_gallery_ui" in called, \
        "_process_pending_deletes must call _rebuild_gallery_ui"


def test_run_loop_processes_pending_deletes():
    """run() 主循环必须调 _process_pending_deletes(处理从事件回调 deferred 的删除)。"""
    tree = _parse(APP_PATH)
    run_fn = _function_node(tree, "run")
    called = _called_names(run_fn)
    assert "_process_pending_deletes" in called, \
        "run() main loop must call _process_pending_deletes"


def test_rebuild_gallery_ui_decls_global_gallery_list():
    """_rebuild_gallery_ui 必须 global 声明 _gallery_list。

    根因:函数内 `_gallery_list = None` 赋值使 _gallery_list 成为局部变量
    (Python 作用域规则),导致前面 `if _gallery_list is not None:` 引用未赋值
    局部变量 → UnboundLocalError("local variable referenced before assignment")。
    板端现象:图库删照片后 _rebuild_gallery_ui 报 NameError 重启。
    必须显式 `global _gallery_list`。
    """
    tree = _parse(APP_PATH)
    fn = _function_node(tree, "_rebuild_gallery_ui")
    globals_declared = set()
    for node in ast.walk(fn):
        if isinstance(node, ast.Global):
            for name in node.names:
                globals_declared.add(name)
    assert "_gallery_list" in globals_declared, \
        "_rebuild_gallery_ui must declare 'global _gallery_list' (it assigns _gallery_list=None)"


def test_no_missing_global_decls():
    """全局扫描:所有函数中对模块级变量赋值,必须 global 声明。

    防御 _rebuild_gallery_ui/_leave_gallery 同类 UnboundLocalError 根因复发:
    函数内对模块级变量赋值(=)却不声明 global → 变量变局部 → 同函数内先前
    的读取引用未赋值局部变量 → NameError 重启。此测试扫描全文件,任一函数
    违反即 FAIL。
    """
    tree = _parse(APP_PATH)
    # 收集模块级赋值变量名
    mod_names = set()
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name):
                    mod_names.add(t.id)

    missing = []
    for node in tree.body:
        if not isinstance(node, ast.FunctionDef):
            continue
        globals_decl = set()
        for n in ast.walk(node):
            if isinstance(n, ast.Global):
                globals_decl.update(n.names)
        assigned = set()
        for n in ast.walk(node):
            if isinstance(n, ast.Assign):
                for t in n.targets:
                    if isinstance(t, ast.Name):
                        assigned.add(t.id)
            elif isinstance(n, ast.AugAssign) and isinstance(n.target, ast.Name):
                assigned.add(n.target.id)
        for a in assigned:
            if a in mod_names and a not in globals_decl:
                missing.append("%s: %s" % (node.name, a))
    assert not missing, \
        "missing global declarations (would cause UnboundLocalError): %s" % missing


def test_gallery_date_label_uses_dash_not_chinese_units():
    """图库日期分组标题必须用 '-' 分隔,不得用 '年/月/日'。

    用户要求:日期统一用 - - - 表示(如 2026-06-25),不要 年月日。
    """
    src = open(APP_PATH, encoding="utf-8").read()
    tree = _parse(APP_PATH)
    fn = _function_node(tree, "_group_photos_by_date")
    seg = ast.get_source_segment(src, fn) or ""
    # 旧格式 "%d年%d月%d日" 用 年/月 作单位;新格式用 - 分隔。"未知日期"占位允许。
    assert "年" not in seg, "date label must not use 年; use '-' separator (e.g. 2026-06-25)"
    assert "月" not in seg, "date label must not use 月; use '-' separator"
    assert "%d年" not in src and "月%d日" not in src, \
        "must not format date as 年/月/日; use '-' separator"


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
