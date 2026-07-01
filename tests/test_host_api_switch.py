# tests/test_host_api_switch.py -- 主机→摄像头切换命令帧解析(host 侧 AST/字节契约)
import ast, os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOST_API_PATH = os.path.join(ROOT, "comm", "host_api.py")


def _src():
    with open(HOST_API_PATH, "r", encoding="utf-8") as f:
        return f.read()


def _load_host_api_ns():
    """exec host_api.py 在 host 侧的命名空间,注入 fake machine/time 避开 MicroPython 依赖。

    `from machine import UART` 走 import 系统(查 sys.modules),故须注册进 sys.modules
    而非仅放进 exec 命名空间。
    """
    import types, sys
    machine = types.ModuleType("machine")
    machine.UART = type("UART", (), {})
    sys.modules["machine"] = machine
    time = types.ModuleType("time")
    time.ticks_ms = lambda: 0
    time.ticks_diff = lambda a, b: 0
    sys.modules["time"] = time
    ns = {"__name__": "host_api_under_test"}
    exec(compile(ast.parse(_src()), HOST_API_PATH, "exec"), ns)
    return ns


def _make_switch_frame(mode):
    """构造主机切换命令帧 5A 97 A7 01 FF <mode> <chk> A5。"""
    head = [0x5A, 0x97, 0xA7, 0x01, 0xFF, mode]
    chk = sum(head) & 0xFF
    return bytearray(head + [chk, 0xA5])


def test_mode_to_category_map_values():
    """MODE_TO_CATEGORY 反向映射:0x01→None(回菜单), 各脚本 mode→category。"""
    src = _src()
    assert "MODE_TO_CATEGORY" in src, "must define MODE_TO_CATEGORY"
    tree = ast.parse(src, filename=HOST_API_PATH)
    mtc = None
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name) and t.id == "MODE_TO_CATEGORY":
                    mtc = node.value
    assert isinstance(mtc, ast.Dict), "MODE_TO_CATEGORY must be a dict literal"
    mapping = {}
    for k, v in zip(mtc.keys, mtc.values):
        assert isinstance(k, ast.Constant) and isinstance(k.value, int), \
            "MODE_TO_CATEGORY keys must be int literals"
        if isinstance(v, ast.Constant):
            mapping[k.value] = v.value  # None 或 str
        elif isinstance(v, ast.Name) and v.id == "None":
            mapping[k.value] = None
    assert mapping.get(0x01) is None, "0x01 must map to None (main menu)"
    assert mapping.get(0x02) == "camera"
    assert mapping.get(0x03) == "face_detect"
    assert mapping.get(0x04) == "tag_detect"
    assert mapping.get(0x05) == "object_detect"
    assert mapping.get(0x06) == "color_detect"
    assert mapping.get(0x07) == "road_detect"
    assert mapping.get(0x10) == "gesture_detect"
    assert mapping.get(0x11) == "body_detect"
    assert mapping.get(0x12) == "object_classify"
    assert mapping.get(0x13) == "image_classify"


def test_type_mode_switch_constant():
    """TYPE_MODE_SWITCH 必须 == 0xFF(命令帧 index 字段)。"""
    import re
    src = _src()
    assert "TYPE_MODE_SWITCH" in src
    # 容忍对齐空格:TYPE_MODE_SWITCH <空格*> = 0xFF
    assert re.search(r"TYPE_MODE_SWITCH\s*=\s*0xFF\b", src), \
        "TYPE_MODE_SWITCH must be 0xFF"


def test_parse_switch_frame_valid():
    """_parse_switch_frame 对正确帧返回 mode。"""
    ns = _load_host_api_ns()
    # _parse_switch_frame 是 HostAPI 的方法;取类里的 staticmethod 或实例方法
    HostAPI = ns["HostAPI"]
    # 若是实例方法/staticmethod,通过类访问
    parse = HostAPI._parse_switch_frame
    frame = _make_switch_frame(0x13)
    result = parse(frame, 0)
    assert result is not None, "valid frame must parse"
    mode, nxt = result
    assert mode == 0x13
    assert nxt == 8


def test_parse_switch_frame_bad_checksum():
    """校验和错 → None。"""
    ns = _load_host_api_ns()
    parse = ns["HostAPI"]._parse_switch_frame
    frame = bytearray([0x5A, 0x97, 0xA7, 0x01, 0xFF, 0x13, 0x00, 0xA5])  # chk 故意错
    assert parse(frame, 0) is None


def test_parse_switch_frame_bad_tail():
    """尾非 A5 → None。"""
    ns = _load_host_api_ns()
    parse = ns["HostAPI"]._parse_switch_frame
    head = [0x5A, 0x97, 0xA7, 0x01, 0xFF, 0x13]
    chk = sum(head) & 0xFF
    frame = bytearray(head + [chk, 0x00])  # 尾错
    assert parse(frame, 0) is None


def test_parse_switch_frame_bad_prefix():
    """前缀错 → None。"""
    ns = _load_host_api_ns()
    parse = ns["HostAPI"]._parse_switch_frame
    head = [0x5A, 0x97, 0xA6, 0x01, 0xFF, 0x13]  # dst 错(0xA6)
    chk = sum(head) & 0xFF
    frame = bytearray(head + [chk, 0xA5])
    assert parse(frame, 0) is None


def test_runner():
    import sys
    mod = sys.modules[__name__]
    fails = 0
    for n in sorted(dir(mod)):
        if n.startswith("test_") and callable(getattr(mod, n)) and n != "test_runner":
            try:
                getattr(mod, n)()
                print("  PASS %s" % n)
            except Exception as e:
                print("  FAIL %s: %s" % (n, e))
                fails += 1
    assert fails == 0, "%d tests failed" % fails


if __name__ == "__main__":
    test_runner()
