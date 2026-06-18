# 图库页面改版设计

**日期**：2026-06-15  
**状态**：已确认  
**依赖**：LVGL v8 (K230 MicroPython)、`image` 模块 (K230 media)、`/data/photo/` 目录

---

## 1. 目标

将当前纯文字文件列表（[app.py `_build_gallery_list`](../scripts/camera/app.py#L657)）改为带照片缩略图预览 + 按日期分组 + 删除按钮的图库页面。

## 2. 布局

```
┌──────────────────────────────────────────┐ y=0
│  ← 返回         图库                     │ 52px 顶栏 (复用现 BAR_H)
├──────────────────────────────────────────┤ y=52
│  ─── 2026年6月15日 ───                   │ 日期分组标题 28px
│  ┌─────────┐  IMG_20260615_143022.jpg   X│
│  │ 缩略图   │  2026-06-15 14:30          │ 照片行 100px
│  │ 120×90  │                            │
│  └─────────┘                            │
│  ┌─────────┐  IMG_20260615_120514.jpg   X│
│  │ 缩略图   │  2026-06-15 12:05          │
│  └─────────┘                            │
│  ─── 2026年6月14日 ───                   │
│  ┌─────────┐  IMG_20260614_090000.jpg   X│
│  │ 缩略图   │  2026-06-14 09:00          │
│  └─────────┘                            │
├──────────────────────────────────────────┤ y=480
```

### 尺寸常量

| 元素 | 尺寸 | 说明 |
|------|------|------|
| 顶栏 | 640×52 | 复用现有 `_build_top_bar` 的 bar 对象，只改 title 文字 |
| 日期分组标题 | 640×28 | 居中灰色文字，深色背景条 |
| 照片行 | 640×100 | 圆角卡片（8px），深灰背景 0x1A1A1A |
| 缩略图 | 120×90 | 照片等比缩放适配 120×90 容器，居中裁剪 |
| 缩略图圆角 | 6px | |
| 删除按钮 | 36×36 | ✕ 图标，右上角区域 |
| 文件名 | 最大宽度 ~300px | 省略截断 |
| 行间距 | 6px | |

### 颜色

| 用途 | 色值 |
|------|------|
| 列表背景 | 0x000000 (纯黑，复用 Colors.BG) |
| 照片行卡片 | 0x1A1A1A (沿用原图库卡片色) |
| 分组标题背景 | 0x111111 |
| 分组标题文字 | 0x9E9E9E (Colors.TEXT_DIM) |
| 文件名文字 | 0xFFFFFF (Colors.TEXT) |
| 日期文字 | 0x9E9E9E |
| 删除按钮图标 | 0xCC4444 (红色) |

## 3. 数据结构

### 分组逻辑

1. 扫描 `/data/photo/`，过滤 `.jpg` / `.bmp`（`.avi` 暂不处理——录像为空壳）
2. `os.stat()` 取 mtime
3. 按日期（年-月-日）分组：`{"2026-06-15": [photo, ...], "2026-06-14": [...]}`
4. 组间倒序（最新日期在前），组内倒序（最新照片在前）

### 缩略图对象

```python
class Thumbnail:
    path: str           # 文件完整路径
    fname: str          # 文件名
    mtime: int          # 修改时间戳
    img_dsc: lv.img_dsc_t  # LVGL 图像描述符（RGB565 像素数据）
    pixel_data: bytes   # 缩放后像素字节（保活用）
```

## 4. 缩略图加载流程

在 `_enter_gallery()` 中，**隐藏相机 UI 后、构建列表前**，一次性批量加载所有照片缩略图：

```
1. 扫描文件 → 按日期分组
2. for 每组每张照片:
   a. try: img = image.Image(path)
   b. img.resize(120, 90) 或等比缩放
   c. 转 RGB565 → bytes
   d. 构造 lv.img_dsc_t
   e. 存入 Thumbnail 对象，挂到 self 保活
   f. except: placeholder (灰色方块)
3. 构建 LVGL 列表 UI
```

**关键安全约束（K230 坑 #2）**：
- 加载发生在进入图库入口，相机预览已停（`on_frame` 不再推 OSD1 帧），Display.show_image 冲突概率大降
- 所有文件 I/O 在构建 LVGL 对象**之前**完成，构建期零 I/O（与主菜单 preload_icons 同策略）
- 每张照片加载时调用 `os.exitpoint()` 出让调度，避免长时间阻塞

**缩略图尺寸计算**：照片可能是 640×480 (4:3) 或其他比例。缩放到适配 120×90 容器（4:3），保持比例、居中：

```python
def fit_thumb_size(img_w, img_h, max_w=120, max_h=90):
    scale = min(max_w / img_w, max_h / img_h)
    return int(img_w * scale), int(img_h * scale)
```

## 5. 删除流程

1. 点击 ✕ 按钮 → `os.remove(path)`
2. 成功 → 删除该照片行 LVGL 对象，从 `self._thumbnails` 列表移除
3. 如果该日期组变为空 → 同时删除分组标题
4. 如果全部删除 → 显示 "暂无照片" 空状态
5. 失败 (如文件不存在) → 静默忽略

## 6. LVGL 组件拆分

在 `CameraApp` 中**不新建类文件**（保持与现有代码一致的单文件风格），用内部方法实现：

| 方法 | 职责 |
|------|------|
| `_enter_gallery()` | 入口：分组 + 加载缩略图 + 调 `_build_gallery_ui` |
| `_load_thumbnails(files)` | 批量加载缩略图，返回 Thumbnail 列表 |
| `_fit_thumb_size(img_w, img_h)` | 计算等比缩放尺寸 |
| `_build_gallery_ui(groups)` | 构建 LVGL 列表（分组标题 + 照片行） |
| `_make_date_header(parent, y, text)` | 创建日期分组标题 bar |
| `_make_photo_row(parent, y, thumb, fname, date, on_delete)` | 创建照片行 |
| `_on_delete_photo(thumb, row_obj)` | 删除文件 + 移除 UI |
| `_leave_gallery()` | 清理所有缩略图数据 + LVGL 对象，恢复相机 UI |

## 7. 内存管理

- `self._gallery_thumbs: list[Thumbnail]` — 保活所有缩略图像素数据（LVGL 重绘时解引用）
- `self._gallery_objects: list[lv.obj]` — 保活所有 LVGL 对象引用（现有的 `_gallery_objects`）
- 离开图库时：
  1. 遍历 `_gallery_objects` → `obj.delete()`
  2. 清空 `self._gallery_thumbs = []`（释放像素内存）
  3. `gc.collect()` — 此时安全（LVGL 对象已删，不在渲染期）

## 8. 错误处理

| 场景 | 处理 |
|------|------|
| 目录不存在 / 空 | 显示 "暂无照片" 居中文案 |
| 单张图片加载失败 | 灰色占位方块 + 文件名正常显示 |
| `os.remove()` 失败 | 静默返回（不删 UI，用户可重试） |
| 内存不足 (MemoryError) | 终止加载，已加载的显示，未加载的灰色占位 |
| `os.listdir()` 失败 | 显示 "暂无照片" |

## 9. 与现有代码的接口

- 复用 `_build_top_bar()` 的 `_top_bar` 对象 → 进入图库时改标题为 `lang.t("camera.gallery")`，离开时恢复
- 复用 `_on_back()` 的分支逻辑（`STATE_GALLERY` 时调 `_leave_gallery()`）
- `_gallery_objects` 列表扩展用途（原来只存列表行，现在存所有图库 UI 对象）
- 新增翻译 key（如果需要）：`camera.delete`（删除确认，暂时不需要——直接删除无确认）

## 10. 不做的事

- 不处理 `.avi` 视频缩略图（录像功能为空壳）
- 不实现大图全屏预览（点击缩略图暂不打开大图）
- 不实现多选批量删除
- 不实现删除确认弹窗（后续可加，首版直接删）
