# Step 7: face_detect K2 注册人脸 + 可复用 ID 管理 Design

> 日期：2026-06-18
> 主题：face_detect 接 K2 物理按键注册人脸，抽出可复用的 ID 注册管理模块（face_db.register + id_registry），供后续手势/物体等脚本复用。reset 架构已验证稳定（不卡、帧率稳定），本步在稳定基础上收尾"临时定位"代码（face_detect 走完整 init_app + buzzer 接回）并接入注册。

## 1. 背景

### 1.1 已验证稳定的基础

- reset 切换架构（main.py 启动器 + core/app_runtime.py 每进程独立 init）已落地，Task 1.1/1.2 完成，契约测试 4/4 PASS。
- face_detect 在 reset 架构下**板端验证不卡、帧率稳定、对脸能检测（白框）**——卡死根因（坑#17 同进程常驻冲突）已解决。
- Step 5 识别链路（face_reg.run 提 512 维特征 + database_search 余弦比对 + rec_map 彩框）已接入且没把基线跑废，当前 0 face → 全白框。

### 1.2 当前临时状态（待收尾）

face_detect 为定位卡死，main.py:106-110 **临时跳过 `runtime.init_app`**，由 `face_detect.run()` 自己全套 init 对齐裸跑基线；app_runtime `init_app` 对 face_detect **临时跳过 touch/fonts/services**（buzzer=None）。本步收尾：face_detect 走完整 init_app，buzzer 接回。

### 1.3 现有可复用基础设施

- `core/face_db.py`：`_FaceDB` 单例，已有 `init_features()`（读 .bin）、`flush_to_disk()`（全量写盘）、`clear_disk()`（删 .bin）、`get_features()`。4 个 slot（id1-4），512 维 float32（mobile kmodel，坑#19）。
- backup `app_full_debug_backup.py`：保留 `_do_register`/`_register_face`/`_register_pending`/`_status_dirty` 注册下游机制方法体（K2 实际读取代码在剥裸跑基线时已删，本步重新实现）。

## 2. 目标

- 按 K2（GPIO0 物理按键）注册当前帧最大人脸到 face_db，注册后当场识别（彩色框+ID）。
- 注册/ID 管理做成可复用模块，后续脚本（手势/物体等）只需"自己提特征 → 调注册接口"，K2 轮询/slot 管理/写盘零重写。
- 收尾 face_detect 临时定位：走完整 init_app + buzzer 接回，验证加回不卡。

## 3. 非目标（本次不做）

- UART 上送（Step 6，设计文档已有，后续）。
- 保存/清除弹窗、toast、完整顶/底栏 LVGL UI（后续增量加）。
- 抽象 `AIBase` 之上的 `RegisterableAIScript` 基类（reset 架构设计文档 §10 列为后续不做；本步用组合的 id_registry 模块代替继承，复用粒度足够且不超前）。
- 阈值 0.75 实测调优（注册后板端观察识别效果时顺带调，不单独成步）。

## 4. 关键决策（已确认）

- **K2 硬件接法**：GPIO0 输入，`fpioa.set_function(0, FPIOA.GPIO0)` + `Pin(0, Pin.IN, Pin.PULL_UP)`。按下低电平有效（valid_level=0，板端实测调）。
- **触发方式**：主线程软件边沿检测（松开→按下瞬间置 pending 一次，防按住连触发）。不用 GPIO 中断（避免引入新并发源，对齐裸跑 task_handler 轮询模式）。
- **slot 分配**：空 slot 优先（前 4 次注册依次填 id1-4）；4 个都满 → 轮转覆盖（规则 B）：记 `_next_slot` 指针，覆盖指针指向的 slot 后指针 +1（循环 1→2→3→4→1）。
- **`_next_slot` 持久化**：存 `/data/fac_db/.next_slot`，reset 后 `init_features` 读回延续上次覆盖顺序（不归 1）。
- **写盘时机**：注册时**当场 flush**（试法1）——`face_db.register` 写内存后立刻 `flush_to_disk()` + `_save_next_slot()`，验证运行期写盘卡不卡（坑#18 根因B 延伸：写路径 open wb+write 是否卡）。卡则退化为"主线程 task_handler 间隙 flush"（试法2，本步不实现，留作 fallback）。
- **特征复用**：注册用 Step 5 已提的最大脸特征（`face_reg.run` 结果），不重复 NPU 推理，零额外开销。
- **pending 超时**：pending 置位后 2 秒内未消费则丢弃（防"按了→走开→别人来→误注册"）。
- **buzzer**：接回（注册成功短响 80ms / 失败长响 200ms / 无脸按下短响 30ms），同时验证 PWM0 是否卡死元凶。buzzer=None 守卫保留（对无 buzzer 脚本友好）。

## 5. 架构与模块边界

三个解耦单元，各管一摊：

```
┌─────────────────────────────────────────────────────────┐
│ scripts/face_detect/app.py (及后续手势/物体脚本)        │
│   职责：提特征。AI 线程 face_det/face_reg.run → feature │
│   不关心 slot/写盘/按键                                 │
└───────────────┬─────────────────────────────────────────┘
                │ feature (512维 ndarray)
                ▼
┌─────────────────────────────────────────────────────────┐
│ core/id_registry.py (新建，可复用注册控制器)            │
│   职责：K2 边沿检测 + 注册协作                          │
│   - poll_k2()：主线程调，软件边沿置 register_pending     │
│   - try_register(feature, buzzer)：AI 线程调，          │
│     pending(2秒内) → face_db.register + 蜂鸣 + 清pending│
│   不绑死任何 AI 模型，只接收 feature                     │
└───────────────┬─────────────────────────────────────────┘
                │ face_db.register(feature)
                ▼
┌─────────────────────────────────────────────────────────┐
│ core/face_db.py (已存在，增 register/clear)             │
│   职责：slot 持久化 + 轮转覆盖                          │
│   - register(feature)：空slot优先/满了轮转覆盖 + 当场flush│
│   - clear()：清内存 + clear_disk + 删.next_slot+指针回1 │
│   - get_features()：供 database_search 只读             │
└─────────────────────────────────────────────────────────┘
```

**复用方式**：后续脚本只需自己提特征 → 调 `id_registry.try_register(feature, buzzer)`。K2 轮询、slot 管理、写盘全不用重写。

## 6. 组件改动

### 6.1 `core/face_db.py` — 增 register / clear / 指针持久化

新增 `_next_slot` 指针（1-4 循环）+ `.next_slot` 持久化文件。

```python
_NEXT_SLOT_PATH = "/data/fac_db/.next_slot"

class _FaceDB:
    def __init__(self):
        self._features = {}
        self._loaded = False
        self._next_slot = 1   # 新增：轮转覆盖指针

    def init_features(self):
        # ... 现有读 .bin 逻辑不动 ...
        self._load_next_slot()   # 新增：读完 .bin 后读指针
        self._loaded = True
        return self._features

    def _load_next_slot(self):
        """读 _next_slot（init_features 内，与读 .bin 同安全窗口）。
        文件不存在/损坏 → 默认 1。"""
        try:
            with open(_NEXT_SLOT_PATH, 'r') as f:
                v = int(f.read().strip())
            self._next_slot = v if 1 <= v <= 4 else 1
        except Exception:
            self._next_slot = 1

    def _save_next_slot(self):
        """写 _next_slot（register 内，与 flush_to_disk 同批写盘）。"""
        try:
            with open(_NEXT_SLOT_PATH, 'w') as f:
                f.write(str(self._next_slot))
        except Exception as e:
            print("[FaceDB] save next_slot failed: %s" % e)

    def register(self, feature):
        """注册特征到 slot（轮转覆盖 B）+ 当场写盘。返回 slot_id(1-4)。

        - 有空 slot：填第一个空 slot（不动 _next_slot 指针）
        - 无空 slot：覆盖 _next_slot 指向的 slot，指针 +1（1→2→3→4→1）
        - 写内存后立刻 flush_to_disk() + _save_next_slot()（试法1）
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
        self.flush_to_disk()
        self._save_next_slot()
        print("[FaceDB] registered → id%d (flushed)" % slot)
        return slot

    def clear(self):
        """清内存 + 删 .bin + 删 .next_slot + 指针回 1。"""
        import os
        self._features.clear()
        for i in range(1, 5):
            try:
                os.remove(f"{_DB_DIR}/id{i}.bin")
            except Exception:
                pass
        try:
            os.remove(_NEXT_SLOT_PATH)
        except Exception:
            pass
        self._next_slot = 1
        print("[FaceDB] cleared (memory + disk + pointer)")
```

注：现有 `clear_disk()`（只删 .bin）保留，`clear()`（删 .bin + .next_slot + 指针回1）新增，后者供后续清除按钮用。本步不接 UI，但方法补全。

### 6.2 `core/id_registry.py` — 新建可复用注册控制器

```python
# core/id_registry.py — 可复用注册控制器
#
# K2 = GPIO0 输入（fpioa.set_function(0, FPIOA.GPIO0)）。
# 主线程软件边沿检测：按下瞬间置 pending=True（只一次，防按住连触发）。
# AI 线程每帧调 try_register(feature)：pending(2秒内) → face_db.register
# + 蜂鸣 + 清 pending。
#
# 不依赖具体 AI 模型：脚本自己提 feature 传入。后续手势/物体脚本复用。

import time
from machine import Pin, FPIOA

class IdRegistry:
    def __init__(self, fpioa, pin=0, valid_level=0):
        """valid_level：按下时电平（默认 0=低电平有效）。"""
        fpioa.set_function(pin, FPIOA.GPIO0 + pin)
        self._k2 = Pin(pin, Pin.IN, Pin.PULL_UP)
        self._valid_level = valid_level
        self._prev_pressed = False
        self._pending = False
        self._pending_time = 0
        self._last_slot = None

    def poll_k2(self):
        """主线程 task_handler 间隙调。软件边沿：松开→按下 瞬间置 pending。"""
        pressed = (self._k2.value() == self._valid_level)
        if pressed and not self._prev_pressed:
            self._pending = True
            self._pending_time = time.ticks_ms()
        self._prev_pressed = pressed

    def try_register(self, feature, buzzer=None):
        """AI 线程每帧调。pending(2秒内) → face_db.register + 蜂鸣 + 清 pending。
        返回 slot_id（注册了）或 None（没按/超时/失败）。"""
        if not self._pending:
            return None
        if time.ticks_diff(time.ticks_ms(), self._pending_time) > 2000:
            self._pending = False
            print("[IdRegistry] pending timeout, discarded")
            return None
        self._pending = False
        try:
            from core.face_db import face_db
            slot = face_db.register(feature)
            self._last_slot = slot
            if buzzer is not None:
                buzzer.beep(ms=80)
            return slot
        except Exception as e:
            print("[IdRegistry] register failed: %s" % e)
            if buzzer is not None:
                buzzer.beep(ms=200)
            return None

    @property
    def last_slot(self):
        return self._last_slot
```

### 6.3 `scripts/face_detect/app.py` — 接入 + 收尾

**a. run() 加 id_registry 初始化 + 主循环 poll_k2：**

```python
def run(runtime):
    global running, db_features, id_registry
    # ... 现有 media_init/lvgl_init/face_db.init_features/face_reg 加载 ...
    from core.id_registry import IdRegistry
    id_registry = IdRegistry(runtime.fpioa, pin=0)
    running = True
    time.sleep_ms(100)
    _thread.start_new_thread(face_det_thread, ())
    while True:
        os.exitpoint()
        id_registry.poll_k2()              # 主线程 K2 边沿检测
        time.sleep_ms(lv.task_handler())
```

**b. AI 线程 face_det_thread 识别后加 try_register（复用 Step 5 特征）：**

```python
            if det_boxes and landms and face_reg is not None:
                try:
                    max_i = max(range(len(det_boxes)),
                                key=lambda i: det_boxes[i][2] * det_boxes[i][3])
                    face_reg.config_preprocess(landms[max_i])
                    feature = face_reg.run(img_np)
                    matched_id = database_search(feature, db_features)
                    recognition_results.append((max_i, matched_id))
                    # Step 7: K2 注册（复用刚提的特征，不重复 NPU 推理）
                    id_registry.try_register(feature, runtime.buzzer)
                except Exception as e:
                    print("[baseline-face] recog error: %s" % e)
```

### 6.4 `main.py` + `core/app_runtime.py` — 收尾临时定位

**main.py:106-110**：删除"face_detect 跳过 init_app"分支，face_detect 走 `runtime.init_app("face_detect", fpioa)`（与其他脚本统一）。

**app_runtime.py `init_app`**：删除"face_detect 跳过 touch/fonts/services"分支（line 151-163），face_detect 正常 `_init_touch` + `fonts.load_all()` + `_init_services`。

**app_runtime.py `_init_services`**：恢复 Buzzer 创建（取消 line 184-188 注释），`self.buzzer = Buzzer(...)` + `set_enabled`。buzzer=None 守卫在 id_registry/face_detect 调用处保留（对无 buzzer 场景友好）。

## 7. 数据流（完整）

```
每帧 AI 线程：
  snapshot(chn0+chn2) → face_det.run → (boxes, landms)
    → 选最大脸 → face_reg.run → feature(512维)
    → database_search(feature, db_features) → matched_id → recognition_results(识别,彩框)
    → id_registry.try_register(feature, buzzer)
        ├─ 无 pending → 返回 None（不注册）
        └─ pending(2秒内) → face_db.register(feature)
                              ├─ 空slot优先/轮转覆盖
                              ├─ 写内存 + flush_to_disk + save_next_slot（当场落盘）
                              └─ 返回 slot → 蜂鸣80ms
                              （失败 → 蜂鸣200ms）
  draw_result(彩框+ID) → show_image(OSD1) → gc.collect()
主线程每帧： id_registry.poll_k2(边沿置pending) + task_handler

注册后下一帧：feature 已在 db_features 内存 → database_search 命中 → 彩框+ID 当场显示
持久化：当场 flush 落盘 → reset/断电后 init_features 读回 → 下次进 face_detect 仍识别
```

## 8. 错误处理

- **K2 按下时无脸**：`_do_register`/`try_register` 无 pending 不触发；若 pending 置位但本帧无脸（det_boxes 空）→ 不进入识别分支 → try_register 不被调 → pending 留待 2 秒超时丢弃（不会误注册到空特征）。
- **face_reg 提特征失败**：识别分支 try/except 捕获，try_register 不被调，pending 保留待超时。
- **face_db.register 写盘失败**：flush_to_disk/_save_next_slot 各自 try/except 打印，不抛（内存已写，识别仍生效，仅持久化失败）。
- **buzzer=None**：try_register/poll_k2 守卫，无声但功能正常。
- **.next_slot 文件损坏**：`_load_next_slot` 解析失败 → 默认 1。
- **GPIO0 电平假设错误**：valid_level 板端实测，若按下是高电平则改 valid_level=1（构造参数）。

## 9. 线程/坑约束

- **坑#2（FATFS/DMA 文件 I/O 时机）**：`face_db.register` 的 flush 在 AI 线程运行期执行（试法1）——这是本次核心验证点。若卡 → 退化为"主线程 task_handler 间隙 flush"（试法2，fallback，本步不实现）。`_load_next_slot` 在 `init_features` 内（主线程、AI 线程启动前安全窗口）。
- **坑#18（kmodel 加载 + open 异常污染）**：`_load_next_slot` 是 init_features 内额外 open（读 .next_slot），与读 .bin 同位置，一并验证读路径是否卡（根因B 延伸）。register 内 flush + save_next_slot 是写路径 open，验证写路径是否卡。
- **坑#10（gc 触发 LVGL 终结器）**：AI 线程不碰 LVGL（注册只写 face_db 内存 + 文件，不碰 LVGL 对象），每帧 gc 安全。
- **坑#16（AI 循环每帧 gc）**：保留，不变。
- **坑#19（mobile kmodel）**：不变，face_reg 仍用 mobile 512 维。
- **线程协作**：`_pending`/`_prev_pressed`/`_pending_time` 主线程写、AI 线程读（单方向标志传递，MicroPython GIL 下布尔/整数读写原子，无锁安全）；`face_db._features` AI 线程 register 写、AI 线程 database_search 读（同 AI 线程，无竞争）。

## 10. 测试（host AST + import stub）

新增 `tests/test_face_register.py`（照搬 test_face_detect.py runner 模式）：

- `test_face_db_register_fills_empty_slot_first`：register 填空 slot 优先（stub face_db，连 register 4 次填 id1-4，_next_slot 不动）。
- `test_face_db_register_rotates_when_full`：4 满后第 5 次 register 覆盖 _next_slot(=1)→指针变2；第 6 次覆盖 2→指针变3。
- `test_face_db_persists_next_slot`：register 后 _save_next_slot 被调；_load_next_slot 读回正确值。
- `test_face_db_clear_resets_pointer`：clear 后 _next_slot=1 + 删 .next_slot。
- `test_id_registry_poll_k2_edge_detect`：stub Pin，模拟 松开→按下 置 pending 一次；按住不松只置一次。
- `test_id_registry_pending_timeout`：pending 置位后 >2 秒 try_register 返回 None 且清 pending。
- `test_id_registry_try_register_calls_face_db`：pending 时 try_register 调 face_db.register + 蜂鸣 + 清 pending；无 pending 返回 None。
- `test_face_detect_run_inits_id_registry`：AST 检查 face_detect.run 含 IdRegistry 初始化 + poll_k2 调用。
- `test_face_detect_ai_thread_calls_try_register`：AST 检查 face_det_thread 含 try_register(feature, runtime.buzzer)。
- `test_main_no_face_detect_init_app_skip`：main.py 不再含 face_detect 跳过 init_app 的分支。
- `test_app_runtime_init_app_no_face_detect_skip`：app_runtime init_app 不再对 face_detect 跳过 touch/fonts/services。
- `test_app_runtime_buzzer_created`：_init_services 创建 Buzzer（不再注释）。

现有 `tests/test_face_detect.py`：Step 5 测试保留；若 init_app 收尾影响 `test_face_detect_baseline_has_no_camerai_ui_dependencies` 等基线隔离测试，相应调整（face_detect 不再"零 CamerAI UI 依赖"）。

## 11. 板端验收

1. 上电 → 主菜单 → 点 face_detect → 进 face_detect 不卡（验证 init_app 收尾 + buzzer 接回不引入卡死）。
2. 长时间连跑（5 分钟+）不卡，帧率稳定。
3. 对脸按 K2：蜂鸣 80ms + 当场出现彩色框+ID（注册成功，内存即生效）。
4. 注册 4 张脸（id1-4）后，第 5 次按 K2 覆盖 id1（轮转覆盖 B），第 6 次覆盖 id2。
5. 注册后退出 face_detect → reset 回主菜单 → 再进 face_detect → 已注册人脸仍识别（持久化生效，init_features 读回 .bin + .next_slot）。
6. **核心验证**：注册时当场 flush 不卡（试法1）——若卡，记录现象，退化为试法2（主线程 flush，后续 step）。
7. buzzer 接回后不卡 → 排除 PWM0 为卡死元凶；若卡 → buzzer/PWM0 即元凶，回退 buzzer=None。

## 12. 不在 Step 7 范围

- UART 上送（Step 6）。
- 保存/清除弹窗、toast、完整顶/底栏 UI。
- 阈值 0.75 实测调优（验收时顺带观察）。
- 试法2（主线程 flush fallback）——仅当试法1卡死才做。
- 后续脚本（手势/物体）实际接入 id_registry（本步只把模块做好 + face_detect 接入验证）。
