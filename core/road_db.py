# core/road_db.py — 道路识别 ID 内存数据库
#
# 镜像 ColorDB 的内存 + flush_to_disk 模式,但:
#   - 只存 1 个配置(单片 ID1)
#   - save() 覆盖写入(不轮转)
#   - 多存 samples 数组(左表 3 槽采色历史,供重启还原 UI)
#
# 纯 Python(无 MicroPython 依赖)→ 可在 host 端真单元测试。

from core import db_store


class RoadDB:
    """道路配置内存库。单槽存 6 阈值 + 中心 LAB + RGB + 采色历史。"""

    def __init__(self):
        self._entry = None         # dict: {'threshold':th,'lab':lab,'rgb':rgb,'samples':samples}
        self._dirty = False
        self._clear_dirty = False

    @property
    def saved(self):
        """是否已保存(有配置)。"""
        return self._entry is not None

    def save(self, threshold, lab, rgb, samples):
        """覆盖保存配置到 slot 1。返回 1。设 _dirty。"""
        self._entry = {
            'threshold': threshold,
            'lab': lab,
            'rgb': rgb,
            'samples': samples,
        }
        self._dirty = True
        self._clear_dirty = False
        print("[RoadDB] saved lab=%r -> ID1 (memory, dirty)" % (lab,))
        return 1

    def get(self):
        """取当前配置 dict 或 None。"""
        return self._entry

    def clear(self):
        """清内存,设 _clear_dirty。"""
        self._entry = None
        self._clear_dirty = True
        self._dirty = False
        print("[RoadDB] cleared (memory, clear_dirty)")

    def load_from_disk(self, path):
        """从磁盘加载配置。ENOENT 安全(通过 db_store)。无配置返回 None。"""
        data = db_store.load_json(path)
        if data is None:
            return None
        try:
            self._entry = {
                'threshold': tuple(data['threshold']),
                'lab': tuple(data['lab']),
                'rgb': int(data['rgb']),
                'samples': [(tuple(s[0]), int(s[1])) for s in data['samples']],
            }
            self._dirty = False
            self._clear_dirty = False
            print("[RoadDB] loaded from disk")
        except Exception as e:
            print("[RoadDB] load corrupt: %s" % e)
            self._entry = None
        return self._entry

    def flush_to_disk(self, path):
        """持久化到磁盘。dirty=True 写入;clear_dirty=True 写空文件(清磁盘);无变化跳过。"""
        if self._clear_dirty:
            db_store.save_json(path, None)
            self._clear_dirty = False
            print("[RoadDB] flushed clear to disk")
        elif self._dirty and self._entry is not None:
            data = {
                'threshold': list(self._entry['threshold']),
                'lab': list(self._entry['lab']),
                'rgb': self._entry['rgb'],
                'samples': [(list(s[0]), s[1]) for s in self._entry['samples']],
            }
            db_store.save_json(path, data)
            self._dirty = False
            print("[RoadDB] flushed to disk")
