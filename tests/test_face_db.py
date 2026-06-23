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


def test_flush_to_disk_handles_clear_dirty_and_dirty():
    src = _method_src("flush_to_disk")
    assert "_clear_dirty" in src, "flush_to_disk must handle _clear_dirty (remove all)"
    assert "_dirty" in src, "flush_to_disk must handle _dirty (write)"
    assert "os.remove" in src or "clear_disk" in src, \
        "flush_to_disk must remove .bin when _clear_dirty"


def test_face_db_has_init_features_and_get_features():
    tree = _parse()
    cls = _class_node(tree, "_FaceDB")
    names = {n.name for n in cls.body if isinstance(n, ast.FunctionDef)}
    for m in ("init_features", "get_features", "register", "clear", "flush_to_disk"):
        assert m in names, "_FaceDB missing method: %s" % m


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
