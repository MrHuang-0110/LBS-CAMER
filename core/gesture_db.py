# core/gesture_db.py — 手势 ID 内存数据库
#
# 镜像 ObjectDB 的内存-only + flush_to_disk 模式:
#   - 存 int label_idx(0-3, hand_reco.kmodel 标签索引 gun/other/yeah/five)
#   - 精确匹配(label_idx 相等即命中),无相似度概念,score=1.0
#   - 同类不重复占槽:同一 label_idx 再注册返回原槽,不推进轮转指针
#   - registrar 签名(供 IdRegistry 复用)
#
# 纯 Python(无 MicroPython 依赖)→ 可在 host 端真单元测试。

from core import db_store

GESTURE_DB_PATH = "/sdcard/CamerAi/data/gesture_db.json"


class _GestureDB:
    """手势标签内存库。label_idx 为 int(0-3:gun/other/yeah/five)。"""

    def __init__(self):
        self._features = {}        # {slot_id: label_idx}
        self._next_slot = 1        # 轮转覆盖指针(1-25 循环)
        self._dirty = False
        self._clear_dirty = False

    def register(self, label_idx):
        """注册 label_idx 到槽位(轮转覆盖;同类不重复占槽)。

        已注册该 label_idx → 返回原槽(不推进指针)。
        否则空槽优先(不推进 _next_slot);无空槽覆盖 _next_slot 并推进(1→2→3→4→1)。
        返回 slot_id(1-4)。纯内存,设 _dirty。
        """
        for slot_id, lid in self._features.items():
            if lid == label_idx:
                return slot_id
        slot = None
        for i in range(1, 26):
            if i not in self._features:
                slot = i
                break
        if slot is None:
            slot = self._next_slot
            self._next_slot = self._next_slot % 25 + 1
        self._features[slot] = label_idx
        self._dirty = True
        self._clear_dirty = False
        print("[GestureDB] registered label_idx=%d -> id%d (memory, dirty)" % (label_idx, slot))
        return slot

    def match(self, label_idx):
        """精确匹配 label_idx。返回 (slot_id, 1.0) 或 (None, 0.0)。

        label_idx 精确相等即命中(无相似度),score=1.0 作上位机置信度。
        """
        for slot_id, lid in self._features.items():
            if lid == label_idx:
                return slot_id, 1.0
        return None, 0.0

    def clear(self):
        """清内存,设 _clear_dirty(clear wins over _dirty)。"""
        self._features.clear()
        self._clear_dirty = True
        self._dirty = False
        self._next_slot = 1
        print("[GestureDB] cleared (memory, clear_dirty)")

    def _serialize(self):
        return {"next_slot": self._next_slot,
                "slots": {str(k): v for k, v in self._features.items()}}

    def load_from_disk(self, path=GESTURE_DB_PATH):
        """启动加载。db_store os.stat 预检查,文件不存在返回 None(避 ENOENT)。"""
        data = db_store.load_json(path)
        if data is None:
            return None
        try:
            self._next_slot = data.get("next_slot", 1)
            for slot_str, label_idx in data.get("slots", {}).items():
                self._features[int(slot_str)] = label_idx
        except Exception as e:
            print("[GestureDB] load parse failed: %s" % e)
        return self._features

    def flush_to_disk(self, path=GESTURE_DB_PATH):
        """注册即写 / 退出兜底。open('w') 不抛 ENOENT。(镜像 ObjectDB,始终写盘)"""
        db_store.save_json(path, self._serialize())
        self._dirty = False
        self._clear_dirty = False
        print("[GestureDB] flushed %d gesture(s) to %s" % (len(self._features), path))

    def init_features(self, path=GESTURE_DB_PATH):
        """启动时加载已注册手势标签到内存(同 face_db.init_features)。"""
        self.load_from_disk(path)
        print("[GestureDB] init_features: loaded %d gesture(s)" % len(self._features))
        return self._features

    @property
    def count(self):
        return len(self._features)


# 全局单例
gesture_db = _GestureDB()

# 导出供宿主测试 import
GestureDB = _GestureDB
