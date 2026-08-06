# core/tag_db.py — 标签 ID 内存数据库(AprilTag / 二维码共用)
#
# 镜像 face_db 的内存-only + flush_to_disk 预留模式,但:
#   - 存标量 code_id(int=AprilTag.id 或 str=QR.payload),非 ulab ndarray
#   - 精确匹配(相等即命中),无相似度概念,score=1.0
#
# 纯 Python(无 MicroPython 依赖)→ 可在 host 端真单元测试。
#
# 持久化路径待定(同 face_db):flush_to_disk 当前 no-op,后续决定存哪。
# K230 坑#2:运行时 SD 写与 display flush 抢 DMA,故运行时只改内存,退出刷盘。

from core import db_store


class TagDB:
    """标签 ID 内存库。code_id 由调用方决定类型(int/str)。"""

    def __init__(self):
        self._features = {}        # {slot_id: code_id}
        self._next_slot = 1        # 轮转覆盖指针(1-25 循环)
        self._dirty = False
        self._clear_dirty = False

    def register(self, code_id):
        """注册 code_id 到槽位(轮转覆盖,同 face_db)。

        空槽优先(不推进 _next_slot);无空槽覆盖 _next_slot 并推进(1→2→3→4→1)。
        返回 slot_id(1-4)。纯内存,设 _dirty。
        """
        slot = None
        for i in range(1, 26):
            if i not in self._features:
                slot = i
                break
        if slot is None:
            slot = self._next_slot
            self._next_slot = self._next_slot % 25 + 1
        self._features[slot] = code_id
        self._dirty = True
        self._clear_dirty = False
        print("[TagDB] registered code_id=%r -> id%d (memory, dirty)" % (code_id, slot))
        return slot

    def match(self, code_id):
        """精确匹配 code_id。返回 (slot_id, 1.0) 或 (None, 0.0)。

        标签码精确相等即命中(无相似度),score=1.0 作上位机置信度。
        """
        for slot_id, cid in self._features.items():
            if cid == code_id:
                return slot_id, 1.0
        return None, 0.0

    def clear(self):
        """清内存,设 _clear_dirty(clear wins over _dirty)。"""
        self._features.clear()
        self._clear_dirty = True
        self._dirty = False
        self._next_slot = 1
        print("[TagDB] cleared (memory, clear_dirty)")

    def _serialize(self):
        return {"next_slot": self._next_slot,
                "slots": {str(k): v for k, v in self._features.items()}}

    def load_from_disk(self, path):
        """启动加载。db_store os.stat 预检查,文件不存在返回 None（避 ENOENT）。"""
        data = db_store.load_json(path)
        if data is None:
            return None
        try:
            self._next_slot = data.get("next_slot", 1)
            for slot_str, code_id in data.get("slots", {}).items():
                self._features[int(slot_str)] = code_id
        except Exception as e:
            print("[TagDB] load parse failed: %s" % e)
        return self._features

    def flush_to_disk(self, path):
        """注册即写 / 退出兜底。open('w') 不抛 ENOENT。"""
        db_store.save_json(path, self._serialize())
        self._dirty = False
        self._clear_dirty = False
        print("[TagDB] flushed %d code(s) to %s" % (len(self._features), path))

    @property
    def count(self):
        return len(self._features)
