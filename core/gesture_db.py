# core/gesture_db.py — 手势 ID 内存数据库(任意手势特征匹配)
#
# 2026-08-07 语义重构:从"4 类 label_idx 精确匹配"改为"任意手势特征匹配":
#   - 特征 = hand_reco.kmodel 的 4 维 softmax 分布(手势形态特征),K2 学习入库
#   - 余弦匹配 score = cos/2+0.5,阈值 GESTURE_MATCH_THRESHOLD;已注册手势间
#     best/second 差 < GESTURE_MATCH_MARGIN → 判不确定(防 ID 混淆,镜像 face_db)
#   - 注册:相似特征(同一手势重复学习)返回原槽不占新槽;否则空槽优先/轮转覆盖
#   - 持久化:特征 list 序列化 JSON;旧版 label_idx(int) 数据加载时自动转
#     one-hot 特征平滑迁移
#
# 纯 Python(无 MicroPython 依赖)→ 可在 host 端真单元测试。

from core import db_store

GESTURE_DB_PATH = "/sdcard/CamerAi/data/gesture_db.json"

# hand_reco.kmodel 输出维度(4 类 softmax → 手势形态特征)
GESTURE_FEAT_DIM = 4
# 匹配阈值(score = cos/2+0.5 映射后)。0.85 ⇒ cos≥0.7:同手势不同帧推理
# 分布抖动可命中,不同手势分布差异明显(如 gun≈[1,0,0,0] vs five≈[0,0,0,1])
# 不命中。板端若误匹配可调高,漏匹配可调低。
GESTURE_MATCH_THRESHOLD = 0.85
# 多人注册时 best 与 second 的最小分数差。差更小 → 特征不可区分,
# 判"不确定"返回 None(防已注册手势之间 ID 混淆)。单注册手势不受影响。
GESTURE_MATCH_MARGIN = 0.10


def _cos_similarity(a, b):
    """两特征余弦相似度 → score = cos/2+0.5 (0~1,纯 Python,host/板端通用)。"""
    dot = 0.0
    na = 0.0
    nb = 0.0
    for i in range(len(a)):
        av = a[i]
        bv = b[i]
        dot += av * bv
        na += av * av
        nb += bv * bv
    if na == 0 or nb == 0:
        return 0.0
    import math
    cos = dot / (math.sqrt(na) * math.sqrt(nb))
    return cos / 2 + 0.5


class _GestureDB:
    """手势特征内存库。特征为 4 维 list(hand_reco softmax 分布)。"""

    def __init__(self):
        self._features = {}        # {slot_id: feat_list(4 维)}
        self._next_slot = 1        # 轮转覆盖指针(1-25 循环)
        self._dirty = False
        self._clear_dirty = False

    def register(self, feature):
        """注册特征(任意手势)到槽位(轮转覆盖;相似特征不重复占槽)。

        先匹配:已注册相似手势 → 返回原槽(同一手势重复学习不占新槽)。
        否则空槽优先(不推进 _next_slot);无空槽覆盖 _next_slot 并推进。
        返回 slot_id(1-25)。纯内存,设 _dirty。
        """
        feat = list(feature)
        slot, _score = self.match(feat)
        if slot is not None:
            return slot
        slot = None
        for i in range(1, 26):
            if i not in self._features:
                slot = i
                break
        if slot is None:
            slot = self._next_slot
            self._next_slot = self._next_slot % 25 + 1
        self._features[slot] = feat
        self._dirty = True
        self._clear_dirty = False
        print("[GestureDB] registered feature -> id%d (memory, dirty)" % slot)
        return slot

    def match(self, feature):
        """特征余弦匹配。返回 (slot_id, score) 或 (None, 0.0)。

        同手势特征(softmax 分布近似)score ≥ 阈值命中;不同手势不命中。
        多人注册且 best/second 差 < margin → 判不确定返回 None。
        """
        if not self._features:
            return None, 0.0
        feat = list(feature)
        best_id = None
        best_score = 0.0
        second_score = 0.0
        for slot_id, db_feat in self._features.items():
            try:
                score = _cos_similarity(feat, db_feat)
            except Exception:
                continue
            if score > best_score:
                second_score = best_score
                best_score = score
                best_id = slot_id
            elif score > second_score:
                second_score = score
        if best_score < GESTURE_MATCH_THRESHOLD:
            return None, 0.0
        if len(self._features) >= 2 and (best_score - second_score) < GESTURE_MATCH_MARGIN:
            return None, 0.0
        return best_id, best_score

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
        """启动加载。db_store os.stat 预检查,文件不存在返回 None(避 ENOENT)。

        兼容旧版数据:{slot: label_idx(int)} → 转 one-hot 特征平滑迁移;
        新版 {slot: feat_list} 直接使用。
        """
        data = db_store.load_json(path)
        if data is None:
            return None
        try:
            self._next_slot = data.get("next_slot", 1)
            for slot_str, feat in data.get("slots", {}).items():
                slot_id = int(slot_str)
                if isinstance(feat, (list, tuple)):
                    self._features[slot_id] = [float(v) for v in feat]
                else:
                    # 旧版 label_idx(0-3)→ one-hot 特征
                    one_hot = [0.0] * GESTURE_FEAT_DIM
                    idx = int(feat)
                    if 0 <= idx < GESTURE_FEAT_DIM:
                        one_hot[idx] = 1.0
                    else:
                        one_hot[0] = 1.0
                    self._features[slot_id] = one_hot
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
        """启动时加载已注册手势特征到内存(同 face_db.init_features)。"""
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
