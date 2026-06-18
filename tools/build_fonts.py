#!/usr/bin/env python3
# tools/build_fonts.py — 从 i18n 语言包提取用字，生成 lv_font_conv 命令
#
# 用法：
#   1. python tools/build_fonts.py          # 打印 lv_font_conv 命令
#   2. python tools/build_fonts.py --run    # 直接执行生成 .bin 文件
#   3. python tools/build_fonts.py --dry    # 仅打印命令，不执行（默认）
#
# 前提：
#   - 安装 Node.js
#   - npm install -g lv_font_conv
#   - 下载思源黑体 Regular OTF 放到 tools/fonts_src/SourceHanSansSC-Regular.otf
#     下载地址：https://github.com/adobe-fonts/source-han-sans/raw/release/OTF/SimplifiedChinese/SourceHanSansSC-Regular.otf
#
# 输出：
#   resource/font/font_title_20.bin    (20px, 用于标题)
#   resource/font/font_body_18.bin     (18px, 用于正文)
#   resource/font/font_caption_14.bin  (14px, 用于辅助文字)

import json
import os
import re
import shutil
import subprocess
import sys

# ── 路径 ────────────────────────────────────────────────
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
I18N_DIR = os.path.join(PROJECT_ROOT, "resource", "i18n")
FONT_OUT = os.path.join(PROJECT_ROOT, "resource", "font")
UI_CHARS_FILE = os.path.join(PROJECT_ROOT, "tools", "ui_chars.txt")

# 源字体候选列表（按优先级自动探测）
_FONT_CANDIDATES = [
    # 1. 项目内 tools/fonts_src/
    os.path.join(PROJECT_ROOT, "tools", "fonts_src",
                 "SourceHanSansSC-Regular.otf"),
    os.path.join(PROJECT_ROOT, "tools", "fonts_src",
                 "SourceHanSansSC-Normal.otf"),
    # 2. Windows 系统字体
    "C:/Windows/Fonts/simhei.ttf",       # 黑体
    "C:/Windows/Fonts/msyh.ttf",         # 微软雅黑
    "C:/Windows/Fonts/Deng.ttf",         # 等线
    "C:/Windows/Fonts/NotoSansSC-VF.ttf",# Noto Sans SC
    # 3. macOS 系统字体
    "/System/Library/Fonts/PingFang.ttc",
    "/System/Library/Fonts/Hiragino Sans GB.ttc",
    # 4. Linux 系统字体
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
]

def _detect_font() -> str:
    """自动探测可用的中文字体，返回第一个存在者。"""
    for path in _FONT_CANDIDATES:
        if os.path.exists(path):
            return path
    return None

FONT_SRC = _detect_font()  # 启动时自动选择，也可手动覆盖

# ── 字号配置 ────────────────────────────────────────────
# (输出文件名, 字号, 用途)
FONT_SPECS = [
    ("font_title_50.bin",   50, "标题"),   # 卡片名称，约等于图标高度(100)的一半
    ("font_body_18.bin",    18, "正文"),
    ("font_caption_14.bin", 14, "辅助文字"),
]

BPP = 4  # 4 bits per pixel，平衡质量和体积

# ── 必须包含的字符范围 ──────────────────────────────────
ASCII_RANGE = "0x20-0x7F"  # 英文、数字、标点


def collect_chars_from_json(filepath: str) -> set:
    """递归提取 JSON 中所有字符串值的字符。"""
    chars = set()

    def walk(obj):
        if isinstance(obj, str):
            for ch in obj:
                chars.add(ch)
        elif isinstance(obj, dict):
            for v in obj.values():
                walk(v)
        elif isinstance(obj, list):
            for item in obj:
                walk(item)

    with open(filepath, "r", encoding="utf-8") as f:
        data = json.load(f)
    walk(data)
    return chars


def collect_chars_from_file(filepath: str) -> set:
    """从纯文本文件提取字符（每行一个词或自由文本）。"""
    chars = set()
    if not os.path.exists(filepath):
        return chars
    with open(filepath, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            for ch in line:
                chars.add(ch)
    return chars


def cjk_ranges(chars: set) -> list:
    """将 Unicode 码点集合压缩为范围字符串列表。

    只保留非 ASCII 字符（码点 >= 0x80），ASCII 统一用 ASCII_RANGE。
    """
    codepoints = sorted([ord(c) for c in chars if ord(c) >= 0x80])
    if not codepoints:
        return []

    ranges = []
    start = end = codepoints[0]
    for cp in codepoints[1:]:
        if cp == end + 1:
            end = cp
        else:
            ranges.append((start, end))
            start = end = cp
    ranges.append((start, end))

    result = []
    for s, e in ranges:
        if s == e:
            result.append(f"0x{s:04X}")
        else:
            result.append(f"0x{s:04X}-0x{e:04X}")
    return result


def _resolve_lv_font_conv() -> str:
    """解析 lv_font_conv 可执行文件的真实路径。

    Windows 上 lv_font_conv 是 Node 的 .cmd 垫片，subprocess.run([...])
    在不开 shell 时无法用裸名经 CreateProcess 执行（WinError 2）。
    用 shutil.which 解析出 .cmd/.exe 全路径再交给 subprocess。
    """
    found = shutil.which("lv_font_conv")
    return found if found else "lv_font_conv"


def build_command(size: int, ranges: list, output_path: str) -> list:
    """构建 lv_font_conv 命令行。

    ⚠️ 必须加 --no-compress：lv_font_conv 默认对字形位图启用压缩，
    但 K230 固件的 LVGL 未开启 LV_USE_FONT_COMPRESSED，无法解压，
    字体加载成功(返回非 None)但文字不渲染。详见项目进度变更日志。
    """
    range_str = ",".join([ASCII_RANGE] + ranges)
    cmd = [
        _resolve_lv_font_conv(),
        "--no-compress",
        "--font", FONT_SRC,
        "-r", range_str,
        "--size", str(size),
        "--format", "bin",
        "--bpp", str(BPP),
        "-o", output_path,
    ]
    return cmd


def main():
    dry_run = "--run" not in sys.argv

    # 1. 检查源字体
    if not FONT_SRC or not os.path.exists(FONT_SRC):
        print(f"[ERROR] 未找到可用的中文字体。")
        print()
        print("请将任意中文字体文件（.ttf / .otf）放入以下任一位置：")
        print(f"  {os.path.join(PROJECT_ROOT, 'tools', 'fonts_src')}/")
        print("  或 C:/Windows/Fonts/")
        print()
        print("已尝试的候选：")
        for p in _FONT_CANDIDATES:
            mark = "✓" if os.path.exists(p) else "✗"
            print(f"  {mark} {p}")
        sys.exit(1)

    # 2. 收集字符
    all_chars = set()

    # 2a. i18n JSON
    if os.path.isdir(I18N_DIR):
        for fname in sorted(os.listdir(I18N_DIR)):
            if fname.endswith(".json"):
                fpath = os.path.join(I18N_DIR, fname)
                chars = collect_chars_from_json(fpath)
                all_chars.update(chars)
                print(f"[i18n] {fname}: +{len(chars)} chars")

    # 2b. UI 常用字表
    ui_chars = collect_chars_from_file(UI_CHARS_FILE)
    all_chars.update(ui_chars)
    if ui_chars:
        print(f"[ui]   ui_chars.txt: +{len(ui_chars)} chars")

    non_ascii = {c for c in all_chars if ord(c) >= 0x80}
    print(f"[total] {len(all_chars)} unique chars "
          f"(ASCII: {len(all_chars) - len(non_ascii)}, "
          f"non-ASCII: {len(non_ascii)})")

    # 3. 生成范围
    ranges = cjk_ranges(all_chars)
    if not ranges:
        print("[WARN] 没有非 ASCII 字符需要生成。")
        return

    range_str = ",".join([ASCII_RANGE] + ranges)
    print(f"[range] {range_str}")
    print()

    # 4. 生成命令 / 执行
    os.makedirs(FONT_OUT, exist_ok=True)

    for fname, size, desc in FONT_SPECS:
        out_path = os.path.join(FONT_OUT, fname)
        cmd = build_command(size, ranges, out_path)

        print(f"[{desc}] {size}px → {fname}")
        print(f"  {' '.join(cmd)}")
        print()

        if not dry_run:
            print(f"  → generating...")
            result = subprocess.run(cmd, capture_output=True, text=True)
            if result.returncode != 0:
                print(f"  [FAIL] {result.stderr}")
            else:
                fsize = os.path.getsize(out_path) if os.path.exists(out_path) else 0
                print(f"  [OK] {fsize:,} bytes")
            print()

    # 5. 输出板端部署提示
    print("=" * 60)
    if dry_run:
        print("以上为预览命令。确认无误后执行：")
        print("  python tools/build_fonts.py --run")
    else:
        print("字体生成完毕。将 resource/font/font_*.bin 拷贝到板端：")
        print("  /sdcard/CamerAi/resource/font/")
        print("然后更新 core/font_manager.py 的 PHASE2_FONT_MAP。")
    print("=" * 60)


if __name__ == "__main__":
    main()
