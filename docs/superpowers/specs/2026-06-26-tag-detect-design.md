# 标签识别(tag_detect)脚本设计

> **状态**:设计已确认(2026-06-26),待出实施计划。
> **前置 bug**:先修 face_detect 双语切换——非标题文本硬编码中文未走 i18n。
> **复用**:`scripts/_template/app.py` 单线程模板 + `core/id_registry.py` + `core/face_db.py` 内存-only/flush_to_disk 预留模式。

## 目标

1. 修复 face_detect 双语切换 bug(非标题文本不随语言切换)。
2. 新增 tag_detect 脚本:AprilTag + 二维码双功能,布局同 face_detect。
3. 两功能各自独立 ID 设置(最多4),KEY2 注册,暂不持久化(预留接口),按串口协议(类型 0x04)上传。
4. 所有文本走 i18n,中英双语。

## 决策(用户确认)

| 决策点 | 选择 |
|--------|------|
| 检测图像来源 | 专用 RGB565 通道(chn1),官方 demo 同款 |
| 协议类型 | AprilTag 与 QR 都用 `TYPE_TAG_DETECT=0x04` |
| ID 注册语义 | 检测到码 → 按 K2 → 存入下一槽位(1-4) |
| AprilTag 家族 | 仅 `TAG36H11`(官方默认,误检率最低) |

## 模块设计

### M0 — face_detect 双语修复(systematic-debugging 根因已定位)

**根因**:`scripts/face_detect/app.py` 硬编码中文:`"已注册 %d/4"`(line 158/370)、`"清除"`(line 187)、`"保存"`(line 196)。标题走 `lang.t("category.face_detect")` 故能切换,其余未走 `t()`。

**修法**:
- `face_detect.registered` i18n 值改为带格式:`zh "已注册 %d/4"` / `en "Registered %d/4"`。
- 三处硬编码改为:`_RUNTIME.lang.t("face_detect.registered", len(_db_features))`、`t("face_detect.clear")`、`t("face_detect.save")`。
- tag_detect 全部文本同样走 `lang.t()`。

退出再进重建 UI,无需运行时热切换。

### M1 — tag_db(新建 `core/tag_db.py`,纯 Python 可单测)

镜像 face_db 的内存-only + flush_to_disk 预留模式,泛型化(code_id 由调用方决定类型):

```python
class TagDB:
    def __init__(self):
        self._features = {}     # {slot_id: code_id}  code_id=int(AprilTag) 或 str(QR)
        self._next_slot = 1     # 轮转覆盖指针(1-4)
        self._dirty = False
        self._clear_dirty = False

    def register(self, code_id):
        """轮转覆盖(同 face_db):空槽优先,否则覆盖 _next_slot 并推进。
        返回 slot_id(1-4)。纯内存,设 _dirty。"""
        ...

    def match(self, code_id):
        """精确匹配 code_id。返回 (slot_id, 1.0) 或 (None, 0.0)。
        score=1.0(标签码精确匹配,无相似度概念)。"""
        ...

    def clear(self):
        """清内存,设 _clear_dirty。"""
        ...

    def flush_to_disk(self):
        """no-op 预留(同 face_db):持久化路径待定,当前仅复位 dirty 标志。"""
        ...

    @property
    def count(self):
        return len(self._features)
```

**为什么新建而非复用 face_db**:face_db 存 ulab ndarray(余弦相似度匹配),tag 存标量 id(精确匹配),匹配算法完全不同。新建独立类更清晰,且纯 Python 可在 Windows 跑真单元测试(face_db 依赖 ulab 不可导入)。

### M2 — IdRegistry 加可选 registrar 参数(向后兼容)

`try_register(feature, buzzer=None, registrar=None)`:
- `registrar=None`(默认)→ 走 `face_db.register(feature)`(face_detect 签名不变,零影响)。
- `registrar` 传入 → 调 `registrar(code_id)`(tag_detect 传 `tag_db.register`)。

K2 边沿检测 / 2s 超时 / 蜂鸣逻辑零重写。tag_detect 调用:
`slot = _id_registry.try_register(code_id, buzzer, registrar=_db.register)`

### M3 — tag_detect 脚本(`scripts/tag_detect/app.py`)

**sensor 通道**(`app_runtime._channels_for` 加分支):
- chn0: VGA RGB888(640×480,显示 + 画框,OSD1)
- chn1: **QVGA RGB565**(320×240,检测)—— 官方 AprilTag demo 用 QVGA(VGA 下 AprilTag 极慢);rect 坐标 ×2 映射显示(QVGA→VGA 正好 2 倍整数缩放)。

**UI 布局**(同 face_detect,顶栏/透明预览/底栏):
- 顶栏:back 图标 + 标题 `lang.t("category.tag_detect")`。
- 预览:透明,透出 OSD1 摄像画面。
- 底栏:`[list 图标(纯显示不绑功能)] [AprilTag 卡片] [二维码 卡片] [N/4 计数]`
  - list 图标:`tag_detect_icon/list.png`,**只显示不绑功能**(区别于 face_detect 的弹浮层)。
  - 两卡片文本走 i18n;选中卡片背景置绿(`0x2E7D32`),默认选中 AprilTag。
  - 点卡片切换 `_active_fn`(april/qr)。
  - N/4 显示当前功能已注册数(对齐 face_detect 计数反馈)。

**on_frame 流程**:
1. `img_det = runtime.sensor.snapshot(chn=CAM_CHN_ID_1)`(QVGA RGB565)
2. 按 `_active_fn`:
   - april:`tags = img_det.find_apriltags(families=image.TAG36H11)`,`code_id = tag.id()`
   - qr:`codes = img_det.find_qrcodes()`,`code_id = code.payload()`
3. 对每个码:`(slot, score) = _db.match(code_id)`;命中 → `slots[slot-1] = (slot, x*2, y*2, w*2, h*2, 100)`(×2 缩放,conf=100)
4. `_id_registry.has_pending()` 且当前帧有码 → `try_register(code_id, buzzer, registrar=_db.register)`;命中刷新计数。
5. chn0(`img`)画框 + slot id 文字。
6. `_RUNTIME.host_tick(slots)`(类型 0x04 由 CATEGORY_TYPE 映射)。

**主循环**(同模板):snapshot chn0 → on_frame(try/except 隔离)→ show_image OSD1 → `_id_registry.poll_k2()` → `lv.task_handler` → fc 计数。

**退出**:`_destroy_ui()` + `flush_to_disk()`(no-op 预留)。

### M4 — 协议 + 注册 + 图标

- `comm/host_api.py` `CATEGORY_TYPE` 加 `"tag_detect": TYPE_TAG_DETECT`(0x04)。两功能都用 0x04(用户确认)。
- `config/categories.json` 已有 tag_detect 条目(order 4),无需改;部署时确认 `resource/icons/menu_icon/tag_detect.png` 存在。
- 新建 `resource/icons/tag_detect_icon/`(back.png + list.png,从 face_detect_icon 复制同名文件)。
- `core/icon_cache.py` 加 `preload_tag_icons()`/`get_tag_icon(name)`;`init_app` 加 `elif category_id == "tag_detect": icon_cache.preload_tag_icons()` 分支。

### M5 — i18n(zh + en,结构对称)

```json
"face_detect": {
  ...,
  "registered": "已注册 %d/4"   // en: "Registered %d/4"(格式化参数,带 %d)
},
"tag_detect": {
  "april_tag": "AprilTag",       // en 同
  "qr_code": "二维码",           // en: "QR Code"
  "registered": "已注册 %d/4"    // en: "Registered %d/4"
}
```

### M6 — 测试(TDD)

| 测试文件 | 类型 | 覆盖 |
|----------|------|------|
| `tests/test_tag_db.py` | **真单元测试**(tag_db 纯 Python 可导入) | register 空槽优先/满4轮转覆盖、match 命中(score=1.0)/未命中(None,0.0)、clear、flush_to_disk 不崩 |
| `tests/test_host_api.py` | AST 契约 | CATEGORY_TYPE 覆盖 tag_detect→0x04 |
| `tests/test_id_registry.py` | AST 契约 | try_register 含可选 registrar 参数,默认走 face_db |
| `tests/test_tag_detect_app.py` | AST 契约 | on_frame 调 find_apriltags/find_qrcodes、host_tick、_channels_for 配 QVGA RGB565、KEY2 走 registrar=tag_db.register |

## 风险

- **AprilTag @QVGA 帧率**:官方 demo 同分辨率,应可接受。若仍慢,v2 可降 chn1 到 QQVGA 或限检测频率(隔帧检测)。
- **find_qrcodes/apriltags 在 RGB565**:官方 demo 已验证,风险低。
- **QVGA→VGA ×2 坐标缩放**:QVGA 320×240 → VGA 640×480 正好 2 倍整数,无舍入误差。

## 不做(YAGNI)

- 不持久化 tag ID(预留 flush_to_disk 接口,当前 no-op,后续决定存哪)。
- 不区分 AprilTag/QR 子类型上传(都用 0x04,主机按 tag 处理)。
- 不识别多个 AprilTag 家族(仅 TAG36H11)。
- face_detect 运行时热切换语言(退出再进即可,scope 外)。
