# 标签识别改为全屏扫描 + 动态排序上报（设计）

日期：2026-08-03
状态：已与用户确认需求

## 背景与动机

当前 `scripts/tag_detect/app.py` 的标签识别采用"按键学习 ID"模式：按 KEY2 把当前检测到的第一个码注册进 `TagDB` 槽位 1-4（轮转覆盖），上传协议 `TYPE_TAG_DETECT (0x04)` 固定 4 槽 × 10 字节 = 40 字节载荷，id 字段是槽位号。

用户希望改为**全屏自动扫描**：不做按键学习，每帧识别屏幕上所有可识别的码，按屏幕从左到右排序，最左边为目标 1，依此类推；上传的 id 字段直接使用实际码值（AprilTag 的 `tag.id()`），不再使用槽位号。

## 需求（已澄清）

1. **保留双功能**：AprilTag + 二维码（底栏切换卡片保留），两者都走同样的全屏扫描 + 排序逻辑。
2. **取消按键学习链路**：移除 KEY2 注册（`id_registry`）、`TagDB` 匹配/持久化、清空/保存浮层、注册计数；底栏最左边 list 图标仅作占位（不可点击）。
3. **排序**：每帧对当前检测到的所有码按 x（左上角）从小到大排序，最左边 = 目标 1，每帧独立重排。同 x 按 y 升序（稳定排序）。
4. **id 字段**：
   - AprilTag 模式：id = 实际 `tag.id()`；TAG36H11 id 范围 0~586，超过 255 固定输出 255（协议 id 字段 1 字节）。
   - QR 模式：payload 是字符串无法直接数值化，id = 排序序号（第 1 个=1，第 2 个=2 …），照常参与排序与上报。
   - 主机侧不区分当前功能，id 语义由板端当前激活功能决定，协议文档注明。
5. **动态数量上报**：检测到 N 个就发 N 组（N×10 字节），**每帧不固定、不补齐**。单帧上限 25 个（协议 length 字段 1 字节，payload ≤ 255B = 25×10B），超出按 x 排序后截取前 25 个。
6. **画框颜色**：所有目标画框**统一白色**（不按序号取色、不循环）。
7. **协议帧格式不变**：HEAD/SRC/DST/length(1B)/type/payload/chk/TAIL 完全保持；length 字段本来就是动态的（`tx[3] = len(payload)`），仅载荷长度从固定 40B 变为动态 N×10B。

## 架构

### 1. `comm/host_api.py` — 泛化 send_id_data 支持动态槽位

- `send_id_data(msg_type, slots)`：从"固定 4 槽 × 10B"改为"任意 N 槽 × 10B"。
  - `slots` 长度任意（≤ 25），payload = `len(slots) * 10` 字节。
  - **只编码实际存在的槽位，不补齐 0 组**：`slots=None` 或空列表 → 0 字节载荷（length=0 帧，主菜单/相机场景；主机按 length 解析为"无目标"）。
  - 每组仍 10 字节：id(1B) + x(2B BE) + y(2B BE) + w(2B BE) + h(2B BE) + conf(1B)。
- 预分配缓冲调整：
  - `_id_payload`：40B → `bytearray(250)`（25×10 上限）。
  - `_tx`：64B → `bytearray(257)`（5 + 250 + chk + TAIL）。
  - 帧注释同步更新（payload ≤ 40 → payload ≤ 250）。
- 新增模块级常量 `MAX_ID_SLOTS = 25`。
- `tick()` / `send_face_data()` 不动（仍传 4 槽 → 40B，向后兼容）。

**兼容性**：face_detect / object_detect / color_detect / gesture 等脚本仍传 4 槽列表 → 40B 载荷，行为与现在完全一致。`send_id_data` 是唯一改动点，`send_frame` 的 length 计算逻辑（`length = len(payload)`）不变。

### 2. `scripts/tag_detect/app.py` — 移除学习链路，改为扫描排序

**删除/停用**：
- `from core.id_registry import IdRegistry`、`_init_registry()`、`_id_registry` 全局、`poll_k2()` 主循环调用、`has_pending`/`try_register` 分支。
- `from core.tag_db import TagDB`、`_april_db`/`_qr_db` 实例、`_APRIL_DB_PATH`/`_QR_DB_PATH`、`load_from_disk`/`flush_to_disk`。
- 清空/保存浮层：`_on_list_clicked`、`_on_clear_clicked`、`_on_save_clicked`、`_on_overlay_clicked`、`_process_overlay_close` 全部移除；list 图标不挂任何点击事件（纯占位）。
- 注册计数 `_count_label`、`_refresh_count()`、i18n key `tag_detect.registered` 的使用。

**on_frame 新流程**：
```
img_det = sensor.snapshot(chn=CAM_CHN_ID_1)
detected = []  # [(code_id, x, y, w, h), ...]
if 当前功能 == april:
    tags = img_det.find_apriltags(families=image.TAG36H11)
    detected = [(tag.id(), *tag.rect()) for tag in tags]
else:
    codes = img_det.find_qrcodes()
    detected = [(code.payload(), *code.rect()) for code in codes]

slots = tag_scan.build_slots(detected, qr_mode=(当前功能 != april))  # 纯逻辑模块
# 按 x 升序(同 x y 升序)、截断 25、id 映射(april>255→255 / qr=序号)、坐标 x2

for i, (id_val, x, y, w, h, conf) in enumerate(slots):
    # 全白框 + 码值标签
    img.draw_rectangle(x, y, w, h, color=(0xFF, 0xFF, 0xFF, 0xFF), thickness=2)
    img.draw_string_advanced(x, y - 24, 24, str(detected[i][0]), color=白)

if _RUNTIME.host is not None:
    _RUNTIME.host_tick(slots)                 # 动态 N 槽
```

**新增纯逻辑模块 `core/tag_scan.py`**（无 MicroPython 依赖，host 可真单测）：
- `build_slots(detected, qr_mode)` → `list[tuple(id_val, x*2, y*2, w*2, h*2, 100)]`
  - 按 `(x, y)` 升序排序（x 同则 y 升序，稳定）
  - 截断前 25 个
  - `qr_mode=False`（AprilTag）：`id_val = code_id if code_id <= 255 else 255`
  - `qr_mode=True`：`id_val = 排序后序号 i+1`（code_id 仅用于标签显示，不参与编码）
- 模块级常量 `MAX_SLOTS = 25`（供 host_api 与 app 共用/对齐）
- 纯内置类型，import 无副作用

- 画框颜色常量：删除 `BOX_COLORS`/`BOX_UNKNOWN` 区分，统一白色 `0xFFFFFF`。
- 标签文本：AprilTag 显示实际码值（`str(tag.id())`），QR 显示 payload 文本。
- 屏幕中心绿色十字参考线保留。
- `run()`：移除 DB 加载/退出刷盘/`_init_registry`，主循环不再 `poll_k2()`。
- `_build_ui()`：底栏只剩 AprilTag/QR 卡片 + list 占位图标（无事件）。

### 3. 协议文档 — `通讯协议.txt`

在"标签识别:0x04"处补充说明：
- 载荷为动态 `N×10` 字节（N = 本帧检测到的目标数，N ≤ 25），每组：id(1B) + x/y/w/h(各 2B 大端) + conf(1B)。
- id 语义：AprilTag 功能 = 实际 AprilTag 码值（>255 → 255）；QR 功能 = 排序序号（1,2,3…）。
- 槽位顺序 = 屏幕从左到右排序（x 最小 = 目标 1）。
- 主机侧按帧的 length 字段解析，不要假设固定 40B。

### 4. 测试

| 文件 | 类型 | 内容 |
|---|---|---|
| `tests/test_tag_scan.py` | **真单元测试**（新增，import core/tag_scan） | build_slots：x 升序、同 x y 升序、>255 截断为 255、25 截断、qr_mode 序号递增、坐标 x2 |
| `tests/test_tag_detect_app.py` | AST 契约（更新） | on_frame 仍调 find_apriltags/find_qrcodes/host_tick；**不再断言** try_register/registrar/TagDB/poll_k2；断言调用 tag_scan.build_slots |
| `tests/test_host_api.py` | 纯逻辑（更新） | send_id_data：4 槽 → 40B 载荷、0 槽/None → 0B、25 槽 → 250B、坐标大端不变 |

## 数据流

```
sensor chn1 (QVGA) → find_apriltags / find_qrcodes
   → 全屏码列表 → core/tag_scan.build_slots（排序 x→y、截断 25、id 映射、坐标 x2）
   → 构建 N 槽 (id_val, x*2, y*2, w*2, h*2, 100)
   → chn0 画白框 + 码值标签 → Display OSD1 显示
   → host_tick(slots) → send_id_data 动态 N×10B 载荷 → UART1 帧
```

## 错误处理

- `find_apriltags` / `find_qrcodes` 异常沿用现有 try/except → 空列表（AI 异常不杀循环）。
- 排序/截断为纯数据操作，无异常路径。
- `send_id_data` 若 slots 长度 > 25，截断到 25（防御，板端已截断）。

## 明确不做（YAGNI）

- 不扩展 length 字段到 2 字节（保持帧格式不变，上限 25 个已够用）。
- 不做跨帧跟踪/首次出现顺序锁定（每帧独立排序，用户例子已确认语义）。
- 不删除 `core/tag_db.py` / `core/id_registry.py`（face_detect 等其他脚本仍用 id_registry；tag_db 保留作为范式参考，仅 tag_detect 不再引用）。
- 不在帧内加模式标志（主机不区分，文档注明语义）。
- 不做颜色区分/循环（统一白色，用户明确要求）。

## 文件变更清单

| 文件 | 操作 | 说明 |
|---|---|---|
| `core/tag_scan.py` | 新建 | 纯 Python 排序/截断/id 映射，host 可真单测 |
| `scripts/tag_detect/app.py` | 修改 | 移除学习链路，on_frame 走 tag_scan，全白框，动态槽 |
| `comm/host_api.py` | 修改 | send_id_data 支持 N 槽动态载荷，缓冲扩到 250/257B |
| `通讯协议.txt` | 修改 | 0x04 载荷动态 N×10B + id 语义说明 |
| `tests/test_tag_scan.py` | 新建 | tag_scan 真单测 |
| `tests/test_tag_detect_app.py` | 修改 | AST 契约更新（无注册断言，有 build_slots） |
| `tests/test_host_api.py` | 修改 | 动态载荷断言 |
| `docs/superpowers/plans/2026-08-03-tag-scan-sort-plan.md` | 后续 | writing-plans 产出 |

## 主机侧配合（用户负责）

1. 主机 PikaScript 解析 0x04 帧时按 length 字段动态解析 N 组，不再假设固定 40B / 4 组。
2. 语义：id 按当前板端功能解释（AprilTag=码值 / QR=序号），或仅展示透传。
3. 本仓库同步更新 `通讯协议.txt` 供主机侧参考。
