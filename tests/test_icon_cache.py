# tests/test_icon_cache.py — icon_cache tag 图标接口契约(AST)
import ast
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
IC_PATH = os.path.join(ROOT, "core", "icon_cache.py")


def _src():
    with open(IC_PATH, "r", encoding="utf-8") as f:
        return f.read()


def _parse():
    return ast.parse(_src(), filename=IC_PATH)


def _method(name):
    tree = _parse()
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == "_IconCache":
            for n in node.body:
                if isinstance(n, ast.FunctionDef) and n.name == name:
                    return n
    raise AssertionError("Method %s missing" % name)


def test_preload_tag_icons_exists():
    """icon_cache 须有 preload_tag_icons() 预读 tag_detect 图标。"""
    try:
        _method("preload_tag_icons")
    except AssertionError:
        assert False, "_IconCache must define preload_tag_icons()"


def test_get_tag_icon_exists():
    """icon_cache 须有 get_tag_icon(name) 取 tag 图标。"""
    try:
        m = _method("get_tag_icon")
    except AssertionError:
        assert False, "_IconCache must define get_tag_icon(name)"
    args = [a.arg for a in m.args.args]
    assert "name" in args, "get_tag_icon must take name"


def test_preload_tag_icons_reads_tag_detect_icon_dir():
    """preload_tag_icons 必须读 tag_detect_icon/ 目录。"""
    seg = ast.get_source_segment(_src(), _method("preload_tag_icons")) or ""
    assert "tag_detect_icon" in seg, "preload_tag_icons must read tag_detect_icon/"


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
