# tests/test_host_api.py — host_api 协议接入契约（AST，板端模块不可导入）
import ast
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOST_API_PATH = os.path.join(ROOT, "comm", "host_api.py")


def _src():
    with open(HOST_API_PATH, "r", encoding="utf-8") as f:
        return f.read()


def _class_node(tree, name):
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == name:
            return node
    raise AssertionError("Class %s missing" % name)


def _method_node(cls_node, name):
    for n in cls_node.body:
        if isinstance(n, ast.FunctionDef) and n.name == name:
            return n
    raise AssertionError("Method %s missing" % name)


def test_category_type_mapping_covers_all_categories():
    """CATEGORY_TYPE 必须映射所有 reset 框架 category。"""
    src = _src()
    assert "CATEGORY_TYPE" in src, "must define CATEGORY_TYPE mapping"
    for cat, code in [("main_menu", "0x01"), ("settings", "0x01"),
                      ("camera", "0x02"), ("face_detect", "0x03"),
                      ("_template", "0x01")]:
        assert ('"%s"' % cat) in src or ("'%s'" % cat) in src, \
            "CATEGORY_TYPE must cover %s" % cat


def test_send_id_data_exists_with_slots_param():
    """send_id_data(msg_type, slots=None) — 泛化4组发送。"""
    tree = ast.parse(_src(), filename=HOST_API_PATH)
    cls = _class_node(tree, "HostAPI")
    m = _method_node(cls, "send_id_data")
    args = [a.arg for a in m.args.args]
    assert "msg_type" in args, "send_id_data must take msg_type"
    assert "slots" in args, "send_id_data must take slots"


def test_tick_exists_and_calls_poll_and_send():
    """tick(category_id, slots=None): poll_handshake + send_id_data。"""
    src = _src()
    tree = ast.parse(src, filename=HOST_API_PATH)
    cls = _class_node(tree, "HostAPI")
    m = _method_node(cls, "tick")
    args = [a.arg for a in m.args.args]
    assert "category_id" in args, "tick must take category_id"
    seg = ast.get_source_segment(src, m) or ""
    assert "poll_handshake" in seg, "tick must call poll_handshake"
    assert "send_id_data" in seg, "tick must call send_id_data"
    assert "CATEGORY_TYPE" in seg, "tick must look up CATEGORY_TYPE"


def test_send_face_data_delegates_to_send_id_data():
    """send_face_data 保留为薄封装（旧调试备份引用），委托 send_id_data。"""
    src = _src()
    tree = ast.parse(src, filename=HOST_API_PATH)
    cls = _class_node(tree, "HostAPI")
    m = _method_node(cls, "send_face_data")
    seg = ast.get_source_segment(src, m) or ""
    assert "send_id_data" in seg, "send_face_data must delegate to send_id_data"


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
