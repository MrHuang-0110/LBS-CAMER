# 主菜单滚动内存稳定性设计

> 创建日期:2026-06-29
> 范围:`main.py` 主菜单模式与 `ui/main_menu.py` 主菜单滚动实现

## 背景

当前主菜单在静止时 `gc.mem_free()` 缓慢下降，滚动时下降更快；当内存耗尽触发自动 GC 后会死机。已知 `C:\Users\24160\Desktop\摄像头开发\CamerAPP\DurUI.py` 的菜单不会死机，即使 mem 耗尽后自动回收，主动 `gc.collect()` 也不会死。

对比后，最可疑差异不是 reset 运行框架，而是主菜单滚动路径和 LVGL flush 路径：

- CamerAI 主菜单每次选中态变化会创建 `lv.anim_t()` 和 Python `set_custom_exec_cb()` 闭包，用 token 让旧动画失效，但没有真正删除旧动画。
- DurUI 使用 LVGL 原生 scroll snap，并在已有对象上调用 `set_style_transform_zoom()` / `set_x()`，滚动过程中不创建 Python 自定义动画闭包。
- CamerAI 菜单运行在 `FULL + OSD2 + flush 清非活跃 buffer` 路径；DurUI 是更简单的 `DIRECT + 单层 show_image` 路径。

## 目标

1. 先用最小诊断验证主因，避免继续盲改。
2. 若验证指向 Python 自定义动画，则将主菜单滚动改为 DurUI 风格，消除滚动过程中的 `lv.anim_t + Python callback` 分配。
3. 保留当前主菜单视觉语义：黑底、深灰卡片、左图标、右文字、居中选中、非选中缩小/靠右/半透明。
4. 不改 reset 切换架构，不改脚本运行框架，不改各识别脚本。

## 非目标

- 不重写 `core/app_runtime.py` 的脚本模式初始化。
- 不改变相机、settings、face/tag/object/color_detect 的 UI 和主循环。
- 不在首轮改动 display render mode 或 OSD2 flush 策略，除非动画改造验证失败。

## 方案概览

采用两阶段方案：

1. **根因验证阶段**：给主菜单增加可控的动画禁用/诊断路径，让选中态变化直接应用几何，不创建 `lv.anim_t`；同时增加主菜单 mem 诊断打印，记录静止、慢滚、快滚下每秒 `gc.mem_free()`。
2. **正式改造阶段**：移除滚动选中态中的 Python 自定义几何动画，改用 DurUI 风格的距离驱动视觉状态。每次滚动根据卡片中心与视口中心距离计算 `t`，用 `transform_zoom`、`set_x`、`opa` 更新卡片视觉。

如果这两阶段后仍然出现持续内存下降或 GC 死机，再进入第三阶段单变量验证 display flush：主菜单专用 `DIRECT`、去掉 flush 内 `bytearray(0)` 清零、或调整 GC 安全点。第三阶段不包含在首轮实现内。

## 组件设计

### `ui/main_menu.py`

职责保持为主菜单 UI。新增/调整以下内部能力：

- 增加模块级诊断常量，例如 `MENU_DIAG_MEM = True` 或类似命名，用于板端诊断时打印每秒内存。
- 增加模块级视觉模式常量，例如 `USE_PY_ANIM = False`，首轮默认不使用 Python 自定义 `lv.anim_t`。
- `_CardSlot.set_visual_state()` 不再在滚动选中态中调用 `_animate_geometry()`。
- 新增或改造 `_CardSlot.apply_scroll_visual(t)`：
  - `t=0` 表示卡片中心在视口中心；`t=1` 表示远离中心。
  - `zoom = ZOOM_CENTER - t * (ZOOM_CENTER - ZOOM_SIDE)`。
  - `x = selected_left + int(t * card_shift)`，或保持当前右边缘收拢模型的等价视觉。
  - `opa = OPA_SELECTED - t * (OPA_SELECTED - OPA_NORMAL)`。
- `_on_scroll()` 调用 `_apply_scroll_visuals()`，只更新已有对象的 style/x/opa，不创建 Python 动画对象。
- `_on_scroll_end()` 保留吸附到最近卡片；优先考虑 LVGL 原生 `SCROLL_SNAP.CENTER`，如 K230 绑定不可用则保留当前 `_snap_to_nearest()`，但吸附过程不创建 Python 动画。
- `press_animation()` 属于点击反馈，不是滚动持续分配主因。首轮可保留；若后续仍有点击后内存异常，再单独替换为瞬时反馈。

### `main.py`

主循环保持不变。若需要 mem 诊断，可在 `ui/main_menu.py` 内部完成，避免污染 main loop。

### `core/app_runtime.py` / `hw/lcd.py`

首轮不改。它们仅作为后续 display flush 单变量验证对象。

## 数据流

滚动输入数据流：

1. 触摸输入进入 LVGL。
2. LVGL 触发 `MainMenu._on_scroll()`。
3. `_on_scroll()` 读取 `scroll_y` 和视口高度。
4. `_apply_scroll_visuals()` 逐张计算卡片中心距和归一化距离 `t`。
5. 每张 `_CardSlot` 只在已有对象上更新 `transform_zoom`、`x`、`opa`。
6. `SCROLL_END` 后计算最近卡片并吸附，更新 `_selected_index`。

诊断数据流：

1. 主菜单记录上次打印时间和序号。
2. 在 `_on_scroll()` 或主菜单周期安全点统计 `gc.mem_free()`。
3. 每秒打印一次 `seq`、`mem`、`selected_index`，必要时打印滚动视觉更新次数。
4. 主动 `gc.collect()` 只允许放在 `lv.task_handler()` 返回后的安全点，不放在 LVGL 事件回调、flush 回调、对象构建循环中。

## 错误处理与 K230 约束

- 不在滚动事件中执行文件 I/O。
- 不在滚动事件中主动 `gc.collect()`。
- 不在 `menu.show()`、BootSplash 回调栈、LVGL flush 回调里主动 GC。
- 不引入新的每帧 Python 对象分配热点，例如闭包、临时大 list、动态 bytearray。
- 对 `set_style_transform_zoom()` 等 LVGL API 使用 `try/except BaseException` 兜底，避免单个绑定不支持导致菜单不可用。

## 测试策略

### 静态/单元测试

在 PC pytest 环境中无法真实运行 K230 LVGL，但可以做 AST/源码级防回归测试：

- 测试主菜单滚动路径不再调用 `_animate_geometry()`。
- 测试 `_animate_geometry()` 不再由 `set_visual_state()` 的默认路径触发。
- 测试源码中存在 `apply_scroll_visual` 或等价函数，明确滚动视觉由距离驱动。

### 板端验证

1. 启动主菜单后不触摸，观察 5 分钟 mem 日志。
2. 慢速上下滚动 5 分钟，观察 mem 是否持续单调下降。
3. 快速连续滚动 10 分钟，观察是否死机。
4. 在 `lv.task_handler()` 返回后的安全点执行一次主动 `gc.collect()`，确认不死机。
5. 点击进入一个脚本，再 reset 返回主菜单，确认主菜单仍可显示并滚动。

## 验收标准

- 快速滚动 10 分钟不死机。
- `gc.mem_free()` 允许小幅波动，但不能持续单调下降到耗尽。
- 主菜单视觉仍满足当前产品设计：居中卡片明显突出，非居中卡片较小/靠右/半透明。
- 点击居中卡片仍能写入 `.next_script` 并 `machine.reset()` 进入脚本。
- 若首轮验证失败，必须记录失败现象，并只开启 display flush 单变量排查，不叠加其它修复。

## 实施顺序

1. 增加 PC 侧源码级回归测试，先证明当前实现仍有滚动动画风险。
2. 加诊断开关并禁用滚动 Python 自定义动画，形成最小验证版本。
3. 板端验证滚动 mem 行为。
4. 将滚动视觉正式改为 DurUI 风格距离驱动。
5. 运行 PC 测试和板端验收。
6. 若仍失败，再单独设计 display flush 验证方案。
