# 主机→摄像头脚本切换指令 设计

> 状态:已确认,待 writing-plans 产出实施计划。
> 日期:2026-07-01
> 范围:仅 K230 项目 e:\LBS-CAMER-AI。主机侧 E:\LBS-NEW-AI 不动(已实现)。

## 目标

K230 摄像头能接收主机发送的协议帧命令,切换到任意脚本模式(或回主菜单)。
切换机制 = reset:写 `.next_script` + `machine.reset()`,与菜单点击路径完全一致。

## 现状(已探明,不需改动)

### 主机侧 `_camer_changer_camer_mode`(已实现)

`E:\LBS-NEW-AI\python\pikascript-lib\camer\_camer.c:7`,已绑定 PikaScript Python
`changer_camer_mode(port, mode)`。调
`MultiUart_SendFrame(port, &mode, 1, 0xA7, 0xFF, 10, 250)`。

`MultiUart_SendFrame`(`uart.c:888`)组帧:byte[0]=0x5A, [1]=0x97,
[2]=type参数(=DEV_ID_CAMER=0xA7), [3]=len(=1), [4]=index参数(=0xFF),
[5..]=payload(=mode), [5+len]=chk, [6+len]=0xA5。

→ 命令帧 = `5A 97 A7 01 FF <mode> <chk> A5`(8 字节),
chk = (0x5A+0x97+0xA7+0x01+0xFF+mode) & 0xFF。

mode 取主机 `CAMER_MODE` 枚举,与 K230 协议类型码一致:
0x01 菜单 / 0x02 相机 / 0x03 人脸 / 0x04 标签 / 0x05 物体识别 /
0x06 颜色 / 0x07 道路 / 0x10 手势 / 0x11 人体 / 0x12 物体分类 / 0x13 图像分类。

### K230 侧切换机制(已有,复用)

- `_write_next_script(category_id)`(`main.py:51`)+ `machine.reset()`(`main.py:132`)
  —— 菜单点击走此路径。
- 重启后 `main()` 读 `.next_script`(`main.py:190`)→ `run_script()`(`main.py:155`)
  直接进新脚本(不进菜单)。
- `_clear_next_script()`(`main.py:59`)清空文件。
- `run_script()` 末尾 `_clear_next_script()`(`main.py:179`)——切换是"写完即 reset",
  不走正常退出,不冲突。
- `.next_script` 用 `open(path,"w")` 创建,无 ENOENT 坑(坑#18 是读不存在文件触发)。

### K230 HostAPI UART 接收(已有,待扩展)

`comm/host_api.py` `poll_handshake()`(`host_api.py:205`)已把 UART1 全部输入读进
`self._rx_buf`,按 0x5A 对齐扫描 18 字节握手常量。当前只匹配握手常量,不校验和,
半帧保留尾部等下次拼接。`register_handler(cmd, cb)` 预留模式已存在(`host_api.py:269`)。

## 架构:方案 A

HostAPI 只管协议(解析帧、校验和、mode→category 映射、回调通知);
main.py 注册真实回调执行写文件+reset。职责清晰,HostAPI 可纯单测(喂字节、验回调),
无循环 import,复用已有 `register_handler` 预留模式。

## 组件与文件

### 1. `comm/host_api.py`(改)

- 常量:`TYPE_MODE_SWITCH = 0xFF`(命令帧 index 字节)、`MODE_SWITCH_SRC = 0x97`、
  `MODE_SWITCH_DST = 0xA7`、`MODE_SWITCH_FRAME_LEN = 8`。
- `MODE_TO_CATEGORY` dict(反向映射):
  - `0x01: None`(主菜单:清 .next_script + reset)
  - `0x02: "camera"` / `0x03: "face_detect"` / `0x04: "tag_detect"`
  - `0x05: "object_detect"` / `0x06: "color_detect"` / `0x07: "road_detect"`
  - `0x10: "gesture_detect"` / `0x11: "body_detect"`
  - `0x12: "object_classify"` / `0x13: "image_classify"`
- `_switch_handler = None` + `register_switch_handler(cb)`:注册切换回调,
  cb 签名 `cb(category)`(category 为 str 或 None)。
- 纯函数 `_parse_switch_frame(buf, offset)` → `(mode, next_offset)` 或 `None`:
  - 校验 buf[offset:offset+8] = `5A 97 A7 01 FF <mode> <chk> A5`
  - 校验 chk == (0x5A+0x97+0xA7+0x01+0xFF+mode) & 0xFF
  - 返回 mode 与 offset+8(消费整帧)
- `poll_handshake()` 扫描循环扩展:在每个 0x5A 位置,除尝试握手常量匹配外,
  也尝试 `_parse_switch_frame`;命中且 mode 在 `MODE_TO_CATEGORY` 中:
  - 取 category = `MODE_TO_CATEGORY[mode]`
  - 若 `self._switch_handler` 已注册 → 调 `self._switch_handler(category)`
  - 消费该帧(buf 从 offset+8 继续)
  - 打印诊断 `[HostAPI] switch frame mode=0x%02X category=%s`
- 校验和错/尾错/前缀错/未知 mode → 丢弃该帧,打印,不切换。
- 半帧(从某 0x5A 到 buf 末尾不足 8 字节)→ 等下一帧拼接(复用 `_rx_buf` 滑动保留逻辑)。

### 2. `main.py`(改)

- 启动时注册回调:在构造 runtime.host 之后(main_menu 与 run_script 两路径都需,
  或在 AppRuntime.init_app 后统一注册),调
  `runtime.host.register_switch_handler(_on_remote_switch)`。
- `_on_remote_switch(category)`:
  - `category is None`(回菜单):`_clear_next_script()` + `machine.reset()`
  - 否则:`_write_next_script(category)` + `machine.reset()`
  - 复用现成 `_write_next_script`/`_clear_next_script`,与菜单点击路径完全一致。
  - 打印 `[CamerAi] remote switch -> %s` % category。

### 3. `tests/test_host_api_switch.py`(新)

纯 Python 可跑(板端模块不可导入,只测 host_api.py 的解析/映射/回调):
- `test_mode_to_category_map`:MODE_TO_CATEGORY 反向映射正确
  (0x01→None, 0x13→image_classify, 0x10→gesture_detect 等)。
- `test_parse_switch_frame_valid`:构造正确帧 → 返回 mode。
- `test_parse_switch_frame_bad_checksum`:校验和错 → None。
- `test_parse_switch_frame_bad_tail`:尾非 A5 → None。
- `test_parse_switch_frame_bad_prefix`:前缀错 → None。
- `test_register_and_dispatch`:注册回调,喂帧到 `_rx_buf`,调 `poll_handshake`
  → 回调以正确 category 调用。
- `test_half_frame_no_dispatch`:半帧(不足 8 字节)不触发回调。
- `test_unknown_mode_no_dispatch`:未知 mode(如 0x99)不触发回调。
- AST 契约:`register_switch_handler` 存在、`MODE_TO_CATEGORY` 是 dict、
  `TYPE_MODE_SWITCH == 0xFF`。
- 自跑 `test_runner`(项目无 pytest)。

### 4. `tests/test_main_remote_switch_ast.py`(新)

AST 契约守护 main.py 注册点:
- `test_main_registers_switch_handler`:main.py 含
  `register_switch_handler(_on_remote_switch)`。
- `test_main_has_on_remote_switch`:main.py 含 `def _on_remote_switch`。
- `_on_remote_switch` 体含 `_write_next_script` 或 `_clear_next_script` +
  `machine.reset`(对应有/无 category 两分支)。

## 数据流

主机 `changer_camer_mode(port, mode)` → UART1 TX `5A 97 A7 01 FF <mode> <chk> A5`
→ K230 UART1 RX → `poll_handshake` 解析 → `MODE_TO_CATEGORY[mode]`
→ `_switch_handler(category)` → main.py `_on_remote_switch`:
  - category 为 None → `_clear_next_script()` + `machine.reset()` → 进主菜单
  - 否则 → `_write_next_script(category)` + `machine.reset()` → 进新脚本(热启动跳 LOGO)

## 错误处理

- 未知 mode(不在映射表)、校验和错、尾非 A5、前缀错 → 丢弃该帧 + 打印,不切换。
- 半帧 → 等下一帧拼接(不触发回调)。
- 切换回调未注册 → 只打印诊断,不切换(HostAPI 仍可独立单测)。
- `_write_next_script`/`machine.reset` 异常 → 由现有路径处理(与菜单点击同路径)。

## 验收标准(板端)

1. 摄像头跑任意脚本(如 image_classify 预览)。
2. 主机 PikaScript 调 `changer_camer_mode(4, 0x10)` → 摄像头 reset 后进手势识别。
3. 主机调 `changer_camer_mode(4, 0x01)` → 摄像头 reset 回主菜单。
4. 各 mode(0x02~0x13)切换均进对应脚本,无卡死/无残留。
5. 乱发坏帧(校验和错/尾错)→ 摄像头不切换、不崩、继续正常运行。
6. 切换后主机仍能收到新脚本的对应协议数据帧(mode 字段正确)。

## 非目标

- 主机侧任何改动(C API/Python 业务/帧格式均已就位)。
- 进程内切换(不 reset)。
- settings 模式切换(无对应 type 码)。
- 切换确认回执(主机不要求摄像头 ACK)。
- 多帧合并/流控优化。
