# tests/test_face_detect_i18n.py — face_detect 双语修复契约
import ast
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP_PATH = os.path.join(ROOT, "scripts", "face_detect", "app.py")
ZH_PATH = os.path.join(ROOT, "resource", "i18n", "zh_CN.json")
EN_PATH = os.path.join(ROOT, "resource", "i18n", "en_US.json")


def _src():
    with open(APP_PATH, "r", encoding="utf-8") as f:
        return f.read()


def test_no_hardcoded_chinese_strings():
    """face_detect app 不得硬编码中文 UI 文本(须走 lang.t())。"""
    src = _src()
    bad = ["已注册", "清除", "保存"]
    for s in bad:
        assert ('"%s"' % s) not in src and ("'%s'" % s) not in src, \
            "face_detect must not hardcode Chinese '%s'; use lang.t()" % s


def test_i18n_registered_has_format_placeholder():
    """face_detect.registered 必须带 %d 格式化占位(注册数)。"""
    for path in (ZH_PATH, EN_PATH):
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        val = data["face_detect"]["registered"]
        assert "%d" in val, "face_detect.registered must contain %%d in %s" % path


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
