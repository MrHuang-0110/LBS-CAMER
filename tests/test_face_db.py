# tests/test_face_db.py — host-side AST tests for face_db memory-only + dirty flags.
# Run with:
#   python tests/test_face_db.py
import ast
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(ROOT, "core", "face_db.py")


def _src():
    with open(DB_PATH, "r", encoding="utf-8") as f:
        return f.read()


def _parse():
    return ast.parse(_src(), filename=DB_PATH)


def _class_node(tree, name):
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == name:
            return node
    raise AssertionError("Class %s missing" % name)


def _method_node(cls, name):
    for node in cls.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError("Method %s missing" % name)


def _method_src(name):
    tree = _parse()
    cls = _class_node(tree, "_FaceDB")
    fn = _method_node(cls, name)
    return ast.get_source_segment(_src(), fn) or ""


def test_face_db_defines_database_search():
    src = _src()
    assert "def database_search" in src, "face_db must define database_search"


def test_register_is_memory_only():
    """register must NOT flush to disk (pitfall #2). Sets _dirty instead."""
    src = _method_src("register")
    assert "flush_to_disk" not in src, \
        "register must not call flush_to_disk (deferred to exit, pitfall #2)"
    assert "_save_next_slot" not in src, \
        "register must not persist next_slot (deferred to exit)"
    assert "_dirty" in src, "register must set _dirty flag"


def test_clear_is_memory_only():
    """clear must NOT os.remove (pitfall #2). Sets _clear_dirty instead."""
    src = _method_src("clear")
    assert "os.remove" not in src, \
        "clear must not os.remove (deferred to exit, pitfall #2)"
    assert "_clear_dirty" in src, "clear must set _clear_dirty flag"


def test_flush_to_disk_is_noop_and_resets_flags():
    """flush_to_disk 当前为 no-op（持久化路径待定，用户决定先不保存）。

    守护：(1) 仍是 exit-stage 入口，复位 _clear_dirty/_dirty 标志；
          (2) 不做盘 I/O（坑#2：runtime SD 写与 display DMA flush 死锁），
              无 os.remove/open/write——恢复持久化时再在此实现。
    """
    src = _method_src("flush_to_disk")
    assert "_clear_dirty" in src, "flush_to_disk must reset _clear_dirty flag"
    assert "_dirty" in src, "flush_to_disk must reset _dirty flag"
    assert "os.remove" not in src, \
        "flush_to_disk must not os.remove (persistence disabled, pitfall #2)"
    assert "open(" not in src, \
        "flush_to_disk must not open files (persistence disabled, pitfall #2)"


def test_face_db_has_init_features_and_get_features():
    tree = _parse()
    cls = _class_node(tree, "_FaceDB")
    names = {n.name for n in cls.body if isinstance(n, ast.FunctionDef)}
    for m in ("init_features", "get_features", "register", "clear", "flush_to_disk"):
        assert m in names, "_FaceDB missing method: %s" % m


ID_REGISTRY_PATH = os.path.join(ROOT, "core", "id_registry.py")


def test_id_registry_has_pending_method():
    src = open(ID_REGISTRY_PATH, encoding="utf-8").read()
    assert "def has_pending" in src, "IdRegistry must expose has_pending() for on_frame"


def test_id_registry_has_no_long_press_clear():
    """Clear is overlay-only; IdRegistry must not implement long-press clear."""
    src = open(ID_REGISTRY_PATH, encoding="utf-8").read()
    assert "long" not in src.lower() or "long press" not in src.lower(), \
        "IdRegistry must not implement long-press clear (overlay-only now)"


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
