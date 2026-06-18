# 人脸识别 APP AI 帧数据路径修复 Design

> 日期：2026-06-17
> 主题：face_detect 从"chn0 预览帧软件转置 CHW 喂 NPU"改为"chn2 RGB888_PLANAR 硬件直出 CHW 喂 NPU"，对齐官方 demo 双通道架构，根治连跑卡死。

## 1. 背景

### 1.1 现象

阶段 1 三个 bugfix（`_img_to_chw` 强制拷贝 / 删 on_frame 每帧 gc / face_db 改 frombuffer）提交后，板端验收时 APP 连跑**卡死**。systematic-debugging 四阶段定位 + 四次局部修补实验后，确认根因不是"每帧 gc vs 坑#10"的冲突，而是 **AI 输入数据路径错误**。

### 1.2 根因（systematic-debugging 结论）

当前 face_detect 用 **chn0 预览帧（RGB888 interleaved）**软件转 CHW 喂 NPU：

```
chn0 snapshot (RGB888 HWC) → to_numpy_ref() → reshape/transpose/copy/copy → CHW → kpu
```

而官方 demo `main2.py` 经 `libs/PipeLine.get_frame()` 用 **chn2（RGB888_PLANAR）** 硬件直出 CHW：

```python
# libs/PipeLine.py:110-114
self.cur_frame = self.sensor.snapshot(chn=CAM_CHN_ID_2)   # PLANAR
input_np = self.cur_frame.to_numpy_ref()                  # 直接 planar CHW，零软件转置
return input_np
```

证据链：

1. 官方 demo main2.py 每帧 gc + 长期稳定 → 用 PipeLine → chn2 PLANAR `to_numpy_ref()` 直接得 planar CHW，零软件转置。
2. 旧颜色识别代码（camer-2026-05-14-back）稳定 → `Sensor(640,480) RGB565` 直接 `img.draw_*` 画 OSD，**不转 CHW、不喂 planar NPU 输入**。
3. 当前 face_detect 卡死 → chn0 RGB888(interleaved) → 软件转 CHW → 喂 kpu。软件转置产出的 ulab buffer 与 nncase/ai2d tensor 的内存持有关系是黑盒：无 gc 时累积损坏 kpu 输入 → 帧死 `kpu.run`；有 gc 时回收 ai2d/kpu 仍引用的 ulab buffer → 帧死帧间。
4. 隔离实验：移除 `lv.task_handler()` 后**卡死一模一样** → 与 LVGL flush DMA 无关，是 AI 数据路径自身问题。

### 1.3 附带发现

`hw/lcd.py` 工作树把 chn2 从 `PIXEL_FORMAT_RGB_888_PLANAR` 改成了 `Sensor.RGB888`，注释称"K230 Sensor 无 RGB888_PLANAR 常量"。这是**误判**：常量名是 `PIXEL_FORMAT_RGB_888_PLANAR`（`from media.sensor import *` 提供，PipeLine.py:96 在用），而非 `Sensor.RGB888_PLANAR`。本次一并纠正。

## 2. 目标

- 根治 face_detect 连跑卡死：AI 帧走 chn2 PLANAR 硬件直出，删除软件转置黑盒。
- 对齐官方 demo 已验证路径，使每帧 gc（坑#16）在此数据流下安全。
- 保留完整功能：4 脸检测+识别、K2 注册、退出重进持久化、UART 上送。
- 流畅度优先：chn2 用官方默认 1024×768（模型设计基准，最稳）。

## 3. 非目标（本次不做）

- ROI 单脸过滤（稳定性计划 Task 2.2，延后）。
- AIScriptBase 继承重构（Task 2.3，延后）。
- chn0 改 `bind_layer(VIDEO1)` 硬件直显（保持现有 snapshot+show_image 路径，与 camera app 一致）。
- 任何 UI / i18n 改动。

## 4. 架构

对齐官方 demo 双通道架构，AI 帧与显示帧硬件隔离：

```
┌─────────────────────────────────────────────────────────┐
│  chn0  VGA/RGB888        │  chn1 SXGAM/RGB565  │  chn2 1024×768/RGB888_PLANAR  │
│  预览（不动）             │  拍照（不动）        │  AI 帧（本次修复）             │
└─────────────────────────────────────────────────────────┘
        │                                                  │
   snapshot()                                        snapshot(chn=CAM_CHN_ID_2)
        │                                                  │
   show_image(OSD1)                                  to_numpy_ref()  → planar CHW
        │                                                  │
   self._prev_img (DMA安全持有1帧)                    self._current_frame_data
                                                          │
                                                  face_det.run / face_reg.run
```

### 4.1 通道职责

- **chn0 (VGA/RGB888)** — 仅预览：`snapshot()` → `Display.show_image(OSD1)`。**零改动**，与 camera app 同路径，已验证稳定。
- **chn2 (1024×768/RGB888_PLANAR)** — 仅 AI：`snapshot(chn=CAM_CHN_ID_2)` → `to_numpy_ref()` 直接得 planar CHW → 喂 NPU。**删除 `_img_to_chw` 软件转置**。

### 4.2 保留项

- 每帧 `gc.collect()`（坑#16：AI 循环必须每帧 gc，否则 NPU 池几帧耗尽）。
- 启动期预建人脸框对象池（坑#10：on_frame 不 create/delete LVGL 对象 → 无 pending 终结器 → 每帧 gc 安全）。
- `self._prev_img` 延迟持有 chn0 预览帧一帧（show_image OSD1 的 DMA 安全区）。

## 5. 详细改动

### 5.1 hw/lcd.py

chn2 配置纠正：

```python
# chn2 — AI推理输入：RGB888_PLANAR（planar CHW），1024×768（XGA）。
# PLANAR 格式：snapshot(chn=CAM_CHN_ID_2).to_numpy_ref() 直接得到
# (1,3,H,W) planar CHW，零软件转置，对齐官方 libs/PipeLine.get_frame()。
# 常量名是 PIXEL_FORMAT_RGB_888_PLANAR（非 Sensor.RGB888_PLANAR）。
# 所有 AI 类 APP 共用此通道，必须启动期声明
# （MediaManager.init() 之后池不可重建，K230 pitfall #15）。
self.sensor.set_framesize(Sensor.XGA, chn=CAM_CHN_ID_2)
self.sensor.set_pixformat(PIXEL_FORMAT_RGB_888_PLANAR, chn=CAM_CHN_ID_2)
```

需确认 `PIXEL_FORMAT_RGB_888_PLANAR` 在 lcd.py 的 import 域可见（`from media.sensor import Sensor, CAM_CHN_ID_0, CAM_CHN_ID_1, CAM_CHN_ID_2` 当前未导入该常量，需补导入）。

chn0 / chn1 不动。

### 5.2 scripts/face_detect/app.py

#### 5.2.1 `_init_ai_models` — rgb888p_size 改回 1024×768

```python
rgb888p = [1024, 768]  # chn2 XGA（AI 帧源，对齐官方 demo）
```

此值影响 `config_preprocess` 的 AI2D pad/resize 参数与 `_update_face_boxes` 的坐标映射，必须与 chn2 实际分辨率一致。

#### 5.2.2 on_frame — AI 帧改从 chn2 取，删 `_img_to_chw`

预览段（chn0）不变。AI 段替换：

```python
if self._face_det is not None:
    _diag_step = "ai_snapshot"
    # AI 帧走独立 chn2（RGB888_PLANAR），与预览 chn0 硬件隔离。
    # to_numpy_ref() 直接得 planar CHW，零软件转置——对齐官方
    # libs/PipeLine.get_frame()。删除 _img_to_chw 软件转置黑盒。
    ai_img = sensor.snapshot(chn=CAM_CHN_ID_2)
    if ai_img is not None:
        frame = ai_img.to_numpy_ref()
        if _fc <= 20:
            print("[FaceDetect] ai_snapshot done, frame=%s" % (frame is not None))
        if frame is not None:
            self._current_frame_data = None
            self._current_frame_data = frame
            _diag_step = "face_det.run"
            det_boxes, landms = self._face_det.run(frame)
            ...  # 识别循环、update_face_boxes 不变
```

`CAM_CHN_ID_2` 需在 app.py 可见（从 `media.sensor` 导入，或经 lcd 取）。当前 app.py 顶部 try 块有 `import image as _image_lib` 等，需补 `CAM_CHN_ID_2` 导入或经 `self.ctx.lcd.ai_chn` 取（lcd 已有 `self.ai_chn = CAM_CHN_ID_2` 属性）。

#### 5.2.3 删除 `_img_to_chw` 静态方法

`_img_to_chw`（含坑#14 软件转置逻辑）整段删除。PLANAR `to_numpy_ref()` 是 chn2 独立 buffer，与 chn0 预览帧无 VB 共享，断链逻辑不再需要。

#### 5.2.4 `_register_current_face` 同步

该方法（K2 注册）也调 `self._face_reg.run(self._current_frame_data)`。`_current_frame_data` 现在是 chn2 planar CHW，与 on_frame 一致，**无需改动**——注册时复用 on_frame 存的最新 AI 帧。

#### 5.2.5 诊断探针

修复验证通过后收缩回精简版（前 3 帧逐步 + 每 60 帧心跳）。本次保留增强版（前 20 帧 + 每 30 帧 + mem_free）用于验收。

### 5.3 main.py

**恢复**主循环原状（移除临时隔离实验的"运行期跳过 lv.task_handler"）。每帧 `lv.task_handler()` + `runner.tick()` + `sleep_ms(5)`。

## 6. 数据流（修复后）

```
每帧 on_frame:
  1. chn0: sensor.snapshot() → img
     → Display.show_image(img, OSD1)
     → self._prev_img = img          # DMA 安全区，持有 1 帧
  2. chn2: sensor.snapshot(chn=CAM_CHN_ID_2) → ai_img
     → frame = ai_img.to_numpy_ref() # planar CHW，零转置
     → self._current_frame_data = frame
     → face_det.run(frame)           # AI2D: 1024×768 → 320×320 pad+resize → kpu
     → (boxes>0) for landm: face_reg.run(frame) ×N  # 最多4脸
     → _update_face_boxes()          # 对象池复用
  3. host.send_face_data() (10ms 节流)
  4. _tick_toast()
  5. gc.collect()                    # AI帧独立buffer，与显示帧隔离，gc安全
```

## 7. 错误处理

- chn0 snapshot 返回 None（偶发）→ 跳过预览（现有 try/except）。
- chn2 snapshot 返回 None → 跳过本帧 AI，仍推预览（`if ai_img is not None` 守卫）。
- `face_det.run` / `face_reg.run` 抛异常 → 现有 `except Exception` 兜底，记 `_diag_step`，不崩。
- 双 snapshot 死锁风险 → 板端验收项（见 §9）。

## 8. 测试

### 8.1 主机端（Windows，纯 AST/逻辑）

- **删除**：`test_face_detect_img_to_chw_shape`、`test_face_detect_img_to_chw_returns_independent_copy`（`_img_to_chw` 已删，契约不再适用）。
- **新增**：`test_face_detect_on_frame_uses_chn2_planar_frame` —— AST 契约：on_frame 必须从 `CAM_CHN_ID_2` 取 AI 帧、不得调用 `_img_to_chw`。
- **保留**：其余 16 项测试（含 `test_face_detect_on_frame_collects_gc_each_frame` 坑#16 守门）。
- **新增**：`test_lcd_chn2_uses_rgb888_planar` —— AST 契约：lcd.py chn2 必须用 `PIXEL_FORMAT_RGB_888_PLANAR`。

### 8.2 板端

- **验收 1（核心）**：连跑 5 分钟不卡死。心跳推进过 6/20/100 帧，`mem_free` 大致稳定。
- **验收 2（双 snapshot 无死锁）**：进 APP 后立刻观察前 20 帧是否正常推进（双通道 snapshot 不阻塞）。
- **验收 3（功能回归）**：4 脸识别框正确、K2 注册、退出重进读到已注册人脸、UART 上送数据。

## 9. 风险与板端验证项

| 风险 | 验证方式 | 回退 |
|------|---------|------|
| 双 snapshot（chn0+chn2）死锁 | 验收 2：前 20 帧推进 | 撤回本次改动，回到 chn0 单通道 |
| `PIXEL_FORMAT_RGB_888_PLANAR` 常量在 lcd.py import 域不可见 | AST 检查 + 板端启动日志无 NameError | 改用 `from media.sensor import *` |
| chn2 1024×768 与 chn0 VGA 同时配置缓冲池不足 | 板端启动日志 `buffer_size` 检查 | 降 chn2 到 640×480 |
| 人脸框坐标映射因 rgb888p_size 640→1024 偏移 | 验收 3：框贴脸 | 校正 `_update_face_boxes` 映射 |

## 10. K230 硬约束遵守

- 坑#2（FATFS/DMA）：无 on_frame 文件 I/O。✓
- 坑#10（gc 触发 LVGL 终结器）：对象池消除 LVGL churn，每帧 gc 安全。✓
- 坑#14（VB 不归还）：PLANAR `to_numpy_ref()` 是 chn2 独立 buffer，与 chn0 预览帧无共享；`_prev_img` 持有 chn0 帧 1 帧保 DMA 安全。✓
- 坑#15（多通道启动期声明）：chn2 在 `MediaManager.init()` 前声明，本次只改 pixformat/分辨率，不改声明时机。✓
- 坑#16（AI 循环每帧 gc）：保留每帧 `gc.collect()`，AI 帧独立 buffer 使 gc 安全。✓
