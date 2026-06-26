# core/color_db.py — 颜色 ID 内存数据库
#
# 镜像 tag_db 的内存-only + flush_to_disk 预留模式,但:
#   - 存 6 阈值 tuple (Lmin,Lmax,Amin,Amax,Bmin,Bmax) + 中心 LAB + RGB
#   - 同阈值(完全相同)不重复占槽,返回已有槽
#   - 精确匹配(阈值完全相等即命中),score=1.0
#
# 纯 Python(无 MicroPython 依赖)→ 可在 host 端真单元测试。
#
# 持久化预留:flush_to_disk 当前 no-op。K230 坑#2:运行时 SD 写与 display
# flush 抢 DMA,故运行时只改内存,退出刷盘。


class ColorDB:
    """颜色 ID 内存库。每槽存 6 阈值 + 中心 LAB + RGB。

    threshold 形如 ((Lmin,Lmax,Amin,Amax,Bmin,Bmax),(L,A,B))。
    """

    def __init__(self):
        self._features = {}        # {slot_id: {'threshold':th, 'lab':(L,A,B), 'rgb':int}}
        self._next_slot = 1        # 轮转覆盖指针(1-4 循环)
        self._dirty = False
        self._clear_dirty = False

    def register(self, threshold, rgb=0):
        """注册颜色到槽位(轮转覆盖,空槽优先)。

        同阈值(完全相同)不重复占槽,返回已有 slot。
        返回 slot_id(1-4)。纯内存,设 _dirty。
        """
        # 同阈值去重:已存在则返回已有槽
        for slot_id, entry in self._features.items():
            if entry['threshold'] == threshold:
                return slot_id
        slot = None
        for i in range(1, 5):
            if i not in self._features:
                slot = i
                break
        if slot is None:
            slot = self._next_slot
            self._next_slot = self._next_slot % 4 + 1
        th, lab = threshold
        self._features[slot] = {'threshold': threshold, 'lab': lab, 'rgb': rgb}
        self._dirty = True
        self._clear_dirty = False
        print("[ColorDB] registered lab=%r -> id%d (memory, dirty)" % (lab, slot))
        return slot

    def match(self, threshold):
        """精确匹配 threshold(6 阈值完全相等)。返回 (slot_id, 1.0) 或 (None, 0.0)。"""
        for slot_id, entry in self._features.items():
            if entry['threshold'] == threshold:
                return slot_id, 1.0
        return None, 0.0

    def clear(self):
        """清内存,设 _clear_dirty。"""
        self._features.clear()
        self._clear_dirty = True
        self._dirty = False
        self._next_slot = 1
        print("[ColorDB] cleared (memory, clear_dirty)")

    def get_slot(self, slot_id):
        """取某槽 entry(threshold/lab/rgb),不存在返回 None。"""
        return self._features.get(slot_id)

    def iter_slots(self):
        """遍历所有槽 entry(供每帧检测用)。"""
        return self._features.values()

    def flush_to_disk(self):
        """退出时刷盘(预留)。当前 no-op,仅复位 dirty 标志。"""
        if self._clear_dirty:
            print("[ColorDB] exit: clear intent recorded (persistence disabled)")
        elif self._dirty:
            print("[ColorDB] exit: %d color(s) pending (persistence disabled)"
                  % len(self._features))
        self._clear_dirty = False
        self._dirty = False

    @property
    def count(self):
        return len(self._features)
