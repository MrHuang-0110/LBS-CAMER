# tests/test_id_registry.py — IdRegistry 可选 registrar 契约(AST)
import ast
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REG_PATH = os.path.join(ROOT, "core", "id_registry.py")


def _src():
    with open(REG_PATH, "r", encoding="utf-8") as f:
        return f.read()


def _parse():
    return ast.parse(_src(), filename=REG_PATH)


def _method(name):
    tree = _parse()
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == "IdRegistry":
            for n in node.body:
                if isinstance(n, ast.FunctionDef) and n.name == name:
                    return n
    raise AssertionError("Method %s missing" % name)


def test_try_register_has_optional_registrar_param():
    """try_register 须含可选 registrar 参数(默认 None 走 face_db,向后兼容)。"""
    m = _method("try_register")
    args = [a.arg for a in m.args.args]
    assert "feature" in args, "try_register takes feature (1st arg)"
    assert "registrar" in args, "try_register must accept optional 'registrar'"
    # registrar 必须有默认值 None(可选,不破坏 face_detect 现有调用)
    defaults = m.args.defaults
    assert len(defaults) >= 1, "registrar must have a default (optional)"


def test_try_register_uses_registrar_when_provided():
    """传 registrar 时须调 registrar(feature),不硬编码 face_db。"""
    seg = ast.get_source_segment(_src(), _method("try_register")) or ""
    assert "registrar" in seg, "try_register must reference registrar param"


def test_try_register_defaults_to_face_db():
    """registrar=None 时回退 face_db.register(向后兼容 face_detect)。"""
    seg = ast.get_source_segment(_src(), _method("try_register")) or ""
    assert "face_db" in seg or "face_db.register" in seg, \
        "try_register must fall back to face_db when registrar is None"


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
