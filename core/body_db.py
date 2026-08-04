# core/body_db.py — 人体特征 ID 内存数据库
#
# 镜像 face_db 的内存-only + flush_to_disk 模式,但:
#   - 存特征向量(plain list,余弦匹配),非 label_idx 精确匹配
#   - database_search 纯 Python cosine(不硬依赖 ulab)→ host 端可真单测
#   - score = dot/2 + 0.5(余弦 [-1,1] → [0,1],同 face_db)
#   - 轮转覆盖 4 槽(空槽优先,满则覆盖 _next_slot 并推进)
#   - 无"同类不重复占槽"(人体是连续特征,每次注册进新槽或覆盖)
#
# 纯 Python(无 MicroPython 依赖)→ 可在 host 端真单元测试。

from core import db_store

BODY_DB_PATH = "/sdcard/CamerAi/data/body_db.json"

# 默认匹配阈值(score=cos/2+0.5 映射后)。0.82 ⇒ cos≥0.64,对齐 face_db 修复。
# ⚠️ 勿用 0.5:0.5 ⇒ cos≥0,CNN 自然图像特征几乎总正相关 → 所有人都命中同一 ID。
# 旧 0.75(cos≥0.5)对不同人过松 → 换人误识别(同 face_detect 修复前)。
BODY_MATCH_THRESHOLD = 0.82
# 多人注册时 best 与 second 的最小分数差。差更小 → 特征不可区分,判不确定。
BODY_MATCH_MARGIN = 0.06


def _to_list(feat):
    """特征归一为 plain list。板端 ulab ndarray → .tolist();host list 直通。"""
    if isinstance(feat, list):
        return feat
    try:
        return list(feat.tolist())   # ulab ndarray
    except Exception:
        try:
            return list(feat)
        except Exception:
            return feat


def database_search(feature, db_features, threshold=BODY_MATCH_THRESHOLD):
    """Cosine-match feature against db_features. Return (slot_id, score) or (None, 0.0).

    纯 Python cosine(host + board 通用)。db_features: {slot_id: list}。
    score = cos/2 + 0.5(余弦 [-1,1] → [0,1],同 face_db)。低于阈值 → (None, 0.0)。
    """
    if not db_features:
        return None, 0.0
    try:
        feat_list = _to_list(feature)
    except Exception:
        return None, 0.0
    feat_norm = sum(x * x for x in feat_list) ** 0.5
    if feat_norm == 0:
        return None, 0.0
    best_id = None
    best_score = 0.0
    second_score = 0.0
    for slot_id, db_feat in db_features.items():
        try:
            db_list = _to_list(db_feat)
        except Exception:
            continue
        db_n = sum(x * x for x in db_list) ** 0.5
        if db_n == 0:
            continue
        dot = sum(a * b for a, b in zip(feat_list, db_list))
        score = (dot / (feat_norm * db_n)) / 2 + 0.5
        if score > best_score:
            second_score = best_score
            best_score = score
            best_id = slot_id
        elif score > second_score:
            second_score = score
    if best_score < threshold:
        return None, 0.0
    # 次佳区分:注册多人且 best/second 差距过小 → 特征不可区分,拒绝
    if len(db_features) >= 2 and (best_score - second_score) < BODY_MATCH_MARGIN:
        return None, 0.0
    return best_id, best_score


class _BodyDB:
    """人体特征内存库。feature 为 plain list(余弦匹配)。"""

    def __init__(self):
        self._features = {}        # {slot_id: list}
        self._next_slot = 1        # 轮转覆盖指针(1-4 循环)
        self._dirty = False
        self._clear_dirty = False

    def register(self, feature):
        """注册 feature 到槽位(轮转覆盖)。返回 slot_id(1-4)。纯内存,设 _dirty。

        空槽优先(不推进 _next_slot);无空槽覆盖 _next_slot 并推进(1→2→3→4→1)。
        feature 转 plain list 存储(避 ulab ndarray 不可 JSON 序列化)。
        """
        feat = _to_list(feature)
        slot = None
        for i in range(1, 5):
            if i not in self._features:
                slot = i
                break
        if slot is None:
            slot = self._next_slot
            self._next_slot = self._next_slot % 4 + 1
        self._features[slot] = feat
        self._dirty = True
        self._clear_dirty = False
        print("[BodyDB] registered feature(%d-dim) -> id%d (memory, dirty)" % (len(feat), slot))
        return slot

    def get_features(self):
        """返回特征字典的引用(运行时匹配用)。"""
        return self._features

    def clear(self):
        """清内存,设 _clear_dirty(clear wins over _dirty)。"""
        self._features.clear()
        self._clear_dirty = True
        self._dirty = False
        self._next_slot = 1
        print("[BodyDB] cleared (memory, clear_dirty)")

    def _serialize(self):
        return {"next_slot": self._next_slot,
                "slots": {str(k): v for k, v in self._features.items()}}

    def load_from_disk(self, path=BODY_DB_PATH):
        """启动加载。db_store os.stat 预检查,文件不存在返回 None(避 ENOENT)。"""
        data = db_store.load_json(path)
        if data is None:
            return None
        try:
            self._next_slot = data.get("next_slot", 1)
            for slot_str, feat_list in data.get("slots", {}).items():
                self._features[int(slot_str)] = list(feat_list)
        except Exception as e:
            print("[BodyDB] load parse failed: %s" % e)
        return self._features

    def flush_to_disk(self, path=BODY_DB_PATH):
        """注册即写 / 退出兜底。open('w') 不抛 ENOENT。(镜像 face_db,始终写盘)"""
        db_store.save_json(path, self._serialize())
        self._dirty = False
        self._clear_dirty = False
        print("[BodyDB] flushed %d body(s) to %s" % (len(self._features), path))

    def init_features(self, path=BODY_DB_PATH):
        """启动时加载已注册人体特征到内存(同 face_db.init_features)。"""
        self.load_from_disk(path)
        print("[BodyDB] init_features: loaded %d body(s)" % len(self._features))
        return self._features

    @property
    def count(self):
        return len(self._features)


# 全局单例
body_db = _BodyDB()

# 导出供宿主测试 import
BodyDB = _BodyDB
