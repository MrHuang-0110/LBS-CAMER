# CamerAi reset 切换框架 + 全 APP 迁移 Design

> 日期：2026-06-17
> 主题：CamerAi 从"同进程脚本切换 + 常驻显示栈"架构改为"reset 切换 + 每脚本独立 init"架构（对齐官方综合例程），根治 face_detect 连跑卡死。一个计划内迁移全部 APP（face_detect/settings/camera），最终无两套架构并存。face_detect 先做验证稳定，同计划内接着迁移 settings/camera。

## 1. 背景

### 1.1 卡死根因（systematic-debugging 结论）

face_detect 连跑卡死。多轮定位后确认根因在**架构层**：

- **裸跑测试** `test_face_baseline_camerai_sensor.py`（CamerAi Sensor 配置 1280×960 + 3 通道 + chn2 1024×768，自己 init 显示栈）→ fc=3060 稳定。
- **同代码经 CamerAi main.py 架构启动**（复用开机常驻 lcd/sensor/Display/MediaManager，runner.launch 同进程切换）→ 第一次 fc=1200 卡，之后断电/复位/换板子都 fc=1。
- **裸跑仍稳定**（换板子也稳定）→ 不是板子硬件，是 CamerAi 同进程架构与 K230 硬件状态管理冲突。

关键证据（`test_face_baseline_with_menu_sim.py` 决定性实验）：
- sensor.run() + 主线程单独长跑 task_handler 循环 → 卡死
- sensor.run() + 跳过循环直接启 AI 线程（持续 snapshot 消费）→ 稳定
- → sensor.run() 后必须有消费者持续 snapshot，否则缓冲/DMA 累积卡死

CamerAi 同进程架构无法保证此条件（on_enter 加载 kmodel 数秒、runner.tick 与 task_handler 交织、常驻栈跨脚本状态污染）。官方综合例程用 `machine.reset()` 切换脚本，每次进程独立 init，天然干净，稳定。

### 1.2 官方综合例程模式

`E:\...\综合例程-CanMV版\CanMV v1.3\main.py` 的 `DemoScriptRunner.run`：
```python
def run(self, demo_script_path):
    with open("/sdcard/main.py", "wb") as f:
        f.write(code_src)   # 覆写 main.py 为目标脚本
    machine.reset()          # 整机重启跑目标脚本
```
官方**覆写 main.py + reset**。但覆写 main.py 中途断电有变砖风险。本设计用更安全的"main.py 不动 + next_script 标记"变体。

### 1.3 已验证的稳定模式

`test_face_baseline.py` / `test_face_baseline_camerai_sensor.py` 裸跑脚本证明：脚本自己 `Display.init + MediaManager.init + Sensor 配置 + sensor.run + LVGL init + 双线程（AI 线程 snapshot+NPU+show+gc / 主线程 task_handler）` → 稳定跑数千帧。face_detect 迁移即复用此模式。

## 2. 目标

- 根治 face_detect 卡死：改 reset 切换，每脚本进程独立 init，无状态污染。
- 对齐官方综合例程的 reset 切换模式。
- **全部 APP 迁移到 reset 框架**（face_detect/settings/camera），最终无两套架构并存。
- 实施顺序：face_detect 先做（验证新框架稳定）→ 同计划内迁移 settings/camera → 删除旧同进程架构（ScriptRunner/BaseScript 的 launch/tick 机制）。
- 保留所有功能：face_detect（4 脸识别+注册+持久化+UART）、camera（拍照/录像/图库）、settings（语言/蜂鸣等）。

## 3. 非目标（本次不做）

- 主菜单 UI 重构（保持现有 MainMenu，仅改启动/切换机制）。
- 新增 AI 类 APP（手势/物体等，框架稳定后再加）。
- 抽象 AIScriptBase 基类（框架稳定后后续做）。

## 4. 架构

### 4.1 reset 切换总流程

```
上电 → main.py（启动器，永不被覆写）
  │
  ├─ 读 /sdcard/CamerAi/.next_script
  │
  ├─ 空或不存在 → 主菜单模式:
  │     app_runtime.init_menu()  (Display/MediaManager/sensor/LVGL/字体/图标/host)
  │     显示 MainMenu → 卡片点击 → 写 .next_script=<category_id> → machine.reset()
  │
  └─ 有值 → 脚本模式:
        app_runtime.init_app(category_id)  (按 APP 需求 init)
        import scripts.<script> ; script.run(runtime)
        脚本退出 → 清 .next_script → machine.reset() 回主菜单
```

每次 reset 从 main.py 干净启动，按 .next_script 决定模式。每个进程独立 init，无跨脚本状态污染。

### 4.2 与当前架构对比

| 维度 | 当前（同进程）| 新（reset 切换）|
|------|-------------|---------------|
| 脚本切换 | runner.launch 同进程 | 写 .next_script + machine.reset |
| 显示栈 | 开机 init 常驻，跨脚本复用 | 每进程独立 init/deinit |
| sensor | 常驻，跨脚本复用 | 每进程独立配置+run |
| 状态共享 | 全局对象（lcd/runner/host）| 文件（config/lang/face_db/.next_script）|
| 握手 | 全局 host 常驻 | 每进程 init host，重连 |
| LVGL | 常驻 | 每进程独立 init |

### 4.3 组件

**main.py（重写为启动器）**
- 读 .next_script 决定模式
- 主菜单模式：init_menu + MainMenu + 卡片点击写 .next_script + reset
- 脚本模式：init_app + import script + script.run(runtime) + 清 .next_script + reset
- 不再有 runner.tick 主循环驱动 on_frame（脚本自己跑主循环）

**core/app_runtime.py（新建，公共 init + 资源管理）**
- `AppRuntime` 类：持有 display/sensor/lvgl_disp/host/lang/config/buzzer 等引用
- `init_menu()`：Display + MediaManager + sensor（菜单模式可能不需 AI 通道，按需）+ LVGL + 字体 + 图标预读 + host
- `init_app(category_id)`：Display + MediaManager + sensor（含 AI 通道 chn2）+ LVGL + 字体 + host；按 category 决定 sensor 通道配置
- `cleanup()`：sensor.stop + Display.deinit + MediaManager.deinit + lvgl_deinit（脚本退出前调，干净移交，虽然 reset 会清但显式更稳）
- 封装握手：HostAPI 实例 + poll_handshake（每脚本共用一套握手逻辑，状态不跨进程）
- 字体/图标加载复用现有 font_manager/icon_cache

**scripts/face_detect/app.py（重构为独立脚本）**
- `run(runtime)` 入口：用 runtime 的 sensor/display/host
- 自己 init NPU（face_det/face_reg kmodel + ai2d）
- 双线程：AI 线程（snapshot chn0+chn2 + NPU + 画叠加 + show_image + gc + UART + 注册检查）+ 主线程（task_handler + K2/握手/退出检测）
- UI：顶/底栏 + 十字架/人脸框画 image.Image（对齐官方，AI 线程不碰 LVGL）
- 退出：停 AI 线程 + deinit NPU + runtime.cleanup + 返回（main.py 清 .next_script + reset）
- 复用裸跑已验证的稳定 AI 线程结构

**.next_script 标记文件**
- `/sdcard/CamerAi/.next_script`，内容 = category_id（如 "face_detect"）或空
- main.py 启动读，脚本退出清

### 4.4 数据流（face_detect 完整流程）

```
1. 主菜单点 face_detect 卡片:
   MainMenu on_card_click → 写 "face_detect" 到 .next_script → machine.reset()
2. main.py 启动 → 读 .next_script="face_detect" → app_runtime.init_app("face_detect")
   → import scripts.face_detect.app → face_detect.run(runtime)
3. face_detect.run:
   - init NPU (kmodel + ai2d)
   - 建 UI (顶/底栏 LVGL)
   - 启 AI 线程 (snapshot+NPU+画叠加+show+gc+UART)
   - 主循环: task_handler + K2/握手/退出检测
   - 用户按返回 → 停 AI 线程 → deinit NPU → cleanup → return
4. main.py 收到 run 返回 → 清 .next_script → machine.reset()
5. main.py 启动 → 读 .next_script=空 → 主菜单模式
```

## 5. 详细改动

### 5.1 core/app_runtime.py（新建）

封装公共 init。关键方法：

```python
class AppRuntime:
    def __init__(self):
        self.display = None
        self.sensor = None
        self.lv_disp = None
        self.host = None
        self.lang = None
        self.config = None
        self.buzzer = None
        # draw_buf 等 LVGL 资源

    def init_menu(self):
        """主菜单模式 init：Display/MediaManager/sensor(预览通道)/LVGL/字体/图标预读/host"""
        # 复用现有 lcd.py 的 Display+MediaManager+sensor 配置逻辑（抽出）
        # + lvgl_init + fonts.load_all + icon_cache.preload_* + HostAPI

    def init_app(self, category_id):
        """APP 模式 init：按 category 决定 sensor 通道配置（face_detect 需 chn2 AI 通道）"""
        # Display/MediaManager/sensor(含 chn2)/LVGL/字体/host
        # 不预读所有图标（只按 APP 需要的）

    def cleanup(self):
        """脚本退出前清理（显式，虽 reset 会清）"""
        # sensor.stop + Display.deinit + MediaManager.deinit + lvgl_deinit
```

> sensor 通道配置（chn0 预览/chn1 拍照/chn2 AI）从 lcd.py 抽到 app_runtime，按 category 决定配哪些通道（主菜单只需 chn0 预览或不需要 sensor；face_detect 需 chn0+chn2）。

### 5.2 main.py（重写为启动器）

```python
def main():
    next_script = _read_next_script()  # 读 .next_script
    runtime = AppRuntime()
    if next_script:
        # 脚本模式（所有 APP 统一走此路径）
        runtime.init_app(next_script)
        script = _load_script(next_script)  # import scripts.<script>
        try:
            script.run(runtime)
        except Exception as e:
            print("[CamerAi] script run error: %s" % e)
        _clear_next_script()
        machine.reset()
    else:
        # 主菜单模式
        runtime.init_menu()
        menu = MainMenu(runtime.config, runtime.buzzer, runtime.lang,
                        on_card_click=lambda cid: _launch(cid))
        menu.show()
        while True:
            lv.task_handler()
            time.sleep_ms(lv.task_handler())  # 对齐官方
```

`_launch(cid)`：写 .next_script=cid + machine.reset()。所有 APP（face_detect/settings/camera）统一走 reset 切换，无 runner.launch 同进程路径。

### 5.3 scripts/face_detect/app.py（重构）

从继承 BaseScript + runner.tick 架构，改为独立 `run(runtime)` 脚本：

```python
def run(runtime):
    """face_detect 独立脚本入口（reset 切换框架下，每进程独立 init）。"""
    app = FaceDetectApp(runtime)
    app.start()  # init NPU + UI + 启 AI 线程 + 主循环
    # start 返回时表示用户退出
    app.cleanup()  # deinit NPU
    # main.py 负责清 .next_script + reset
```

`FaceDetectApp`：
- `start()`：init NPU（kmodel+ai2d，复用现有 FaceDetApp/FaceRegistrationApp 类）+ 建 UI（顶/底栏 LVGL）+ 启 AI 线程 + 主循环（task_handler + K2 轮询设注册标志 + 握手 poll + 退出检测）
- AI 线程 `_ai_loop`：复用裸跑已验证结构（snapshot chn0+chn2 + face_det.run + 画叠加 + show_image + gc + UART + 注册检查）
- 画叠加 `_draw_overlay`：十字架+人脸框+ID 画 image.Image（AI 线程不碰 LVGL）
- `cleanup()`：停 AI 线程 + deinit NPU（del kpu/ai2d/tensors + gc）

业务逻辑（识别循环、K2 注册、_search_face、face_db、保存/清除弹窗、toast、UART 协议）从现有 face_detect 迁移，逻辑不变，只改生命周期入口（BaseScript.on_enter/on_frame/on_exit → run/start/cleanup）。

### 5.4 settings/camera 迁移（同计划内，face_detect 验证后）

face_detect 新架构板端验证稳定后，同计划内迁移 settings/camera，最终删除旧同进程架构。

**scripts/settings/app.py** → `run(runtime)` 入口：
- 当前 SettingsApp(BaseScript) + on_enter/on_exit，page 模式纯 LVGL UI
- 迁移：`run(runtime)` 用 runtime 的 LVGL/字体，跑设置 UI（语言/蜂鸣等），退出 return（main.py 清 .next_script + reset）
- 设置数据已文件化（config/app.json），reset 安全
- 不需 sensor/AI 通道，init_app("settings") 只 init Display/LVGL/字体/host

**scripts/camera/app.py** → `run(runtime)` 入口：
- 当前 CameraApp(BaseScript) + on_enter/on_frame/on_exit，stream 模式 snapshot+show_image
- 迁移：`run(runtime)` 用 runtime 的 sensor/display，跑相机主循环（snapshot+show_image+拍照/录像/图库），退出 return
- camera 当前稳定（无 NPU），迁移主要改生命周期入口（BaseScript → run）+ 自己 init sensor
- camera 用 chn0 预览 + chn1 拍照，init_app("camera") 配 chn0+chn1（不需 chn2 AI）

**删除旧架构**：
- 删 ScriptRunner 的 launch/tick/exit 同进程切换机制（reset 取代）
- 删 BaseScript 的 on_enter/on_frame/on_exit 生命周期（改 run/runtime 模式）
- 删 main.py 的 runner 主循环
- _base.py 保留共享工具（如有）或废弃

**最终状态**：所有 APP 走 reset 切换 + run(runtime)，无 ScriptRunner/BaseScript 同进程机制。

## 6. 错误处理

- .next_script 读取失败/损坏 → 当空处理（进主菜单）
- 脚本 run() 抛异常 → main.py 捕获 + 清 .next_script + reset 回主菜单（不砖）
- AI 线程异常 → 单帧 try/except，记日志继续
- 脚本退出 AI 线程同步 → _ai_running=False + 轮询 _ai_thread_alive（无 join）
- machine.reset() 失败 → 极少，板子硬件问题

## 7. 测试

### 7.1 主机端（AST/逻辑）
- main.py：AST + 读 .next_script 逻辑
- app_runtime.py：AST + init 方法存在性
- face_detect：AST + run() 入口存在 + _ai_loop 不碰 LVGL（线程安全契约）+ gc 每帧（坑#16）

### 7.2 板端
- **验收 1**：上电 → 主菜单正常显示
- **验收 2**：点 face_detect → reset → 进 face_detect → 连跑 5 分钟不卡死（核心，裸跑已验证模式）
- **验收 3**：face_detect 功能（4 脸识别+K2 注册+保存+退出重进持久化+UART 上送）
- **验收 4**：face_detect 退出 → reset → 回主菜单 → 再进 face_detect 仍稳定（反复进出，验证 reset 干净环境）
- **验收 5**（settings/camera 迁移后）：点 settings → reset → 进设置 → 改语言/蜂鸣 → 退出回主菜单，设置生效
- **验收 6**（settings/camera 迁移后）：点 camera → reset → 进相机 → 拍照/录像/图库正常 → 退出回主菜单
- **验收 7**：三个 APP 反复进出（face_detect ↔ settings ↔ camera），每次 reset 干净环境，都稳定

## 8. 风险

| 风险 | 应对 |
|------|------|
| main.py 重写影响开机 | 保留主菜单逻辑，仅加 .next_script 分支；先测主菜单能进 |
| face_detect 业务逻辑迁移量大 | 业务逻辑（识别/注册/DB/UI）直接搬，只改生命周期入口 |
| app_runtime 接口覆盖菜单+APP | init_menu/init_app 分离，按 category 配 sensor 通道（menu 无 sensor 或仅 chn0；face_detect chn0+chn2；camera chn0+chn1）|
| settings/camera 迁移 | 当前稳定，迁移主要改生命周期入口；camera 图库/录像逻辑直接搬 |
| 删除旧架构（ScriptRunner/BaseScript）影响 | 迁移完所有 APP 后才删；删除前确认无引用 |
| .next_script 写中途断电 | 文件小（几字节），写原子性风险低；写完才 reset |
| 三个 APP 都走 reset，频繁 reset 体验 | reset ~1-2 秒，可接受；官方综合例程同模式 |

## 9. K230 硬约束遵守

- 坑#2（FATFS/DMA）：每进程 init 后首次 task_handler 前完成文件 I/O（图标/字体/kmodel 加载）。face_detect on_enter 等价阶段（run 开始）加载 kmodel，在 AI 线程启动前。
- 坑#10（gc 触发 LVGL 终结器）：AI 线程不碰 LVGL（画 image.Image），每帧 gc 安全。
- 坑#14/15（VB/显示栈）：reset 切换每进程独立 init/deinit，不再"常驻不拆"——坑#14/15 的"常驻"前提被 reset 架构取代，每进程干净 init 自然满足。
- 坑#16（AI 循环每帧 gc）：AI 线程每帧 gc，对齐官方。

## 10. 后续（本次不做）

- settings/camera 迁移到 reset 框架（消除过渡期两套架构）
- 抽象 AIScriptBase（reset 框架下的 AI 脚本基类，含 _ai_loop 模板）
- 后续 AI APP（手势/物体/颜色）直接继承 AIScriptBase
