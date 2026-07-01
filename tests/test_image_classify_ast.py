# tests/test_image_classify_ast.py -- host-side AST 契约测试(image_classify)
import ast, os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP_RUNTIME_PATH = os.path.join(ROOT, "core", "app_runtime.py")
HOST_API_PATH = os.path.join(ROOT, "comm", "host_api.py")
APP_PATH = os.path.join(ROOT, "scripts", "image_classify", "app.py")


def _read(path):
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def test_image_classify_in_category_type_map():
    """CATEGORY_TYPE 必须包含 'image_classify': TYPE_IMAGE_CLASSIFY。

    缺它则 HostAPI.tick 回退 TYPE_MAIN_MENU(0x01),协议发错。
    """
    src = _read(HOST_API_PATH)
    assert '"image_classify":' in src
    after = src.split('"image_classify":')[1][:80]
    assert "TYPE_IMAGE_CLASSIFY" in after


def test_channels_for_image_classify_is_single_chn0():
    """_channels_for 的 image_classify 分支应为单 chn0(预览模式,无 AI 通道)。

    显式 pass 分支镜像 road_detect,表意:本轮不附加 chn2 AI 通道。
    """
    src = _read(APP_RUNTIME_PATH)
    start = src.find("def _channels_for(")
    assert start != -1, "must define _channels_for"
    body = src[start:start + 2200]
    assert "image_classify" in body, "_channels_for must handle image_classify"
    # image_classify 分支不应 append 任何 AI 通道(预览模式)
    after = body.split('"image_classify"')[1][:200]
    assert "append" not in after, "image_classify must NOT append an AI channel (preview-only)"


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
