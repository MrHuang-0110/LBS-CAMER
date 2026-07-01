# 摄像头协议号顺延设计

- 日期: 2026-07-01
- 状态: 已确认,待实施
- 范围: 同步修改 K230 摄像头项目 `e:\LBS-CAMER-AI` 与主机固件项目 `E:\LBS-NEW-AI`,避开主机旧协议占用的 `0x08/0x09/0x0A`。

## 1. 背景与根因

板端运行 `gesture_detect` 时已通过诊断确认实际发送:

```text
[HostAPI] tick category=gesture_detect msg_type=0x08 slots=data
```

因此 K230 端脚本与 HostAPI 映射正确。主机端异常来自 `E:\LBS-NEW-AI\Drivers\DataFile\portAgree\portagree.c` 的 `port_data_parsing()`:

- `0x01~0x07` 被当作设备数据,会进入 `set_sensor_parameter()` → `refsh_camer()` → JSON 更新。
- `0x08` 被旧逻辑当作固件版本写入 ACK。
- `0x09` 是 `DEV_PORT_LINKE` 握手命令。
- `0x0A` 没有作为摄像头数据 case,会被丢弃。

所以道路识别 `0x07` 正常,从手势识别 `0x08` 开始主机 JSON 保持默认/旧值,表现为 `{"port":4,"camer":{"mode":0}}`。

## 2. 新协议号映射

保留已验证正常的 `0x01~0x07`,从手势识别开始改到 `0x10` 段。

| 功能 | 旧类型码 | 新类型码 |
|---|---:|---:|
| 主菜单 | `0x01` | `0x01` |
| 相机 | `0x02` | `0x02` |
| 人脸识别 | `0x03` | `0x03` |
| 标签识别 | `0x04` | `0x04` |
| 物体识别 | `0x05` | `0x05` |
| 颜色识别 | `0x06` | `0x06` |
| 道路识别 | `0x07` | `0x07` |
| 手势识别 | `0x08` | `0x10` |
| 人体识别 | `0x09` | `0x11` |
| 物体分类 | `0x0A` | `0x12` |
| 图像分类 | `0x0B` | `0x13` |

## 3. K230 项目修改

文件: `e:\LBS-CAMER-AI\comm\host_api.py`

修改类型常量:

```python
TYPE_GESTURE_DETECT = 0x10
TYPE_BODY_DETECT = 0x11
TYPE_OBJECT_CLASSIFY = 0x12
TYPE_IMAGE_CLASSIFY = 0x13
```

`CATEGORY_TYPE` 的 key 不变,继续映射到这些常量。脚本层不改。

文件: `e:\LBS-CAMER-AI\通讯协议.txt`

更新类型表,把手势/人体/物体分类/图像分类写成 `0x10~0x13`。

测试: 更新 `tests/test_host_api.py` / 对应 AST 测试里的类型断言。

保留本轮排查加入的 HostAPI 低频诊断打印,用于板端确认新类型:

```text
[HostAPI] tick category=gesture_detect msg_type=0x10 slots=data
```

## 4. 主机项目修改

文件: `E:\LBS-NEW-AI\Drivers\DataFile\camer\camer.h`

将摄像头模式枚举显式赋值,避免隐式连续值重新落入 `0x08/0x09/0x0A` 冲突区:

```c
CAMER_MENU_TYPE = 0x01,
CAMER_MODE_TYPE = 0x02,
CAMER_FACE_TYPE = 0x03,
CAMER_LABE_TYPE = 0x04,
CAMER_OBJECT_TYPE = 0x05,
CAMER_COLOR_TYPE = 0x06,
CAMER_WAY_TYPE = 0x07,
CAMER_GESTURE_TYPE = 0x10,
CAMER_BODY_TYPE = 0x11,
CAMER_OBJECT_BODY_TYPE = 0x12,
CAMER_PHOTO_TYPE = 0x13,
```

文件: `E:\LBS-NEW-AI\Drivers\DataFile\portAgree\portagree.c`

在 `port_data_parsing()` 的设备数据 case 中加入新摄像头类型:

```c
case 0x10:
case 0x11:
case 0x12:
case 0x13:
```

旧 `case 0x08` / `DEV_PORT_LINKE(0x09)` 保持不动,不破坏主机旧固件下载/握手机制。

## 5. 验证标准

1. K230 手势识别日志:

```text
[HostAPI] tick category=gesture_detect msg_type=0x10 slots=data
```

2. 主机 JSON 对应:

```json
{"port":4,"camer":{"mode":16,"id1":0,...}}
```

3. 人体识别:

```text
[HostAPI] tick category=body_detect msg_type=0x11 slots=data
```

主机 `mode=17`。

4. 物体分类:

```text
[HostAPI] tick category=object_classify msg_type=0x12 slots=data
```

主机 `mode=18`。

## 6. 非目标

- 不重构主机协议解析架构。
- 不改变帧头/长度/校验/40 字节 ID 数据格式。
- 不删除 `0x08` 版本 ACK 或 `0x09` 握手机制。
- 本轮先保留 K230 HostAPI 诊断打印,验证通过后可单独删除。
