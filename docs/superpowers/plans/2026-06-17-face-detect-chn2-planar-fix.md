# face_detect chn2 PLANAR 数据路径修复 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 face_detect 的 AI 帧数据路径从"chn0 预览帧软件转 CHW"改为"chn2 RGB888_PLANAR 硬件直出 CHW"，对齐官方 demo 双通道架构，根治连跑卡死。

**Architecture:** chn0(VGA/RGB888) 仅预览 `snapshot()+show_image(OSD1)`，chn2(1024×768/RGB888_PLANAR) 仅 AI `snapshot(chn=CAM_CHN_ID_2).to_numpy_ref()` 直接得 planar CHW 喂 NPU。删除 `_img_to_chw` 软件转置黑盒。保留每帧 `gc.collect()`（坑#16）+ 人脸框对象池（坑#10）+ `_prev_img` 预览 DMA 安全区。

**Tech Stack:** MicroPython + LVGL v8 + ulab.numpy + nncase_runtime（NPU）+ aidemo + K230D BOX。所有 UI 文字走 `lang.t()`。

**Spec:** [docs/superpowers/specs/2026-06-17-face-detect-chn2-planar-fix-design.md](docs/superpowers/specs/2026-06-17-face-detect-chn2-planar-fix-design.md)

---

## K230 硬约束（实施前必读）

1. **坑 #2 — FATFS/DMA**：`lv.task_handler()` 启动后做 SD 卡 I/O 永久死锁。安全窗口仅 `main.py` 启动期首次 task_handler 之前 + 脚本 `on_enter`。**禁止区**：on_frame、按钮回调。本次无 on_frame 文件 I/O。
2. **坑 #10 — gc 触发 LVGL 终结器**：on_frame 不 create/delete LVGL 对象（用预建对象池），故每帧 `gc.collect()` 安全。
3. **坑 #14 — VB pool 不归还**：PLANAR `to_numpy_ref()` 是 chn2 独立 buffer，与 chn0 预览帧无 VB 共享；`_prev_img` 持有 chn0 帧 1 帧保 DMA 安全。
4. **坑 #15 — 多通道启动期声明**：chn2 在 `MediaManager.init()` 前声明，本次只改 pixformat/分辨率，不改声明时机。
5. **坑 #16 — AI 循环每帧 gc**：保留每帧 `gc.collect()`，AI 帧独立 buffer 使 gc 安全。

---

## File Structure

| 文件 | 角色 |
|------|------|
| `hw/lcd.py` | chn2 pixformat 改回 `PIXEL_FORMAT_RGB_888_PLANAR`（补 import），分辨率 1024×768 |
| `scripts/face_detect/app.py` | on_frame AI 帧改从 chn2 取；删 `_img_to_chw`；`rgb888p` 改 1024×768；main 循环恢复 |
| `main.py` | 恢复原主循环（移除临时隔离实验的"运行期跳过 task_handler"）|
| `tests/test_face_detect.py` | 删 2 个 `_img_to_chw` 契约测试；新增 2 个 chn2 契约测试；收紧 chn2 pixformat 断言 |

---

## Task 1: lcd.py chn2 改回 RGB888_PLANAR

**Files:**
- Modify: `hw/lcd.py:6`（import）+ `hw/lcd.py:65-72`（chn2 配置）
- Test: `tests/test_face_detect.py`（先改测试，TDD）

- [ ] **Step 1.1: 改测试 `test_lcd_declares_chn2_ai_channel` 收紧为 PLANAR 断言**

打开 [tests/test_face_detect.py:304-339](tests/test_face_detect.py#L304-L339)，把整个 `test_lcd_declares_chn2_ai_channel` 替换为：

```python
def test_lcd_declares_chn2_ai_channel():
    """chn2 必须配 RGB888_PLANAR（planar CHW），对齐官方 libs/PipeLine.get_frame()。
    PLANAR 格式 snapshot(chn=CAM_CHN_ID_2).to_numpy_ref() 直接得 planar CHW，
    零软件转置。常量名 PIXEL_FORMAT_RGB_888_PLANAR（非 Sensor.RGB888_PLANAR）。
    """
    tree = _parse(LCD_PATH)
    lcd_cls = _class_node(tree, "LCD")
    init_method = _method_node(lcd_cls, "__init__")

    found_framesize = False
    found_planar = False
    found_ai_chn = False

    for node in ast.walk(init_method):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr == "set_framesize":
                for i, arg in enumerate(node.args):
                    if i >= 1 and isinstance(arg, ast.Name) and "CHN_ID_2" in arg.id:
                        found_framesize = True
                for kw in node.keywords:
                    if kw.arg == "chn" and isinstance(kw.value, ast.Name):
                        if "CHN_ID_2" in kw.value.id:
                            found_framesize = True
            if node.func.attr == "set_pixformat":
                # chn2 的 set_pixformat 第一个参数必须是 PIXEL_FORMAT_RGB_888_PLANAR
                for arg in node.args:
                    if isinstance(arg, ast.Name) and "PLANAR" in arg.id:
                        found_planar = True
                # 同时确认是给 chn2 设的
                is_chn2 = False
                for i, arg in enumerate(node.args):
                    if i >= 1 and isinstance(arg, ast.Name) and "CHN_ID_2" in arg.id:
                        is_chn2 = True
                for kw in node.keywords:
                    if kw.arg == "chn" and isinstance(kw.value, ast.Name):
                        if "CHN_ID_2" in kw.value.id:
                            is_chn2 = True
                if not is_chn2:
                    found_planar = False
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Attribute) and target.attr == "ai_chn":
                    found_ai_chn = True

    assert found_framesize, "LCD.__init__ must set_framesize for chn2"
    assert found_planar, \
        "LCD.__init__ must set chn2 pixformat to PIXEL_FORMAT_RGB_888_PLANAR " \
        "(planar CHW, aligns with libs/PipeLine.get_frame)"
    assert found_ai_chn, "LCD must set self.ai_chn = CAM_CHN_ID_2"
```

- [ ] **Step 1.2: 跑测试确认红**

```
cd e:/LBS-Project/CanMV/CamerAi
python tests/test_face_detect.py
```

预期：`test_lcd_declares_chn2_ai_channel` **FAIL**（当前 chn2 是 `Sensor.RGB888`，无 PLANAR 常量），其余 PASS。

- [ ] **Step 1.3: 修改 lcd.py import 补 PLANAR 常量**

[hw/lcd.py:6](hw/lcd.py#L6) 当前：

```python
from media.sensor import Sensor, CAM_CHN_ID_0, CAM_CHN_ID_1, CAM_CHN_ID_2
```

改为：

```python
from media.sensor import (Sensor, CAM_CHN_ID_0, CAM_CHN_ID_1, CAM_CHN_ID_2,
                          PIXEL_FORMAT_RGB_888_PLANAR)
```

- [ ] **Step 1.4: 修改 lcd.py chn2 配置改回 PLANAR + 注释**

[hw/lcd.py:65-72](hw/lcd.py#L65-L72) 当前：

```python
        # chn2 — AI推理输入：RGB888，XGA 分辨率。
        # K230 Sensor 无 RGB888_PLANAR 常量；用 RGB888 interleaved，
        # 由 get_ai_frame() 做 HWC→CHW 软件转置得到 planar 格式。
        # 所有 AI 类 APP 共用此通道，必须启动期声明
        # （MediaManager.init() 之后池不可重建，K230 pitfall #15）。
        self.sensor.set_framesize(Sensor.XGA, chn=CAM_CHN_ID_2)
        self.sensor.set_pixformat(Sensor.RGB888, chn=CAM_CHN_ID_2)
```

改为：

```python
        # chn2 — AI推理输入：RGB888_PLANAR（planar CHW），1024×768（XGA）。
        # PLANAR 格式：snapshot(chn=CAM_CHN_ID_2).to_numpy_ref() 直接得到
        # planar CHW，零软件转置，对齐官方 libs/PipeLine.get_frame()。
        # 常量名是 PIXEL_FORMAT_RGB_888_PLANAR（非 Sensor.RGB888_PLANAR——
        # 之前误判常量不存在改成 RGB888 interleaved，导致 face_detect 软件转置
        # CHW 喂 NPU 卡死，见 specs/2026-06-17-face-detect-chn2-planar-fix-design.md）。
        # 所有 AI 类 APP 共用此通道，必须启动期声明
        # （MediaManager.init() 之后池不可重建，K230 pitfall #15）。
        self.sensor.set_framesize(Sensor.XGA, chn=CAM_CHN_ID_2)
        self.sensor.set_pixformat(PIXEL_FORMAT_RGB_888_PLANAR, chn=CAM_CHN_ID_2)
```

- [ ] **Step 1.5: 同步更新 get_ai_frame() 注释（PLANAR 无需软件转置）**

[hw/lcd.py:132-154](hw/lcd.py#L132-L154) 的 `get_ai_frame()` 当前注释说"由 get_ai_frame() 做 HWC→CHW 软件转置"，且代码做了 `to_rgb888()+reshape/transpose`。但**本 task 不改 get_ai_frame 实现**（face_detect 不再调它，camera app 也不调；保留方法以备其他脚本，但其内部软件转置路径已过时）。

只更新其 docstring 头部注释，加一行警告：

打开 `get_ai_frame` 的 docstring（约 [hw/lcd.py:133-139](hw/lcd.py#L133-L139)），在 docstring 第一段后追加：

```python
        ⚠️ 此方法内部用 to_rgb888()+reshape/transpose 软件转置，是 chn2 误配为
        RGB888 interleaved 时期的遗留路径。chn2 已改回 RGB888_PLANAR 后，
        AI 帧应直接 sensor.snapshot(chn=CAM_CHN_ID_2).to_numpy_ref()（见
        face_detect on_frame），无需此方法。保留以备其他脚本，勿新增调用。
```

- [ ] **Step 1.6: AST 检查 + 跑测试确认绿**

```
cd e:/LBS-Project/CanMV/CamerAi
python -c "import ast; ast.parse(open('hw/lcd.py', encoding='utf-8').read()); print('OK')"
python tests/test_face_detect.py
```

预期：AST `OK`；全测试 PASS（含收紧后的 `test_lcd_declares_chn2_ai_channel`）。

- [ ] **Step 1.7: 提交**

```
cd e:/LBS-Project/CanMV/CamerAi
git add hw/lcd.py tests/test_face_detect.py
git commit -m "fix(lcd): chn2 改回 RGB888_PLANAR — 修正误判常量不存在

工作树曾把 chn2 从 PIXEL_FORMAT_RGB_888_PLANAR 改成 Sensor.RGB888(interleaved)，
注释称'K230 Sensor 无 RGB888_PLANAR 常量'——误判。常量名是
PIXEL_FORMAT_RGB_888_PLANAR(from media.sensor import *)，官方 libs/PipeLine.py:96
在用。

chn2 改回 PLANAR 后 snapshot(chn=CAM_CHN_ID_2).to_numpy_ref() 直接得 planar CHW，
对齐官方 demo 双通道架构。face_detect 软件转置 CHW 喂 NPU 卡死的根因。

test_lcd_declares_chn2_ai_channel 收紧为断言 PLANAR 常量。

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## Task 2: face_detect on_frame AI 帧改从 chn2 取

**Files:**
- Modify: `scripts/face_detect/app.py:545-596`（删 `_img_to_chw`）+ `scripts/face_detect/app.py:385-413`（on_frame AI 段）+ `scripts/face_detect/app.py:591`（rgb888p）
- Test: `tests/test_face_detect.py`

- [ ] **Step 2.1: 删 2 个 `_img_to_chw` 契约测试，新增 chn2 契约测试**

打开 [tests/test_face_detect.py:108-154](tests/test_face_detect.py#L108-L154)。删除 `test_face_detect_on_frame_uses_single_snapshot`（约 108-116 行）和 `test_face_detect_img_to_chw_returns_independent_copy`（约 119-154 行）整段。

> 删除理由：`_img_to_chw` 即将删除，其契约测试不再适用；`test_face_detect_on_frame_uses_single_snapshot` 禁止 `get_ai_frame()`（第二次 snapshot）的契约已被新的"必须从 chn2 取"契约取代。新契约见下。

在原位置插入：

```python
# ── Test 2: on_frame AI 帧走 chn2 PLANAR（不再软件转置）──

def test_face_detect_on_frame_uses_chn2_planar_frame():
    """on_frame 必须从 chn2(CAM_CHN_ID_2) 取 AI 帧并 to_numpy_ref() 得 planar CHW，
    不得调用 _img_to_chw（已删除的软件转置黑盒）。

    根因（specs/2026-06-17-face-detect-chn2-planar-fix-design.md）：用 chn0
    预览帧软件转 CHW 喂 NPU 会卡死——软件转置的 ulab buffer 与 nncase tensor
    内存关系是黑盒。改走 chn2 RGB888_PLANAR 硬件直出 CHW，对齐官方 demo。
    """
    tree = _parse(APP_PATH)
    cls = _class_node(tree, "FaceDetectApp")

    # 1. _img_to_chw 必须已删除
    method_names = _method_names(cls)
    assert "_img_to_chw" not in method_names, \
        "_img_to_chw must be DELETED — AI frame now comes from chn2 PLANAR " \
        "via snapshot(chn=CAM_CHN_ID_2).to_numpy_ref()"

    # 2. on_frame 必须有 snapshot(chn=...CHN_ID_2) 调用
    method = _method_node(cls, "on_frame")
    found_chn2_snapshot = False
    for node in ast.walk(method):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr == "snapshot":
                for arg in node.args:
                    if isinstance(arg, ast.Name) and "CHN_ID_2" in arg.id:
                        found_chn2_snapshot = True
                for kw in node.keywords:
                    if kw.arg == "chn" and isinstance(kw.value, ast.Name):
                        if "CHN_ID_2" in kw.value.id:
                            found_chn2_snapshot = True
    assert found_chn2_snapshot, \
        "on_frame must call sensor.snapshot(chn=CAM_CHN_ID_2) for AI frame " \
        "(chn2 RGB888_PLANAR, separate from chn0 preview)"
```

- [ ] **Step 2.2: 跑测试确认红**

```
cd e:/LBS-Project/CanMV/CamerAi
python tests/test_face_detect.py
```

预期：`test_face_detect_on_frame_uses_chn2_planar_frame` **FAIL**（`_img_to_chw` 还在、on_frame 还没 chn2 snapshot），其余 PASS。

- [ ] **Step 2.3: 在 app.py 顶部 try 块补 CAM_CHN_ID_2 导入**

[scripts/face_detect/app.py:20-28](scripts/face_detect/app.py#L20-L28) 当前：

```python
try:
    import ulab.numpy as np
    import nncase_runtime as nn
    import aidemo
    import image as _image_lib
    import math
except ImportError:
    pass  # IDE 环境无此模块，仅 AST 测试用
```

改为：

```python
try:
    import ulab.numpy as np
    import nncase_runtime as nn
    import aidemo
    import image as _image_lib
    import math
    from media.sensor import CAM_CHN_ID_2
except ImportError:
    pass  # IDE 环境无此模块，仅 AST 测试用
```

- [ ] **Step 2.4: 修改 on_frame AI 段——从 chn2 取帧替代 _img_to_chw**

[scripts/face_detect/app.py:381-413](scripts/face_detect/app.py#L381-L413) 当前 AI 段（从 `# 同一帧转 CHW numpy 喂 AI 推理` 到 `update_face_boxes done` 打印）。把这整段替换为：

```python
                    # AI 帧走独立 chn2（RGB888_PLANAR），与预览 chn0 硬件隔离。
                    # to_numpy_ref() 直接得 planar CHW，零软件转置——对齐官方
                    # libs/PipeLine.get_frame()。删除 _img_to_chw 软件转置黑盒
                    # （根因见 specs/2026-06-17-face-detect-chn2-planar-fix-design.md）。
                    if self._face_det is not None:
                        _diag_step = "ai_snapshot"
                        ai_img = sensor.snapshot(chn=CAM_CHN_ID_2)
                        if _fc <= 20:
                            print("[FaceDetect] ai_snapshot done, ai_img=%s" % (ai_img is not None))
                        if ai_img is not None:
                            _diag_step = "to_numpy_ref"
                            frame = ai_img.to_numpy_ref()
                            if _fc <= 20:
                                print("[FaceDetect] img_to_chw done, frame=%s" % (frame is not None))
                            if frame is not None:
                                # 显式释放上一帧数据，辅助 GC 回收
                                self._current_frame_data = None
                                self._current_frame_data = frame
                                _diag_step = "face_det.run"
                                det_boxes, landms = self._face_det.run(frame)
                                if _fc <= 20:
                                    print("[FaceDetect] face_det.run done, boxes=%d" % (len(det_boxes) if det_boxes else 0))
                                self._current_boxes = det_boxes if det_boxes else []
                                self._current_landmarks = landms if landms else []

                                # 人脸识别（与 DB 比对）
                                self._recognition_results = []
                                if self._current_boxes and self._current_landmarks:
                                    if _fc <= 20:
                                        print("[FaceDetect] recognition loop, %d faces" % len(self._current_landmarks))
                                    for i, landm in enumerate(self._current_landmarks):
                                        if i >= 4:
                                            break
                                        try:
                                            _diag_step = "face_reg.config_preprocess[%d]" % i
                                            self._face_reg.config_preprocess(landm)
                                            _diag_step = "face_reg.run[%d]" % i
                                            feature = self._face_reg.run(frame)
                                            if _fc <= 20:
                                                print("[FaceDetect] face_reg.run[%d] done" % i)
                                            _diag_step = "_search_face[%d]" % i
                                            matched_id, score = self._search_face(feature)
                                            self._recognition_results.append(
                                                (self._current_boxes[i], matched_id, score))
                                        except Exception:
                                            self._recognition_results.append(
                                                (self._current_boxes[i], None, 0.0))
                                        finally:
                                            _diag_step = ""

                                # 更新 LVGL 人脸框
                                _diag_step = "update_face_boxes"
                                self._update_face_boxes()
                                if _fc <= 20:
                                    print("[FaceDetect] update_face_boxes done")
```

> 关键变化：
> - `frame = self._img_to_chw(img)` → `ai_img = sensor.snapshot(chn=CAM_CHN_ID_2); frame = ai_img.to_numpy_ref()`
> - 缩进层级：原 `if frame is not None:` 块整体多缩进一层（因为多了 `if ai_img is not None:` 守卫）。**仔细核对缩进**——AI 段原本在 `if img is not None:` 内（show_image 之后），现在 chn2 snapshot 不依赖 chn0 的 img，但仍在 `if img is not None:` 块内（保持预览成功才跑 AI 的语义）。`if self._face_det is not None:` 及其内部所有代码缩进不变（仍 24 空格起），只是内部从 `_img_to_chw` 改为 chn2 snapshot + `to_numpy_ref` + 多一层 `if ai_img is not None:`。

- [ ] **Step 2.5: 删除 `_img_to_chw` 静态方法**

[scripts/face_detect/app.py:549-575](scripts/face_detect/app.py#L549-L575)（`_img_to_chw` 整段，从 `@staticmethod` 装饰器 + `def _img_to_chw(img):` 到其 docstring + 函数体 `return out.copy()`）整段删除。

> 删除前确认：grep 确认无其他调用点。
> ```
> cd e:/LBS-Project/CanMV/CamerAi
> grep -n "_img_to_chw" scripts/face_detect/app.py
> ```
> 预期：仅 Step 2.4 改完后**不应**再有 `self._img_to_chw` 调用（Step 2.4 已删该调用）。若仍有引用，说明 Step 2.4 未替换干净，回去修。

- [ ] **Step 2.6: 修改 rgb888p 改回 1024×768**

[scripts/face_detect/app.py:591](scripts/face_detect/app.py#L591) 当前：

```python
        rgb888p = [640, 480]  # chn0 VGA（临时：chn2 关闭验证内存）
```

改为：

```python
        rgb888p = [1024, 768]  # chn2 XGA（AI 帧源，对齐官方 demo + lcd.py chn2 配置）
```

- [ ] **Step 2.7: AST 检查 + 跑测试确认绿**

```
cd e:/LBS-Project/CanMV/CamerAi
python -c "import ast; ast.parse(open('scripts/face_detect/app.py', encoding='utf-8').read()); print('OK')"
grep -n "_img_to_chw" scripts/face_detect/app.py || echo "no _img_to_chw refs (good)"
python tests/test_face_detect.py
```

预期：AST `OK`；`no _img_to_chw refs (good)`；全测试 PASS。

- [ ] **Step 2.8: 提交**

```
cd e:/LBS-Project/CanMV/CamerAi
git add scripts/face_detect/app.py tests/test_face_detect.py
git commit -m "fix(face_detect): AI 帧改走 chn2 PLANAR — 删 _img_to_chw 软件转置

根因(specs/2026-06-17-face-detect-chn2-planar-fix-design.md): 用 chn0 预览帧
(interleaved RGB888) 软件转 CHW 喂 NPU 会卡死——软件转置的 ulab buffer 与
nncase tensor 内存关系是黑盒，无 gc 累积损坏 kpu 输入，有 gc 回收 ai2d/kpu
仍引用的 buffer。

修法: on_frame AI 帧改从 chn2(CAM_CHN_ID_2) snapshot + to_numpy_ref() 直接得
planar CHW，对齐官方 libs/PipeLine.get_frame()。删除 _img_to_chw 软件转置黑盒。
rgb888p 改回 1024×768 与 chn2 一致。

删 test_face_detect_img_to_chw_returns_independent_copy + ..._uses_single_snapshot
(契约不再适用)，新增 test_face_detect_on_frame_uses_chn2_planar_frame 守门。

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## Task 3: 恢复 main.py 主循环

**Files:**
- Modify: `main.py:145-167`

- [ ] **Step 3.1: 恢复 main.py 主循环为原状**

[main.py:145-167](main.py#L145-L167) 当前（临时隔离实验版本）：

```python
    # ── Step 4: 主循环（对齐 demo while True 结构）───────
    print("[CamerAi] step 4/4: main loop...")

    # ⚠️ 临时隔离实验：验证 LVGL flush DMA 是否为每帧 gc 死锁根因。
    # 脚本运行期间跳过 lv.task_handler() —— 只跑 AI + 摄像头（OSD1
    # show_image），不跑 LVGL flush。主菜单态仍正常刷新 LVGL（否则
    # 点不进 APP）。face_detect 心跳（帧 1-20 + 每 30 帧）观察是否还卡。
    #   跑过第 6/20/100 帧 → LVGL flush DMA 就是死锁根因（架构问题确认）
    #   仍卡第 6 帧 kpu.run → LVGL 无关，AI/gc/DMA 自身问题
    # 注：跳过 task_handler 后返回按钮（LVGL 事件）不响应，测完复位板子退出。
    # 验证后恢复下方注释的原循环。
    while True:
        os.exitpoint()
        if not runner.running:
            lv.task_handler()
        runner.tick()
        time.sleep_ms(5)
    # 原循环（验证后恢复）：
    # while True:
    #     os.exitpoint()
    #     lv.task_handler()
    #     runner.tick()
    #     time.sleep_ms(5)
```

替换为：

```python
    # ── Step 4: 主循环（对齐 demo while True 结构）───────
    print("[CamerAi] step 4/4: main loop...")

    while True:
        os.exitpoint()
        lv.task_handler()
        runner.tick()
        time.sleep_ms(5)
```

- [ ] **Step 3.2: AST 检查**

```
cd e:/LBS-Project/CanMV/CamerAi
python -c "import ast; ast.parse(open('main.py', encoding='utf-8').read()); print('OK')"
```

预期：`OK`。

- [ ] **Step 3.3: 提交**

```
cd e:/LBS-Project/CanMV/CamerAi
git add main.py
git commit -m "chore(main): 恢复主循环 — 移除 face_detect 卡死定位的临时隔离实验

face_detect chn2 PLANAR 修复完成后，临时'运行期跳过 lv.task_handler'实验
不再需要（已证明卡死与 LVGL flush DMA 无关，根因是 AI 数据路径）。恢复
每帧 lv.task_handler() + runner.tick() + sleep_ms(5)。

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## Task 4: 板端验收（用户手动跑）

> 前置：Task 1-3 全部提交完成。

**部署 3 个文件到 SD 卡**：
```
hw/lcd.py                       → /sdcard/CamerAi/hw/lcd.py
scripts/face_detect/app.py      → /sdcard/CamerAi/scripts/face_detect/app.py
main.py                         → /sdcard/CamerAi/main.py
```

- [ ] **Step 4.1: 验收 1 — 双 snapshot 无死锁 + 启动正常**

1. 上电 → 主菜单 → 进 face_detect
2. 串口看启动日志：`[FaceDetect] shared sensor running` 后**不应**有 `sensor(0) ... buffer_size 0`（chn2 PLANAR 配置后缓冲池应正常）
3. 看前 20 帧日志：`on_frame #N begin → snapshot done → show_image done → ai_snapshot done → img_to_chw done → face_det.run done → update_face_boxes done → on_frame #N done` 帧号 1→20 正常推进
4. 若卡在 `ai_snapshot`（chn2 snapshot 不返回）→ 双通道死锁，回退 Task 1，降 chn2 到 640×480 重试

- [ ] **Step 4.2: 验收 2 — 连跑 5 分钟不卡死（核心）**

1. 进 face_detect 后**不动手静置 5 分钟**
2. 串口看帧号推进过 20、过 100（每 30 帧一条 `[FD hb] fc=N begin/done mem_free=...` 心跳）
3. `mem_free` 数值应大致稳定（不单调暴跌）
4. 5 分钟后按返回键 → 看 `[FaceDetect] on_exit:` 系列日志走完到 `_destroy_ui: done` → 正常回主菜单
5. **若仍卡死**：贴卡死前最后 10-20 行日志（含 `_diag_step` 与心跳 mem_free），回 systematic-debugging Phase 1

- [ ] **Step 4.3: 验收 3 — 功能回归（4 脸识别 + K2 注册 + 持久化）**

1. 进 face_detect → 对着脸 → 看屏幕画矩形框（坐标应贴脸，无错位；若框偏到角落→ rgb888p_size/坐标映射问题，查 `_update_face_boxes`）
2. K2 注册第 1 张脸（蜂鸣 80ms）→ 底栏 `已注册: ID1`
3. 换脸 → K2 → `已注册: ID1 ID2`，重复到 ID4
4. 点 list → 弹窗 → 点"保存" → toast `保存成功`
5. 返回主菜单 → 串口看 `[FaceDB] flushed id1.bin` ×4
6. **重新进 face_detect** → 串口看 `[FaceDB] loaded id1.bin (128 floats)` ×4 + `init_features done: 4 face(s)` → 底栏 `已注册: ID1 ID2 ID3 ID4`
7. 上位机 UART 收到人脸数据（slot[0] 有值，无脸时 4 个 None）

任一项失败 → 不算完成，回 systematic-debugging Phase 1，贴日志。

- [ ] **Step 4.4: 验收通过后收缩诊断探针**

板端验收 1-3 全部通过后，把 [scripts/face_detect/app.py](scripts/face_detect/app.py) 的诊断探针从"前 20 帧 + 每 30 帧 + mem_free"收缩回精简版"前 3 帧 + 每 60 帧心跳（无 mem_free）"。

具体：把所有 `if _fc <= 20:` 改回 `if _fc <= 3:`；把 `elif _fc % 30 == 0:` 改为 `elif _fc % 60 == 0:`；心跳行去掉 `mem_free`（删 `import gc as _gc; print(... mem_free=...)`，恢复为简单 `print("[FD hb] fc=%d begin" % _fc)`）。

```
cd e:/LBS-Project/CanMV/CamerAi
python -c "import ast; ast.parse(open('scripts/face_detect/app.py', encoding='utf-8').read()); print('OK')"
git add scripts/face_detect/app.py
git commit -m "chore(face_detect): 收缩诊断探针 — 验收通过回精简版

板端验收 chn2 PLANAR 修复通过(连跑5分钟不卡死+4脸识别+K2注册持久化)。
诊断从'前20帧+每30帧+mem_free'收缩回'前3帧+每60帧心跳'。

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## 自检（Plan Self-Review）

- ✅ **Spec coverage**：
  - spec §5.1 lcd.py chn2 PLANAR → Task 1
  - spec §5.2.1 rgb888p 1024×768 → Task 2 Step 2.6
  - spec §5.2.2 on_frame chn2 取帧 → Task 2 Step 2.4
  - spec §5.2.3 删 _img_to_chw → Task 2 Step 2.5
  - spec §5.2.4 _register_current_face 无需改动（复用 _current_frame_data）→ 计划中未列改动，正确
  - spec §5.2.5 诊断探针收缩 → Task 4 Step 4.4
  - spec §5.3 main.py 恢复 → Task 3
  - spec §8.1 测试删/新增 → Task 1 Step 1.1 + Task 2 Step 2.1
  - spec §8.2 板端验收 → Task 4
  - spec §9 风险验证项 → Task 4 各 Step 内
- ✅ **No placeholders**：所有 step 都给了具体代码、具体命令；无 TBD/TODO。
- ✅ **Type consistency**：`CAM_CHN_ID_2`、`PIXEL_FORMAT_RGB_888_PLANAR`、`to_numpy_ref()`、`rgb888p`、`_current_frame_data`、`_prev_img` 在各 Task 间名称一致。测试函数名 `test_face_detect_on_frame_uses_chn2_planar_frame`、`test_lcd_declares_chn2_ai_channel` 一致。
- ✅ **K230 硬约束**：Task 1 只改 chn2 pixformat/分辨率不改声明时机（坑#15）；Task 2 保留每帧 gc（坑#16）+ 对象池（坑#10）+ _prev_img（坑#14）；无 on_frame 文件 I/O（坑#2）。
