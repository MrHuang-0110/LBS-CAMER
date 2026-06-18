# tests/test_font_coverage.py — host-side guard: the font build must cover
# every non-ASCII char the camera app renders.
# Run with either:
#   python tests/test_font_coverage.py      (standalone, no deps)
#   python -m pytest tests/                 (if pytest installed)
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))

import build_fonts  # noqa: E402

# Chars the camera UI renders outside i18n JSON (timer bullets etc.).
# Emoji are intentionally NOT here — they are not in the CJK subset and
# the app must use renderable substitutes instead (see plan Task 4).
REQUIRED_EXTRA = set("●○")


def _built_charset():
    chars = set()
    i18n_dir = os.path.join(ROOT, "resource", "i18n")
    for fn in os.listdir(i18n_dir):
        if fn.endswith(".json"):
            chars |= build_fonts.collect_chars_from_json(
                os.path.join(i18n_dir, fn))
    chars |= build_fonts.collect_chars_from_file(
        os.path.join(ROOT, "tools", "ui_chars.txt"))
    return chars


def test_font_build_covers_camera_i18n():
    built = _built_charset()
    for ch in "图库暂无照片录制中":
        assert ch in built, f"camera glyph {ch!r} missing from font build set"


def test_font_build_covers_ui_bullets():
    built = _built_charset()
    missing = REQUIRED_EXTRA - built
    assert not missing, f"bullet glyphs missing from font build set: {missing}"


if __name__ == "__main__":
    failures = 0
    for name in sorted(n for n in dir() if n.startswith("test_")):
        try:
            globals()[name]()
            print(f"PASS {name}")
        except AssertionError as e:
            failures += 1
            print(f"FAIL {name}: {e}")
    print(f"\n{'ALL PASS' if failures == 0 else str(failures) + ' FAILED'}")
    sys.exit(1 if failures else 0)
