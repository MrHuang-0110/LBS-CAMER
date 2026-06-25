# core/face_db.py — 人脸特征数据库
#
# K230 硬约束：lv.task_handler() 运行后，SD/FATFS 文件读写会与
# display flush/DMA 永久死锁（K230 坑 #2）。
#
# 安全策略（v2 — 零 init 阶段文件 I/O）：
#   - 加载：_init_db() 在 on_enter 中使用 np.fromfile() 直接读取。
#     np.fromfile() 与 _init_ai_models() 加载 anchors 完全相同的 I/O
#     路径，已在 task_handler 内部验证安全。
#   - 运行时：APP 内零文件 I/O，所有 DB 操作仅改内存 dict。
#   - 回写：on_exit 期间批量写盘（lv.task_handler 已完成，DMA 空闲）。
#
# 关键变更（v1→v2）：
#   - 移除 preload()：main.py 启动期不再做任何 face_db 文件 I/O。
#     init 阶段 open().read() 即使只读也触发全局 DMA 死锁
#     （与 icon_cache 的关键差异：/data/fac_db/ 目录可能不存在，
#     对不存在路径的 open 调用在 K230 FATFS 上污染 DMA 状态）。
#   - init_features() 改用 np.fromfile()：与 anchors 加载同路径，安全。

_DB_DIR = "/sdcard/CamerAi/fac_db"  # 诊断：从 /data 改 /sdcard（同 anchors/kmodel 分区，避坑#18 /data I/O 污染）
_NEXT_SLOT_PATH = "/sdcard/CamerAi/fac_db/.next_slot"  # Step 7: 轮转覆盖指针持久化（1-4 循环）


class _FaceDB:
    """全局人脸特征缓存"""

    def __init__(self):
        self._features = {}   # {slot_id: np_array}  运行时使用
        self._loaded = False
        self._next_slot = 1   # Step 7: 轮转覆盖指针(1-4 循环)，clear()/init_features() 读写
        self._dirty = False        # register changed memory; flush at exit
        self._clear_dirty = False  # clear requested; remove all .bin at exit

    # ── 运行时加载（APP 内 on_enter → _init_db 调用）────

    def init_features(self):
        """加载已注册特征到内存。

        ⚠️ 持久化路径待定：listdir/open 对 /data 或 /sdcard 的特征库 I/O 都会
        污染 K230 FATFS/DMA 状态（坑#18），导致主循环卡死。当前阶段不读盘，
        返回空库——注册/识别/清除的内存逻辑正常推进，重启后不保留历史特征。
        持久化路径和加载方式后续决定（接口已预留，见 flush_to_disk/clear）。
        """
        self._features = {}
        self._loaded = True
        print("[FaceDB] init_features: persistence disabled (in-memory only), 0 face(s)")
        return self._features

    # ── Step 7: 轮转覆盖指针持久化 ──────────────────────

    def _load_next_slot(self):
        """读 _next_slot 指针文件（init_features 内，与读 .bin 同安全窗口）。
        文件不存在/损坏 → 默认 1。"""
        try:
            with open(_NEXT_SLOT_PATH, 'r') as f:
                v = int(f.read().strip())
            self._next_slot = v if 1 <= v <= 4 else 1
        except Exception:
            self._next_slot = 1

    def _save_next_slot(self):
        """写 _next_slot 指针文件（register 内，与 flush_to_disk 同批写盘）。"""
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
        """Register feature to slot (round-robin) in MEMORY only. Returns slot_id(1-4).

        No disk I/O here (pitfall #2: runtime SD write races display DMA flush).
        Sets _dirty; persistence runs at exit stage (task_handler stopped).

        - Empty slot first (do not move _next_slot)
        - No empty slot: overwrite _next_slot, advance pointer (1→2→3→4→1)
        """
        slot = None
        for i in range(1, 5):
            if i not in self._features:
                slot = i
                break
        if slot is None:
            slot = self._next_slot
            self._next_slot = self._next_slot % 4 + 1
        self._features[slot] = feature
        self._dirty = True
        self._clear_dirty = False  # a register after clear cancels the clear intent
        print("[FaceDB] registered → id%d (memory, dirty)" % slot)
        return slot

    # ── 退出时刷盘（on_exit 中调用，lv.task_handler 已完成）──

    def flush_to_disk(self):
        """Exit-stage persistence. Called after task_handler stopped (pitfall #2 safe).

        ⚠️ 持久化路径待定（见 init_features 注释）：当前不写盘，仅复位 dirty 标志。
        持久化路径和写盘方式后续决定后，在此实现 _clear_dirty 删盘 / _dirty 写盘。
        """
        if self._clear_dirty:
            print("[FaceDB] exit: clear intent recorded (persistence disabled, no disk write)")
        elif self._dirty:
            print("[FaceDB] exit: %d feature(s) pending (persistence disabled, no disk write)"
                  % len(self._features))
        self._clear_dirty = False
        self._dirty = False

    def clear(self):
        """Clear all features in MEMORY only.

        No file deletion here (pitfall #2). Sets _clear_dirty; the exit stage
        would remove all .bin + .next_slot (currently disabled). Cancels pending
        _dirty (clear wins).
        """
        self._features.clear()
        self._clear_dirty = True
        self._dirty = False
        self._next_slot = 1
        print("[FaceDB] cleared (memory, clear_dirty)")


def database_search(feature, db_features, threshold=0.75):
    """Cosine-match feature against db_features. Return slot_id or None.

    db_features: {slot_id: np_array} (in-memory, read-only during on_frame).
    Empty / bad / below-threshold → None. Aligns with official main2.py.
    """
    if not db_features:
        return None
    try:
        import ulab.numpy as np
        feat_norm = np.linalg.norm(feature)
        if feat_norm == 0:
            return None
        feature = feature / feat_norm
    except Exception:
        return None
    best_id = None
    best_score = 0.0
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
            best_score = score
            best_id = slot_id
    if best_score < threshold:
        return None
    return best_id


# 全局单例
face_db = _FaceDB()
