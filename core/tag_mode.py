# core/tag_mode.py — 标签识别功能记忆（.tag_fn 标记文件读写）
#
# tag_detect 每次进入时按此标记决定初始功能（AprilTag / 二维码），
# 卡片切换写回；主机远程快捷切换（mode 0x14=二维码 / 0x15=AprilTag）
# 也经此文件落盘，reset 后 tag_detect 按记忆启动。
#
# 与 main.py 的 .next_script 同目录同风格（/sdcard/CamerAi/），reset 后仍在。
#
# K230 坑#18：open() ENOENT 异常污染 FATFS → 读前必须 os.stat 预检查；
# 写用 open('w') 不抛 ENOENT。纯 Python（无 MicroPython 依赖）→ host 可真单测。

import os

TAG_FN_PATH = "/sdcard/CamerAi/.tag_fn"
DEFAULT_FN = "april"          # 默认功能：AprilTag（首次进入 / 标记缺失 / 非法）
VALID_FNS = ("april", "qr")   # 合法功能值


def read_tag_fn(path=TAG_FN_PATH):
    """读上次选择的标签功能。缺失/非法 → DEFAULT_FN（"april"）。

    os.stat 预检查避免 open(ENOENT) 污染 K230 FATFS 状态（坑#18）。
    值须为 VALID_FNS 之一（strip 后精确匹配），否则回退默认。
    """
    try:
        os.stat(path)
    except Exception:
        return DEFAULT_FN
    try:
        with open(path, "r") as f:
            val = f.read().strip()
        return val if val in VALID_FNS else DEFAULT_FN
    except Exception:
        return DEFAULT_FN


def write_tag_fn(fn, path=TAG_FN_PATH):
    """写标签功能标记。值非法则忽略（不产生文件）。open('w') 不抛 ENOENT。"""
    if fn not in VALID_FNS:
        print("[tag_mode] ignore invalid tag fn: %r" % fn)
        return
    try:
        with open(path, "w") as f:
            f.write(fn)
    except Exception as e:
        print("[tag_mode] write %s failed: %s" % (path, e))
