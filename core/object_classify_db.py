# core/object_classify_db.py — 物体分类特征 ID 内存数据库
#
# 镜像 body_db 的内存-only + flush_to_disk 模式(逐字复刻,仅改名 + 暴露
# cosine_score/to_feature_list 供 object_classify_lock 复用):
#   - 存特征向量(plain list,余弦匹配),非 label_idx 精确匹配
#   - database_search 纯 Python cosine(不硬依赖 ulab)→ host 端可真单测
#   - score = dot/2 + 0.5(余弦 [-1,1] → [0,1],同 face_db/body_db)
#   - 轮转覆盖 4 槽(空槽优先,满则覆盖 _next_slot 并推进)
#
# 纯 Python(无 MicroPython 依赖)→ 可在 host 端真单元测试。

from core import db_store

OBJECT_CLASSIFY_DB_PATH = "/sdcard/CamerAi/data/object_classify_db.json"

# 学习 ID 上限(用户确认 2026-08-07):底栏 5 个 ID 选项卡,注册只进 1..5。
# 老数据槽 >5 仍可读(加载不截断),新注册/轮转只写 1..5。
MAX_SLOTS = 5

# 默认匹配阈值(score=cos/2+0.5 映射后)。0.82 ⇒ cos≥0.64,对齐 face_db 修复。
# ⚠️ 勿用 0.5:0.5 ⇒ cos≥0,CNN 自然图像特征几乎总正相关 → 所有目标命中同一 ID。
OBJECT_CLASSIFY_MATCH_THRESHOLD = 0.82
# 多人/多物体注册时 best 与 second 的最小分数差。差更小 → 判不确定。
OBJECT_CLASSIFY_MATCH_MARGIN = 0.06


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


def to_feature_list(feat):
    """公开版 _to_list:供 app 把板端 ndarray 特征拷成 plain list 跨帧持有(锁定态)。"""
    return _to_list(feat)


def cosine_score(a, b):
    """余弦相似度映射 score = cos/2 + 0.5。返回 [0,1] 或 0.0(零向量/异常)。

    纯 Python(host + board 通用)。供 database_search 与 object_classify_lock 复用。
    """
    try:
        al = _to_list(a)
        bl = _to_list(b)
    except Exception:
        return 0.0
    an = sum(x * x for x in al) ** 0.5
    bn = sum(x * x for x in bl) ** 0.5
    if an == 0 or bn == 0:
        return 0.0
    dot = sum(p * q for p, q in zip(al, bl))
    return (dot / (an * bn)) / 2 + 0.5


def database_search(feature, db_features, threshold=OBJECT_CLASSIFY_MATCH_THRESHOLD):
    """Cosine-match feature against db_features. Return (slot_id, score) or (None, 0.0)。

    纯 Python cosine(host + board 通用)。db_features: {slot_id: list}。
    score = cos/2 + 0.5。低于阈值 → (None, 0.0)。
    """
    if not db_features:
        return None, 0.0
    best_id = None
    best_score = 0.0
    second_score = 0.0
    for slot_id, db_feat in db_features.items():
        sc = cosine_score(feature, db_feat)
        if sc > best_score:
            second_score = best_score
            best_score = sc
            best_id = slot_id
        elif sc > second_score:
            second_score = sc
    if best_score < threshold:
        return None, 0.0
    # 次佳区分:注册多人且 best/second 差距过小 → 特征不可区分,拒绝
    if len(db_features) >= 2 and (best_score - second_score) < OBJECT_CLASSIFY_MATCH_MARGIN:
        return None, 0.0
    return best_id, best_score


class _ObjectClassifyDB:
    """物体特征内存库。feature 为 plain list(余弦匹配)。"""

    def __init__(self):
        self._features = {}        # {slot_id: list}
        self._next_slot = 1        # 轮转覆盖指针(1-MAX_SLOTS 循环)
        self._dirty = False
        self._clear_dirty = False

    def register(self, feature):
        """注册 feature 到槽位(轮转覆盖)。返回 slot_id(1-MAX_SLOTS)。纯内存,设 _dirty。

        空槽优先(不推进 _next_slot);无空槽覆盖 _next_slot 并推进。
        feature 转 plain list 存储(避 ulab ndarray 不可 JSON 序列化)。
        """
        feat = _to_list(feature)
        slot = None
        for i in range(1, MAX_SLOTS + 1):
            if i not in self._features:
                slot = i
                break
        if slot is None:
            slot = self._next_slot
            self._next_slot = self._next_slot % MAX_SLOTS + 1
        self._features[slot] = feat
        self._dirty = True
        self._clear_dirty = False
        print("[ObjectClassifyDB] registered feature(%d-dim) -> id%d (memory, dirty)" % (len(feat), slot))
        return slot

    def register_at(self, feature, slot_id):
        """注册 feature 到指定槽位(底栏 ID 选项卡选中槽),覆盖已有。

        返回 slot_id;槽位越界(非 1..MAX_SLOTS)拒绝并返回 None。
        纯内存,设 _dirty(同 register)。
        """
        if not (1 <= slot_id <= MAX_SLOTS):
            print("[ObjectClassifyDB] register_at rejected slot %s (out of 1..%d)" % (slot_id, MAX_SLOTS))
            return None
        feat = _to_list(feature)
        self._features[slot_id] = feat
        self._dirty = True
        self._clear_dirty = False
        print("[ObjectClassifyDB] registered feature(%d-dim) -> id%d (memory, dirty)" % (len(feat), slot_id))
        return slot_id

    def get_features(self):
        """返回特征字典的引用(运行时匹配用)。"""
        return self._features

    def clear(self):
        """清内存,设 _clear_dirty(clear wins over _dirty)。"""
        self._features.clear()
        self._clear_dirty = True
        self._dirty = False
        self._next_slot = 1
        print("[ObjectClassifyDB] cleared (memory, clear_dirty)")

    def _serialize(self):
        return {"next_slot": self._next_slot,
                "slots": {str(k): v for k, v in self._features.items()}}

    def load_from_disk(self, path=OBJECT_CLASSIFY_DB_PATH):
        """启动加载。db_store os.stat 预检查,文件不存在返回 None(避 ENOENT)。"""
        data = db_store.load_json(path)
        if data is None:
            return None
        try:
            self._next_slot = data.get("next_slot", 1)
            for slot_str, feat_list in data.get("slots", {}).items():
                self._features[int(slot_str)] = list(feat_list)
        except Exception as e:
            print("[ObjectClassifyDB] load parse failed: %s" % e)
        return self._features

    def flush_to_disk(self, path=OBJECT_CLASSIFY_DB_PATH):
        """注册即写 / 退出兜底。open('w') 不抛 ENOENT。(镜像 body_db,始终写盘)"""
        db_store.save_json(path, self._serialize())
        self._dirty = False
        self._clear_dirty = False
        print("[ObjectClassifyDB] flushed %d object(s) to %s" % (len(self._features), path))

    def init_features(self, path=OBJECT_CLASSIFY_DB_PATH):
        """启动时加载已注册物体特征到内存(同 body_db.init_features)。"""
        self.load_from_disk(path)
        print("[ObjectClassifyDB] init_features: loaded %d object(s)" % len(self._features))
        return self._features

    @property
    def count(self):
        return len(self._features)


# 全局单例
object_classify_db = _ObjectClassifyDB()

# 导出供宿主测试 import
ObjectClassifyDB = _ObjectClassifyDB
