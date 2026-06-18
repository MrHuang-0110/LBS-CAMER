# 图库改版实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将图库页面从纯文字文件列表改为带照片缩略图预览、按日期分组、支持删除的照片管理页面。

**Architecture:** 所有改动集中在 `scripts/camera/app.py`，不新建文件。进入图库时暂停相机预览推帧，在安全窗口内用 K230 `image.Image` 批量加载照片缩略图到内存（像素字节），然后构建 LVGL 纵向滚动列表（日期分组标题 + 照片行卡片）。删除操作用 `os.remove()` + 移除对应 LVGL 对象。

**Tech Stack:** K230 MicroPython, LVGL v8, `image` 模块 (K230 media), `os` 文件系统

**关键依赖已确认：**
- K230 `image.Image(path)` 可从文件加载 JPEG（参考 `demo/AI类实验例程/实验4 人脸识别实验/main1.py:318`）
- i18n key `camera.gallery` / `camera.no_photos` 已存在（`resource/i18n/zh_CN.json:48,52`）
- `fonts.body` / `fonts.caption` 字体可用

---

## 文件修改清单

| 文件 | 操作 | 说明 |
|------|------|------|
| `scripts/camera/app.py` | 修改 | 主要改动：重写图库相关方法，新增缩略图加载/分组/删除逻辑 |

---

### Task 1: 新增图库常量与数据结构初始化

**Files:**
- Modify: `scripts/camera/app.py:25-40`（在现有常量区追加）
- Modify: `scripts/camera/app.py:81-113`（在 `__init__` 中追加实例变量）

- [ ] **Step 1: 在布局常量区追加图库常量**

在 `app.py` 第 35 行 `BAR_BG = 0x1A1A1A` 之后追加：

```python
# ── 图库常量 ──
GAL_THUMB_W = 120       # 缩略图最大宽度
GAL_THUMB_H = 90        # 缩略图最大高度
GAL_ROW_H = 100          # 照片行高度
GAL_ROW_BG = 0x1A1A1A    # 照片行卡片背景
GAL_DATE_H = 28          # 日期分组标题高度
GAL_DATE_BG = 0x111111   # 日期分组标题背景
GAL_DELETE_SIZE = 36     # 删除按钮尺寸
GAL_ROW_GAP = 6          # 行间距
GAL_ROW_RADIUS = 8       # 照片行圆角
GAL_THUMB_RADIUS = 6     # 缩略图圆角
```

- [ ] **Step 2: 在 `__init__` 中追加图库相关实例变量**

在 `app.py` 第 111 行 `self._flash_start = 0` 之后追加：

```python
        # 图库 — 缩略图数据（像素字节常驻内存供 LVGL 重绘）
        self._gallery_thumbs = []   # list[dict]: path/fname/mtime/img_dsc/pixel_data
        self._gallery_groups = []   # list[dict]: date_str/label/text/rows→LVGL objects
```

---

### Task 2: 实现缩略图加载 helper

**Files:**
- Modify: `scripts/camera/app.py`（在 `_build_gallery_list` 原位置附近插入新方法）

- [ ] **Step 1: 添加 `_fit_thumb_size` 等比缩放计算方法**

在 `CameraApp` 类中（例如 `_flash_feedback` 方法之后）插入：

```python
    @staticmethod
    def _fit_thumb_size(img_w, img_h, max_w=GAL_THUMB_W, max_h=GAL_THUMB_H):
        """计算适配缩略图容器的等比缩放尺寸"""
        if img_w <= 0 or img_h <= 0:
            return max_w, max_h
        scale = min(max_w / img_w, max_h / img_h)
        if scale > 1.0:
            scale = 1.0  # 不放大
        return int(img_w * scale), int(img_h * scale)
```

- [ ] **Step 2: 添加 `_load_thumbnail` 单张缩略图加载方法**

在 `_fit_thumb_size` 之后插入：

```python
    def _load_thumbnail(self, path):
        """用 K230 image.Image 加载 JPEG/BMP 并缩放为 RGB888 像素字节。

        Returns: dict {pixel_data: bytes, w: int, h: int, img_dsc: lv.img_dsc_t}
                 或 None（加载失败时）
        """
        import image
        import os as _os
        _os.exitpoint()

        img = None
        try:
            # Step A: 从文件加载
            img = image.Image(path)

            # Step B: 计算等比缩放尺寸
            src_w, src_h = img.width, img.height  # 可能抛异常，用 getattr 兜底
            if src_w is None or src_h is None:
                src_w = getattr(img, 'width', 640)
                src_h = getattr(img, 'height', 480)
        except Exception as e:
            print(f"[Gallery] image.Image({path}) failed: {e}")
            return None

        try:
            tw, th = self._fit_thumb_size(src_w, src_h)

            # Step C: 缩放到缩略图尺寸
            img.resize(tw, th)
            _os.exitpoint()

            # Step D: 创建同尺寸 RGB888 目标图像，拷贝像素
            #   方案1: 如果 image.Image 支持 copy() + 格式转换
            #   方案2: 用 ALLOC_REF 反向获取像素数据
            #   根据板端验证调整——见 Task 6 板端验证步骤
            from image import Image, RGB888, ALLOC_REF

            # 尝试构造 RGB888 目标 buffer 并把源图画进去
            # 已知 K230 image.resize 同格式缩放保持原格式
            # 这里先把数据转为可被 LVGL 使用的 raw RGB888 bytes
            pixel_data = None

            # 尝试 A: buffer protocol (MicroPython 某些构建支持)
            try:
                pixel_data = bytes(img)
            except Exception:
                pass

            # 尝试 B: 建同尺寸 RGB888 目标图 + 逐像素复制
            if pixel_data is None:
                try:
                    dst = Image(tw, th, RGB888)
                    # K230 image 模块可能支持 draw 或 bitblt
                    # 尝试用 replace 或直接赋值
                    if hasattr(dst, 'draw_image'):
                        dst.draw_image(img, 0, 0)
                    elif hasattr(dst, 'replace'):
                        dst.replace(img)
                    pixel_data = bytes(dst)
                    del dst
                except Exception:
                    pass

            # 尝试 C: 读取源图为 RGB888 格式
            if pixel_data is None:
                try:
                    rgb_img = img.copy()
                    # 如果原图是 JPEG，copy 后可能是 RGB888
                    pixel_data = bytes(rgb_img)
                    del rgb_img
                except Exception:
                    pass

            if pixel_data is None or len(pixel_data) == 0:
                print(f"[Gallery] thumbnail pixel extraction failed for {path}")
                return None

            # Step E: 构造 LVGL 原始像素图像描述符
            dsc = lv.img_dsc_t()
            dsc.header.w = tw
            dsc.header.h = th
            dsc.header.cf = lv.IMG_CF.TRUE_COLOR  # RGB888 = 4
            dsc.data_size = len(pixel_data)
            dsc.data = pixel_data

            _os.exitpoint()
            return {
                'pixel_data': pixel_data,
                'w': tw,
                'h': th,
                'img_dsc': dsc,
            }

        except Exception as e:
            print(f"[Gallery] thumbnail resize/convert failed for {path}: {e}")
            return None
        finally:
            # 释放临时 image.Image 对象
            if img is not None:
                try:
                    del img
                except Exception:
                    pass
```

---

### Task 3: 实现日期分组逻辑

**Files:**
- Modify: `scripts/camera/app.py`（在 `_load_thumbnail` 之后插入）

- [ ] **Step 1: 添加 `_group_photos_by_date` 方法**

```python
    def _group_photos_by_date(self, photo_dir):
        """扫描照片目录，按日期分组后批量加载缩略图。

        Returns: list[dict] 每组 {date_key, label, photos: [{path,fname,mtime,thumb}]}
                 空列表表示没有照片
        """
        import os as _os
        import time as _time

        # ── 扫描文件 ──
        files = []
        try:
            for f in _os.listdir(photo_dir):
                low = f.lower()
                # .avi 暂不处理（录像为空壳）
                if low.endswith('.jpg') or low.endswith('.bmp'):
                    full_path = photo_dir + f
                    try:
                        st = _os.stat(full_path)
                        files.append((f, full_path, st[8]))  # (name, path, mtime)
                    except Exception:
                        files.append((f, full_path, 0))
        except Exception as e:
            print(f"[Gallery] listdir failed: {e}")
            return []

        if not files:
            return []

        # ── 按 mtime 倒序 → 按日期分组 ──
        files.sort(key=lambda x: x[2], reverse=True)

        groups_dict = {}
        for fname, fpath, mtime in files:
            if mtime > 0:
                t = _time.localtime(mtime)
                date_key = f"{t[0]:04d}-{t[1]:02d}-{t[2]:02d}"
                date_label = f"{t[0]}年{t[1]}月{t[2]}日"
            else:
                date_key = "unknown"
                date_label = "未知日期"

            if date_key not in groups_dict:
                groups_dict[date_key] = {
                    'date_key': date_key,
                    'label': date_label,
                    'photos': [],
                }
            groups_dict[date_key]['photos'].append({
                'fname': fname,
                'path': fpath,
                'mtime': mtime,
                'thumb': None,  # 待加载
            })

        # ── 保持日期倒序 ──
        groups = list(groups_dict.values())
        groups.sort(key=lambda g: g['date_key'], reverse=True)

        # ── 批量加载缩略图（此时相机预览已停，I/O 安全）──
        import gc as _gc
        for group in groups:
            for photo in group['photos']:
                _os.exitpoint()
                thumb = self._load_thumbnail(photo['path'])
                photo['thumb'] = thumb
                if thumb is None:
                    print(f"[Gallery] thumb FAILED: {photo['fname']}")

            # 每加载完一组释放一次临时对象
            _gc.collect()

        return groups
```

---

### Task 4: 重写 `_enter_gallery()` 入口方法

**Files:**
- Modify: `scripts/camera/app.py:591-631`（替换现有 `_enter_gallery`）

- [ ] **Step 1: 用新逻辑替换 `_enter_gallery`**

```python
    def _enter_gallery(self):
        """进入图库页面 — 扫描 + 分组 + 加载缩略图 + 构建 UI"""
        import os as _os
        import gc as _gc

        self._state = STATE_GALLERY

        # 隐藏相机 UI
        if self._bottom_bar is not None:
            self._bottom_bar.add_flag(lv.obj.FLAG.HIDDEN)
        if self._preview_bg is not None:
            self._preview_bg.add_flag(lv.obj.FLAG.HIDDEN)
        if self._timer_label is not None:
            self._timer_label.add_flag(lv.obj.FLAG.HIDDEN)

        # 更新标题
        if self._title_label is not None:
            self._title_label.set_text(self.ctx.lang.t("camera.gallery"))

        # ── 扫描 + 分组 + 加载缩略图（所有 I/O 在构建 LVGL 之前完成）──
        photo_dir = "/data/photo/"
        self._gallery_thumbs = []
        self._gallery_groups = []
        self._gallery_objects = []

        try:
            _os.mkdir(photo_dir)
        except Exception:
            pass

        groups = self._group_photos_by_date(photo_dir)
        self._gallery_groups = groups

        # 保活所有缩略图引用
        for group in groups:
            for photo in group['photos']:
                if photo['thumb'] is not None:
                    self._gallery_thumbs.append(photo['thumb'])

        # ── 构建 LVGL 列表 UI（此时零 I/O）──
        self._build_gallery_ui(groups)

        # 安全回收
        _gc.collect()
        print(f"[Gallery] enter done: {len(groups)} groups, {len(self._gallery_thumbs)} thumbs")
```

---

### Task 5: 重写图库 UI 构建（分组标题 + 照片行）

**Files:**
- Modify: `scripts/camera/app.py:657-728`（替换 `_build_gallery_list`）

- [ ] **Step 1: 添加 `_make_date_header` 方法**

在 `_enter_gallery` 之后插入：

```python
    def _make_date_header(self, parent, y, text):
        """创建日期分组标题 bar — 深色背景 + 居中灰色文字"""
        bar = lv.obj(parent)
        bar.set_size(lv.pct(100), GAL_DATE_H)
        bar.set_pos(0, y)
        bar.set_style_bg_color(lv.color_hex(GAL_DATE_BG), 0)
        bar.set_style_bg_opa(255, 0)
        bar.set_style_border_width(0, 0)
        bar.set_style_pad_all(0, 0)
        bar.set_style_radius(0, 0)
        bar.clear_flag(lv.obj.FLAG.SCROLLABLE)
        bar.clear_flag(lv.obj.FLAG.CLICKABLE)
        self._gallery_objects.append(bar)

        label = lv.label(bar)
        label.set_text(text)
        label.align(lv.ALIGN.CENTER, 0, 0)
        label.add_style(make_back_bar_text_style(fonts.caption), 0)
        label.set_style_text_color(lv.color_hex(Colors.TEXT_DIM), 0)
        self._gallery_objects.append(label)

        return bar
```

- [ ] **Step 2: 添加 `_make_photo_row` 方法**

```python
    def _make_photo_row(self, parent, y, photo):
        """创建照片行 — 缩略图 + 文件名/日期 + 删除按钮

        Args:
            parent: 滚动列表容器
            y: 行 Y 坐标
            photo: dict {fname, path, mtime, thumb}

        Returns: row obj（用于后续删除定位）
        """
        import time as _time

        row = lv.obj(parent)
        row.set_size(lv.pct(100), GAL_ROW_H)
        row.set_pos(0, y)
        row.set_style_bg_color(lv.color_hex(GAL_ROW_BG), 0)
        row.set_style_bg_opa(255, 0)
        row.set_style_border_width(0, 0)
        row.set_style_radius(GAL_ROW_RADIUS, 0)
        row.set_style_pad_all(4, 0)
        row.clear_flag(lv.obj.FLAG.SCROLLABLE)
        self._gallery_objects.append(row)

        # ── 缩略图区域 ──
        thumb_info = photo.get('thumb')
        if thumb_info is not None and thumb_info.get('img_dsc') is not None:
            thumb_img = lv.img(row)
            thumb_img.set_src(thumb_info['img_dsc'])
            thumb_img.set_style_radius(GAL_THUMB_RADIUS, 0)
            thumb_img.set_style_clip_corner(True, 0)
            thumb_img.align(lv.ALIGN.LEFT_MID, 6, 0)
            self._gallery_objects.append(thumb_img)
        else:
            # 灰色占位方块（加载失败时）
            placeholder = lv.obj(row)
            placeholder.set_size(GAL_THUMB_W, GAL_THUMB_H)
            placeholder.align(lv.ALIGN.LEFT_MID, 6, 0)
            placeholder.set_style_bg_color(lv.color_hex(0x333333), 0)
            placeholder.set_style_bg_opa(255, 0)
            placeholder.set_style_radius(GAL_THUMB_RADIUS, 0)
            placeholder.set_style_border_width(0, 0)
            placeholder.clear_flag(lv.obj.FLAG.SCROLLABLE)
            placeholder.clear_flag(lv.obj.FLAG.CLICKABLE)
            self._gallery_objects.append(placeholder)

        # ── 文件名（缩略图右侧）──
        name_lbl = lv.label(row)
        fname = photo['fname']
        # 截断过长的文件名
        if len(fname) > 24:
            fname = fname[:22] + ".."
        name_lbl.set_text(fname)
        # 定位：缩略图右边缘 + 间距
        name_x = 6 + GAL_THUMB_W + 10
        name_lbl.align(lv.ALIGN.LEFT_MID, name_x, -12)
        name_lbl.set_style_text_color(lv.color_hex(WHITE), 0)
        name_lbl.add_style(make_back_bar_text_style(fonts.body), 0)
        self._gallery_objects.append(name_lbl)

        # ── 日期时间（文件名下方）──
        date_lbl = lv.label(row)
        mtime = photo['mtime']
        if mtime > 0:
            t = _time.localtime(mtime)
            date_str = f"{t[0]}-{t[1]:02d}-{t[2]:02d} {t[3]:02d}:{t[4]:02d}"
        else:
            date_str = "?"
        date_lbl.set_text(date_str)
        date_lbl.align(lv.ALIGN.LEFT_MID, name_x, 12)
        date_lbl.set_style_text_color(lv.color_hex(Colors.TEXT_DIM), 0)
        date_lbl.add_style(make_back_bar_text_style(fonts.caption), 0)
        self._gallery_objects.append(date_lbl)

        # ── 删除按钮（右侧）──
        del_btn = lv.obj(row)
        del_btn.set_size(GAL_DELETE_SIZE, GAL_DELETE_SIZE)
        del_btn.align(lv.ALIGN.RIGHT_MID, -12, 0)
        del_btn.set_style_bg_opa(0, 0)
        del_btn.set_style_border_width(0, 0)
        del_btn.set_style_shadow_width(0, 0)
        del_btn.set_style_outline_width(0, 0)
        del_btn.set_style_outline_opa(0, 0)
        del_btn.set_style_pad_all(0, 0)
        del_btn.clear_flag(lv.obj.FLAG.SCROLLABLE)
        del_btn.add_flag(lv.obj.FLAG.CLICKABLE)

        # 删除 ✕ 标签
        x_lbl = lv.label(del_btn)
        x_lbl.set_text("✕")
        x_lbl.center()
        x_lbl.set_style_text_color(lv.color_hex(0xCC4444), 0)
        x_lbl.add_style(make_back_bar_text_style(fonts.body), 0)
        self._gallery_objects.extend([del_btn, x_lbl])

        # 捕获 photo/row 在闭包中（lambda 默认参数方式）
        del_btn.add_event(
            lambda e, p=photo, r=row: (
                self._on_delete_photo(p, r) if e.get_code() == lv.EVENT.CLICKED else None
            ),
            lv.EVENT.CLICKED, None)

        return row
```

- [ ] **Step 3: 重写 `_build_gallery_ui`（替换原 `_build_gallery_list`）**

```python
    def _build_gallery_ui(self, groups):
        """构建图库纵向滚动列表 — 日期分组标题 + 照片行"""
        screen = self._screen
        list_h = screen.get_height() - BAR_H

        lst = lv.obj(screen)
        lst.set_size(lv.pct(100), list_h)
        lst.set_pos(0, BAR_H)
        lst.set_style_bg_color(lv.color_hex(Colors.BG), 0)
        lst.set_style_bg_opa(255, 0)
        lst.set_style_border_width(0, 0)
        lst.set_style_pad_all(8, 0)
        lst.set_style_radius(0, 0)
        lst.set_scroll_dir(lv.DIR.VER)
        self._gallery_list = lst

        if not groups:
            # 空状态
            lang = self.ctx.lang
            empty = lv.label(lst)
            empty.set_text(lang.t("camera.no_photos"))
            empty.align(lv.ALIGN.CENTER, 0, 0)
            empty.add_style(make_back_bar_text_style(fonts.body), 0)
            empty.set_style_text_color(lv.color_hex(Colors.TEXT_DIM), 0)
            self._gallery_objects.append(empty)
            return

        y = 4
        for group in groups:
            # 日期分组标题
            self._make_date_header(lst, y, group['label'])
            y += GAL_DATE_H + 4

            # 照片行
            for photo in group['photos']:
                row = self._make_photo_row(lst, y, photo)
                # 把 row 引用存到 photo 中，删除时需要
                photo['_row_obj'] = row
                y += GAL_ROW_H + GAL_ROW_GAP

            y += 8  # 组间额外间距

        lst.set_content_height(y + 4)
```

---

### Task 6: 实现删除功能

**Files:**
- Modify: `scripts/camera/app.py`（在 `_build_gallery_ui` 之后插入）

- [ ] **Step 1: 添加 `_on_delete_photo` 方法**

```python
    def _on_delete_photo(self, photo, row_obj):
        """删除照片文件 + 移除 UI 行"""
        import os as _os

        path = photo['path']
        print(f"[Gallery] delete: {path}")

        # 1. 删除文件
        try:
            _os.remove(path)
        except Exception as e:
            print(f"[Gallery] remove failed: {e}")
            return  # 删除失败，保留 UI（用户可重试）

        # 2. 从保活列表移除缩略图数据
        thumb = photo.get('thumb')
        if thumb is not None and thumb in self._gallery_thumbs:
            self._gallery_thumbs.remove(thumb)

        # 3. 从分组数据移除
        for group in self._gallery_groups:
            if photo in group['photos']:
                group['photos'].remove(photo)
                break

        # 4. 删除 LVGL 对象
        #    LVGL v8 删除父对象自动级联删除所有子对象，无需手动递归
        try:
            row_obj.delete()
        except Exception as e:
            print(f"[Gallery] row delete failed: {e}")
        # 注意：_gallery_objects 中可能残留 row 子对象的过期引用，
        # _leave_gallery 中 delete 是 try/except 包裹的，不影响退出

        # 5. 蜂鸣反馈
        self.ctx.buzzer.beep(ms=20)
```

---

### Task 7: 更新清理方法

**Files:**
- Modify: `scripts/camera/app.py:633-655`（更新 `_leave_gallery`）
- Modify: `scripts/camera/app.py:740-771`（更新 `_destroy_ui`）

- [ ] **Step 1: 更新 `_leave_gallery` — 增加缩略图数据清理**

替换现有 `_leave_gallery` 方法：

```python
    def _leave_gallery(self):
        """离开图库，清理缩略图数据 + LVGL 对象 + 恢复相机 UI"""
        import gc as _gc

        # ── 清理 LVGL 对象 ──
        for obj in self._gallery_objects:
            try:
                obj.delete()
            except Exception:
                pass
        self._gallery_objects = []

        if self._gallery_list is not None:
            try:
                self._gallery_list.delete()
            except Exception:
                pass
            self._gallery_list = None

        # ── 释放缩略图像素内存 ──
        self._gallery_thumbs = []
        self._gallery_groups = []

        # ── 恢复相机 UI ──
        if self._bottom_bar is not None:
            self._bottom_bar.clear_flag(lv.obj.FLAG.HIDDEN)
        if self._preview_bg is not None:
            self._preview_bg.clear_flag(lv.obj.FLAG.HIDDEN)

        # 恢复标题 + 状态
        if self._title_label is not None:
            self._title_label.set_text(self.ctx.lang.t("category.camera"))
        self._state = STATE_PHOTO
        self._refresh_shutter()
        self._refresh_mode_icon()

        # 此时 LVGL 对象已全部删除，安全 gc
        _gc.collect()
        print("[Gallery] leave done")
```

- [ ] **Step 2: 更新 `_destroy_ui` — 增加新数据结构清理**

在现有 `_destroy_ui` 方法的属性遍历列表前插入缩略图数据清理：

```python
    def _destroy_ui(self):
        # 先清理非 LVGL 数据
        self._gallery_thumbs = []
        self._gallery_groups = []
        self._gallery_objects = []

        for attr in ('_top_bar', '_bottom_bar', '_preview_bg',
                     '_timer_label', '_gallery_list'):
            # ... 保持现有代码不变 ...
```

即把 `self._gallery_objects = []` 从 `for attr` 循环内部移到前面，并追加 `_gallery_thumbs` 和 `_gallery_groups` 的清空。

更精确地：修改 `_destroy_ui` 的开头部分，将：

```python
    def _destroy_ui(self):
        for attr in ('_top_bar', '_bottom_bar', '_preview_bg',
                     '_timer_label', '_gallery_list'):
```

改为：

```python
    def _destroy_ui(self):
        # 释放图库缩略图内存（非 LVGL 对象）
        self._gallery_thumbs = []
        self._gallery_groups = []

        for attr in ('_top_bar', '_bottom_bar', '_preview_bg',
                     '_timer_label', '_gallery_list'):
```

并删除 `for attr` 循环后面的 `self._gallery_objects = []`（移到 `_leave_gallery` 中统一管理）。

---

### Task 8: 板端验证（K230 BOX）

**注意：** 此任务必须在 K230D BOX 板端执行，不可跳过。

- [ ] **Step 1: 验证缩略图 `image.Image` API**

在 K230 REPL 中手动测试：

```python
import image
img = image.Image("/data/photo/IMG_xxx.jpg")  # 使用实际照片路径
print(img.width, img.height)

# 测试 resize
img.resize(120, 90)

# 测试像素提取（三种方法逐一测试）
# 方法 A: buffer protocol
try:
    data = bytes(img)
    print("bytes(img):", len(data))
except Exception as e:
    print("bytes(img) failed:", e)

# 方法 B: copy + resize
try:
    img2 = img.copy()
    img2.resize(120, 90)
    data2 = bytes(img2)
    print("bytes(copy):", len(data2))
except Exception as e:
    print("copy failed:", e)
```

根据测试结果调整 `_load_thumbnail` 中的像素提取代码路径。

- [ ] **Step 2: 验证 LVGL 原始图像显示**

```python
import lvgl as lv
# 用提取的像素数据创建 img_dsc_t 并显示
dsc = lv.img_dsc_t()
dsc.header.w = 120
dsc.header.h = 90
dsc.header.cf = lv.IMG_CF.TRUE_COLOR  # 或直接 = 4
dsc.data_size = len(pixel_data)
dsc.data = pixel_data

scr = lv.scr_act()
img = lv.img(scr)
img.set_src(dsc)
img.center()
```

如 `lv.IMG_CF.TRUE_COLOR` 不存在，尝试 `4`（LVGL v8 中 `LV_IMG_CF_TRUE_COLOR = 4`，表示 RGB888）。

- [ ] **Step 3: 完整流程测试**

1. 进入相机 APP → 拍照 3-5 张
2. 点击图库按钮 → 验证缩略图显示、日期分组、文件名/时间
3. 点击 ✕ 删除按钮 → 验证文件被删除、UI 行消失
4. 删除某日期组全部照片 → 验证分组标题也消失
5. 删除全部照片 → 验证 "暂无照片" 空状态
6. 按返回 → 验证回到相机预览正常
7. 再次进入图库 → 验证文件列表已更新

- [ ] **Step 4: 异常测试**

1. `/data/photo/` 目录为空 → 验证 "暂无照片"
2. 损坏的 JPEG 文件 → 验证灰色占位 + 文件名正常显示
3. 10+ 张照片 → 验证滚动流畅、无内存不足崩溃

---

### Task 9: 板端修正（根据验证结果调整）

**Files:**
- Modify: `scripts/camera/app.py`（`_load_thumbnail` 方法中的像素提取路径）

- [ ] **Step 1: 根据 Task 6 的 API 测试结果，精简 `_load_thumbnail`**

移除未使用的像素提取尝试路径，只保留板端验证通过的方案。例如如果 `bytes(img.copy())` 有效：

```python
    def _load_thumbnail(self, path):
        import image
        import os as _os
        _os.exitpoint()

        img = None
        try:
            img = image.Image(path)
            src_w, src_h = img.width, img.height
            tw, th = self._fit_thumb_size(src_w, src_h)
            img.resize(tw, th)

            # 已验证：copy() 后支持 bytes()
            rgb_img = img.copy()
            pixel_data = bytes(rgb_img)
            del rgb_img, img

            if not pixel_data:
                return None

            dsc = lv.img_dsc_t()
            dsc.header.w = tw
            dsc.header.h = th
            dsc.header.cf = lv.IMG_CF.TRUE_COLOR
            dsc.data_size = len(pixel_data)
            dsc.data = pixel_data

            return {
                'pixel_data': pixel_data,
                'w': tw,
                'h': th,
                'img_dsc': dsc,
            }
        except Exception as e:
            print(f"[Gallery] thumbnail failed: {e}")
            return None
```

- [ ] **Step 2: 如果板端发现格式不对（RGB888 vs BGRA8888 颜色偏移）**

调整 header.cf：
```python
dsc.header.cf = 5  # LV_IMG_CF_TRUE_COLOR_ALPHA (ARGB8888)
```
或创建对应格式的 `image.Image` 再提取。

- [ ] **Step 3: 如果 `lv.img_dsc_t` header 赋值方式不同**

尝试备用 API：
```python
dsc = lv.img_dsc_t({
    'data_size': len(pixel_data),
    'data': pixel_data,
})
dsc.header.w = tw
dsc.header.h = th
dsc.header.cf = 4
```

---

## 可能的板端调整项（提前准备）

| 问题 | 症状 | 应对 |
|------|------|------|
| `image.Image(path)` 不支持 JPEG | 抛异常 | 尝试 `.bmp` 格式先测通；或需改拍照保存格式为 BMP |
| `img.resize()` 不存在 | AttributeError | 改用 `image.resize(img, w, h)` 函数形式 |
| `bytes(img)` 不支持 | TypeError | 尝试 `memoryview(img)` 或 `img.__data__` |
| `lv.IMG_CF.TRUE_COLOR` 未定义 | AttributeError | 直接写数值 `4` |
| 缩略图颜色异常（偏蓝/偏绿） | 显示颜色不对 | 尝试不同 `header.cf` 值（3=RGB565, 4=RGB888, 5=ARGB8888） |
| 进图库时卡死 | 黑屏无响应 | 文件 I/O 在 task_handler 期间冲突；给 `_group_photos_by_date` 每组间加 `time.sleep_ms(50)` 出让调度 |
| 内存不足 (MemoryError) | 加载到某张时崩溃 | 限制最大缩略图数量（如 20 张），超出部分不加载缩略图仅显示占位 |
