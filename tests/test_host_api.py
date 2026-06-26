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
                      ("tag_detect", "0x04"),
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


def test_send_id_data_uses_big_endian_for_coords():
    """x/y/w/h 必须**大端**打包(高字节在前),对齐主机 _camer_cam_data 解析。

    主机: (data[off+1]<<8)|data[off+2] → 大端,高字节在 off+1。
    旧代码小端(低字节在 off+1) → x=320(0x0140) 发 40 01,主机解析成 0x4001=16385
    → "坐标值很大"。改大端:发 01 40,主机解析 0x0140=320。
    """
    src = _src()
    tree = ast.parse(src, filename=HOST_API_PATH)
    cls = _class_node(tree, "HostAPI")
    m = _method_node(cls, "send_id_data")
    seg = ast.get_source_segment(src, m) or ""
    # 大端:高字节 (x>>8) 在前(off+1),低字节 x 在后(off+2)
    assert "(x >> 8) & 0xFF" in seg and "buf[off + 1]" in seg, \
        "x must pack big-endian: high byte (x>>8) at off+1, low byte at off+2"
    # 不得小端:低字节 x 在 off+1
    assert "buf[off + 1] = x & 0xFF" not in seg, \
        "must NOT pack little-endian (x&0xFF at off+1); host parses big-endian"


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


def test_send_frame_length_excludes_type_byte():
    """length 字段必须只算 payload,不含 type(对齐主机 dataAgreeAnalys 解析)。

    主机:data[3]=length, data[4]=index(type), memcpy(data+5, data[3])。
    即 length = payload 字节数, type 在 data[4] 不计入 length。
    旧代码 length = 1(type) + len(payload) 多算1字节 → 帧尾错位 → 主机
    AGREE_MEN_ERROR 丢弃 → 设备识别失败。
    """
    src = _src()
    tree = ast.parse(src, filename=HOST_API_PATH)
    cls = _class_node(tree, "HostAPI")
    m = _method_node(cls, "send_frame")
    seg = ast.get_source_segment(src, m) or ""
    # length 必须等于 len(payload),不得 +1 含 type
    assert "len(payload)" in seg, "length must be len(payload)"
    assert "len(inner)" not in seg, \
        "length must NOT be len(inner) (inner includes type byte)"


def test_handshake_reply_payload_matches_host_expectation():
    """握手应答 payload 必须是 'Play Aplication'(15字符,对齐主机硬匹配)。

    主机按文档拼写错误 'Play Aplication' 匹配(用户确认)。代码原用正确拼写
    'Play Application'(17字符) → 主机不认 → 握手失败。
    """
    src = _src()
    assert b"Play Aplication" in src.encode() or "Play Aplication" in src, \
        "HANDSHAKE_REPLY_PAYLOAD must be 'Play Aplication' (host expects this exact spelling)"


def test_poll_handshake_matches_full_request_frame_not_substring():
    """poll_handshake 必须按完整握手请求帧匹配,不得用子串 magic 触发应答。

    根因:主机超时检测的是"有没有收到数据帧"。摄像头持续发数据帧则主机不重发
    握手;仅进程重启/断连间隙主机才重发完整握手请求帧。摄像头须按完整帧
    (0x5A 对齐 + 完整请求帧字节)匹配才应答,避免缓冲区残留字节误触发重复应答。
    参考 DurUI._usart1_try_handshake:0x5A 对齐 + 完整 START_FRAME 匹配。
    """
    src = _src()
    tree = ast.parse(src, filename=HOST_API_PATH)
    cls = _class_node(tree, "HostAPI")
    m = _method_node(cls, "poll_handshake")
    seg = ast.get_source_segment(src, m) or ""
    # 必须有完整请求帧常量(或其字节序列)的匹配,不是子串 'Please Link'
    assert "HANDSHAKE_REQUEST_FRAME" in src or "REQUEST_FRAME" in src, \
        "must define a full handshake request frame constant (HANDSHAKE_REQUEST_FRAME)"
    assert "in raw" not in seg and "in self._rx_buf" not in seg, \
        "poll_handshake must not use substring 'in raw' matching; match full frame on 0x5A boundary"


def test_poll_handshake_does_not_short_circuit_on_connected():
    """poll_handshake 不得在入口用 _connected 短路。

    主机超时(没收到数据帧)后会重发握手请求,摄像头新进程/断连后必须能重新
    应答。入口短路会阻止重新应答 → 切换脚本后连不上。_connected 仅作状态
    标记,不阻断握手检测。
    """
    src = _src()
    tree = ast.parse(src, filename=HOST_API_PATH)
    cls = _class_node(tree, "HostAPI")
    m = _method_node(cls, "poll_handshake")
    seg = ast.get_source_segment(src, m) or ""
    # 不得有 "if self._connected: ... return" 入口短路(except 内是赋值 self._connected=False,不是 if 判断)
    assert "if self._connected" not in seg, \
        "poll_handshake must NOT short-circuit with 'if self._connected' (host re-handshakes on timeout after data-frame gap)"


def test_tick_has_handshake_cooldown_before_sending_data():
    """tick 必须有握手应答后 100ms 起步延迟:静默期内跳过 send_id_data。

    协议:握手应答后间隔 100ms 才开始上传数据帧。tick 用时间戳检查距上次
    握手应答不足 100ms 则跳过发送(只 poll_handshake),100ms 后恢复每帧发。
    """
    src = _src()
    tree = ast.parse(src, filename=HOST_API_PATH)
    cls = _class_node(tree, "HostAPI")
    m = _method_node(cls, "tick")
    seg = ast.get_source_segment(src, m) or ""
    assert "100" in seg or "HANDSHAKE_COOLDOWN" in src or "cooldown" in seg.lower(), \
        "tick must enforce ~100ms cooldown after handshake reply before sending data"
    assert "ticks_ms" in seg or "ticks_diff" in seg, \
        "tick must use time.ticks_ms/ticks_diff for the cooldown check"


def test_poll_handshake_rfind_does_not_pass_int():
    """poll_handshake 的 rfind 不得直接传 int(FRAME_HEAD)。

    MicroPython bytearray.rfind 不接受 int 参数,传 int 会崩
    "can't convert 'int' object to str implicitly"(UART 收到数据即触发,
    run_menu 第一帧卡死)。必须传 bytes,如 buf.rfind(bytes([self.FRAME_HEAD]))。
    CPython 接受 int,故 host AST 测试须显式守护。
    """
    src = _src()
    tree = ast.parse(src, filename=HOST_API_PATH)
    cls = _class_node(tree, "HostAPI")
    m = _method_node(cls, "poll_handshake")
    seg = ast.get_source_segment(src, m) or ""
    assert "rfind(self.FRAME_HEAD)" not in seg, \
        "poll_handshake must not rfind(self.FRAME_HEAD) (int crashes MicroPython); use bytes([self.FRAME_HEAD])"


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
