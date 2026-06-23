# camera APP 框架迁移设计 (BaseScript → run(runtime))

> 日期: 2026-06-23
> 范围: 将 camera APP(最后一个旧 BaseScript 孤儿)迁移到 reset 框架的 `run(runtime)` 范式
> 类型: 纯框架迁移(业务逻辑原样保留,删缩略图死代码)

## 1. 背景与问题

camera APP 当前是 `CameraApp(BaseScript)` 类(1136 行),依赖旧架构的 `self.ctx.lcd` 共享常驻 sensor。在 reset 框架下:

- **无法启动** — 没有 `run()` 入口,main.py 调 `mod.run(runtime)` 失败("script has no run()")
- **依赖不存在的 ctx** — `ctx.lcd.get_sensor()`/`ensure_sensor_running()`/`clear_framebuffers()`/`capture_chn` 在新 `runtime` 上不存在
- settings 已于 2026-06-23 完成同款迁移(BaselineScript → run(runtime)),camera 是最后一个孤儿

本设计复用 settings 迁移确立的范式:模块级 `run(runtime)` 函数式 + 模块级状态 + `_RUNTIME` 缓存供 LVGL 回调取用 + `_destroy_ui()` 只删 LVGL 对象(硬件由 main.py `runtime.cleanup()` 统一释放)。

## 2. 决策(已与用户确认)

| 决策点 | 选择 | 理由 |
|--------|------|------|
| 改造范围 | **纯框架迁移** | 只改通启动/退出,业务(拍照/录像/图库)原样保留。最小风险最快回到可用 |
| 死代码处理 | **删死代码+删过时测试** | `_load_thumbnail` 等从未被调用,图库本就只显示文件名+日期+删除 |
| 文件结构 | **单文件 app.py** | 与 settings/_template 范式一致;状态机贯穿主循环与回调,拆文件反而增加跨文件耦合 |
| on_frame 钩子 | **不加** | camera 是叶子 APP(自带业务),非 AI 脚本基座;per-frame 是内在业务不是插件槽 |

**关于未来 face_detect 迁移**:face_detect 的基座是 _template(单文件 run(runtime)+ on_frame 钩子),不是 camera。两者都采用单文件 run(runtime) 范式,只是 camera 无钩子(叶子)、face_detect 有钩子(AI 插件)。camera 选单文件对未来 face_detect 迁移无阻碍。runtime 的 `_channels_for` 已为 face_detect 配 chn2(XGA/RGBP888),AI 通道在 init_app 阶段声明,on_frame 里 `runtime.sensor.snapshot(chn=CAM_CHN_ID_2)` 即可。

## 3. 架构与入口

模块级 `run(runtime)` 函数式,删除 `CameraApp` 类与 `on_enter/on_frame/on_exit` 生命周期。

### 3.1 主循环(单线程,一个写者)

对齐 _template 的"一个写者"结构 —— 单线程 snapshot→show_image→task_handler 串行,从结构上消除双线程双写者 display DMA 竞争:

```python
def run(runtime):
    global _RUNTIME, _state
    _RUNTIME = runtime
    _state = STATE_PHOTO
    exit_flag = [False]
    _build_ui(runtime, exit_flag)
    while not exit_flag[0]:
        os.exitpoint()
        if _state != STATE_GALLERY:          # 非图库态推预览帧
            try:
                img = runtime.sensor.snapshot(chn=CAM_CHN_ID_0)
                Display.show_image(img, 0, 0, Display.LAYER_OSD1)
            except Exception:
                pass                          # 偶发 snapshot 失败不刷屏
        if _state == STATE_RECORDING:         # 录像计时
            _update_timer()
        _update_flash()                        # 拍照白闪清理(120ms 后删)
        time.sleep_ms(lv.task_handler())
    _destroy_ui()
```

### 3.2 模块级状态(替代 self._xxx)

- 运行态: `_state`
- 录像: `_record_start_ticks`、`_timer_blink`、`_record_path`
- 白闪: `_flash_obj`、`_flash_start`
- 图库: `_gallery_objects`、`_gallery_list`、`_gallery_groups`
- UI 对象: `_screen`、`_top_bar`、`_bottom_bar`、`_preview_bg`、`_timer_label`、`_shutter_btn`、`_mode_green_dot`、`_title_label`
- runtime 缓存: `_RUNTIME`(供 LVGL 回调通过 `_ctx_runtime()` 取用,同 settings 模式)

### 3.3 不加 on_frame 钩子的理由

camera 是叶子 APP,per-frame 逻辑(录像计时、白闪清理)是内在业务,直接写在主循环里。on_frame 钩子只属于 _template(给后续 AI 脚本扩展用)。这点与 settings 一致 —— page/leaf 型不挂钩子。

## 4. 传感器简化

reset 框架下 camera 不再需要旧架构的"常驻 sensor + 不 stop"技巧。

### 4.1 旧架构(hw/lcd.py 共享 sensor)

- `ctx.lcd.get_sensor()` 取启动期常驻实例
- `ctx.lcd.ensure_sensor_running()` 幂等启动(全机只 run 一次)
- 退出**不 stop** sensor(坑:stop 后须 reset,会触碰不可重建的缓冲池)
- `ctx.lcd.clear_framebuffers()` 清 LVGL 双缓冲
- 拍照用 `ctx.lcd.capture_chn`(= CAM_CHN_ID_1)

### 4.2 新架构(每进程独立 runtime.sensor)

- `runtime.sensor` 由 `init_app("camera")` 配好:chn0 VGA/RGB888(预览)+ chn1 SXGAM/RGB565(拍照),与 hw/lcd.py 配置完全一致(已核对 app_runtime.py `_channels_for`)
- `init_app` 末尾已调 `sensor.run()`,进入 run() 时 sensor 已在跑
- 预览: `runtime.sensor.snapshot(chn=CAM_CHN_ID_0)` → `Display.show_image(OSD1)`
- 拍照: `runtime.sensor.snapshot(chn=CAM_CHN_ID_1)` → `img.save(jpg)`
- 退出: main.py 统一调 `runtime.cleanup()`(已 stop sensor)

**简化收益**: 删 `_init_camera`/`_stop_camera` 方法及 `clear_framebuffers` 调用。camera run() 开箱即用 runtime.sensor,退出靠 cleanup。GALLERY 态不推预览帧由 `if _state != STATE_GALLERY` 守住(旧代码已有此判断)。

## 5. 业务逻辑保留

纯框架迁移,业务原样保留(除删死代码)。

### 5.1 状态机
PHOTO ↔ VIDEO → RECORDING,任意待机态 → GALLERY。
`_on_mode_toggle`(PHOTO↔VIDEO)、`_on_shutter`(拍照/开始录像/停止录像)、`_refresh_shutter`(快门外观按状态)、`_refresh_mode_icon`(录像模式绿点)全部保留为模块级函数。

### 5.2 拍照
`_capture_photo` 原样保留:`/data/photo/IMG_YYYYMMDD_HHMMSS.jpg`,chn1 snapshot(短重试 5 次)、`img.save(path)`、`_flash_feedback` 白闪。`ctx.lcd.capture_chn` 改为直接常量 `CAM_CHN_ID_1`。

### 5.3 录像(空壳)
`_start_recording`/`_stop_recording`/`_show_timer`/`_update_timer` 原样保留。状态切换 + 计时器显示/红点闪烁,无实际视频编码(旧代码即如此)。本次不补真录像。

### 5.4 图库
`_on_gallery`/`_enter_gallery`/`_leave_gallery`/`_group_photos_by_date`/`_make_date_header`/`_make_photo_row`/`_build_gallery_ui`/`_on_delete_photo`/`_remove_photo_from_groups`/`_rebuild_gallery_ui` 全部保留为模块级函数。图库仍只显示**文件名 + 日期 + 删除按钮**(无缩略图)。

### 5.5 删除死代码
- 删 `_load_thumbnail`、`_fit_thumb_size`、`_bmp_dimensions`(缩略图相关,从未被调用)
- 删 `_gallery_thumbs` 状态变量及所有引用(`_enter_gallery` 保活循环、`_on_delete_photo` 移除缩略图、`_leave_gallery`/`_destroy_ui` 清空)
- 删 GAL_THUMB_* 常量
- 删 `import image as _image_lib`(缩略图解码用,删死代码后不再需要)

### 5.6 返回逻辑
`_on_back`:GALLERY 态调 `_leave_gallery`(回相机),其他态设 `exit_flag[0]=True`(回菜单)。

### 5.7 ctx → runtime 替换
- `self.ctx.lang` → `runtime.lang`(或 `_ctx_runtime().lang`)
- `self.ctx.buzzer.beep` → `_ctx_runtime().buzzer.beep`
- `self.ctx.request_exit()` → `exit_flag[0]=True`(run() 主循环用闭包捕获的 exit_flag,返回钮回调设它)
- `self.ctx.lcd.*` → 删除(传感器简化,见第 4 节)

### 5.8 标题文字
相机态 `lang.t("category.camera")`,图库态 `lang.t("camera.gallery")`,图库返回恢复(与旧代码一致)。

## 6. 退出清理与 _destroy_ui

### 6.1 退出路径
返回钮(非 GALLERY 态)设 `exit_flag[0]=True` → 主循环退出 → `_destroy_ui()` → run() 返回 → main.py 调 `runtime.cleanup()`(stop sensor + deinit display/media/lvgl) + reset 回菜单。

### 6.2 _destroy_ui() 职责
对齐 settings 的 `_destroy_ui`,只删 LVGL 对象,不碰 runtime 硬件:
- 删顶栏/底栏/预览区/计时器/图库列表等 LVGL 对象(try/except 逐个 delete)
- 清空 `_gallery_objects` 列表
- 删 `_flash_obj`(若有残留白闪)
- **恢复屏幕不透明背景**(`set_style_bg_opa(255)` + BG 色)—— camera 运行期设过 `bg_opa=0`(透出 OSD1),主菜单需不透明背景,必须恢复
- 各模块级引用置 None

### 6.3 图库态退出
GALLERY 态按返回先 `_leave_gallery()` 回相机态(不直接退出 APP),再按一次返回才 `exit_flag=True`。这是旧逻辑,保留。`_leave_gallery` 负责恢复屏幕透明 + 相机 UI + 状态。用户若硬退,`_destroy_ui` 统一 delete 全部对象,安全。

### 6.4 与 settings 的差异
settings `_destroy_ui` 不需恢复 `bg_opa`(本就 255 不透明);camera 必须恢复(运行期是 0)。迁移时保留旧 camera `_destroy_ui` 末尾的恢复逻辑。

## 7. 测试策略

host 端 AST 测试(板端模块不可导入,沿用 ast.parse 模式)。分两类:

### 7.1 重写 tests/test_camera_gallery.py
- 删 `test_thumbnail_loader_decodes_jpg_via_image_module`(死代码已删)
- 类相关断言改为模块级函数断言(`CameraApp` 类不再存在)
- `test_camera_app_imports_image_module` → 断言**不再** import image
- 保留 `test_photo_capture_saves_as_jpg`(拍照存 JPG)→ 改为模块级 `_capture_photo` 断言
- 保留删除 reflow(`_remove_photo_from_groups` + `_rebuild_gallery_ui` 存在)→ 改为模块级

### 7.2 新增 tests/test_camera.py(迁移契约,对齐 test_settings)
- `test_camera_has_run_entry` — 模块有 `def run(runtime)`
- `test_camera_no_basescript` — 不 import/不继承 BaseScript,无 `on_enter`/`on_frame`/`on_exit`
- `test_camera_no_ctx_lcd` — 不依赖 `ctx.lcd`/`get_sensor`/`ensure_sensor_running`/`clear_framebuffers`
- `test_camera_uses_runtime_sensor` — 用 `runtime.sensor.snapshot(chn=CAM_CHN_ID_0)` 推预览
- `test_camera_exit_flag_loop` — run() 有 `while not exit_flag[0]` 主循环 + `time.sleep_ms(lv.task_handler())`
- `test_camera_top_bar_back_button` — 顶栏有返回钮 + CLICKED 设 exit_flag
- `test_camera_state_machine` — PHOTO/VIDEO/RECORDING/GALLERY 四状态常量 + `_on_shutter`/`_on_mode_toggle` 存在
- `test_camera_gallery_no_thumbnails` — 不存在 `_load_thumbnail`/`_fit_thumb_size`/`_bmp_dimensions`
- `test_camera_no_image_import` — 顶层不 import image
- `test_camera_destroy_ui_restores_opacity` — `_destroy_ui` 恢复屏幕不透明(set_style_bg_opa 到 255)

### 7.3 板端验收(用户执行)
1. 从主菜单进 camera,顶栏(返回+标题)+预览+底栏(图库/快门/模式)正常,画面流畅不卡
2. 拍照:点快门(白闪 + 蜂鸣),照片存入 /data/photo/
3. 模式切换:点模式钮 PHOTO↔VIDEO(绿点+快门外观变化)
4. 录像:点快门开始(计时器+红点闪烁),再点停止(空壳,仅状态)
5. 图库:点图库进图库页(文件名+日期+删除),删除一张照片(行上移),返回回相机
6. 返回:点返回回主菜单,再进其他 APP(settings/模板)验证未受污染
7. fc 不卡(单线程无竞争)

## 8. 文件清单

| 文件 | 操作 |
|------|------|
| `scripts/camera/app.py` | 整体重写:CameraApp 类 → 模块级 run(runtime) 函数式,删死代码 |
| `tests/test_camera_gallery.py` | 重写:删缩略图测试,类断言改模块级 |
| `tests/test_camera.py` | 新增:迁移契约测试(对齐 test_settings) |

不改动: `core/app_runtime.py`(init_app 已支持 camera chn 配置)、`config/categories.json`(camera category 已存在)、`main.py`(run_script 通用路径已支持)。
