# 相机 APP 布局重设计

日期：2026-06-12 | 状态：已确认

## 1. 概述

将相机从纯全屏预览重设计为「拍照/录像 + 图库」的完整相机应用。
核心架构变化：LVGL 和 Sensor 相机**同时运行**（双层显示），不再互斥。

## 2. 显示栈

```
┌──────────────────────────────────────┐
│  LVGL UI 层（前景）                  │
│  ┌──────────────────────────────────┐│
│  │ 顶栏 52px (bg=0x1A1A1A, opa=255) ││
│  ├──────────────────────────────────┤│
│  │ 预览区 640×376 (bg_opa=0 透明)    ││  ← VIDEO1 层透出
│  ├──────────────────────────────────┤│
│  │ 底栏 52px (bg=0x1A1A1A, opa=255) ││
│  └──────────────────────────────────┘│
└──────────────────────────────────────┘
         ↑ 透明区域透出 ↓
┌──────────────────────────────────────┐
│  VIDEO1 层 — Sensor 相机画面（背景）  │
└──────────────────────────────────────┘
```

- Sensor → `Display.bind_layer(layer=VIDEO1)`，始终在 LVGL 下层
- LVGL 保持初始化，渲染顶栏/底栏/计时器
- 预览区 LVGL 对象 `bg_opa=0`，透明让 VIDEO1 层可见

### ScriptRunner 改动

| 方法 | 旧行为 | 新行为 |
|------|--------|--------|
| `_switch_mode_for('stream')` | 调 `lv.deinit()` 关闭 LVGL | **不再关闭 LVGL**，仅绑定 Sensor 到 VIDEO1 |
| `_switch_to_lvgl_mode()` | 重建 LVGL 全栈 | 仅释放 Sensor/VIDEO1，LVGL 保持运行 |
| 顶栏管理 | Runner 自动挂载统一 BackBar | 相机脚本**自己管顶栏**（居中标题 ≠ BackBar 左标题） |

## 3. 布局参数

### 通用常量

```python
BAR_H = 52           # 顶栏/底栏高度
PREVIEW_H = 376      # 480 - 52*2
BTN_ICON_SIZE = 40   # 栏上图标目标尺寸
SHUTTER_SIZE = 44    # 快门按钮外径（含圆环）
SHUTTER_INNER = 36   # 快门内圆直径
BAR_BG = 0x1A1A1A   # 栏背景色
TIMER_H = 28         # 录制计时器高度
```

### 顶栏（所有视图共用）

- 返回按钮：48×48 透明点击区 + 自定义图标（40px），左边距 2px
- 标题：body 字体，`ALIGN.CENTER` 绝对居中
- 相机模式标题 = `lang.t("camera")`，图库模式标题 = `lang.t("gallery")`

### 底栏 — 拍照模式

- 图库图标(左, 48×48 clickable) + 快门白圈(中, 44px 外径, 3px 白色 border, 50% radius) + 模式图标(右, 48×48 clickable)
- 图标均居中显示，可自定义 PNG

### 底栏 — 录像待机模式

- 模式图标变**绿色** (0x44CC44)
- 快门变**红色实心圆**（36px 红色圆 + 3px 白色外环）

### 底栏 — 录像中模式

- 快门变**红色正方形**（28×28, border_radius=4, bg=0xCC4444）
- 模式图标保持绿色
- 预览区顶部居中显示录制计时器 `● 00:00:23`（18px 字体, 红色, 圆点闪烁）

## 4. CameraApp 视图状态机

```
PREVIEW_PHOTO  ←→  PREVIEW_VIDEO  ←→  RECORDING
     ↓                    ↓               ↓
  GALLERY             GALLERY         (图库不可用)
```

| 状态 | 底部栏 | 快门 | 模式图标 | 计时器 |
|------|--------|------|----------|--------|
| PREVIEW_PHOTO | 显示 | 白圈 | 白色 | 无 |
| PREVIEW_VIDEO | 显示 | 红圆 | 绿色 | 无 |
| RECORDING | 显示 | 红方 | 绿色 | ● hh:mm:ss |
| GALLERY | 隐藏 | — | — | 无 |

状态切换：
- **模式按钮点击**：PHOTO ↔ VIDEO（仅在待机状态切换）
- **快门点击(PHOTO)**：拍照 → 保存 → 留在 PHOTO
- **快门点击(VIDEO)**：进入 RECORDING → 开始录像
- **快门点击(RECORDING)**：停止录像 → 保存 → 回到 VIDEO
- **图库按钮点击**：进入 GALLERY（仅 PHOTO/VIDEO 待机时可用）

## 5. 图库子页面

### 布局

- 顶栏：返回按钮(左) + 居中标题「图库」
- 内容区：垂直滚动列表
- 每行：缩略图(64×48) + 文件名 + 日期时间 + 类型图标(📷/🎬)

### 数据加载

1. `os.listdir('/data/photo/')` 列出文件
2. 过滤 `.jpg`, `.bmp`, `.avi`
3. `os.stat()` 取 mtime，按时间倒序排列
4. 构建 LVGL 列表行（lv.obj + SCROLLABLE）

### 交互

- 点击返回 → 回到相机预览（恢复之前的模式状态）
- 点击照片 → 全屏查看（再次点击返回列表）

## 6. 数据流

### 拍照

1. 按快门 → 蜂鸣 30ms
2. Sensor 捕获当前帧 → JPEG 编码
3. 写入 `/data/photo/IMG_YYYYMMDD_HHMMSS.jpg`
4. 屏幕短暂闪烁反馈

### 录像

1. 按红圆 → 开始录制 + 计时器启动
2. 每帧编码写入 `/data/photo/VID_YYYYMMDD_HHMMSS.avi`
3. 计时器每 1s 更新一次，圆点闪烁(500ms on/off)
4. 按红方 → 停止录制，关闭文件，计时器消失

### 图库

1. 进入时扫描 `/data/photo/`
2. 文件 I/O 仅在进入/退出图库时发生
3. 缩略图按需解码（如内存允许可预读）

## 7. 文件 I/O 策略

- 路径：`/data/photo/`（可能是内部 flash，需板端验证是否与 DMA 冲突）
- 拍照时：单次 `open/write/close`，短时间 I/O
- 录像时：持续 `write`（需重点关注板端稳定性）
- 图库扫描：`listdir` + `stat`（进入时一次性完成）
- 如果 `/data/photo/` 触发了 DMA 死锁，回退计划：
  - 拍照：内存缓冲 → 退出相机模式 → 批量写入
  - 录像：仅内存缓冲（限制时长），退出后写入

## 8. 与现有代码的关系

| 文件 | 改动程度 | 说明 |
|------|----------|------|
| `scripts/camera/app.py` | **重写** | 全新布局 + 拍照/录像/图库逻辑 |
| `core/script_runner.py` | **中等** | 不关 LVGL + 相机脚本自管顶栏 |
| `ui/back_bar.py` | 不改 | 相机脚本不用 BackBar |
| `core/icon_cache.py` | **小改** | 新增相机专用图标（图库、模式、快门等） |
| `resource/i18n/*.json` | **小改** | 新增 camera/gallery 相关 i18n key |
| `scripts/_base.py` | 不改 | — |
| `core/app.py` | 不改 | — |

## 9. 国际化新 key

```json
{
  "camera": { "zh_CN": "相机", "en_US": "Camera" },
  "gallery": { "zh_CN": "图库", "en_US": "Gallery" },
  "camera.photo_mode": { "zh_CN": "拍照", "en_US": "Photo" },
  "camera.video_mode": { "zh_CN": "录像", "en_US": "Video" }
}
```
