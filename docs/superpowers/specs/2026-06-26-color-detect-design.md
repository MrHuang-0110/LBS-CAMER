# 颜色识别(color_detect)设计

> 创建日期:2026-06-26
> 脚本 ID:`color_detect`(categories.json 已存在,order 6, enabled true)

## 目标

新增 color_detect 脚本:屏幕点击取色,LAB 阈值滑块实时调参,find_blobs 检测同色区域,KEY2 把当前检测色注册为 ID(4 槽),协议 0x06 上传匹配框坐标。

## 架构

复用 _template 单线程主循环 + tag_detect 双通道范式:

- `chn0 VGA RGB888` → 显示 + 屏幕取色(`get_pixel` 直接在 VGA 空间,坐标无需换算)
- `chn1 QVGA RGB565` → `find_blobs` 检测(官方 demo 同款,坐标 ×2 映射回 VGA 上传)
- 主循环:`snapshot chn0 → on_frame → show OSD1 → task_handler`,与 tag_detect 一致

## 通道配置

`core/app_runtime.py` 的 `_channels_for` 新增分支:

```
color_detect → (CAM_CHN_ID_1, Sensor.QVGA, Sensor.RGB565)  # 同 tag_detect
```

chn0 复用默认 VGA RGB888。

## 检测逻辑(on_frame)

每帧:

1. `img_det = chn1 快照`
2. **当前色检测**:用底栏 6 滑块构成的 `(Lmin,Lmax,Amin,Amax,Bmin,Bmax)` 调 `find_blobs`,取最大 blob → 画白框(未注册)
3. **注册色检测**:对 ColorDB 中每个已注册槽(slot 1-4)用其阈值 `find_blobs`,取最大 blob → 画彩色 `ID%d` 框(框色按 slot,BOX_COLORS 同 tag_detect),填 `slots[slot-1]=(id, x*2, y*2, w*2, h*2, 100)`
4. 居中绿色十字 `(320,240)`,size=20,thickness=2(同 tag_detect)
5. KEY2 注册:`pending` 且有当前色 blob → `ColorDB.register(当前6阈值)` 轮转 slot 1-4
6. `host_tick(slots)` 协议 0x06

find_blobs 调用次数 = 1(当前色) + N(已注册槽,≤4),C 加速,可接受。

## UI 布局(640×480,对齐 tag_detect)

### 顶栏(0–52,y=0)
- 返回钮(左,48×48)+ 标题"颜色识别"(居中)

### 左侧取色表(y=52 起,顶栏左下方)
- 4 行 3 列表格,叠在预览区左缘
- 第 1 行:表头 `L | A | B`
- 第 2–4 行:3 个采色历史槽
  - 每行底色 = 采样色 RGB
  - 文字 = 该色 LAB 值(L / A / B 三格)
  - 点击屏幕取色后循环填入:槽1 → 槽2 → 槽3 → 槽1...
  - 未采样行:空 / 默认底色

### 预览区(y=52, h=376)
- 透明,透出 OSD1 摄像画面
- **可点击取色**(CLICKED 事件,记录屏幕坐标 = VGA 坐标)
- 居中绿色十字

### 底栏(y=428, h=52)
- **list 图标**(左,48×48):点弹清除/保存浮层(同 tag_detect)
- **6 个阈值格**(填充满底栏剩余宽度):`Lmin Lmax Amin Amax Bmin Bmax`
  - 每格上行显示标签,下行显示当前数值
  - 点选某格 → 置绿(选中态)→ 滑块绑定该格
  - 选中格实时跟随滑块变化
- **共享滑块**(底栏右侧或下方区域):
  - L 格范围 0–100,A/B 格范围 -128~127(切换选中格时滑块 range 跟随)
  - VALUE_CHANGED → 更新该格数值 → 立即用于 find_blobs
- 计数标签:registered %d/4(右)

> 滑块与 6 格的具体排版(横排 vs 滑块单独一行)在实现时按 52px 高度容纳,优先保证 6 格可点选 + 数值可读 + 滑块可拖。

## 取色与阈值(采样即套用阈值)

1. 点预览区 → LVGL 给屏幕坐标(VGA 空间)→ 存 `pending_click = (x, y)`
2. 下一帧 on_frame:在 `img`(chn0 VGA)该坐标 `get_pixel` 取 RGB → 纯 Python RGB→LAB
3. 容差 ±10(L/A/B 统一):生成 6 阈值
   - `Lmin=lab_L-10, Lmax=lab_L+10`
   - `Amin=lab_A-10, Amax=lab_A+10`
   - `Bmin=lab_B-10, Bmax=lab_B+10`
   - 边界裁剪到各自有效范围(L:0-100, A/B:-128~127)
4. 更新 6 滑块格数值 + 同步共享滑块到选中格
5. 把该色(LAB + RGB)压入左表 3 槽循环(覆盖最旧)
- 滑块手动微调:点格置绿 → 拖滑块改值 → 立即用于 find_blobs

## RGB→LAB 转换(纯 Python)

仅点击时执行一次(非每帧),无性能压力。标准 sRGB→XYZ→Lab:

```python
def _rgb_to_lab(r, g, b):
    # sRGB [0,255] -> [0,1] -> 线性化 -> XYZ (D65) -> Lab
    ...
    return (L, A, B)  # L:0-100, A/B:-128~127
```

实现参考标准公式,gamma 校正 + D65 白点。

## ID 注册与协议

- KEY2 → `ColorDB.register(当前6阈值)` → 轮转 slot 1-4
  - 同色(6 阈值完全相同)不重复占槽,返回已有 slot
  - 每槽存:6 阈值 + 中心 LAB + RGB(用于左表底色 / 框色查表)
- 每帧对每个注册色 find_blobs 取最大 blob → 彩色 `ID%d` 框 + 填 slot
- `host_tick(slots)`,slots 格式同 tag_detect:`(id, x*2, y*2, w*2, h*2, conf)`,大端,40 字节
- 协议类型 `0x06`(TYPE_COLOR_DETECT 已定义,需加进 `CATEGORY_TYPE` 映射)

## ColorDB(`core/color_db.py`)

镜像 tag_db / object_db 纯 Python 实现:

- `register(thresholds) -> slot`:6 阈值 tuple,轮转 1-4,同阈值返回已有槽
- `match(thresholds) -> (slot, 1.0) | (None, 0.0)`:精确匹配(阈值完全相同)
- `clear()`:清内存
- `flush_to_disk()`:no-op(持久化预留)
- `count` 属性
- 每槽额外存中心 LAB + RGB(注册时由调用方一并传入)

## i18n(zh_CN + en_US)

新增 `color_detect` 段:

| key | zh | en |
|-----|----|----|
| `registered` | 已注册 %d/4 | Registered %d/4 |
| `clear` | 清除 | Clear |
| `save` | 保存 | Save |
| `L` / `A` / `B` | L / A / B | L / A / B |
| `Lmin`/`Lmax`/`Amin`/`Amax`/`Bmin`/`Bmax` | L最小/L最大/A最小/A最大/B最小/B最大 | Lmin/Lmax/Amin/Amax/Bmin/Bmax |

## 文件清单

| 操作 | 文件 | 职责 |
|------|------|------|
| 新建 | `scripts/color_detect/app.py` | 主脚本:run/on_frame/_build_ui/_destroy_ui/取色/滑块/左表 |
| 新建 | `scripts/color_detect/__init__.py` | 包标记 |
| 新建 | `core/color_db.py` | 纯 Python ColorDB |
| 改 | `core/app_runtime.py` | _channels_for 加 color_detect 分支 |
| 改 | `comm/host_api.py` | CATEGORY_TYPE 加 `"color_detect": TYPE_COLOR_DETECT` |
| 改 | `resource/i18n/zh_CN.json` | color_detect 段 |
| 改 | `resource/i18n/en_US.json` | color_detect 段 |
| 新建 | `tests/test_color_db.py` | ColorDB 纯 Python 真单测 |
| 新建 | `tests/test_color_detect_ast.py` | AST 契约测试(板端模块 Windows 不可导入) |

> manifest.json 非必需(tag_detect/object_detect 均无),categories.json 条目已存在。

## 风险点

- **find_blobs 多次调用**:1(当前)+≤4(注册),C 加速,可接受
- **RGB→LAB**:仅点击时一次,无性能问题;容差套用后用户滑块微调
- **LVGL slider 首次使用**:demo 有用法,VALUE_CHANGED 回调刷新格数值;range 随选中格类型切换
- **点击坐标映射**:LVGL 屏幕坐标 = VGA 空间,直接对 chn0 img 取色,无需换算
- **左表底色 = 采样 RGB**:注册色的 RGB 已存槽;左表 3 槽与 ID 4 槽独立(采色历史仅参考)

## 测试策略

- `test_color_db.py`:register 轮转/同色不重复占槽/match 精确/clear/count(纯 Python,真跑)
- `test_color_detect_ast.py`:AST 检查 app.py 结构(run 入口/on_frame 钩子/try-except 隔离/find_blobs/CATEGORY_TYPE 映射/_channels_for 分支/i18n 键)— 板端模块 Windows 不可导入,用 AST + 字符串契约
