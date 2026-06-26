# tests/test_i18n_tag.py — tag_detect i18n 键契约
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ZH = os.path.join(ROOT, "resource", "i18n", "zh_CN.json")
EN = os.path.join(ROOT, "resource", "i18n", "en_US.json")


def _load(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def test_tag_detect_section_exists_both_langs():
    for path in (ZH, EN):
        data = _load(path)
        assert "tag_detect" in data, "missing tag_detect section in %s" % path


def test_tag_detect_keys_present():
    required = ["april_tag", "qr_code", "registered"]
    for path in (ZH, EN):
        td = _load(path)["tag_detect"]
        for k in required:
            assert k in td, "missing tag_detect.%s in %s" % (k, path)


def test_tag_detect_registered_has_placeholder():
    for path in (ZH, EN):
        val = _load(path)["tag_detect"]["registered"]
        assert "%d" in val, "tag_detect.registered must contain %%d in %s" % path


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
