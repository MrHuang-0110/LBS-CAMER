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

_DB_DIR = "/data/fac_db"  # 无尾部斜杠：os.mkdir 在 MicroPython 上可能拒绝 / 结尾
_NEXT_SLOT_PATH = "/data/fac_db/.next_slot"  # Step 7: 轮转覆盖指针持久化（1-4 循环）


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
        """加载 .bin 文件到 numpy 特征数组。

        使用 open + read + np.frombuffer——与官方 demo
        (demo/AI类实验例程/实验4 人脸识别实验/main2.py:289-290) 完全
        相同的 I/O 路径。曾用 np.fromfile 失败：用户写入的 SD .bin
        在 K230 ulab 下读取静默失败（疑似字节对齐/OSError），导致
        重进 APP 看不到已注册人脸（板端实测：flush_to_disk 写入
        OK / .bin 存在 / init_features 加载 0 face）。

        守卫：必须正好 EXPECTED_BYTES = 512*4 = 2048 字节（mobile kmodel 512 维）。半截写入
        或 dtype 错配都拒绝——避免读到坏数据导致后续 cosine 比对 NaN。
        K230 ulab np.float 等价于 float32（4 字节/元素）。

        调用时机：FaceDetectApp.on_enter() → _init_db()
        在 lv.task_handler() 内部，但 open() 仍属安全窗口（与
        _init_ai_models 加载 anchors 同位置；曾大量验证安全）。
        """
        import os
        import ulab.numpy as np_local
        # face_recognition_mobile.kmodel 输出 512 维特征 → 512×4=2048 字节
        # （标准 face_recognition.kmodel 44MB，LVGL 双线程后 ~3.7MB free 装不下
        # → AIBase.__init__ 死锁；mobile 2.65MB 才装得下。官方 main2.py 用标准
        # 版是因无 LVGL，内存充裕。CamerAi 双线程 LVGL 必须用 mobile。）
        EXPECTED_BYTES = 512 * 4
        self._features = {}
        # ⚠️ 先 os.listdir 一次列出目录，避免对每个 idN.bin 都 open() 抛 ENOENT。
        # 板端验证（坑#18 关联）：open() 异常路径在 K230 FATFS 上疑似污染
        # DMA/文件系统状态 → 后续 AI 线程每帧 snapshot/show_image 累积，
        # fc~30 渐进卡死。listdir 对不存在目录抛一次异常即视为空，0 face 时
        # 完全不进 open。对齐官方实验4 main2.py:280 listdir 模式。
        try:
            files = os.listdir(_DB_DIR)
        except Exception:
            files = []
        for i in range(1, 5):
            fname = f"id{i}.bin"
            if fname not in files:
                continue
            path = f"{_DB_DIR}/{fname}"
            try:
                with open(path, 'rb') as f:
                    data = f.read()
            except Exception as e:
                print(f"[FaceDB] id{i}.bin not loadable: {e}")
                continue
            if not isinstance(data, (bytes, bytearray)):
                print(f"[FaceDB] id{i}.bin read returned non-bytes")
                continue
            if len(data) != EXPECTED_BYTES:
                print(f"[FaceDB] id{i}.bin invalid (got {len(data)} bytes, expect {EXPECTED_BYTES})")
                continue
            try:
                feature = np_local.frombuffer(data, dtype=np_local.float)
            except Exception as e:
                print(f"[FaceDB] id{i}.bin frombuffer failed: {e}")
                continue
            self._features[i] = feature
            print(f"[FaceDB] loaded id{i}.bin ({len(data)//4} floats)")
        self._load_next_slot()   # Step 7: 读回上次覆盖指针（与读 .bin 同安全窗口）
        self._loaded = True
        print(f"[FaceDB] init_features done: {len(self._features)} face(s)")
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

        - _clear_dirty: remove all .bin + .next_slot, reset pointer (clear intent)
        - _dirty: write all memory features to .bin + save _next_slot
        - neither: no-op
        Resets flags after.
        """
        import os
        if self._clear_dirty:
            for i in range(1, 5):
                try:
                    os.remove(f"{_DB_DIR}/id{i}.bin")
                except Exception:
                    pass
            try:
                os.remove(_NEXT_SLOT_PATH)
            except Exception:
                pass
            self._clear_dirty = False
            self._dirty = False
            print("[FaceDB] exit: cleared disk")
            return
        if not self._dirty:
            return
        if not self._features:
            self._dirty = False
            return
        try:
            os.mkdir(_DB_DIR)
        except Exception:
            pass
        for i, feature in self._features.items():
            path = f"{_DB_DIR}/id{i}.bin"
            try:
                with open(path, 'wb') as f:
                    f.write(feature.tobytes())
                print("[FaceDB] exit: flushed id%d.bin" % i)
            except Exception as e:
                print("[FaceDB] exit: flush id%d failed: %s" % (i, e))
        self._save_next_slot()
        self._dirty = False

    def clear(self):
        """Clear all features in MEMORY only.

        No file deletion here (pitfall #2). Sets _clear_dirty; the exit stage
        removes all .bin + .next_slot. Cancels any pending _dirty (clear wins).
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
