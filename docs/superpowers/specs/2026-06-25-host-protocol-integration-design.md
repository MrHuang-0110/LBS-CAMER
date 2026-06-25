# 上位机通讯协议接入设计（握手 + 4组ID推送，主菜单/所有脚本）

> 状态：已确认（用户 2026-06-25 批准设计方向）
> 范围：把 `comm/host_api.py` 已实现的二进制协议（握手 + 帧推送）接入 reset 框架主循环，
> 覆盖主菜单 + 全部脚本（_template / camera / settings / face_detect + 未来扩展）。

## 背景

`comm/host_api.py` 与 `通讯协议.txt` 早已定义协议：

- 帧格式：`帧头(0x5A) + 源地址(0xA7) + 目标地址(0x97) + 长度 + 类型 + 数据 + 校验 + 帧尾(0xA5)`
- 校验：帧头到数据末位逐字节累加取低8位
- 握手：主机发 `0x09` + `"Please Link"` → 摄像头应答 `0x09` + `"Play Application"` → 握手成功后主机不再发握手帧，摄像头按当前脚本类型码周期推送数据
- 类型码：`0x01`主菜单 / `0x02`相机 / `0x03`人脸识别 / … / `0x0B`图像分类
- 数据：所有脚本都发4组ID数据；主菜单/相机4组全0；face_detect 等4组里填检测/识别结果

**问题**：reset 框架迁移后，旧 `core/script_runner.py` 的每帧 `host.poll_handshake()` 调用已废弃，新框架下**没有任何地方**调用握手/推送。`runtime.host` 在 `init_menu`/`init_app` 里已构造（`_init_services`），但从未被 tick。

## 确认的需求（用户逐条确认）

| 场景 | 类型码 | 4组数据 | 说明 |
|---|---|---|---|
| 主菜单 | `0x01` | 全0 | 主菜单模式 |
| settings | `0x01` | 全0 | settings 复用主菜单类型码 |
| camera | `0x02` | 全0 | 相机模式 |
| face_detect | `0x03` | 匹配槽位填值，其余0 | 见下 |
| 未来检测脚本 | 各自类型码 | 各自数据 | 框架预留 |

### face_detect 4组数据语义（用户确认：4个DB槽位）

- 4组**固定对应 DB 槽位 1-4**（即 `_db_features` 的 slot_id）
- 当前帧匹配到某注册 ID → 该 slot 对应组填 `(id, x, y, w, h, conf)`；未匹配到的槽位填全0
- 当前帧无匹配 → 4组全0

### 识别范围（用户确认：只识最大脸）

- 每帧只对最大那张脸跑 `face_reg` kmodel（维持现状，NPU 负担不变，稳定）
- 推论：4槽位里**最多1个有值**（最大脸匹配到的那一个 DB 槽位），其余3个全0
- 框数据来源：`face_det.draw_result` 用的 display 坐标框（rgb888p→display 已缩放）

### 发送节奏（用户确认：每帧都发）

- 每个主循环迭代发一帧（脚本 ~30fps，主菜单随 `task_handler`）
- UART 负担极小（~45B/帧），逻辑最简单，无需帧计数器

## 架构设计

### 核心决策：握手+推送逻辑放框架级，脚本只报 category + slots

在 `app_runtime` 加一个集中入口，每个主循环每帧调一次。协议细节（poll_handshake、按 category 选类型码、组帧发送）只在框架一处，脚本不碰协议。

### 1. `comm/host_api.py` 扩展

新增类型码常量已存在（`TYPE_*`）。新增：

```python
# category_id → msg_type 映射（reset 框架 category 与协议类型码对接）
CATEGORY_TYPE = {
    "main_menu":  HostAPI.TYPE_MAIN_MENU,     # 0x01
    "settings":   HostAPI.TYPE_MAIN_MENU,     # 0x01（复用主菜单）
    "camera":     HostAPI.TYPE_CAMERA,        # 0x02
    "face_detect":HostAPI.TYPE_FACE_DETECT,   # 0x03
    "_template":  HostAPI.TYPE_MAIN_MENU,     # 0x01（默认）
}

def send_id_data(self, msg_type, slots=None):
    """发送4组ID数据（泛化 send_face_data）。

    slots: list[4]，每元素 None 或 (id,x,y,w,h,conf)。
    None / 越界 → 该组全0。msg_type 决定类型码。
    """

def tick(self, category_id, slots=None):
    """每帧调：poll_handshake + 按 category 发送4组数据。"""
    self.poll_handshake()
    msg_type = self.CATEGORY_TYPE.get(category_id, self.TYPE_MAIN_MENU)
    self.send_id_data(msg_type, slots)
```

`send_face_data` 改为 `send_id_data(TYPE_FACE_DETECT, slots)` 的薄封装（保留旧接口，旧调试备份引用）。

### 2. `core/app_runtime.py` 扩展

```python
def init_menu(self, fpioa):
    ...
    self.category_id = "main_menu"
    ...

def init_app(self, category_id, fpioa):
    ...
    self.category_id = category_id
    ...

def host_tick(self, slots=None):
    """每帧调：握手轮询 + 按当前 category 推送4组数据。slots=None → 全0。"""
    if self.host is not None:
        self.host.tick(self.category_id, slots)
```

### 3. 接线（各主循环每帧加一行）

| 位置 | 调用 |
|---|---|
| `main.py:run_menu` 循环 | `runtime.host_tick()` |
| `scripts/_template/app.py:run` 循环 | `runtime.host_tick()` |
| `scripts/camera/app.py:run` 循环 | `runtime.host_tick()` |
| `scripts/settings/app.py:run` 循环 | `runtime.host_tick()` |
| `scripts/face_detect/app.py:on_frame` | 算完识别结果后建4槽位 list → `_RUNTIME.host_tick(slots)` |

### 4. face_detect 槽位构建（on_frame 内）

`draw_result` 后、`gc.collect` 前：

```python
# 构建4槽位推送数据（4组对应 DB slot 1-4）
slots = [None, None, None, None]
for det_idx, mid in recognition_results:
    if mid is None:
        continue
    # mid 是 DB slot_id(1-4)；取该检测框的 display 坐标
    det = det_boxes[det_idx]
    x, y, w, h = det[:4]
    x = int(x * _face_det.display_size[0] // _face_det.rgb888p_size[0])
    y = int(y * _face_det.display_size[1] // _face_det.rgb888p_size[1])
    w = int(w * _face_det.display_size[0] // _face_det.rgb888p_size[0])
    h = int(h * _face_det.display_size[1] // _face_det.rgb888p_size[1])
    conf = int(det[4] * 100) if len(det) > 4 else 0
    if 1 <= mid <= 4:
        slots[mid - 1] = (mid, x, y, w, h, conf)
if _RUNTIME is not None and _RUNTIME.host is not None:
    _RUNTIME.host_tick(slots)
```

说明：
- `recognition_results` 现状只有1项（最大脸），故最多1槽有值，符合"只识最大脸"
- `det[4]` 是置信度（det 前几位是 x,y,w,h,score），取整 0-100；越界兜底0
- display 坐标缩放与 `draw_result` 一致

## 边界 / 不在本次改动

1. **协议长度/校验与 `通讯协议.txt` 不一致**：现有 `_checksum`/`send_frame`/`send_id_data` 的 `length=1+payload`、应答 payload `"Play Application"`(17B) 已是已部署行为，本次**不动**组帧逻辑。doc 写 `length=payload`、`"Play Aplication"`(15B) 与代码不符，要核对上位机抓包另开任务。
2. **request 帧目标地址**：doc 请求帧 `5A 97 98 …` 第3字节 `0x98` 与地址表 `0xA7` 不符；`poll_handshake` 用子串匹配 magic 不校验地址，不影响触发，不本次改。
3. **face_detect NPU 成本不变**：维持只识最大脸，不引入多脸识别（避免坑#16 NPU 累积丢帧）。
4. **UART 异常隔离**：`send_frame` 已 try/except，异常置 `connected=False`，不杀主循环。`host_tick` 不再加额外保护（host.tick 内部已 try）。

## 测试策略（host 侧 AST 契约测试，板端模块 Windows 不可导入）

- `tests/test_host_api.py`（新建/扩展）：
  - `CATEGORY_TYPE` 映射含 main_menu→0x01、settings→0x01、camera→0x02、face_detect→0x03、_template→0x01
  - `send_id_data` 存在且签名 `(self, msg_type, slots=None)`
  - `tick` 存在且签名 `(self, category_id, slots=None)`，源码含 `poll_handshake` 与 `send_id_data`
- `tests/test_framework.py`（扩展）：
  - `app_runtime` AST 含 `category_id` 赋值（init_menu + init_app）与 `host_tick` 方法定义
- `tests/test_face_detect_template.py`（扩展）：
  - `face_detect.on_frame` 源码含 `host_tick` 调用与 `slots` 构建
  - `camera/app.py:run` 源码含 `host_tick`
  - `settings/app.py:run` 源码含 `host_tick`
  - `_template/app.py:run` 源码含 `host_tick`
  - `main.py:run_menu` 源码含 `host_tick`

## 文件清单

- Modify: `comm/host_api.py` — 加 `CATEGORY_TYPE` / `send_id_data` / `tick`，`send_face_data` 改薄封装
- Modify: `core/app_runtime.py` — `init_menu`/`init_app` 存 `category_id`，加 `host_tick`
- Modify: `main.py` — `run_menu` 循环加 `runtime.host_tick()`
- Modify: `scripts/_template/app.py` — `run` 循环加 `runtime.host_tick()`
- Modify: `scripts/camera/app.py` — `run` 循环加 `runtime.host_tick()`
- Modify: `scripts/settings/app.py` — `run` 循环加 `runtime.host_tick()`
- Modify: `scripts/face_detect/app.py` — `on_frame` 构建槽位 + `host_tick(slots)`
- Create: `tests/test_host_api.py`（若不存在）
- Modify: `tests/test_framework.py`、`tests/test_face_detect_template.py`
