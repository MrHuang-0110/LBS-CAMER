# core/face_db.py — 人脸特征数据库
#
# K230 硬约束：lv.task_handler() 运行后，SD/FATFS 文件读写会与
# display flush/DMA 永久死锁（K230 坑 #2）。
#
# 安全策略（v2 — 零运行时文件 I/O）：
#   - 加载：init_features() 在 _init_ai 中（首次 task_handler 前安全窗口）
#     经 load_from_disk → db_store.load_json 读 FACE_DB_PATH；db_store 先
#     os.stat 预检查，文件不存在返回空库，绝不触发 open(ENOENT)（坑#18）。
#   - 运行时：APP 内注册/清除仅改内存 dict + dirty 标志，零文件 I/O。
#   - 写盘：注册即写/清除即写在主循环 task_handler 前安全窗口调用
#     flush_to_disk()（坑#2 不冲突），退出 finally 再兜底一次。
#
# 关键变更（v1→v2）：
#   - 移除 preload()：main.py 启动期不再做任何 face_db 文件 I/O。
#     init 阶段 open().read() 即使只读也触发全局 DMA 死锁
#     （与 icon_cache 的关键差异：/data/fac_db/ 目录可能不存在，
#     对不存在路径的 open 调用在 K230 FATFS 上污染 DMA 状态）。
#   - init_features() 改用 db_store.load_json：os.stat 预检查，安全。

_DB_DIR = "/sdcard/CamerAi/fac_db"  # 诊断:从 /data 改 /sdcard(与 anchors/kmodel 同分区,避坑#18 /data I/O 污染)
_NEXT_SLOT_PATH = _DB_DIR + "/.next_slot"  # Step 7: 轮转覆盖指针持久化(1-4 循环)

import os
from core import db_store

FACE_DB_PATH = "/sdcard/CamerAi/data/face_db.json"

# 匹配阈值(score=cos/2+0.5 映射后)。0.82 ⇒ cos≥0.64。
# 旧值 0.75(cos≥0.5)对 MobileFaceNet 过松 → 不同人脸也命中,
# 典型现象"换个人仍识别为上一个人"。提高后未注册人不再误匹配。
FACE_MATCH_THRESHOLD = 0.82
# 多人注册时 best 与 second 的最小分数差。差更小 → 特征不可区分,
# 判"不确定"返回 None(防已注册多人之间 ID 混淆)。单注册人不受影响。
FACE_MATCH_MARGIN = 0.06


class _FaceDB:
    """全局人脸特征缓存"""

    def __init__(self):
        self._features = {}   # {slot_id: np_array}  运行时使用
        self._loaded = False
        self._next_slot = 1   # Step 7: 轮转覆盖指针(1-25 循环)，clear()/init_features() 读写
        self._dirty = False        # register changed memory; flush at exit
        self._clear_dirty = False  # clear requested; remove all .bin at exit

    # ── 运行时加载（APP 内 on_enter → _init_db 调用）────

    def init_features(self):
        """加载已注册特征到内存（启动期，首次 task_handler 前的安全窗口）。

        从 FACE_DB_PATH 读 JSON（db_store os.stat 预检查，文件不存在返回空库，
        避坑#18 open ENOENT 污染）。
        """
        self._features = {}
        self._loaded = True
        self.load_from_disk(FACE_DB_PATH)
        print("[FaceDB] init_features: loaded %d face(s) from disk" % len(self._features))
        return self._features

    # ── Step 7: 轮转覆盖指针持久化 ──────────────────────

    def _load_next_slot(self):
        """读 _next_slot 指针文件(init_features 内,与加载特征同安全窗口)。
        文件不存在/损坏 → 默认 1。
        坑#18:open('r') ENOENT 异常污染 FATFS,须 os.stat 预检查。"""
        try:
            os.stat(_NEXT_SLOT_PATH)
            with open(_NEXT_SLOT_PATH, 'r') as f:
                v = int(f.read().strip())
            self._next_slot = v if 1 <= v <= 25 else 1
        except Exception:
            self._next_slot = 1

    def _save_next_slot(self):
        """写 _next_slot 指针文件(轮转指针持久化契约入口;register 已改仅内存,
        当前无调用点,退出兜底可复用)。"""
        try:
            with open(_NEXT_SLOT_PATH, 'w') as f:
                f.write(str(self._next_slot))
        except Exception as e:
            print("[FaceDB] save next_slot failed: %s" % e)

    # ── 运行时读取（零文件 I/O）─────────────────────────

    def get_features(self):
        """返回特征字典的引用（APP 直接修改即为内存操作）"""
        return self._features

    # ── Step 7: 注册（轮转覆盖 + 内存-only dirty）────────

    def register(self, feature):
        """注册特征到槽位(轮转覆盖),仅内存操作,返回 slot_id(1-4)。

        不落盘(坑#2:运行时 SD 写与 display DMA flush 竞争),只置 _dirty,
        写盘由 APP 在安全窗口(注册即写)或退出兜底完成。
        - 有空槽:填首个空槽(不移动 _next_slot)
        - 无空槽:覆盖 _next_slot 指向的槽,指针轮转 1→2→3→4→1
        """
        slot = None
        for i in range(1, 26):
            if i not in self._features:
                slot = i
                break
        if slot is None:
            slot = self._next_slot
            self._next_slot = self._next_slot % 25 + 1
        self._features[slot] = feature
        self._dirty = True
        self._clear_dirty = False  # register 在 clear 之后执行 → 取消清除意图
        print("[FaceDB] registered → id%d (memory, dirty)" % slot)
        return slot

    # ── 退出时刷盘（on_exit 中调用，lv.task_handler 已完成）──

    def _serialize(self):
        """序列化为 JSON 可存结构。特征 ulab ndarray → list(float)。"""
        slots = {}
        for slot_id, feat in self._features.items():
            try:
                slots[str(slot_id)] = feat.tolist()
            except Exception:
                slots[str(slot_id)] = list(feat)
        return {"next_slot": self._next_slot, "slots": slots}

    def load_from_disk(self, path):
        """启动加载。db_store os.stat 预检查，文件不存在返回 None（空库，避 ENOENT）。"""
        data = db_store.load_json(path)
        if data is None:
            return None
        try:
            self._next_slot = data.get("next_slot", 1)
            slots = data.get("slots", {})
            for slot_str, feat_list in slots.items():
                try:
                    import ulab.numpy as np
                    feat = np.array(feat_list, dtype=np.float)
                except Exception:
                    feat = list(feat_list)  # PC / ulab 缺失兜底
                self._features[int(slot_str)] = feat
        except Exception as e:
            print("[FaceDB] load parse failed: %s" % e)
        return self._features

    def flush_to_disk(self, path=FACE_DB_PATH):
        """写盘。注册即写(on_frame 内 task_handler 前,坑#2 安全窗口)与清除
        即写空库均走此路径,退出 finally 再兜底一次;失败由 db_store 吞掉不抛。"""
        db_store.save_json(path, self._serialize())
        self._dirty = False
        self._clear_dirty = False
        print("[FaceDB] flushed %d face(s) to %s" % (len(self._features), path))

    def clear(self):
        """清空内存特征,仅内存操作,不删盘文件(坑#2)。

        只置 _clear_dirty;写盘由 APP 主循环在安全窗口执行(清除即写空库,
        覆盖旧数据,防断电回魂)。.next_slot 指针一并复位 1(文件删除逻辑
        当前禁用)。同时取消未落盘的 _dirty(清除优先)。
        """
        self._features.clear()
        self._clear_dirty = True
        self._dirty = False
        self._next_slot = 1
        print("[FaceDB] cleared (memory, clear_dirty)")


def database_search(feature, db_features, threshold=FACE_MATCH_THRESHOLD):
    """在 db_features 中余弦匹配 feature,返回 (slot_id, score) 或 (None, 0.0)。

    db_features: {slot_id: np_array}(on_frame 内只读)。空库/特征异常/低于
    threshold → (None, 0.0)。score = 余弦匹配度(0-1),用作上位机置信度。
    多人注册时 best 与 second 差 < FACE_MATCH_MARGIN → 判不确定(拒绝),
    防特征相近的已注册人互相混淆(单注册人不受影响)。
    """
    if not db_features:
        return None, 0.0
    try:
        import ulab.numpy as np
        feat_norm = np.linalg.norm(feature)
        if feat_norm == 0:
            return None, 0.0
        feature = feature / feat_norm
    except Exception:
        return None, 0.0
    best_id = None
    best_score = 0.0
    second_score = 0.0
    for slot_id, db_feat in db_features.items():
        try:
            norm = np.linalg.norm(db_feat)
            if norm == 0:
                continue
            db_n = db_feat / norm
            score = np.dot(feature, db_n) / 2 + 0.5
        except Exception:
            continue
        if score > best_score:
            second_score = best_score
            best_score = score
            best_id = slot_id
        elif score > second_score:
            second_score = score
    if best_score < threshold:
        return None, 0.0
    # 次佳区分:注册了多人且 best/second 差距过小 → 特征不可区分,拒绝
    if len(db_features) >= 2 and (best_score - second_score) < FACE_MATCH_MARGIN:
        return None, 0.0
    return best_id, best_score


# 全局单例
face_db = _FaceDB()
