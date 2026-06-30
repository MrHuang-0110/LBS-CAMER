# 道路识别(road_detect)设计文档

- 日期:2026-06-30
- 状态:已批准(待 spec 复核)
- 关联脚本:`scripts/road_detect/app.py`(新建)
- 关联模块:`core/road_db.py`(新建)、`comm/host_api.py`(改)

## 1. 背景与目标

中鸣 BE-1756 摄像头的"道路识别"功能:用 LAB 阈值检测道路/引导线,屏幕中间一条绿色线随道路弯曲而跳动。
用户实测:对准黑线、在黑线左/中/右采样 3 个点后,绿线会"垂直起来"。

**机理**:3 个采样点采集线条颜色 → LAB 阈值 → `find_blobs` 找到道路像素 → 逐行求道路像素 x 质心 → 连成绿色折线。
道路笔直居中时各行质心 x 相等 → 绿线垂直;道路弯曲/偏移时质心偏移 → 绿线倾斜跳动。
用途:机器人循迹/导航,绿线指示道路走向。

## 2. 范围

UI 布局与持久化**逐格复刻** `scripts/color_detect/app.py`,只在检测算法 / ID 模型 / 画框 / 协议四处按道路场景调整。
不需要学习多 ID,只用 ID1。

## 3. 文件改动

| 操作 | 路径 | 说明 |
|------|------|------|
| 新建 | `scripts/road_detect/app.py` | 主脚本,复刻 color_detect UI,替换 on_frame 算法 |
| 新建 | `core/road_db.py` | 单配置 DB(仿 ColorDB,只 1 槽) |
| 改 | `comm/host_api.py` | `CATEGORY_TYPE` 加 `"road_detect": TYPE_ROAD_DETECT`(0x07) |
| 已就绪 | `config/categories.json` | road_detect 条目已存在(order 7, enabled, ui_mode stream) |
| 已就绪 | `resource/i18n/{en_US,zh_CN}.json` | "道路识别"/"车道线与循线" 已注册 |
| 待确认 | `resource/icons/menu_icon/road_detect.png` | 菜单图标引用已存在,实现前确认文件在位 |

新增 i18n key:`road_detect.locked`("已锁定 ID1" / "Locked ID1"),替换 color_detect 的 `registered` 计数文案。

## 4. UI 设计(与 color_detect 一模一样)

完全复刻 `color_detect/app.py` 的 `_build_ui`:

- **顶栏**:返回按钮(beep + 触摸灵敏度对齐最新 commit)+ 标题"道路识别"(`category.road_detect`)
- **左表 4×3 网格**:表头 L/A/B + 3 行采色历史(底色=采样 RGB,文字=LAB 三值)
- **透明预览区**(600×376):点击取色(沿用 color_detect 的 `_on_preview_clicked` + chn1 RGB565 get_pixel 方案)
- **底栏**:list 图标(左)+ 6 阈值格(Lmin/Lmax/Amin/Amax/Bmin/Bmax)+ 计数标签(右)+ 预览区右侧竖向滑块
- **list 浮层**:清除 / 保存(同 color_detect 的 `_on_list_clicked` / `_on_overlay_clicked`)

**唯一 UI 差异**:计数标签显示"已锁定 ID1"而非"已学习 N"。

复用的常量与组件:`BAR_H=52`、`PREVIEW_H=376`、`DET_SCALE=2`、`TOLERANCE=10`、`THRESH_CELLS`、`_make_cell`、`_select_cell`、`_on_slider_changed`、`_refresh_table`、`_rgb_to_lab`、`_make_threshold`、`_draw_color`。

## 5. 核心算法(并集阈值 + 逐行质心折线)

### 5.1 取色合并(`_apply_sample` 改造)

- 每次取色 → RGB→LAB→±10 单点阈值,压入左表 3 槽循环(同 color_detect 的 `_swatch = [_swatch[1], _swatch[2], (lab, rgb)]`)
- **检测阈值 = 3 槽并集**,由纯函数 `_union_threshold(samples)` 计算:
  - `Lmin = min(各样本 Lmin)`,`Lmax = max(各样本 Lmax)`,A/B 同理
  - 每个样本阈值由 `_make_threshold(lab)` 得 ±10
  - 3 槽未满时用已有样本并集;无样本用默认全范围(与 color_detect 默认一致:L 0-100,A/B -10~10)
- 6 阈值格显示**并集结果**(取色后自动更新;用户也可手动调滑块覆盖并集)
- `_apply_sample` 仍同步滑块到选中格、刷新左表

### 5.2 逐行质心折线(on_frame 核心)

1. chn1 QVGA RGB565 `find_blobs([union_th_list])` 取最大 blob 作道路区域(rect `[x,y,w,h]` in QVGA),用于 bbox 上报与质心计算
2. **逐行质心**:纯函数 `_row_centroids(blob_rect, img_det, th, step=8)`:
   - 在 blob rect 内,每隔 `step` 行
   - 对该行 y,扫描 x ∈ [blob.x, blob.x+blob.w],用 `img_det.get_pixel` 或按行统计道路像素
   - 道路像素的 x 均值 → 质心点 `(cx, row_y)`
   - 返回质心点列表(均 in QVGA 坐标)
   - 性能:step=8 降开销;QVGA 240 高 → 约 30 个采样点/帧
3. chn0 VGA 上把质心点序列 ×`DET_SCALE`(2)缩放,用 `img.draw_line` 连成**绿色折线**(颜色 `(0xFF, 0x00, 0xFF, 0x00)` = ABGR 绿)
4. 道路笔直居中 → 各行质心 x 相等 → **绿线垂直**;道路弯曲/偏移 → 质心偏移 → **绿线跳动倾斜**
5. 额外画道路 bbox 细绿框(thickness=2)+ 居中十字(对齐 color_detect/tag_detect)

### 5.3 质心计算实现选择

`find_blobs` 返回 blob rect,但不直接给逐行像素分布。两条路:
- **方案 A(推荐)**:对 blob rect 内每 step 行,用 `img_det.get_statistics` 或直接 `find_blobs` 限定 ROI 求小 blob 质心 —— 复杂
- **方案 B(采用)**:对 blob rect 内每 step 行,逐像素 `get_pixel` 判定是否在阈值内,累加 x 求均值。纯 Python,step=8 控制开销,host 端可注入合成像素分布做单元测试

实现时优先方案 B;若板端帧率不可接受再回退减小 step 或改 ROI find_blobs。

## 6. ID / 持久化(RoadDB 单配置 + KEY2 锁定)

`core/road_db.py`,仿 `ColorDB` 的内存 + flush 模式,但**只存 1 个配置**(slot 1):

```
{'threshold': (Lmin,Lmax,Amin,Amax,Bmin,Bmax),
 'lab': (L,A,B),
 'rgb': int,
 'samples': [(lab,rgb), (lab,rgb), (lab,rgb)]}   # 左表 3 槽,用于重启后还原 UI
```

接口:
- `lock(threshold, lab, rgb, samples)`:覆盖写入 slot 1,设 `_dirty`,返回 1
- `get()`:取当前锁定配置 dict 或 None
- `clear()`:清内存,设 `_clear_dirty`
- `load_from_disk(path)`:db_store 安全加载(ENOENT 返回 None)
- `flush_to_disk(path)`:db_store 安全写入
- `@property count`:0 或 1(供计数文案判断)

路径:`/sdcard/CamerAi/data/road_db.json`

**KEY2 行为**(与 color_detect 不同):不轮转 4 槽,而是**锁定当前并集阈值为 ID1**(覆盖)。
`IdRegistry.try_register((union_th, lab_mid), buzzer, registrar=lambda th: road_db.lock(th, lab_mid, latest_rgb, list(_swatch)))`。

**生命周期**:
- `run()` 启动:`road_db.load_from_disk()`;若有锁定配置 → 套用到 6 阈值格 + 还原左表 3 槽
- KEY2 锁定:`road_db.flush_to_disk()`(on_frame 内,task_handler 前)
- 退出兜底:`road_db.flush_to_disk()`
- list 浮层"清除":`road_db.clear()`(仅清内存,退出时 flush 清空文件)

## 7. 协议(0x07)

`host_tick(slots)`:
- `slots = [None, None, None, None]`
- 检测到道路 blob 时:`slots[0] = (1, x*DET_SCALE, y*DET_SCALE, w*DET_SCALE, h*DET_SCALE, 100)`(ID1 + 道路 bbox)
- 与 color_detect 同 4 槽上报格式,只用槽 1

`comm/host_api.py` 的 `CATEGORY_TYPE` 增加 `"road_detect": TYPE_ROAD_DETECT`(0x07)。
TYPE_ROAD_DETECT 常量已存在,只需补映射。

## 8. 错误处理

- on_frame try/except 隔离(模板标准);find_blobs 失败/无 blob → 不画线、slots 全 None、不崩主循环
- `_process_overlay_close` / `_destroy_ui` 同 color_detect
- K230 坑沿用 color_detect 已验证方案:
  - get_pixel 用 chn1 RGB565(chn0 RGB888 返回 None)
  - find_blobs 阈值须 `list`(非 tuple)
  - get_point 需预分配 `point_t`
  - get_pixel 对 RGB565 返回打包 int,需 5/6/5 扩展到 8 位

## 9. 测试(TDD)

- `_union_threshold(samples)` 纯函数:给定多样本 → 验证并集 + 裁剪(含无样本默认、部分样本)
- `_row_centroids(blob_rect, pixels, th, step)` 纯函数:host 端注入合成像素分布 → 验证质心 x(笔直→x 相等,偏移→x 偏移)
- `_make_threshold` / `_rgb_to_lab` 复用 color_detect 已测函数(若提取为公共模块则共享测试;否则在 road_detect 测试中复测)
- `RoadDB` 纯 Python:lock / get / clear / load_from_disk / flush_to_disk / count(对齐 ColorDB 测试模式,用 tmp 路径)
- 协议:`CATEGORY_TYPE["road_detect"] == 0x07`

## 10. 与 color_detect 的差异清单

| 维度 | color_detect | road_detect |
|------|--------------|-------------|
| 检测算法 | 单点阈值 + 最大 blob bbox | 并集阈值 + 逐行质心绿线 |
| ID 模型 | 4 槽轮转注册 | 单槽 ID1 锁定 |
| 画框 | 多色彩色 ID 框 + 居中十字 | 道路 bbox 细绿框 + 绿色中线折线 + 居中十字 |
| 协议 | 0x06 | 0x07 |
| 计数文案 | "已学习 N" | "已锁定 ID1" |
| 持久化 | ColorDB(4 槽) | RoadDB(1 槽,多存 samples) |

UI 布局、取色、滑块、list 浮层、持久化机制、单线程模板、i18n 注册流程——全部与 color_detect 一致。
