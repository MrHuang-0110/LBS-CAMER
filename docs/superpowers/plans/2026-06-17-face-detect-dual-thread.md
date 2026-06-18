# face_detect 双线程架构修复 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** face_detect 改双线程（AI 线程 + LVGL 主线程），对齐官方 ai_lvgl.py，根治 Display.show_image(OSD1) 偶发阻塞卡死，功能全部保留。

**Architecture:** APP 自管线程——on_enter 用 `_thread.start_new_thread` 启 `_ai_loop`（snapshot+NPU+画叠加+show_image+gc+UART），主线程 runner.tick 只留 K2/握手/退出/toast。人脸框+十字架改画到 image.Image（AI 线程不碰 LVGL→线程安全），删 LVGL 对象池。K2 注册改设标志、AI 线程执行。ScriptRunner/main 不动。

**Tech Stack:** MicroPython + LVGL v8 + `_thread` + ulab.numpy + nncase_runtime + image 模块画图 + K230D BOX。

**Spec:** [docs/superpowers/specs/2026-06-17-face-detect-dual-thread-design.md](docs/superpowers/specs/2026-06-17-face-detect-dual-thread-design.md)

---

## K230 硬约束（实施前必读）

1. **坑 #2**：AI 线程无文件 I/O（DB 读写仅在 on_enter/on_exit 主线程安全窗口）。
2. **坑 #10**：AI 线程不碰 LVGL 对象（无 pending 终结器），每帧 gc 安全。
3. **坑 #14**：chn2 PLANAR `to_numpy_ref()` 独立 buffer；`_prev_img` 持 chn0 帧 1 帧。
4. **坑 #15**：不改 lcd.py 通道配置。
5. **坑 #16**：AI 线程每帧 `gc.collect()`，对齐官方 ai_lvgl.py。

## image 模块画图 API（板端已验证签名，照搬）

- `img.draw_line(x1, y1, x2, y2, color=(R,G,B,A), thickness=N)`
- `img.draw_rectangle(x, y, w, h, color=(R,G,B,A), thickness=N)`
- `img.draw_string_advanced(x, y, size, text, color=(R,G,B,A))` （size=字号像素）

color 是 RGBA 四元组。常量 `BOX_COLORS` 当前是 `0xRRGGBB` 整数（如 `0x44CC44`），画图时需转 `(R,G,B,255)`。

---

## File Structure

| 文件 | 改动 |
|------|------|
| `scripts/face_detect/app.py` | 新增 `_ai_loop`/`_draw_overlay`/`_do_register`；改 on_enter/on_exit/on_frame/on_key；删 `_build_crosshair`/`_build_face_box_pool`/`_update_face_boxes`/`_register_current_face` |
| `tests/test_face_detect.py` | 删对象池契约测试；新增双线程契约测试；改 required_methods |

---

## Task 1: 更新测试契约（TDD 红）

**Files:**
- Modify: `tests/test_face_detect.py`

- [ ] **Step 1.1: 改 `test_face_detect_app_has_required_methods` 的 required 列表**

打开 [tests/test_face_detect.py:90-105](tests/test_face_detect.py#L90-L105)。把 `required` 列表替换为（删 `_build_crosshair`/`_update_face_boxes`，加 `_ai_loop`/`_draw_overlay`/`_do_register`）：

```python
def test_face_detect_app_has_required_methods():
    tree = _parse(APP_PATH)
    cls = _class_node(tree, "FaceDetectApp")
    methods = _method_names(cls)
    required = [
        "on_enter", "on_frame", "on_exit", "on_key",
        "_init_ai_models", "_init_db", "_save_db", "_clear_db",
        "_register_face", "_search_face",
        "_build_ui", "_build_top_bar", "_build_bottom_bar",
        "_ai_loop", "_draw_overlay", "_do_register",
        "_send_recognition_data",
        "_on_list_click", "_show_popup", "_dismiss_popup",
        "_update_status_text",
    ]
    missing = [m for m in required if m not in methods]
    assert not missing, f"FaceDetectApp missing methods: {missing}"
```

- [ ] **Step 1.2: 删 `_update_face_boxes`/对象池契约测试，新增双线程契约测试**

打开 [tests/test_face_detect.py](tests/test_face_detect.py)。删除这两个函数整段：
- `test_face_detect_update_boxes_does_not_churn_lvgl_objects`（约 185-203 行）
- `test_face_detect_has_prebuilt_face_box_pool`（约 205 行起到下一个 `# ── Test` 注释前）

删除后，在原位置插入以下 4 个新测试：

```python
# ── Test: 双线程架构契约 ──

def test_face_detect_has_ai_loop():
    """FaceDetectApp 必须有 _ai_loop 方法（AI 线程入口）。"""
    tree = _parse(APP_PATH)
    cls = _class_node(tree, "FaceDetectApp")
    methods = _method_names(cls)
    assert "_ai_loop" in methods, \
        "FaceDetectApp must have _ai_loop() — AI thread entry (dual-thread arch)"


def test_face_detect_ai_loop_does_not_touch_lvgl():
    """_ai_loop 与 _draw_overlay 不得调用任何 lv. 属性——LVGL 非线程安全，
    AI 线程碰 LVGL 对象会与主线程 lv.task_handler 冲突崩溃。对齐官方
    ai_lvgl.py（AI 线程只碰 image.Image + Display.show_image）。"""
    tree = _parse(APP_PATH)
    cls = _class_node(tree, "FaceDetectApp")
    for method_name in ("_ai_loop", "_draw_overlay"):
        method = _method_node(cls, method_name)
        for node in ast.walk(method):
            # lv.xxx 调用：node.func.value 是 Name(id="lv")
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                if isinstance(node.func.value, ast.Name) and node.func.value.id == "lv":
                    raise AssertionError(
                        "%s must NOT call lv.* — LVGL is not thread-safe; "
                        "AI thread draws to image.Image only" % method_name)
            # 直接引用 lv 属性（如 lv.obj）也算碰
            if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
                if node.value.id == "lv":
                    raise AssertionError(
                        "%s must NOT reference lv.* (thread-safety)" % method_name)


def test_face_detect_on_enter_starts_ai_thread():
    """on_enter 必须用 _thread.start_new_thread 启动 _ai_loop。"""
    tree = _parse(APP_PATH)
    cls = _class_node(tree, "FaceDetectApp")
    method = _method_node(cls, "on_enter")
    found = False
    for node in ast.walk(method):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr == "start_new_thread":
                found = True
                break
    assert found, \
        "on_enter must call _thread.start_new_thread(self._ai_loop) to start AI thread"


def test_face_detect_on_exit_stops_ai_thread():
    """on_exit 必须设 _ai_running = False 让 AI 线程退出循环。"""
    tree = _parse(APP_PATH)
    cls = _class_node(tree, "FaceDetectApp")
    method = _method_node(cls, "on_exit")
    found = False
    for node in ast.walk(method):
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Attribute) and t.attr == "_ai_running":
                    found = True
    assert found, \
        "on_exit must set self._ai_running = False to stop AI thread before deinit"
```

- [ ] **Step 1.3: 改 `test_face_detect_on_frame_collects_gc_each_frame` 守 `_ai_loop`**

打开 [tests/test_face_detect.py](tests/test_face_detect.py) 的 `test_face_detect_on_frame_collects_gc_each_frame`（约 147 行）。该测试当前在 `on_frame` 找 gc.collect——双线程后 gc 移到 `_ai_loop`，on_frame 不再有 gc。把方法名从 `"on_frame"` 改为 `"_ai_loop"`，docstring 末尾补一句"双线程后 gc 在 _ai_loop（AI 线程），on_frame 仅留 toast tick"。

具体：找到 `method = _method_node(cls, "on_frame")` 改为 `method = _method_node(cls, "_ai_loop")`。

- [ ] **Step 1.4: 跑测试确认红**

```
cd e:/LBS-Project/CanMV/CamerAi
python tests/test_face_detect.py
```

预期：多个 FAIL——`test_face_detect_app_has_required_methods`（缺 `_ai_loop` 等）、`test_face_detect_has_ai_loop`、`test_face_detect_ai_loop_does_not_touch_lvgl`、`test_face_detect_on_enter_starts_ai_thread`、`test_face_detect_on_exit_stops_ai_thread`、`test_face_detect_on_frame_collects_gc_each_frame`（`_ai_loop` 不存在）。其余 PASS。

- [ ] **Step 1.5: 提交**

```
cd e:/LBS-Project/CanMV/CamerAi
git add tests/test_face_detect.py
git commit -m "test(face_detect): 双线程架构契约测试(红) — _ai_loop/线程安全/启停

删 _update_face_boxes + 对象池契约测试(对象池将删)。新增:
- test_face_detect_has_ai_loop
- test_face_detect_ai_loop_does_not_touch_lvgl (AI线程不碰LVGL)
- test_face_detect_on_enter_starts_ai_thread
- test_face_detect_on_exit_stops_ai_thread
改 required_methods(删_crosshair/_update_boxes,加_ai_loop/_draw_overlay/_do_register)
改 gc 契约守 _ai_loop(gcd 移AI线程)

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## Task 2: 新增 `_ai_loop` + `_draw_overlay` + `_do_register`

**Files:**
- Modify: `scripts/face_detect/app.py`

- [ ] **Step 2.1: 在 app.py 顶部 import `_thread`**

[scripts/face_detect/app.py:11-17](scripts/face_detect/app.py#L11-L17) 当前 import 块。在 `import struct` 后加一行 `_thread`（MicroPython 内置，无需 try）：

```python
import struct
import _thread
import lvgl as lv
```

- [ ] **Step 2.2: 在 `__init__` 加线程标志属性**

[scripts/face_detect/app.py:284-305](scripts/face_detect/app.py#L284-L305) 的 `__init__`。在 `self._prev_img = None` 后加：

```python
        self._prev_img = None

        # 双线程标志（见 _ai_loop）
        self._ai_running = False       # AI 线程循环条件
        self._ai_thread_alive = False  # AI 线程是否在跑（on_exit 等待用）
        self._register_pending = False  # K2 请求注册（主线程设，AI线程消费）
```

- [ ] **Step 2.3: 新增 `_ai_loop` 方法**

在 `on_key` 方法之后（约 [scripts/face_detect/app.py:557](scripts/face_detect/app.py#L557) 后，`# ── AI 模型初始化` 注释前）插入：

```python
    # ── AI 线程 ──────────────────────────────────

    def _ai_loop(self):
        """AI 线程：帧循环——snapshot+画叠加+show_image+NPU+注册+UART+gc。

        对齐官方 examples/21-AI-With-Others/ai_lvgl.py 双线程：本线程独占
        NPU 与 show_image(OSD1)，与主线程 lv.task_handler 分离，避免
        show_image DMA 与 LVGL flush DMA 同线程争用偶发阻塞（卡死根因）。
        绝不碰任何 LVGL 对象（线程安全）。
        """
        import os as _os
        import time as _time
        import gc as _gc
        import sys as _sys
        sensor = self.ctx.lcd.get_sensor()
        self._ai_thread_alive = True
        print("[FaceDetect] AI loop begin")
        try:
            while self._ai_running:
                _os.exitpoint()
                try:
                    # 1. 预览帧（chn0）
                    img = sensor.snapshot()
                    if img is None:
                        continue
                    self._prev_img = img  # DMA 安全区持有1帧

                    # 2. AI 推理（chn2）——先算结果再画框
                    recognition_results = []
                    if self._face_det is not None:
                        ai_img = sensor.snapshot(chn=CAM_CHN_ID_2)
                        if ai_img is not None:
                            frame = ai_img.to_numpy_ref()
                            self._current_frame_data = frame
                            det_boxes, landms = self._face_det.run(frame)
                            self._current_boxes = det_boxes if det_boxes else []
                            self._current_landmarks = landms if landms else []
                            if self._current_boxes and self._current_landmarks:
                                for i, landm in enumerate(self._current_landmarks):
                                    if i >= 4:
                                        break
                                    try:
                                        self._face_reg.config_preprocess(landm)
                                        feature = self._face_reg.run(frame)
                                        matched_id, score = self._search_face(feature)
                                        recognition_results.append(
                                            (self._current_boxes[i], matched_id, score))
                                    except Exception:
                                        recognition_results.append(
                                            (self._current_boxes[i], None, 0.0))
                    self._recognition_results = recognition_results

                    # 3. K2 注册检查（NPU 集中本线程）
                    if self._register_pending:
                        self._register_pending = False
                        self._do_register()

                    # 4. 画叠加层到 img（十字架+人脸框+ID）
                    self._draw_overlay(img, recognition_results)

                    # 5. 显示
                    Display.show_image(img, 0, 0, Display.LAYER_OSD1)

                    # 6. UART 上送（10ms 节流）
                    if self.ctx.host is not None:
                        try:
                            now = _time.ticks_ms()
                            if _time.ticks_diff(now, self._last_send_ticks) >= SEND_INTERVAL_MS:
                                self._last_send_ticks = now
                                self._send_recognition_data()
                        except Exception:
                            pass

                    # 7. 每帧 gc（坑#16，对齐官方 ai_lvgl.py）
                    _gc.collect()
                except Exception as e:
                    print("[FaceDetect] AI loop frame error: %s" % e)
                    _sys.print_exception(e)
        finally:
            self._ai_thread_alive = False
            print("[FaceDetect] AI loop exited")
```

- [ ] **Step 2.4: 新增 `_draw_overlay` 方法**

在 `_ai_loop` 之后插入：

```python
    def _draw_overlay(self, img, recognition_results):
        """在相机帧 img 上画十字架 + 人脸框 + ID 标签（image 模块画图）。

        替代原 LVGL 对象池方案——双线程下 AI 线程不碰 LVGL 对象，改画到
        img 随 show_image(OSD1) 一起显示。对齐官方 ai_lvgl.py draw_result。
        color 用 RGBA 四元组（image 模块要求）。
        """
        # 十字架（绿色，中心 320,240——img 是 chn0 VGA 640×480）
        cx, cy = 320, 240
        arm, gap = CROSSHAIR_ARM, CROSSHAIR_GAP
        green = (0x44, 0xCC, 0x44, 0xFF)
        img.draw_line(cx, cy - arm, cx, cy - gap, color=green, thickness=2)
        img.draw_line(cx, cy + gap, cx, cy + arm, color=green, thickness=2)
        img.draw_line(cx - arm, cy, cx - gap, cy, color=green, thickness=2)
        img.draw_line(cx + gap, cy, cx + arm, cy, color=green, thickness=2)

        # 人脸框 + ID（chn2 坐标 1024×768 → img 坐标 640×480）
        ai_w = self._face_det.rgb888p_size[0] if self._face_det else 1024
        ai_h = self._face_det.rgb888p_size[1] if self._face_det else 768
        for i, (box, matched_id, _) in enumerate(recognition_results):
            if i >= 4:
                break
            x, y, w, h = [int(v) for v in box[:4]]
            sx = x * 640 // ai_w
            sy = y * 480 // ai_h
            sw = max(w * 640 // ai_w, 4)
            sh = max(h * 480 // ai_h, 4)
            color_int = BOX_COLORS.get(matched_id, BOX_UNKNOWN)
            col = ((color_int >> 16) & 0xFF, (color_int >> 8) & 0xFF,
                   color_int & 0xFF, 0xFF)
            img.draw_rectangle(sx, sy, sw, sh, color=col, thickness=2)
            if matched_id is not None:
                img.draw_string_advanced(sx + 2, sy + 2, 16,
                                         "ID%d" % matched_id, color=col)
```

- [ ] **Step 2.5: 新增 `_do_register` 方法（替代 `_register_current_face`）**

在 `_draw_overlay` 之后插入。逻辑同原 `_register_current_face`，但由 AI 线程调用：

```python
    def _do_register(self):
        """将当前帧最大人脸注册到下一个空槽位（AI 线程调用）。

        原 _register_current_face 的逻辑，改名并移到 AI 线程调用——NPU 推理
        （face_reg.run）集中 AI 线程，避免跨线程 NPU。主线程 K2 只设
        _register_pending 标志。
        """
        if not self._current_boxes:
            self.ctx.buzzer.beep(ms=30)
            return
        if not self._current_frame_data or not self._current_landmarks:
            self.ctx.buzzer.beep(ms=30)
            return
        # 找最大人脸（按面积）
        largest_idx = 0
        largest_area = 0
        for i, box in enumerate(self._current_boxes):
            w = box[2] if len(box) > 2 else 0
            h = box[3] if len(box) > 3 else 0
            area = w * h
            if area > largest_area:
                largest_area = area
                largest_idx = i
        if largest_idx >= len(self._current_landmarks):
            self.ctx.buzzer.beep(ms=30)
            return
        landm = self._current_landmarks[largest_idx]
        try:
            self._face_reg.config_preprocess(landm)
            feature = self._face_reg.run(self._current_frame_data)
        except Exception as e:
            print("[FaceDetect] feature extract failed: %s" % e)
            self.ctx.buzzer.beep(ms=30)
            return
        for slot in range(1, 5):
            if slot not in self._db_features:
                self._register_face(feature, slot)
                self._update_status_text()
                self.ctx.buzzer.beep(ms=80)
                return
        self.ctx.buzzer.beep(ms=200)
```

- [ ] **Step 2.6: AST 检查**

```
cd e:/LBS-Project/CanMV/CamerAi
python -c "import ast; ast.parse(open('scripts/face_detect/app.py', encoding='utf-8').read()); print('OK')"
```

预期：`OK`。

- [ ] **Step 2.7: 提交**

```
cd e:/LBS-Project/CanMV/CamerAi
git add scripts/face_detect/app.py
git commit -m "feat(face_detect): 新增 _ai_loop/_draw_overlay/_do_register — AI线程+画叠加

_ai_loop: AI线程帧循环(snapshot+NPU+画叠加+show_image+gc+UART),对齐官方
  ai_lvgl.py。绝不碰LVGL对象(线程安全)。
_draw_overlay: 十字架+人脸框+ID 画到 image.Image(image模块draw_line/
  draw_rectangle/draw_string_advanced,RGBA),替代LVGL对象池。
_do_register: 原_register_current_face逻辑,AI线程调用(NPU集中)。

顶部import _thread; __init__加_ai_running/_ai_thread_alive/_register_pending标志。

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## Task 3: 改 on_enter/on_exit/on_frame/on_key 接入双线程

**Files:**
- Modify: `scripts/face_detect/app.py`

- [ ] **Step 3.1: on_enter 末尾启动 AI 线程**

[scripts/face_detect/app.py:332-344](scripts/face_detect/app.py#L332-L344) 的 `on_enter`。在 `self._build_ui()` 之后、`print("[FaceDetect] on_enter: done")` 之前插入启动线程：

```python
        self._build_ui()
        # 启动 AI 线程（双线程，对齐官方 ai_lvgl.py）
        self._ai_running = True
        self._register_pending = False
        _thread.start_new_thread(self._ai_loop, ())
        print("[FaceDetect] on_enter: AI thread started")
        print("[FaceDetect] on_enter: done")
```

- [ ] **Step 3.2: on_exit 开头停 AI 线程**

[scripts/face_detect/app.py](scripts/face_detect/app.py) 的 `on_exit`（约 484 行）。在 `print("[FaceDetect] on_exit: begin")` 之后、原 Step 1 刷盘 DB 之前，插入停线程逻辑：

```python
    def on_exit(self):
        print("[FaceDetect] on_exit: begin")

        # 0. 停 AI 线程，等其退出（MicroPython _thread 无 join，轮询标志）
        #    必须在 deinit AI 模型之前——否则线程还在跑 NPU 时 deinit 会崩。
        self._ai_running = False
        import time as _time
        for _ in range(100):  # 最多等 ~1s
            if not self._ai_thread_alive:
                break
            _time.sleep_ms(10)
        print("[FaceDetect] AI thread stopped (alive=%s)" % self._ai_thread_alive)

        # ── Step 1: 刷盘 DB 特征 ──
        ...（原逻辑不动）
```

> 先 Read on_exit 全文确认 `print("[FaceDetect] on_exit: begin")` 的确切位置与下方 Step 1 注释，再 Edit 精确插入。

- [ ] **Step 3.3: on_frame 精简为只做 toast tick**

[scripts/face_detect/app.py:346-447](scripts/face_detect/app.py#L346-L447) 的 `on_frame`（整个方法）。替换为：

```python
    def on_frame(self):
        """主线程每帧由 runner.tick 调用——双线程后只留 toast 超时检测。

        AI 推理/snapshot/show_image/gc/UART 全在 _ai_loop（AI 线程）。
        on_frame 仅做 LVGL toast 超时检测（LVGL 操作，必须在主线程）。
        """
        import os
        os.exitpoint()
        self._tick_toast()
```

- [ ] **Step 3.4: on_key 改设标志**

[scripts/face_detect/app.py:555-557](scripts/face_detect/app.py#L555-L557) 的 `on_key`。替换为：

```python
    def on_key(self, key):
        """K2 注册：主线程只设标志，AI 线程检查并执行（NPU 集中 AI 线程）。"""
        if key == 'K2':
            self._register_pending = True
```

- [ ] **Step 3.5: 删除旧 `_register_current_face`**

[scripts/face_detect/app.py:672-716](scripts/face_detect/app.py#L672-L716) 的 `_register_current_face` 整段删除（已被 `_do_register` 取代）。

> 删除前 grep 确认无其他调用：
> ```
> cd e:/LBS-Project/CanMV/CamerAi
> grep -n "_register_current_face" scripts/face_detect/app.py
> ```
> 预期：仅 on_key 原调用（Step 3.4 已改掉）+ 定义本身。改完后应无残留。

- [ ] **Step 3.6: AST 检查 + grep 确认**

```
cd e:/LBS-Project/CanMV/CamerAi
python -c "import ast; ast.parse(open('scripts/face_detect/app.py', encoding='utf-8').read()); print('OK')"
grep -n "_register_current_face" scripts/face_detect/app.py || echo "no refs (good)"
```

预期：`OK`；`no refs (good)`。

- [ ] **Step 3.7: 跑测试**

```
cd e:/LBS-Project/CanMV/CamerAi
python tests/test_face_detect.py
```

预期：`test_face_detect_on_enter_starts_ai_thread` PASS、`test_face_detect_on_exit_stops_ai_thread` PASS。但 `test_face_detect_ai_loop_does_not_touch_lvgl` 可能仍 FAIL（若 `_ai_loop` 里有 lv 引用——本 Task 的 `_ai_loop` 不碰 lv，应 PASS）。`test_face_detect_app_has_required_methods` 仍可能 FAIL（缺删除 `_build_crosshair` 等，Task 4 做）。

- [ ] **Step 3.8: 提交**

```
cd e:/LBS-Project/CanMV/CamerAi
git add scripts/face_detect/app.py
git commit -m "feat(face_detect): on_enter启AI线程/on_exit停/on_frame精简/on_key设标志

on_enter: _thread.start_new_thread(_ai_loop) 启动AI线程。
on_exit: 设_ai_running=False,轮询等_ai_thread_alive退出(无join),再deinit。
on_frame: 精简为只_tick_toast(AI重活全移_ai_loop)。
on_key('K2'): 改设_register_pending标志,AI线程执行注册。
删_register_current_face(被_do_register取代)。

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## Task 4: 删 LVGL 叠加方案（十字架/对象池/`_update_face_boxes`）

**Files:**
- Modify: `scripts/face_detect/app.py`

- [ ] **Step 4.1: `_build_ui` 移除十字架/对象池调用**

[scripts/face_detect/app.py](scripts/face_detect/app.py) 的 `_build_ui`（约 739 行）。当前：

```python
    def _build_ui(self):
        screen = lv.scr_act()
        screen.set_style_bg_opa(0, 0)  # 透明透出 OSD1 相机画面
        self._screen = screen

        self._build_top_bar()
        self._build_preview_area()
        self._build_face_box_pool()
        self._build_bottom_bar()
```

改为（删 `_build_preview_area`/`_build_face_box_pool` 调用——预览区透明背景不再需要，十字架改画 img；但保留 `_build_preview_area` 若它建了 preview_bg 容器……需先 Read 确认 `_build_preview_area` 是否只建十字架）：

> **先 Read `_build_preview_area` 与 `_build_crosshair`**（约 800-848 行）确认：`_build_preview_area` 是否建了 `self._preview_bg`（弹窗/toast 可能挂它）。若 preview_bg 被弹窗/toast 用，保留 `_build_preview_area`，只删 `_build_face_box_pool` 调用。

具体改 `_build_ui`：

```python
    def _build_ui(self):
        screen = lv.scr_act()
        screen.set_style_bg_opa(0, 0)  # 透明透出 OSD1 相机画面
        self._screen = screen

        self._build_top_bar()
        self._build_preview_area()   # 保留：建 preview_bg 容器（toast/弹窗挂点）
        self._build_bottom_bar()
        # 十字架+人脸框改画到 image.Image（_draw_overlay），不再用 LVGL 对象
```

- [ ] **Step 4.2: `_build_preview_area` 删十字架调用**

[scripts/face_detect/app.py](scripts/face_detect/app.py) 的 `_build_preview_area`（约 800 行）。若其末尾调 `self._build_crosshair(preview)`，删除该调用（十字架改画 img）。保留 preview_bg 容器本身。改完 Read 确认 preview_bg 仍正确创建。

- [ ] **Step 4.3: 删除 `_build_crosshair` 方法**

[scripts/face_detect/app.py:821-848](scripts/face_detect/app.py#L821-L848) 的 `_build_crosshair` 整段删除。

- [ ] **Step 4.4: 删除 `_build_face_box_pool` 方法**

[scripts/face_detect/app.py:850-882](scripts/face_detect/app.py#L850-L882) 的 `_build_face_box_pool` 整段删除。

- [ ] **Step 4.5: 删除 `_update_face_boxes` 方法**

[scripts/face_detect/app.py:883-940](scripts/face_detect/app.py#L883-L940) 的 `_update_face_boxes` 整段删除（已被 `_draw_overlay` 取代）。

- [ ] **Step 4.6: 删除 `__init__` 中的对象池/十字架属性**

[scripts/face_detect/app.py:314-315](scripts/face_detect/app.py#L314-L315) 附近。删除：
```python
        self._crosshair_lines = []     # 十字架4条线
        self._box_pool = []            # 预建人脸框对象池
```

- [ ] **Step 4.7: `_destroy_ui` 删对象池/十字架清理**

[scripts/face_detect/app.py](scripts/face_detect/app.py) 的 `_destroy_ui`。删除遍历 `self._box_pool` 和 `self._crosshair_lines` 的清理段（这两个属性已删）。先 Read `_destroy_ui` 全文确认确切位置再 Edit。

- [ ] **Step 4.8: AST 检查 + grep 确认无残留**

```
cd e:/LBS-Project/CanMV/CamerAi
python -c "import ast; ast.parse(open('scripts/face_detect/app.py', encoding='utf-8').read()); print('OK')"
grep -n "_build_crosshair\|_build_face_box_pool\|_update_face_boxes\|_crosshair_lines\|_box_pool" scripts/face_detect/app.py || echo "no refs (good)"
```

预期：`OK`；`no refs (good)`。

- [ ] **Step 4.9: 跑测试确认绿**

```
cd e:/LBS-Project/CanMV/CamerAi
python tests/test_face_detect.py
```

预期：全测试 PASS（含 required_methods——`_build_crosshair`/`_update_face_boxes` 已从 required 删，`_ai_loop`/`_draw_overlay`/`_do_register` 已加）。

- [ ] **Step 4.10: 提交**

```
cd e:/LBS-Project/CanMV/CamerAi
git add scripts/face_detect/app.py
git commit -m "refactor(face_detect): 删LVGL叠加方案 — 十字架/对象池/update_face_boxes

双线程后AI线程不碰LVGL对象(线程安全),十字架+人脸框改画image.Image
(_draw_overlay,Task2)。删除:
- _build_crosshair (LVGL十字架对象)
- _build_face_box_pool (LVGL人脸框对象池,坑#10对策不再需要)
- _update_face_boxes (LVGL框更新,被_draw_overlay取代)
- __init__的_crosshair_lines/_box_pool属性
- _destroy_ui的对象池/十字架清理段
_build_ui/_build_preview_area 移除对应调用(preview_bg容器保留供toast/弹窗挂点)。

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## Task 5: 板端验收（用户手动跑）

> 前置：Task 1-4 全部提交完成。

**部署 1 个文件到 SD 卡**：
```
scripts/face_detect/app.py  → /sdcard/CamerAi/scripts/face_detect/app.py
```

> lcd.py（chn2 RGBP888）、main.py（原主循环）已是当前提交状态，确认 SD 卡版本一致。

- [ ] **Step 5.1: 验收 1 — 双线程启动 + 前 20 帧不卡**

1. 上电 → 主菜单 → 进 face_detect
2. 串口看：`[FaceDetect] AI thread started` → `[FaceDetect] AI loop begin`
3. 前若干帧正常推进（show_image 不阻塞——这是核心验证，双线程应让 show_image 不再与 LVGL flush 争用）
4. 预览画面正常显示 + 绿色十字架

**若前 20 帧仍卡 show_image**：双线程未解决，回 systematic-debugging。贴日志（含 `[FaceDetect] AI loop begin` 后最后几行）。

- [ ] **Step 5.2: 验收 2 — 连跑 5 分钟不卡死（核心）**

1. 进 face_detect 后**不动手静置 5 分钟**
2. 5 分钟内无卡死、无崩溃
3. 5 分钟后按返回键 → `[FaceDetect] AI thread stopped` → `on_exit` 系列 → 回主菜单

**若 5 分钟内卡死或崩溃**：贴卡死前日志 + 是否有 `AI loop frame error`。

- [ ] **Step 5.3: 验收 3 — 功能回归**

1. 对着脸 → 预览有绿色十字架 + 人脸框（彩色）+ ID 标签贴脸（坐标映射正确）
2. K2 注册第 1 张脸（蜂鸣 80ms）→ 底栏 `已注册: ID1`
3. 换脸 → K2 → `已注册: ID1 ID2`，重复到 ID4
4. 点 list → 弹窗 → 点"保存" → toast `保存成功`
5. 返回主菜单 → 串口 `[FaceDB] flushed id1.bin` ×4
6. 重新进 face_detect → `[FaceDB] loaded id1.bin` ×4 → 底栏 `已注册: ID1 ID2 ID3 ID4`
7. 已注册的脸识别时框显示对应 ID 颜色 + ID 标签
8. 上位机 UART 收到人脸数据

**框错位**（坐标映射）→ 校正 `_draw_overlay` 的 `ai_w/ai_h` 映射。
**ID 标签不显示/乱码** → 调 `draw_string_advanced` 的 size/color。

- [ ] **Step 5.4: 验收 4 — 线程退出干净**

1. 进 face_detect → 跑几秒 → 按返回键
2. 串口看：`AI thread stopped (alive=False)` → `on_exit` deinit AI → `_destroy_ui: done` → 回主菜单
3. **反复进出 face_detect 3 次**，每次都干净退出不崩（验证 AI 线程启停稳定）

**若退出崩溃**（AI 线程未退就 deinit）→ 增大 on_exit 等待超时（`range(100)` → `range(200)`）。

- [ ] **Step 5.5: 验收通过后（可选）加心跳**

若验收 1-4 全过，可在 `_ai_loop` 加临时心跳（每 60 帧 print 一次 fc + mem_free）确认长期稳定，验证后删除。

---

## 自检（Plan Self-Review）

- ✅ **Spec coverage**：
  - spec §5.1 `_ai_loop` → Task 2 Step 2.3
  - spec §5.2 `_draw_overlay` → Task 2 Step 2.4
  - spec §5.3 删 LVGL 叠加 → Task 4
  - spec §5.4 on_enter 启线程 → Task 3 Step 3.1
  - spec §5.5 on_exit 停线程 → Task 3 Step 3.2
  - spec §5.6 on_frame 精简 → Task 3 Step 3.3
  - spec §5.7 on_key 设标志 + `_do_register` → Task 2 Step 2.5 + Task 3 Step 3.4
  - spec §8.1 测试删/新增 → Task 1
  - spec §8.2 板端验收 → Task 5
  - spec §9 风险验证项 → Task 5 各 Step
- ✅ **No placeholders**：所有 step 给了具体代码/命令。`_build_preview_area` 是否保留 preview_bg 在 Step 4.1/4.2 标注"先 Read 确认"——这是必要的核实步骤，非占位。
- ✅ **Type consistency**：`_ai_loop`/`_draw_overlay`/`_do_register`/`_ai_running`/`_ai_thread_alive`/`_register_pending` 在各 Task 间名称一致。image 画图 API（draw_line/draw_rectangle/draw_string_advanced + RGBA）与官方 demo 一致。
- ✅ **K230 硬约束**：AI 线程不碰 LVGL（坑#10）；每帧 gc 在 `_ai_loop`（坑#16）；无 on_frame 文件 I/O（坑#2）；不改 lcd.py（坑#15）；_prev_img 持 chn0 帧（坑#14）。
