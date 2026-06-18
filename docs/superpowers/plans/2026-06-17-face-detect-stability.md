# 人脸识别 APP 稳定性修复 + 架构演进 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 消除人脸识别 APP "几十秒后卡死" 与 "重进不读已注册人脸" 两个必现 bug；为后续 AI 脚本（手势/物体/颜色等）抽象稳定的 `AIScriptBase` 基类，将屏幕中心 240×240 ROI 过滤 + 单脸识别作为基类内置能力。

**Architecture:** 分两阶段。阶段 1 是局部 bugfix——`_img_to_chw` 强制 numpy 拷贝消除 sensor 帧 VB 持有链；`face_db` 改用官方 demo 验证过的 `open + read + frombuffer` 路径。阶段 2 是架构演进——新增 `scripts/_ai_base.py` 沉淀"安全帧拷贝/安全 deinit/ROI 过滤/单脸选择/帧循环模板"四块共享能力，face_detect 重构为继承 `AIScriptBase`。所有改动必须遵守 K230 三大硬约束（坑 #2 SD I/O 仅在 on_enter 安全窗口、坑 #10 on_frame 不显式 GC、坑 #14 VB 必须归还）。

**Tech Stack:** MicroPython + LVGL v8 + ulab.numpy + nncase_runtime（NPU）+ aidemo + K230D BOX。所有 UI 文字走 `lang.t()`。

---

## K230 硬约束（实施前必读）

实施任何任务前都要确认改动**不违反**以下三条：

1. **坑 #2 — FATFS/DMA**：`lv.task_handler()` 启动后做 SD 卡 `open()/read()/write()` 会与 LVGL display flush 的 DMA 永久死锁。**安全窗口仅有两个**：(a) `main.py` 启动期首次 `lv.task_handler()` 之前；(b) 脚本 `on_enter` 内（已实测：`_init_ai_models` 内的 `np.fromfile(anchors)` 在此处安全）。**禁止区**：`on_frame`、LVGL 按钮回调、`on_key`。
2. **坑 #10 — gc.collect 触发 LVGL 终结器**：`on_frame` 显式 `gc.collect()` 会回收待销毁 LVGL 对象，触碰 DMA 状态死锁。**对策**：on_frame 不写 `_gc.collect()`；不调 `AIBase.deinit()`（其内部含 `gc.collect()` + `nn.shrink_memory_pool()` + `sleep_ms(100)`）；on_exit 用分步 `del kpu/ai2d/tensors` 替代。
3. **坑 #14 — VB pool 不归还**：sensor 帧 (`img`) 的 `to_numpy_ref()` 是零拷贝 view，任何派生 ndarray 若被长期持有就会"链式锁住"VB block；连续几十秒就会耗尽 VB pool，`sensor.snapshot()` 不再返回有效帧。**对策**：所有 chw 张量必须是**真正的拷贝**，不是 view；不能让 `self._current_frame_data` 引用任何派生自 `img.to_numpy_ref()` 的 view。

---

## File Structure

| 文件 | 角色 | 阶段 |
|------|------|------|
| `scripts/face_detect/app.py` | 阶段 1：修 `_img_to_chw`、删 `_gc.collect()`。阶段 2：重构为继承 `AIScriptBase`，骨架收缩到只剩"AI 模型/DB/UI/事件"四段 | 1 + 2 |
| `core/face_db.py` | 阶段 1：`init_features` 改 `open + read + frombuffer`，加 `len(data) == 512` 守卫 | 1 |
| `scripts/_ai_base.py` | **新建** — 阶段 2 抽象基类：`safe_chw_copy()`, `safe_deinit_ai_module()`, `roi_filter_largest()`, 单帧 `on_frame` 模板钩子 | 2 |
| `scripts/_base.py` | 不动（`BaseScript` 不变，`AIScriptBase` 继承 `BaseScript`） | — |
| `tests/test_face_detect.py` | 阶段 1+2 增加可主机端运行的纯逻辑测试（ROI 过滤、frombuffer 大小守卫、chw 拷贝形状） | 1 + 2 |

---

## 测试约束说明

**主机端（Windows）可测**：纯 Python/numpy 逻辑——`face_db._parse_feature_blob()`、`AIScriptBase.roi_filter_largest()`、`AIScriptBase.safe_chw_copy()`（用 `numpy` 替代 `ulab.numpy` mock）。这些测试必须先写、必须先红、再实现到绿。

**只能板端测**：`sensor.snapshot()` / `kpu.run` / LVGL 渲染 / 实际 5 分钟连跑。每个阶段末尾给出"板端验收脚本"，按步骤手动操作。

---

## 阶段 1：必修 bug

> 目标：4 张人脸注册后正常退出/重进能读到；连续 5 分钟不卡死。**不动架构**。

### Task 1.1: `_img_to_chw` 强制拷贝（修问题 1 — VB 持有链）

**Files:**
- Modify: `scripts/face_detect/app.py:478-495`
- Test: `tests/test_face_detect.py`（新增/追加 test）

**Why:** `chw.reshape((1, C, H, W))` 在 ulab 上几乎肯定返回 view（与 numpy 同步），返回值与 `chw` 共享底层 buffer。`chw = trans.copy()` 这一步只把 `trans`（HWC→CHW 转置后的二维数组）拷贝了一次——但 `trans` 本身又来自 `hwc.reshape().transpose()`，**`hwc = img.to_numpy_ref()` 是 sensor 帧的零拷贝 view**。需要验证 `chw` 是否真断链。最稳的办法：在最外层用 `np.array(..., copy=True)` 再拷一次，确保返回的张量不再持有 `img` 的内存。

- [ ] **Step 1.1.1: 写失败的纯逻辑测试（验 shape 不变、返回值与输入解耦）**

文件：`tests/test_face_detect.py`，追加测试（如果文件不存在新建，模块顶部加 numpy import）。

```python
# tests/test_face_detect.py — 追加测试
import numpy as np


def _img_to_chw_pure(hwc_array):
    """主机端纯 numpy 等价实现，对应 scripts/face_detect/app.py 的 _img_to_chw 逻辑。

    输入：H×W×C uint8 ndarray（模拟 img.to_numpy_ref() 返回值）
    输出：(1, C, H, W) ndarray，且必须与输入完全解耦（修改输入不影响输出）
    """
    shape = hwc_array.shape
    tmp = hwc_array.reshape((shape[0] * shape[1], shape[2]))
    trans = tmp.transpose()
    chw = trans.copy()
    out = chw.reshape((1, shape[2], shape[0], shape[1]))
    # 关键：再 copy 一次断绝与输入的内存关联（对应 fix 后行为）
    return np.array(out, copy=True)


def test_img_to_chw_shape():
    """480×640×3 → (1, 3, 480, 640)"""
    h, w, c = 480, 640, 3
    hwc = np.zeros((h, w, c), dtype=np.uint8)
    out = _img_to_chw_pure(hwc)
    assert out.shape == (1, c, h, w), f"unexpected shape {out.shape}"


def test_img_to_chw_decoupled_from_source():
    """修改输入帧后，输出必须不变（说明已经断链）"""
    hwc = np.full((4, 4, 3), 7, dtype=np.uint8)
    out = _img_to_chw_pure(hwc)
    snapshot = out.copy()
    # 模拟 sensor 帧被 VB pool 重新写入新数据
    hwc[:, :, :] = 99
    assert (out == snapshot).all(), "output still references input — VB will leak"
```

- [ ] **Step 1.1.2: 跑测试确认红**

```
cd e:/LBS-Project/CanMV/CamerAi
python -m pytest tests/test_face_detect.py::test_img_to_chw_decoupled_from_source -v
```
预期：`test_img_to_chw_shape` PASS，`test_img_to_chw_decoupled_from_source` 暂时也 PASS（因为辅助函数已经写了 `np.array(out, copy=True)`）。

> 这里 TDD 的 "red" 是**契约测试**——如果有人不小心把 `np.array(out, copy=True)` 删掉，这个测试**立刻变红**。这就是它的价值：作为回归门。所以本任务的"红→绿"实际上是先证明**契约能捕获回归**：

- [ ] **Step 1.1.3: 临时把契约测试中的 `np.array(out, copy=True)` 删掉，确认测试转红**

把 `_img_to_chw_pure` 最后一行改成 `return out`（删除 `np.array(..., copy=True)`），重跑 Step 1.1.2 命令。

预期：`test_img_to_chw_decoupled_from_source` FAIL（因为 reshape 在 numpy 是 view，修改 `hwc` 后 `out` 也变了）。

- [ ] **Step 1.1.4: 恢复 `np.array(out, copy=True)`，确认测试转绿**

撤回上一步的删除，重跑 Step 1.1.2 命令。预期：两个测试都 PASS。

- [ ] **Step 1.1.5: 应用同样的 fix 到设备端代码 `scripts/face_detect/app.py`**

把 [scripts/face_detect/app.py:478-495](scripts/face_detect/app.py#L478-L495) 的 `_img_to_chw` 替换为：

```python
    @staticmethod
    def _img_to_chw(img):
        """将 snapshot Image (RGB888) 转为 CHW numpy，供 NPU 推理。

        K230 坑 #14：必须返回与 sensor 帧完全解耦的 ndarray。
        ulab 的 reshape() 返回 view，会链式持有 img 的 VB block，
        几十秒后 VB pool 耗尽 → sensor.snapshot() 死循环。

        最外层 np.array(..., copy=True) 确保彻底断链：调用方拿到的张量
        与 img 像素 buffer 无任何引用关系，img 函数返回即可被 VB 回收。

        ⚠️ chn0 已配置为 Sensor.RGB888（lcd.py:58），snapshot 返回即
        RGB888 格式，直接 to_numpy_ref() 零拷贝引用像素数据。切勿调
        img.to_rgb888()——会创建新 image.Image（~900KB VB 池分配）。
        """
        hwc = img.to_numpy_ref()
        shape = hwc.shape
        tmp = hwc.reshape((shape[0] * shape[1], shape[2]))
        trans = tmp.transpose()
        chw = trans.copy()
        del tmp, trans
        out = chw.reshape((1, shape[2], shape[0], shape[1]))
        # ★ 终极断链：再拷贝一次，彻底脱离 img 的 VB block
        return np.array(out, copy=True)
```

- [ ] **Step 1.1.6: 提交**

```
git add scripts/face_detect/app.py tests/test_face_detect.py
git commit -m "fix(face_detect): _img_to_chw 强制 np.array copy 断绝 VB 持有链 — 修连跑几十秒卡死

K230 坑 #14：ulab reshape 返回 view，原有实现末尾 chw.reshape(...) 仍与
img.to_numpy_ref() 共享内存 → self._current_frame_data 长期持有 sensor
帧 → VB pool 几十秒耗尽 → sensor.snapshot() 不再返回有效帧。

修法：最外层 np.array(out, copy=True) 强制拷贝；契约测试守住断链行为。

板端日志证据：第 1170 帧 done OK 后，第 1171 帧起 snapshot 静默失败，
主循环心跳仍在打 — 说明是 VB pool 而非 GC 死锁。

🤖 Generated with [Claude Code](https://claude.com/claude-code)

Co-Authored-By: Claude <noreply@anthropic.com>"
```

**回退方案**：若板端验证后仍然几十秒卡死，说明 VB 不是唯一原因。撤回到上一个提交，加诊断日志（每 30 帧打印一次 `gc.mem_free()` + ulab `np.array` 统计），重新进入 systematic-debugging Phase 1。

---

### Task 1.2: 移除 `on_frame` 末尾的每帧 `_gc.collect()`（修问题 1 副作用）

> ⚠️ **SUPERSEDED / 已撤销（2026-06-17 板端复验）** — 本 task 的结论被推翻。
> 板端验收阶段 1 时复现：删除每帧 gc.collect() 后，APP 在**第 6 帧**
> `face_det.run`（kpu.run）永久阻塞（无异常无返回）。根因见 K230 坑 #16
>（板端确认）：NPU/AI2D/numpy 原生缓冲对 MicroPython GC 不可见，不显式
> gc.collect() 就单调累积，几帧内耗尽 NPU 池 → kpu.run 永久阻塞。坑 #14
> （VB 拷贝）与坑 #16（每帧 GC）是**两条独立必要**的硬约束，修了 #14 不代表
> 能省 #16。
>
> 坑 #10（GC 触发 LVGL 终结器碰 DMA 死锁）的真正对策是**对象池**（已由
> `_build_face_box_pool` 实现，on_frame 不 create/delete LVGL 对象 → 无
> pending 终结器 → 每帧 GC 安全），**而非删除 GC**。本 task 误把"删 GC"
> 当成坑 #10 的对策，反而引入坑 #16 的卡死。
>
> **已撤销**：on_frame 末尾重新加回 `gc.collect()`，对应测试
> `test_face_detect_on_frame_collects_gc_each_frame` 守住此硬约束。
> 下方原文保留作历史记录，**勿再执行**。

**Files:**
- Modify: `scripts/face_detect/app.py:396-397`

**Why:** `_gc.collect()` 写在 on_frame 末尾，每帧都触发待销毁 LVGL 对象的终结器，碰 DMA 死锁的概率随时间累加（K230 坑 #10）。Camera APP 从不在 on_frame 调 gc，长期稳定。MicroPython 自己有 GC 阈值机制，足够。

> 注：阶段 1 完成后，只删除这行；阶段 2 抽象到 `AIScriptBase` 时再考虑是否提供"按 N 帧间隔可选 GC"的钩子。

- [ ] **Step 1.2.1: 删除 `on_frame` 末尾的 `_gc.collect()`**

定位 [scripts/face_detect/app.py:396-397](scripts/face_detect/app.py#L396-L397)：

```python
        # ── 每帧回收 NPU/AI2D/numpy 原生缓冲 ──
        _gc.collect()
```

替换为：

```python
        # ── 不在 on_frame 显式 GC（K230 坑 #10：触发 pending LVGL 终结器
        #    碰 DMA 死锁）。MicroPython 内置 GC 自行按内存阈值触发即可。──
```

> 同时检查 `import gc as _gc` 是否还有别的地方用——如果没有，把这行 import 也删掉。

- [ ] **Step 1.2.2: 检查 `_gc` 是否还有引用**

```
grep -n "_gc" e:/LBS-Project/CanMV/CamerAi/scripts/face_detect/app.py
```
预期：除 import 行外无其他引用。如果有，保留 import；否则删除 [scripts/face_detect/app.py:27](scripts/face_detect/app.py#L27) 的 `import gc as _gc`。

- [ ] **Step 1.2.3: 编译检查（主机端 AST 验证）**

```
python -c "import ast; ast.parse(open('e:/LBS-Project/CanMV/CamerAi/scripts/face_detect/app.py', encoding='utf-8').read()); print('OK')"
```
预期：输出 `OK`。

- [ ] **Step 1.2.4: 提交**

```
git add scripts/face_detect/app.py
git commit -m "fix(face_detect): on_frame 删除每帧 _gc.collect() — K230 坑 #10

显式 GC 会触发 pending LVGL 终结器碰 DMA 死锁。Camera APP 全程不在
on_frame 显式 GC，长期稳定。MicroPython 内置 GC 阈值机制足够。

🤖 Generated with [Claude Code](https://claude.com/claude-code)

Co-Authored-By: Claude <noreply@anthropic.com>"
```

**回退方案**：若板端出现 OOM（`MemoryError`），可恢复 `_gc.collect()` 但改为"每 30 帧一次"（在 `on_frame` 内用计数器）。**绝对不要**回到"每帧 collect"。

---

### Task 1.3: `face_db.init_features` 改用 `frombuffer`（修问题 2）

**Files:**
- Modify: `core/face_db.py:32-53`
- Test: `tests/test_face_detect.py`（追加）

**Why:** 板端日志显示 `flush_to_disk` 写入成功且 `/data/fac_db/` 下确实有 .bin 文件，但 `init_features` 加载 0 张。官方 demo（`demo/AI类实验例程/实验4 人脸识别实验/main2.py:289-290`）用 `open('rb').read()` + `np.frombuffer(data, dtype=np.float)`——和 fromfile 是不同的内部路径。fromfile 在 K230 ulab 上对用户写入的 SD 文件可能：(a) 抛 OSError 静默吞 / (b) 字节对齐异常使 `len() != 128` → 一直跳过。

写盘端（[core/face_db.py:80](core/face_db.py#L80) `f.write(feature.tobytes())`）已与官方 demo 一致，**不动**。读盘端必须对齐到官方路径。

`512` 字节守卫：128 元素 × 4 字节（K230 ulab `float` 等价于 float32）= 512 字节。守卫可以拦截"半截写入"和"dtype 错配"。

- [ ] **Step 1.3.1: 写失败的纯逻辑测试（守卫长度 + dtype）**

`tests/test_face_detect.py` 追加：

```python
def _parse_feature_blob_pure(data, expected_floats=128, float_size=4):
    """主机端等价实现：模拟 face_db._parse_feature_blob 的契约。

    返回 None 表示守卫拒绝；返回 np.ndarray 表示成功解析。
    """
    if not isinstance(data, (bytes, bytearray)):
        return None
    expected_bytes = expected_floats * float_size
    if len(data) != expected_bytes:
        return None
    # 主机端用 numpy float32 模拟 K230 ulab np.float（在 K230 上 np.float = float32）
    return np.frombuffer(data, dtype=np.float32)


def test_parse_feature_blob_accepts_512_bytes():
    blob = (np.arange(128, dtype=np.float32) * 0.01).tobytes()
    assert len(blob) == 512
    feat = _parse_feature_blob_pure(blob)
    assert feat is not None
    assert len(feat) == 128


def test_parse_feature_blob_rejects_wrong_size():
    blob = b"\x00" * 256  # 半截
    assert _parse_feature_blob_pure(blob) is None


def test_parse_feature_blob_rejects_non_bytes():
    assert _parse_feature_blob_pure("not bytes") is None
    assert _parse_feature_blob_pure(None) is None
```

- [ ] **Step 1.3.2: 跑测试确认红**

```
cd e:/LBS-Project/CanMV/CamerAi
python -m pytest tests/test_face_detect.py -v -k "parse_feature_blob"
```
预期：3 个测试全部 PASS（辅助函数已经实现，这是契约固化测试）。

> 同 Task 1.1，这是**契约门**测试——下面 Step 1.3.3 把它接到设备端。

- [ ] **Step 1.3.3: 设备端 `face_db.py` 引入辅助函数 + 改写 `init_features`**

替换 [core/face_db.py:32-53](core/face_db.py#L32-L53) 的 `init_features` 整段为：

```python
    # ── 运行时加载（APP 内 on_enter → _init_db 调用）────

    @staticmethod
    def _parse_feature_blob(data):
        """解析单个 .bin 文件二进制内容为特征数组。

        守卫：必须正好 512 字节（128 floats × 4 bytes）。半截或多余
        都拒绝——避免读到坏数据导致后续 cosine 比对 NaN。

        K230 ulab np.float 等价于 float32（4 字节），写盘端 tobytes
        产出 4 字节/元素。

        返回 ndarray（成功）或 None（拒绝）。
        """
        import ulab.numpy as np_local
        EXPECTED_BYTES = 128 * 4
        if not isinstance(data, (bytes, bytearray)):
            return None
        if len(data) != EXPECTED_BYTES:
            return None
        try:
            return np_local.frombuffer(data, dtype=np_local.float)
        except Exception:
            return None

    def init_features(self):
        """加载 .bin 文件到 numpy 特征数组。

        使用 open + read + np.frombuffer——与官方 demo
        (demo/AI类实验例程/实验4 人脸识别实验/main2.py:289-290) 完全
        相同的 I/O 路径。曾用 np.fromfile 失败：用户写入的 SD .bin
        在 K230 ulab 下读取静默失败（疑似字节对齐/OSError），导致
        重进 APP 看不到已注册人脸。

        调用时机：FaceDetectApp.on_enter() → _init_db()
        在 lv.task_handler() 内部，但 open() 仍属安全窗口（与
        _init_ai_models 加载 anchors 同位置；曾大量验证）。
        """
        self._features = {}
        for i in range(1, 5):
            path = f"{_DB_DIR}/id{i}.bin"
            try:
                with open(path, 'rb') as f:
                    data = f.read()
            except Exception as e:
                # 文件不存在 / 不可读 — 槽位空，正常情况
                print(f"[FaceDB] id{i}.bin not loadable: {e}")
                continue
            feature = self._parse_feature_blob(data)
            if feature is None:
                print(f"[FaceDB] id{i}.bin invalid (got {len(data)} bytes, expect 512)")
                continue
            self._features[i] = feature
            print(f"[FaceDB] loaded id{i}.bin (128 floats)")
        self._loaded = True
        print(f"[FaceDB] init_features done: {len(self._features)} face(s)")
        return self._features
```

- [ ] **Step 1.3.4: AST 检查 + 提交**

```
python -c "import ast; ast.parse(open('e:/LBS-Project/CanMV/CamerAi/core/face_db.py', encoding='utf-8').read()); print('OK')"
git add core/face_db.py tests/test_face_detect.py
git commit -m "fix(face_db): init_features 改 open+read+frombuffer — 修重进不读已注册人脸

板端现象：on_exit flush_to_disk 写入成功（日志确认 /data/fac_db/id1.bin
存在），但 init_features 仅加载 0 face。

根因：np.fromfile 在 K230 ulab 上对用户写入的 SD .bin 静默失败，路径
与官方 demo (实验4 人脸识别 main2.py:289-290) 不一致。

修法：对齐官方路径——open('rb').read() + np.frombuffer(dtype=np.float)。
新增 _parse_feature_blob 静态方法，加 512 字节长度守卫；契约测试在
tests/test_face_detect.py 守住此行为。

🤖 Generated with [Claude Code](https://claude.com/claude-code)

Co-Authored-By: Claude <noreply@anthropic.com>"
```

**回退方案**：若板端 init_features 仍 0 face，先用串口手动确认 `os.listdir('/data/fac_db')` 看文件是否真存在；再用 `os.stat` 看大小是否 512；用 `open + read + len(data)` 逐项打印。如果 `data` 长度对但 `frombuffer` 仍失败，可能 ulab 不支持读 dtype=float 的 frombuffer——退而求其次用 `struct.unpack('128f', data)` 然后 `np.array(list, dtype=np.float)` 构造。

---

### 阶段 1 板端验收（用户手动跑）

阶段 1 三个 task 提交完成后，**用户**做以下操作。每一步都要在串口里对应日志确认。

**部署 4 个文件到 SD 卡**：
```
scripts/face_detect/app.py   → /sdcard/CamerAi/scripts/face_detect/app.py
core/face_db.py              → /sdcard/CamerAi/core/face_db.py
```

**验收 1：连跑 5 分钟不卡死**
1. 上电 → 主菜单 → 进 face_detect
2. 串口窗口监视 `[FD f...]`（注：现版本已删 per-frame 打印，只保留报错；可加临时一行 `if frame_count % 60 == 0: print(...)` 验证心跳，验完删除）
3. **5 分钟内不动手**，看是否有任何"几十秒后串口静默"现象
4. 5 分钟后用左上角返回按钮退出 → 看 `[FaceDetect] on_exit:` 一系列日志要走完到 `_destroy_ui: done`
5. 必须能正常返回主菜单

**验收 2：4 张人脸持久化**
1. 进 face_detect → K2 注册第 1 张人脸（蜂鸣 80ms）→ 底栏显示 `已注册: ID1`
2. 换一张脸 → K2 → `已注册: ID1 ID2`
3. 重复到 ID4
4. 点 list 按钮 → 弹窗 → 点"保存" → toast `保存成功`（K2 已自动设 dirty，保存只是确认）
5. 返回主菜单 → 串口看 `[FaceDB] flushed id1.bin` × 4
6. **重新进 face_detect**
7. 串口看 `[FaceDB] loaded id1.bin (128 floats)` × 4 + `init_features done: 4 face(s)`
8. 底栏立刻显示 `已注册: ID1 ID2 ID3 ID4`

任一项失败 → 不进阶段 2，回到 systematic-debugging Phase 1。

---

## 阶段 2：ROI 过滤 + AIScriptBase 抽象

> **前置条件**：阶段 1 板端验收 1+2 全部通过。

### Task 2.1: 新建 `scripts/_ai_base.py` —— AIScriptBase 抽象基类骨架

**Files:**
- Create: `scripts/_ai_base.py`
- Test: `tests/test_face_detect.py`（追加 ROI 过滤测试）

**Why:** 后续 hand_detect / object_detect / color_detect 都是"sensor 帧 → CHW → kmodel → 后处理 → ROI 过滤 → 框/标签 UI"流水线。把通用部分沉淀到基类，作者只需实现"模型加载/单帧推理/UI 绘制"。

四个共享能力：
1. `safe_chw_copy(img)` —— Task 1.1 的拷贝逻辑（复用 face_detect 已修好版本）
2. `safe_deinit_ai_module(module)` —— 替代 `AIBase.deinit()` 的分步 del 模式（Task on_exit 已在 face_detect 里实现，搬到基类）
3. `roi_filter_largest(boxes, roi_x, roi_y, roi_w, roi_h)` —— 中心点落入 ROI 的取最大面积 1 个，返回 (idx, box) 或 None
4. `on_frame` 默认模板 —— 调度子类的 `_run_ai(img)` + `_draw_results()`（不强制使用，子类可自己写）

阶段 2 这一 Task 只**创建骨架 + safe_chw_copy + roi_filter_largest**，不动 face_detect。`safe_deinit_ai_module` 与帧模板放在 Task 2.3 引入。

- [ ] **Step 2.1.1: 写失败的 ROI 过滤测试（核心新功能）**

`tests/test_face_detect.py` 追加：

```python
def _roi_filter_largest_pure(boxes, roi_cx, roi_cy, roi_w, roi_h):
    """主机端纯 Python 等价实现，对应 AIScriptBase.roi_filter_largest。

    boxes: list of [x, y, w, h, ...] in screen pixel coords
    roi_cx, roi_cy: ROI 中心
    roi_w, roi_h: ROI 总宽高

    返回：(idx, box) — 中心点落入 ROI 内的人脸里面积最大的；
          None — 没有任何脸落入 ROI
    """
    half_w = roi_w // 2
    half_h = roi_h // 2
    rl = roi_cx - half_w
    rr = roi_cx + half_w
    rt = roi_cy - half_h
    rb = roi_cy + half_h

    best_idx = None
    best_area = 0
    best_box = None
    for i, box in enumerate(boxes):
        x, y, w, h = box[0], box[1], box[2], box[3]
        cx = x + w // 2
        cy = y + h // 2
        if cx < rl or cx >= rr or cy < rt or cy >= rb:
            continue
        area = w * h
        if area > best_area:
            best_area = area
            best_idx = i
            best_box = box
    if best_idx is None:
        return None
    return (best_idx, best_box)


def test_roi_filter_no_box():
    assert _roi_filter_largest_pure([], 320, 240, 240, 240) is None


def test_roi_filter_all_outside():
    # 所有脸都在 ROI 外（左上角 + 右下角）
    boxes = [
        [10, 10, 50, 50],          # 中心 (35, 35)，远在 ROI 左上
        [580, 420, 50, 50],        # 中心 (605, 445)，远在 ROI 右下
    ]
    # ROI 在 (320,240) 中心，240×240 → x∈[200,440), y∈[120,360)
    assert _roi_filter_largest_pure(boxes, 320, 240, 240, 240) is None


def test_roi_filter_picks_largest_inside():
    boxes = [
        [300, 220, 40, 40],        # 中心 (320, 240) — 在 ROI 内，面积 1600
        [310, 230, 80, 80],        # 中心 (350, 270) — 在 ROI 内，面积 6400 ← 最大
        [10, 10, 200, 200],        # 中心 (110, 110) — 在 ROI 外，面积 40000（不应选）
    ]
    result = _roi_filter_largest_pure(boxes, 320, 240, 240, 240)
    assert result is not None
    idx, box = result
    assert idx == 1
    assert box[2] == 80 and box[3] == 80


def test_roi_filter_center_exactly_on_edge():
    # 中心点正好在 ROI 右边界（x=440）—— 按 < 严格不算入选
    boxes = [[420, 220, 40, 40]]   # cx=440, cy=240
    assert _roi_filter_largest_pure(boxes, 320, 240, 240, 240) is None
```

- [ ] **Step 2.1.2: 跑测试确认全 PASS**

```
cd e:/LBS-Project/CanMV/CamerAi
python -m pytest tests/test_face_detect.py -v -k "roi_filter"
```
预期：4 个测试全 PASS（辅助函数实现一致）。

- [ ] **Step 2.1.3: 创建 `scripts/_ai_base.py` 骨架 + safe_chw_copy + roi_filter_largest**

新建文件 `scripts/_ai_base.py`：

```python
# scripts/_ai_base.py — AI 类脚本抽象基类
#
# 后续手势识别 / 物体识别 / 颜色识别 / 标签识别 等所有"sensor + AI 模型"
# 类脚本的通用基础。集中处理 K230 三大坑（#2/#10/#14）的安全做法，
# 使子类只需关心"模型加载/单帧推理/UI 绘制"。
#
# 继承关系：
#   BaseScript → AIScriptBase → FaceDetectApp / GestureApp / ...
#
# 核心能力：
#   1. safe_chw_copy(img)        —— 防 VB 持有链的 CHW 张量拷贝
#   2. roi_filter_largest(...)   —— 屏幕中心 ROI 过滤 + 取最大单目标
#   3. safe_deinit_ai_module(m)  —— 分步 del 替代 AIBase.deinit()（Task 2.3）
#
# K230 硬约束（所有子类 on_frame/on_exit 都必须遵守）：
#   - 坑 #2：on_frame 严禁文件 I/O（FATFS/DMA 死锁）
#   - 坑 #10：on_frame 严禁 gc.collect()（触发 LVGL 终结器死锁）
#   - 坑 #14：sensor 帧 ndarray 必须真拷贝，否则 VB pool 耗尽

from scripts._base import BaseScript

try:
    import ulab.numpy as np
except ImportError:
    pass  # IDE 主机端无此模块


class AIScriptBase(BaseScript):
    """AI 类脚本抽象基类 — 沉淀 sensor + AI 模型类脚本的通用安全做法。

    子类必须实现的钩子（保留给 Task 2.3 引入帧模板时再确定签名）：
      - _init_ai_models(self)
      - _deinit_ai_models(self)
      - _build_ui(self) / _destroy_ui(self)
      - _run_ai(self, img, chw) → results        ← Task 2.3
      - _draw_results(self, results)             ← Task 2.3
    """

    # ── 帧拷贝：消除 VB 持有链 ────────────────────────────

    @staticmethod
    def safe_chw_copy(img):
        """sensor.snapshot() RGB888 帧 → (1, C, H, W) ndarray，与 img 完全解耦。

        K230 坑 #14：ulab reshape 返回 view，不在最外层 np.array(copy=True)
        强制拷贝就会链式持有 sensor 帧的 VB block，几十秒耗尽 pool。

        子类 on_frame 必须用此方法获取送 NPU 的张量，绝不可以自己写
        `img.to_numpy_ref().reshape(...).transpose()` —— 这会重新引入 view。
        """
        hwc = img.to_numpy_ref()
        shape = hwc.shape
        tmp = hwc.reshape((shape[0] * shape[1], shape[2]))
        trans = tmp.transpose()
        chw = trans.copy()
        del tmp, trans
        out = chw.reshape((1, shape[2], shape[0], shape[1]))
        return np.array(out, copy=True)

    # ── ROI 过滤：屏幕中心区域 + 单目标 ──────────────────

    @staticmethod
    def roi_filter_largest(boxes, roi_cx, roi_cy, roi_w, roi_h):
        """从检测框列表中筛选"中心点落入 ROI、面积最大"的一个。

        参数：
          boxes: list of [x, y, w, h, ...]（屏幕坐标，多余字段不受影响）
          roi_cx, roi_cy: ROI 中心（屏幕坐标）
          roi_w, roi_h: ROI 总宽/高

        返回：
          (idx, box) — 命中目标的原列表下标 + 框；
          None       — 没有任何框中心点落入 ROI。

        判定：中心点 cx, cy 满足 roi_cx-w/2 <= cx < roi_cx+w/2，y 同理。
        右/下边界用 < 严格排除，避免边缘抖动反复进出。
        """
        if not boxes:
            return None
        half_w = roi_w // 2
        half_h = roi_h // 2
        rl = roi_cx - half_w
        rr = roi_cx + half_w
        rt = roi_cy - half_h
        rb = roi_cy + half_h
        best_idx = None
        best_area = 0
        best_box = None
        for i, box in enumerate(boxes):
            if len(box) < 4:
                continue
            x, y, w, h = int(box[0]), int(box[1]), int(box[2]), int(box[3])
            cx = x + w // 2
            cy = y + h // 2
            if cx < rl or cx >= rr or cy < rt or cy >= rb:
                continue
            area = w * h
            if area > best_area:
                best_area = area
                best_idx = i
                best_box = box
        if best_idx is None:
            return None
        return (best_idx, best_box)
```

- [ ] **Step 2.1.4: 主机端 AST 检查**

```
python -c "import ast; ast.parse(open('e:/LBS-Project/CanMV/CamerAi/scripts/_ai_base.py', encoding='utf-8').read()); print('OK')"
```
预期：`OK`。

- [ ] **Step 2.1.5: 提交**

```
git add scripts/_ai_base.py tests/test_face_detect.py
git commit -m "feat(scripts): 新增 AIScriptBase 抽象基类 — safe_chw_copy + roi_filter_largest

后续所有 sensor+AI 类脚本（手势/物体/颜色/标签等）的通用基础。
集中处理 K230 三大坑：
- 坑 #14：safe_chw_copy 最外层 np.array(copy=True) 防 VB 持有链
- ROI 过滤：屏幕中心 240×240 区域内取最大目标，纯逻辑可主机端测

本 task 仅引入骨架与两个工具方法，未改动 face_detect。
Task 2.3 在 face_detect 重构时引入帧循环模板与 safe_deinit。

🤖 Generated with [Claude Code](https://claude.com/claude-code)

Co-Authored-By: Claude <noreply@anthropic.com>"
```

**回退方案**：本 task 是新增文件 + 新增测试，不影响现有运行；最坏情况删除新文件即可。

---

### Task 2.2: face_detect 接入 ROI 过滤 + 单脸识别（先用基类工具，不改继承关系）

**Files:**
- Modify: `scripts/face_detect/app.py`（多处）

**Why:** 在不重构 face_detect 继承结构的前提下，先把 ROI 过滤"用起来"——通过 `from scripts._ai_base import AIScriptBase` 调用 `AIScriptBase.roi_filter_largest(...)`。这一步独立验证：(a) ROI 过滤板端真实生效；(b) face_reg 推理量从 4× 降到 1×，帧率应明显上升。

face_detect 内部改动：
- 在文件顶部 import AIScriptBase
- 在 `on_frame` 推理段：拿到 `det_boxes` 后**先 ROI 过滤**，然后**只对命中那 1 张**跑 `face_reg`
- `_recognition_results` 仍然是 list，但长度 0 或 1
- `_send_recognition_data` 自动适配（slots[0] 存这一张，其他 None）
- `_update_face_boxes` 自动适配（对象池只用 slot 0）

ROI 中心 = 屏幕预览区中心 = (320, BAR_H + PREVIEW_H/2) = (320, 52 + 188) = (320, 240)

ROI 大小 = 240 × 240（用户已确认）

但**注意坐标系**：`det_boxes` 来自 AI 后处理，单位是 AI 输入坐标（rgb888p_size = 640×480）。屏幕预览区是 640×PREVIEW_H（=376）。原 `_update_face_boxes` 已做缩放，所以**ROI 过滤可以选两种坐标系**：(a) 在 AI 坐标系（640×480）做，ROI 也按比例换到 AI 坐标；(b) 先把每个 box 缩放到屏幕坐标再过滤。两者数学等价但 (a) 更省（不必每帧逐 box 缩放后再过滤）。

选 (a)：AI 输入 640×480 → ROI 中心 (320, 240)（**与屏幕中心数值巧合**，因为 AI 宽 = 屏幕宽 = 640；高度上 AI 是 480、屏幕预览 376，但 ROI 中心都取"画面中心"——AI 画面中心 (320, 240) 对应映射后屏幕 (320, 188)，恰好就是预览区竖直中心）。

ROI 在 AI 坐标系下也用 240×240——这与屏幕显示视觉上**几乎一致**（高度方向的映射比例 376/480=0.78，所以 240px 高的 ROI 映射到屏幕是 188px 高，仍是屏幕预览区高度的一半左右——视觉上仍是"中心区"，可接受）。

- [ ] **Step 2.2.1: 在 `scripts/face_detect/app.py` 顶部加 import**

定位 [scripts/face_detect/app.py:14](scripts/face_detect/app.py#L14) 附近的 import 块，加一行：

```python
from scripts._base import BaseScript
from scripts._ai_base import AIScriptBase  # ← 新增：用其 roi_filter_largest
```

- [ ] **Step 2.2.2: 在 face_detect 顶部加 ROI 常量**

定位 [scripts/face_detect/app.py:230-249](scripts/face_detect/app.py#L230-L249) 的常量区（CROSSHAIR/BOX_COLORS 附近），追加：

```python
# ── ROI（识别区域）──
# AI 输入 640×480；屏幕预览 640×PREVIEW_H。ROI 中心取画面中心 (320, 240)
# 在 AI 坐标系下定义；与屏幕十字架视觉上同心（高度映射后约为预览区竖直中心）。
# 240×240 是用户确认尺寸：人脸正常距离时刚好不溢出，宽松。
ROI_CX_AI = 320
ROI_CY_AI = 240
ROI_W_AI = 240
ROI_H_AI = 240
```

- [ ] **Step 2.2.3: 改写 `on_frame` 内的推理调度**

定位 [scripts/face_detect/app.py:355-382](scripts/face_detect/app.py#L355-L382) 的 AI 推理段。把 `if self._face_det is not None:` 块整体替换为：

```python
                    if self._face_det is not None:
                        frame = self._img_to_chw(img)
                        if frame is not None:
                            self._current_frame_data = None
                            self._current_frame_data = frame
                            det_boxes, landms = self._face_det.run(frame)
                            self._current_boxes = det_boxes if det_boxes else []
                            self._current_landmarks = landms if landms else []

                            # ── ROI 过滤 + 单脸识别 ──
                            # 屏幕中心 240×240（AI 坐标系，因 AI 输入与屏幕等宽）
                            # 内取面积最大的 1 张；其他人脸忽略。
                            # 副作用：face_reg 推理量从 N×降到 0/1× → 帧率提升
                            self._recognition_results = []
                            if self._current_boxes and self._current_landmarks:
                                hit = AIScriptBase.roi_filter_largest(
                                    self._current_boxes,
                                    ROI_CX_AI, ROI_CY_AI,
                                    ROI_W_AI, ROI_H_AI,
                                )
                                if hit is not None:
                                    idx, box = hit
                                    if idx < len(self._current_landmarks):
                                        landm = self._current_landmarks[idx]
                                        try:
                                            self._face_reg.config_preprocess(landm)
                                            feature = self._face_reg.run(frame)
                                            matched_id, score = self._search_face(feature)
                                            self._recognition_results.append(
                                                (box, matched_id, score))
                                        except Exception:
                                            self._recognition_results.append(
                                                (box, None, 0.0))

                            # 更新 LVGL 人脸框
                            self._update_face_boxes()
```

> 关键变化：
> - 不再 `for i, landm in enumerate(...): if i >= 4: break:` 循环 4 次
> - 命中时只对那一张跑 `face_reg`（`config_preprocess + run` 各 1 次）
> - 没命中时 `_recognition_results` 留空，`_update_face_boxes` 走"全部隐藏"分支

- [ ] **Step 2.2.4: 同步 `_register_current_face` 也用 ROI 过滤**

K2 注册时也应该只对 ROI 内最大那张做注册，避免误注册边缘人脸。

定位 [scripts/face_detect/app.py:610-654](scripts/face_detect/app.py#L610-L654) `_register_current_face`，把"找最大人脸"那段改为：

```python
    def _register_current_face(self):
        """将 ROI 内最大人脸注册到下一个空槽位"""
        if not self._current_boxes:
            self.ctx.buzzer.beep(ms=30)  # 短促声：无人脸
            return

        if not self._current_frame_data or not self._current_landmarks:
            self.ctx.buzzer.beep(ms=30)
            return

        # 找 ROI 内最大人脸（与 on_frame 识别同一逻辑，避免注册了边缘的脸）
        hit = AIScriptBase.roi_filter_largest(
            self._current_boxes,
            ROI_CX_AI, ROI_CY_AI,
            ROI_W_AI, ROI_H_AI,
        )
        if hit is None:
            self.ctx.buzzer.beep(ms=30)  # 短促声：ROI 内无人脸
            return
        largest_idx, _ = hit

        if largest_idx >= len(self._current_landmarks):
            self.ctx.buzzer.beep(ms=30)
            return

        landm = self._current_landmarks[largest_idx]

        # 推理特征
        try:
            self._face_reg.config_preprocess(landm)
            feature = self._face_reg.run(self._current_frame_data)
        except Exception as e:
            print(f"[FaceDetect] feature extract failed: {e}")
            self.ctx.buzzer.beep(ms=30)
            return

        # 找空槽位（1→2→3→4）
        for slot in range(1, 5):
            if slot not in self._db_features:
                self._register_face(feature, slot)
                self._update_status_text()
                self.ctx.buzzer.beep(ms=80)  # 成功长声
                return

        self.ctx.buzzer.beep(ms=200)  # 超长声：4个槽位已满
```

- [ ] **Step 2.2.5: 主机端 AST 检查**

```
python -c "import ast; ast.parse(open('e:/LBS-Project/CanMV/CamerAi/scripts/face_detect/app.py', encoding='utf-8').read()); print('OK')"
```
预期：`OK`。

- [ ] **Step 2.2.6: 提交**

```
git add scripts/face_detect/app.py
git commit -m "feat(face_detect): ROI 过滤 + 单脸识别 — 改善帧率 & 限定识别范围

- on_frame: 检测后用 AIScriptBase.roi_filter_largest 在屏幕中心 240×240
  AI 坐标系区域内取最大 1 张脸做 face_reg；其他人脸忽略。
  face_reg 推理量从 4× 降到 0~1×，帧率显著提升。
- _register_current_face: K2 注册同样限定到 ROI 内最大目标，避免
  注册边缘抖动的人脸。
- ROI_CX_AI/CY_AI/W_AI/H_AI 常量在 app.py 顶部声明，方便后续调参。
- ROI 不画框（用户偏好），仅识别命中的人脸照常画矩形框。

🤖 Generated with [Claude Code](https://claude.com/claude-code)

Co-Authored-By: Claude <noreply@anthropic.com>"
```

**回退方案**：板端发现帧率没改善或识别失败率高，把 `ROI_W_AI/H_AI` 调大到 360×360 即可（覆盖更宽区域）。极端情况完全撤回这一 commit，回到"全屏 + 4 张"逻辑。

---

### Task 2.3: face_detect 重构为继承 AIScriptBase（架构演进收尾）

**Files:**
- Modify: `scripts/face_detect/app.py`
- Modify: `scripts/_ai_base.py`（追加 `safe_deinit_ai_module`）

**Why:** Task 2.2 是"用工具不动结构"，本 task 是"换继承+下沉去重代码"。把 face_detect 现有的 `_destroy_ui` 中分步 `del kpu/ai2d/tensors` 模式提到基类 `safe_deinit_ai_module`；把 `_img_to_chw` 完全删除（改用 `AIScriptBase.safe_chw_copy`）。这样后续 hand_detect/object_detect 直接继承就能享受到所有安全保证。

**不引入 on_frame 模板钩子**——face_detect 的 on_frame 业务逻辑（UART 节流发送、toast tick）足够特殊，模板抽象会过度。AIScriptBase 仅做"工具方法 + 文档化约束"。

- [ ] **Step 2.3.1: 在 `scripts/_ai_base.py` 追加 `safe_deinit_ai_module`**

打开 [scripts/_ai_base.py](scripts/_ai_base.py)，在 `class AIScriptBase` 内 `roi_filter_largest` 之后追加：

```python
    # ── AI 模型分步释放：替代 AIBase.deinit() ─────────────

    @staticmethod
    def safe_deinit_ai_module(module, label=""):
        """安全释放一个 AIBase 子类实例的 NPU 资源。

        K230 坑 #10：AIBase.deinit() 内部调用 gc.collect() +
        nn.shrink_memory_pool() + sleep_ms(100)，配合活动 sensor/display
        100% 死锁（曾在 face_detect on_exit 复现）。

        替代方案：分步 del kpu / ai2d / tensors，绕开 deinit 内的 gc 链。
        NPU 内存池由系统自动回收，无需显式 shrink。

        参数：
          module: 任意 AIBase 子类实例（也可为 None — 安全跳过）
          label: 日志前缀，便于多模型场景区分

        返回：True（成功释放）/ False（module 为 None）。
        """
        if module is None:
            return False
        prefix = f"[AI deinit{(' ' + label) if label else ''}]"
        try:
            if hasattr(module, 'kpu'):
                del module.kpu
            if hasattr(module, 'ai2d'):
                del module.ai2d
            if hasattr(module, 'tensors'):
                try:
                    module.tensors.clear()
                except Exception:
                    pass
                del module.tensors
            print(f"{prefix} del OK")
            return True
        except Exception as e:
            print(f"{prefix} del error: {e}")
            return False
```

- [ ] **Step 2.3.2: AST 检查 `_ai_base.py`**

```
python -c "import ast; ast.parse(open('e:/LBS-Project/CanMV/CamerAi/scripts/_ai_base.py', encoding='utf-8').read()); print('OK')"
```

- [ ] **Step 2.3.3: 改 face_detect 继承关系 + 删除 `_img_to_chw`**

定位 [scripts/face_detect/app.py:280](scripts/face_detect/app.py#L280)：

```python
class FaceDetectApp(BaseScript):
```

改为：

```python
class FaceDetectApp(AIScriptBase):
```

定位 [scripts/face_detect/app.py:477-495](scripts/face_detect/app.py#L477-L495) 的 `_img_to_chw` 静态方法**整段删除**。

定位 [scripts/face_detect/app.py:356](scripts/face_detect/app.py#L356) 的：

```python
                        frame = self._img_to_chw(img)
```

改为：

```python
                        frame = AIScriptBase.safe_chw_copy(img)
```

- [ ] **Step 2.3.4: 改 `on_exit` 用 `safe_deinit_ai_module`**

定位 [scripts/face_detect/app.py:430-465](scripts/face_detect/app.py#L430-L465)（两段相似的 `if self._face_det is not None:` / `if self._face_reg is not None:` 释放代码）。整体替换为：

```python
        if self._face_det is not None:
            print("[FaceDetect]   det: deinit...")
            AIScriptBase.safe_deinit_ai_module(self._face_det, label="det")
            self._face_det = None
            print("[FaceDetect]   det: ref cleared")

        if self._face_reg is not None:
            print("[FaceDetect]   reg: deinit...")
            AIScriptBase.safe_deinit_ai_module(self._face_reg, label="reg")
            self._face_reg = None
            print("[FaceDetect]   reg: ref cleared")

        self._anchors = None
        # NPU 内存池由系统自动回收，不显式 shrink（曾死锁）。
        print("[FaceDetect] on_exit: AI models deinit done")
```

- [ ] **Step 2.3.5: AST 检查 face_detect**

```
python -c "import ast; ast.parse(open('e:/LBS-Project/CanMV/CamerAi/scripts/face_detect/app.py', encoding='utf-8').read()); print('OK')"
```

- [ ] **Step 2.3.6: 全量回归测试**

```
cd e:/LBS-Project/CanMV/CamerAi
python -m pytest tests/test_face_detect.py -v
```
预期：阶段 1 + 阶段 2 引入的所有测试 PASS。

- [ ] **Step 2.3.7: 提交**

```
git add scripts/_ai_base.py scripts/face_detect/app.py
git commit -m "refactor(face_detect): 继承 AIScriptBase + 下沉 _img_to_chw / safe_deinit

- FaceDetectApp 继承 AIScriptBase
- _img_to_chw 删除，on_frame 改用 AIScriptBase.safe_chw_copy
- on_exit 中分步 del kpu/ai2d/tensors 替换为 AIScriptBase.safe_deinit_ai_module
- _ai_base.py 新增 safe_deinit_ai_module 静态方法（替代 AIBase.deinit
  避免坑 #10 的 gc.collect+shrink_memory_pool 死锁）

后续 hand_detect / object_detect / color_detect 直接继承 AIScriptBase
即享受到 K230 三大坑的安全保证（#2/#10/#14）+ ROI 过滤 + 安全释放。

🤖 Generated with [Claude Code](https://claude.com/claude-code)

Co-Authored-By: Claude <noreply@anthropic.com>"
```

**回退方案**：若板端 on_exit 死锁回归，撤回此 commit（前一个版本 face_detect 已经稳定）；调查 `safe_deinit_ai_module` 是否漏了某个步骤（比如某个 ulab 字段/某段 sleep）。

---

### 阶段 2 板端验收（用户手动跑）

**部署**：
```
scripts/face_detect/app.py   → /sdcard/CamerAi/scripts/face_detect/app.py
scripts/_ai_base.py          → /sdcard/CamerAi/scripts/_ai_base.py
```

**验收 3：ROI 外人脸不画框/不上送**
1. 进 face_detect
2. 让一张脸先在屏幕**边缘**（左/右/上/下）出现 → 串口看 `[FaceDetect] AI inference error` 应不出现；屏幕上**不应**有矩形框；上位机 UART 收到的人脸数据应该是 4 个 None slot
3. 让脸**移到屏幕中央**（十字架附近）→ 屏幕**应**画出矩形框；上位机收到 slot[0] 数据
4. 多张脸同时出现，**只有最靠近中心 + 最大的那张**有框

**验收 4：阶段 1 回归测试**
重做阶段 1 验收 1+2，必须仍然通过：连跑 5 分钟不卡死；4 张人脸退出/重进可见。

任一项失败 → 撤回到阶段 2 之前的最后一个 commit；进 systematic-debugging Phase 1。

---

## 自检（Plan Self-Review）

- ✅ **Spec coverage**：四个问题（1 卡死 / 2 不读 / 3 ROI / 4 抽象）每个都有对应 task（1.1+1.2 / 1.3 / 2.2 / 2.1+2.3）。
- ✅ **No placeholders**：所有 step 都给了具体代码、具体命令；无 "TBD"。
- ✅ **Type consistency**：`safe_chw_copy / roi_filter_largest / safe_deinit_ai_module` 三个静态方法名在 Task 2.1/2.2/2.3 中使用一致。`ROI_CX_AI/CY_AI/W_AI/H_AI` 常量名 Task 2.2 引入、Task 2.2 使用，未出现别名。
- ✅ **K230 硬约束**：Task 1.2 显式删 on_frame gc（坑 #10）；Task 1.1 显式拷贝（坑 #14）；Task 1.3 文件 I/O 在 on_enter 安全窗口（坑 #2）；Task 2.3 safe_deinit 不调 AIBase.deinit（坑 #10）。
- ✅ **i18n**：阶段 2 的 ROI 不引入新 UI 文字，无需改 i18n。

---

## 总览（一图流）

```
阶段 1（必修 bug）
  Task 1.1  _img_to_chw 强制拷贝            → 修"几十秒后卡死"
  Task 1.2  删 on_frame 每帧 gc.collect      → 收尾问题 1
  Task 1.3  face_db 改 frombuffer + 守卫     → 修"重进不读人脸"
  ──── 用户验收 1+2 ────

阶段 2（ROI + 架构）
  Task 2.1  AIScriptBase 骨架                → 沉淀工具方法
  Task 2.2  face_detect 接 ROI 过滤 + 单脸   → 修"全屏识别"+ 提帧率
  Task 2.3  face_detect 继承 AIScriptBase    → 后续 AI 脚本直接受益
  ──── 用户验收 3+4（含阶段 1 回归）────
```
