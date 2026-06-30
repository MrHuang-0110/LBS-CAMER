# core/db_store.py — ENOENT-safe JSON persistence for ID databases
#
# 坑#18 变体红线：open() 对不存在文件抛 ENOENT 会污染 K230 FATFS/DMA 状态，
# 导致后续 Display/MediaManager/LVGL 在 GC 后卡死。本模块所有读盘走 os.stat
# 预检查，文件不存在直接返回 None，绝不触发 open(ENOENT)。
#
# 写盘 open(path,'w') 文件不存在会创建（不抛 ENOENT），但目录不存在会抛，
# 故 save_json 先 ensure_data_dir。
#
# 纯 Python（os/json），PC 可直接单元测试。

import os
import json

DATA_DIR = "/sdcard/CamerAi/data"


def ensure_data_dir(path=None):
    """确保 path 所在目录存在。path=None 时确保 DATA_DIR。MicroPython 无 os.makedirs。"""
    d = DATA_DIR if path is None else os.path.dirname(path)
    if not d:
        return
    try:
        os.stat(d)
    except Exception:
        try:
            os.mkdir(d)
        except Exception as e:
            print("[db_store] mkdir %s failed: %s" % (d, e))


def load_json(path):
    """读 JSON。os.stat 预检查，文件不存在返回 None（不 open，避 ENOENT）。
    损坏/解析失败返回 None，不抛。"""
    try:
        os.stat(path)
    except Exception:
        return None
    try:
        with open(path, 'r') as f:
            return json.load(f)
    except Exception as e:
        print("[db_store] load %s failed: %s" % (path, e))
        return None


def save_json(path, obj):
    """写 JSON。先 ensure 目录，再 open('w')（不存在会创建，不抛 ENOENT）。
    失败打印不抛（注册数据丢失可接受，卡死不可接受）。"""
    ensure_data_dir(path)
    try:
        with open(path, 'w') as f:
            json.dump(obj, f)
    except Exception as e:
        print("[db_store] save %s failed: %s" % (path, e))
