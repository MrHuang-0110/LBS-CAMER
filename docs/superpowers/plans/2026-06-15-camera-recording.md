# 相机录像功能实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 CamerAi 相机 APP 的录像从"空壳只打日志"改为真实写出可播放的 H264/MP4 视频文件到 /data/video。

**Architecture:** 录像走 K230 第三 sensor 通道 chn2(YUV420SP 640×480),经 `MediaManager.link` 绑定到 H264 硬件编码器(VENC),每帧从编码器 `GetStream` 取码流写入 mp4 muxer。chn2 必须在 `MediaManager.init()` 之前声明(K230 池不可重建,pitfall #15)。chn0 预览(snapshot→OSD1)与 chn2 编码并发互不干扰。严格对齐已板端验证的参考实现 `E:\LBS-Project\CanMV\CamerAPP\camer_mode.py`。

**Tech Stack:** MicroPython + `media.vencoder`(Encoder/ChnAttrStr/StreamData)+ `mpp.mp4_format`/`mpp.mp4_format_struct`(kd_mp4_* + k_mp4_*_s 结构体)+ `media.sensor`(CAM_CHN_ID_2)。

---

## 文件结构

- **hw/lcd.py**(修改):在 chn0/chn1 之后、`MediaManager.init()` 之前增加 chn2 声明(YUV420SP 640×480);暴露 `record_chn = CAM_CHN_ID_2` 供 APP 取用。
- **scripts/camera/app.py**(修改):
  - 顶部新增 vencoder/mp4 imports + 录像常量(REC_WIDTH/REC_HEIGHT)。
  - 新增纯逻辑辅助 `_video_time_name()` / `_next_video_path()`(可 host 测试)。
  - `__init__` 新增 `_rec`(录像运行时上下文 dict)。
  - 重写 `_start_recording`(创建 encoder+muxer+link+start)。
  - 新增 `_record_tick`(从 `on_frame` 在 STATE_RECORDING 时每帧调用)。
  - 重写 `_stop_recording`(stop+destroy+close muxer)。
  - 图库过滤/标签:`.avi` → `.mp4`。
  - 视频路径常量 `/data/video`(照片仍 `/data/photo`,分开存)。

## TDD 说明(诚实边界)

K230 固件的 VENC/sensor DMA(`Encoder.GetStream`、`MediaManager.link`、`kd_mp4_write_frame`)**无法在 host 上复现**——这些是板载硬件路径,没有任何 host 框架能跑。因此:

- **可 host 测试的纯逻辑**:文件名/去重路径生成(`_video_time_name`/`_next_video_path`)。这部分严格走 Red→Green。
- **固件集成部分**:唯一有效验证是**板端录像 + 把 .mp4 拉到电脑播放**。计划在对应任务里给出明确的板端验证步骤与预期串口输出,作为该部分的"测试"。这与本项目此前修拍照 bug 的验证方式一致。

## 内存风险(必须知晓)

当前 chn1 = SXGAM 1280×960 RGB565 ≈ **2.46MB**(拍照已板端验证通过)。新增 chn2 = 640×480 YUV420SP ≈ **460KB**,加 chn0 VGA RGB888 ≈ 921KB、VENC 输出 8 缓冲、LVGL 双缓冲 BGRA8888 ≈ 1.2MB×2。VB 池可能吃紧。
- **回退方案**:若板端出现 VB/内存分配失败(而非编码错误),把 chn1 降到更小尺寸(参考实现用 320×240 RGB565=150KB,拍照同样成功——证明 chn1 不必 SXGAM,声明了 sensor 输入尺寸后下采样即可)。该回退只改 hw/lcd.py chn1 的 `set_framesize`,不影响本计划其它部分。

---

## Task 1: hw/lcd.py 声明录像通道 chn2

**Files:**
- Modify: `hw/lcd.py`(import 行 + chn 声明块 ~49-65)

- [ ] **Step 1: 扩展 sensor 通道 import**

把 import 行改为同时引入 CAM_CHN_ID_2:

```python
from media.sensor import Sensor, CAM_CHN_ID_0, CAM_CHN_ID_1, CAM_CHN_ID_2
```

- [ ] **Step 2: 在 chn1 之后、`self.capture_chn` 之前插入 chn2 声明**

在 `self.capture_chn = CAM_CHN_ID_1` 这一行**之前**插入:

```python
        # chn2 — 录像编码源：YUV420SP 640×480，喂给 H264 硬件编码器(VENC)。
        # 录像不走 snapshot，而是 MediaManager.link(chn2 → VENC)，硬件直连。
        # 通道必须在 MediaManager.init() 前声明全（池不可重建，坑 #15）。
        # 严格对齐参考实现 camer_mode.py 的 chn2 配置（板端验证可用）。
        self.sensor.set_framesize(width=640, height=480, chn=CAM_CHN_ID_2)
        self.sensor.set_pixformat(Sensor.YUV420SP, chn=CAM_CHN_ID_2)
        self.record_chn = CAM_CHN_ID_2
```

- [ ] **Step 3: 板端冒烟验证(固件路径,无 host 测试)**

把 hw/lcd.py 部署到板子,正常开机进相机。
预期:开机 LOGO→主菜单→相机预览正常显示,串口**无** `MediaManager.init` 失败、无 `pool` 相关报错。
如果开机即报内存/池分配失败 → 触发"内存风险"回退(降 chn1 尺寸),不要继续 Task 2。

- [ ] **Step 4: Commit**

```bash
git add hw/lcd.py
git commit -m "feat(camera): declare chn2 (YUV420SP 640x480) for video encoding"
```

---

## Task 2: app.py 视频路径生成(纯逻辑,可 host 测试)

**Files:**
- Create: `tests/test_video_path.py`
- Modify: `scripts/camera/app.py`(模块级新增两个纯函数)

- [ ] **Step 1: 写失败测试**

`tests/test_video_path.py`——只测纯逻辑,不 import lvgl/media。把被测函数复制为可注入依赖的纯实现(时间元组 + stat 探测回调均为参数):

```python
# tests/test_video_path.py
# 纯逻辑测试：视频文件名生成 + 同秒去重。不依赖 K230 固件模块。
from scripts.camera.video_path import video_time_name, next_video_path


def test_time_name_format():
    # localtime 元组：(year, month, day, hour, min, sec, ...)
    t = (2026, 6, 15, 9, 8, 7, 0, 0)
    assert video_time_name(t, "VID", ".mp4") == "VID_20260615_090807.mp4"


def test_next_path_no_collision():
    t = (2026, 6, 15, 9, 8, 7, 0, 0)
    # exists 回调永远返回 False → 直接用原名
    path = next_video_path("/data/video", "VID", ".mp4", t, lambda p: False)
    assert path == "/data/video/VID_20260615_090807.mp4"


def test_next_path_collision_appends_index():
    t = (2026, 6, 15, 9, 8, 7, 0, 0)
    seen = {"/data/video/VID_20260615_090807.mp4"}
    path = next_video_path("/data/video", "VID", ".mp4", t, lambda p: p in seen)
    assert path == "/data/video/VID_20260615_090807_01.mp4"
```

- [ ] **Step 2: 运行测试,确认失败**

Run: `python -m pytest tests/test_video_path.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'scripts.camera.video_path'`

- [ ] **Step 3: 写最小实现**

Create `scripts/camera/video_path.py`(独立纯模块,host 与板端都可 import,因为不依赖固件):

```python
# scripts/camera/video_path.py — 视频/照片文件名生成（纯逻辑，无固件依赖）

def video_time_name(t, prefix, ext):
    """t: localtime 元组 (Y,M,D,h,m,s,...)；返回 'PREFIX_YYYYMMDD_HHMMSS.ext'"""
    base = "%s_%04d%02d%02d_%02d%02d%02d" % (
        prefix, t[0], t[1], t[2], t[3], t[4], t[5]
    )
    return base + ext


def next_video_path(dir_path, prefix, ext, t, exists):
    """生成不冲突的完整路径。exists(path)->bool 探测文件是否已存在（注入以便测试）。"""
    name = video_time_name(t, prefix, ext)
    path = dir_path + "/" + name
    idx = 1
    while exists(path):
        dot = name.rfind(".")
        path = dir_path + "/" + name[:dot] + "_%02d" % idx + ext
        idx += 1
    return path
```

- [ ] **Step 4: 运行测试,确认通过**

Run: `python -m pytest tests/test_video_path.py -v`
Expected: PASS(3 passed)

- [ ] **Step 5: Commit**

```bash
git add tests/test_video_path.py scripts/camera/video_path.py
git commit -m "feat(camera): pure video path/filename helpers with tests"
```

---

## Task 3: app.py 录像 imports、常量与运行时上下文

**Files:**
- Modify: `scripts/camera/app.py`(顶部 imports ~14-22、常量区 ~30-46、`__init__` ~90-106)

- [ ] **Step 1: 新增录像相关 import**

在 `from media.display import Display` 之后插入(严格对齐参考实现的 import 集合,避免缺符号):

```python
from media.media import MediaManager
from media.vencoder import Encoder, ChnAttrStr, StreamData
from mpp.mp4_format import *
from mpp.mp4_format_struct import *
```

> 注:`VENC_CHN_ID_0`/`VIDEO_ENCODE_MOD_ID`/`VENC_DEV_ID` 由 `media.vencoder`/`media.media` 的星号导出提供;参考实现用 `from media.vencoder import *` + `from media.media import *`。若具名 import 在板端报 `ImportError: cannot import name VENC_CHN_ID_0`,改成与参考一致的星号导入:
> ```python
> from media.media import *
> from media.vencoder import *
> ```

- [ ] **Step 2: 新增录像常量**

在状态常量(`STATE_GALLERY = 3`)之后插入:

```python
# 录像编码尺寸（对齐 hw/lcd.py chn2 与参考实现 camer_mode.py）
REC_WIDTH = 640
REC_HEIGHT = 480
VIDEO_DIR = "/data/video"
PHOTO_DIR = "/data/photo"
```

- [ ] **Step 3: `__init__` 新增录像运行时上下文**

在 `self._record_path = ""` 一行附近(录像相关字段处)新增统一的 `_rec` dict,替代散落字段:

```python
        # 录像运行时上下文（None 表示未在录像）
        self._rec = None          # dict: encoder/mp4_handle/mp4_track/...
        self._rec_last_stop_ms = 0
```

- [ ] **Step 4: 语法校验**

Run: `python -c "import ast; ast.parse(open('scripts/camera/app.py',encoding='utf-8').read()); print('OK')"`
Expected: `OK`(注:不能真正 import,因 lvgl/media 在 host 不存在;只校验语法)

- [ ] **Step 5: Commit**

```bash
git add scripts/camera/app.py
git commit -m "feat(camera): add recording imports, constants, runtime ctx"
```

---

## Task 4: app.py 实现 _start_recording(创建编码器+muxer+link)

**Files:**
- Modify: `scripts/camera/app.py`(`_start_recording` ~506-525)

- [ ] **Step 1: 重写 `_start_recording`**

完整替换原 `_start_recording` 方法体(严格对齐 `_record_begin`,改为实例方法、用我们的纯路径函数):

```python
    def _start_recording(self):
        """开始录像：建 H264 编码器 + mp4 muxer + link(chn2→VENC)，启动编码。"""
        import os, time as _time
        from media.sensor import CAM_CHN_ID_2

        if self._rec is not None:
            return
        # 上次 stop 后短时间内重启，媒体链路可能未完全释放，给 260ms 缓冲
        if self._rec_last_stop_ms:
            gap = _time.ticks_diff(_time.ticks_ms(), self._rec_last_stop_ms)
            if gap < 260:
                _time.sleep_ms(260 - gap)

        try:
            os.mkdir(VIDEO_DIR)
        except Exception:
            pass

        def _exists(p):
            try:
                os.stat(p)
                return True
            except Exception:
                return False

        from scripts.camera.video_path import next_video_path
        path = next_video_path(VIDEO_DIR, "VID", ".mp4", _time.localtime(), _exists)

        venc_chn = VENC_CHN_ID_0
        encoder = None
        mp4_handle = None
        link = None
        try:
            encoder = Encoder()
            encoder.SetOutBufs(venc_chn, 8, REC_WIDTH, REC_HEIGHT)
            chn_attr = ChnAttrStr(
                encoder.PAYLOAD_TYPE_H264, encoder.H264_PROFILE_MAIN,
                REC_WIDTH, REC_HEIGHT)
            # mp4 muxer
            mp4_cfg = k_mp4_config_s()
            mp4_cfg.config_type = K_MP4_CONFIG_MUXER
            mp4_cfg.muxer_config.file_name[:] = bytes(path, "utf-8")
            mp4_cfg.muxer_config.fmp4_flag = False
            hp = k_u64_ptr()
            if kd_mp4_create(hp, mp4_cfg):
                raise OSError("kd_mp4_create failed")
            mp4_handle = hp.value
            # video track
            info = k_mp4_track_info_s()
            info.track_type = K_MP4_STREAM_VIDEO
            info.time_scale = 1000
            info.video_info.width = REC_WIDTH
            info.video_info.height = REC_HEIGHT
            info.video_info.codec_id = K_MP4_CODEC_ID_H264
            tp = k_u64_ptr()
            if kd_mp4_create_track(mp4_handle, tp, info):
                raise OSError("kd_mp4_create_track failed")
            mp4_track = tp.value
            # 编码器 + 链路
            encoder.Create(venc_chn, chn_attr)
            src = self._sensor.bind_info(chn=CAM_CHN_ID_2)
            link = MediaManager.link(
                src["src"], (VIDEO_ENCODE_MOD_ID, VENC_DEV_ID, venc_chn))
            encoder.Start(venc_chn)

            self._rec = {
                "venc_chn": venc_chn, "encoder": encoder,
                "mp4_handle": mp4_handle, "mp4_track": mp4_track,
                "stream_data": StreamData(), "frame_data": k_mp4_frame_data_s(),
                "save_idr": bytearray(REC_WIDTH * REC_HEIGHT * 3 // 4),
                "idr_index": 0, "got_first_i": False, "video_start_ts": 0,
                "link": link, "path": path,
            }
            self._state = STATE_RECORDING
            self._record_start_ticks = _time.ticks_ms()
            self._refresh_shutter()
            self._show_timer(True)
            print(f"[Camera] recording started: {path}")
        except Exception as e:
            print(f"[Camera] recording start failed: {e}")
            self._cleanup_recording(encoder, venc_chn, link, mp4_handle)
            self._rec = None
```

- [ ] **Step 2: 新增 `_cleanup_recording` 私有助手(供 start 失败与 stop 复用)**

在 `_start_recording` 之后插入:

```python
    def _cleanup_recording(self, encoder, venc_chn, link, mp4_handle):
        """统一释放编码器/链路/muxer 资源（每步独立 try，确保全部尝试）。"""
        try:
            if encoder is not None:
                encoder.Stop(venc_chn)
        except Exception:
            pass
        try:
            if link is not None:
                del link
        except Exception:
            pass
        try:
            if encoder is not None:
                encoder.Destroy(venc_chn)
        except Exception:
            pass
        try:
            if mp4_handle is not None:
                kd_mp4_destroy_tracks(mp4_handle)
                kd_mp4_destroy(mp4_handle)
        except Exception:
            pass
```

- [ ] **Step 3: 语法校验**

Run: `python -c "import ast; ast.parse(open('scripts/camera/app.py',encoding='utf-8').read()); print('OK')"`
Expected: `OK`

- [ ] **Step 4: Commit**

```bash
git add scripts/camera/app.py
git commit -m "feat(camera): implement _start_recording (encoder+muxer+link)"
```

---

## Task 5: app.py 实现 _record_tick + 接入 on_frame

**Files:**
- Modify: `scripts/camera/app.py`(`on_frame` ~139-141、新增 `_record_tick`)

- [ ] **Step 1: 新增 `_record_tick` 方法**

在 `_cleanup_recording` 之后插入(严格对齐参考 `_record_tick` 的 HEADER/I/P 帧处理):

```python
    def _record_tick(self):
        """每帧调用：从编码器取码流写入 mp4。在 on_frame 的 STATE_RECORDING 分支调用。"""
        import uctypes
        rec = self._rec
        if rec is None:
            return
        enc = rec["encoder"]
        venc_chn = rec["venc_chn"]
        sd = rec["stream_data"]
        fd = rec["frame_data"]
        save_idr = rec["save_idr"]
        try:
            enc.GetStream(venc_chn, sd)
            stype = sd.stream_type[0]
            if not rec["got_first_i"]:
                if stype == enc.STREAM_TYPE_I:
                    rec["got_first_i"] = True
                    rec["video_start_ts"] = sd.pts[0]
                    idx = rec["idr_index"]
                    sz = sd.data_size[0]
                    save_idr[idx:idx + sz] = uctypes.bytearray_at(sd.data[0], sz)
                    rec["idr_index"] = idx + sz
                    fd.codec_id = K_MP4_CODEC_ID_H264
                    fd.data = uctypes.addressof(save_idr)
                    fd.data_length = rec["idr_index"]
                    fd.time_stamp = 0
                    kd_mp4_write_frame(rec["mp4_handle"], rec["mp4_track"], fd)
                    enc.ReleaseStream(venc_chn, sd)
                elif stype == enc.STREAM_TYPE_HEADER:
                    idx = rec["idr_index"]
                    sz = sd.data_size[0]
                    save_idr[idx:idx + sz] = uctypes.bytearray_at(sd.data[0], sz)
                    rec["idr_index"] = idx + sz
                    enc.ReleaseStream(venc_chn, sd)
                else:
                    enc.ReleaseStream(venc_chn, sd)
            else:
                fd.codec_id = K_MP4_CODEC_ID_H264
                fd.data = sd.data[0]
                fd.data_length = sd.data_size[0]
                fd.time_stamp = sd.pts[0] - rec["video_start_ts"]
                kd_mp4_write_frame(rec["mp4_handle"], rec["mp4_track"], fd)
                enc.ReleaseStream(venc_chn, sd)
        except Exception as e:
            print(f"[Camera] record tick: {e}")
```

- [ ] **Step 2: 在 on_frame 接入 tick**

把 `on_frame` 中的录像计时器分支:

```python
        # 录像计时器更新
        if self._state == STATE_RECORDING:
            self._update_timer()
```

改为(先写码流再更新计时,保证录像不丢帧):

```python
        # 录像：每帧取码流写 mp4 + 更新计时器
        if self._state == STATE_RECORDING:
            self._record_tick()
            self._update_timer()
```

- [ ] **Step 3: 语法校验**

Run: `python -c "import ast; ast.parse(open('scripts/camera/app.py',encoding='utf-8').read()); print('OK')"`
Expected: `OK`

- [ ] **Step 4: Commit**

```bash
git add scripts/camera/app.py
git commit -m "feat(camera): implement _record_tick and wire into on_frame"
```

---

## Task 6: app.py 重写 _stop_recording(收尾 muxer)

**Files:**
- Modify: `scripts/camera/app.py`(`_stop_recording` ~527-532)

- [ ] **Step 1: 重写 `_stop_recording`**

完整替换:

```python
    def _stop_recording(self):
        """停止录像：停编码器、释放链路、关闭 mp4 muxer（文件落盘）。"""
        import time as _time
        rec = self._rec
        ok = bool(rec and rec.get("got_first_i"))
        path = rec.get("path", "") if rec else ""
        if rec is not None:
            self._cleanup_recording(
                rec["encoder"], rec["venc_chn"], rec["link"], rec["mp4_handle"])
        self._rec = None
        self._rec_last_stop_ms = _time.ticks_ms()
        self._state = STATE_VIDEO
        self._refresh_shutter()
        self._show_timer(False)
        import gc
        gc.collect()
        if ok:
            print(f"[Camera] recording stopped: {path}")
        else:
            # 没拿到首个 I 帧 → 文件无有效数据，提示而非假成功
            print(f"[Camera] recording stopped but no frames: {path}")
```

- [ ] **Step 2: on_exit / 切走时确保停止录像(防止退出时编码器悬挂)**

检查 `on_exit`(~153)。若退出时仍在录像,先停。把 `on_exit` 改为:

```python
    def on_exit(self):
        if self._rec is not None:
            self._stop_recording()
        self._stop_camera()
        self._destroy_ui()
        super().on_exit()
```

- [ ] **Step 3: 语法校验**

Run: `python -c "import ast; ast.parse(open('scripts/camera/app.py',encoding='utf-8').read()); print('OK')"`
Expected: `OK`

- [ ] **Step 4: Commit**

```bash
git add scripts/camera/app.py
git commit -m "feat(camera): rewrite _stop_recording to finalize mp4 muxer"
```

---

## Task 7: 图库支持 .mp4 + 视频路径迁移到 /data/video

**Files:**
- Modify: `scripts/camera/app.py`(图库过滤 ~592-599、行标签 ~676-678、`_start_recording` 已用 VIDEO_DIR)
- Modify: `tests/test_video_path.py`(扩展扩展名过滤测试)

- [ ] **Step 1: 写失败测试(扩展名过滤纯逻辑)**

把扩展名判断抽成纯函数并测试。在 `tests/test_video_path.py` 追加:

```python
from scripts.camera.video_path import is_media_file


def test_is_media_file_accepts_known_types():
    assert is_media_file("IMG_1.jpg")
    assert is_media_file("IMG_1.JPG")
    assert is_media_file("X.bmp")
    assert is_media_file("VID_1.mp4")


def test_is_media_file_rejects_others():
    assert not is_media_file("notes.txt")
    assert not is_media_file("VID_1.avi")  # 已迁移到 mp4，旧 avi 不再列出
```

- [ ] **Step 2: 运行,确认失败**

Run: `python -m pytest tests/test_video_path.py -v`
Expected: FAIL — `ImportError: cannot import name 'is_media_file'`

- [ ] **Step 3: 在 video_path.py 实现 is_media_file**

追加到 `scripts/camera/video_path.py`:

```python
def is_media_file(name):
    """图库可显示的媒体文件判断（MicroPython endswith 不支持 tuple，逐个判断）。"""
    low = name.lower()
    return (low.endswith('.jpg') or low.endswith('.bmp')
            or low.endswith('.mp4'))
```

- [ ] **Step 4: 运行,确认通过**

Run: `python -m pytest tests/test_video_path.py -v`
Expected: PASS(5 passed)

- [ ] **Step 5: 图库 `_enter_gallery` 改用 is_media_file + 扫描两个目录**

把 `_enter_gallery` 中扫描 `/data/photo/` 的块(~589-601)替换为扫描照片与视频两个目录:

```python
        # 扫描照片 + 视频两个目录
        from scripts.camera.video_path import is_media_file
        files = []
        for d in (PHOTO_DIR, VIDEO_DIR):
            try:
                for f in os.listdir(d):
                    if is_media_file(f):
                        full = d + "/" + f
                        try:
                            st = os.stat(full)
                            files.append((f, full, st[8]))   # (name, fullpath, mtime)
                        except Exception:
                            files.append((f, full, 0))
            except Exception as e:
                print(f"[Camera] gallery scan {d} failed: {e}")
        # 按 mtime 倒序
        files.sort(key=lambda x: x[2], reverse=True)
        self._build_gallery_list(files)
```

> 注:`files` 元素由 `(name, mtime)` 变为 `(name, fullpath, mtime)`。下一步同步 `_build_gallery_list`。

- [ ] **Step 6: 同步 `_build_gallery_list` 的元组解包与类型标签**

在 `_build_gallery_list` 中,遍历 `files` 处把解包改为三元组,并把视频判断从 `.avi` 改为 `.mp4`。找到 `is_video = fname.lower().endswith('.avi')`(~676)改为:

```python
            is_video = fname.lower().endswith('.mp4')
```

并把该函数内对 `files` 的遍历(原 `for fname, mtime in files:` 之类)改为:

```python
        for fname, fpath, mtime in files:
```

(若原代码用索引访问 `f[0]`/`f[1]`,相应改为 `f[0]` 名称、`f[1]` 全路径、`f[2]` mtime。)
类型标签:`type_lbl.set_text("MP4" if is_video else "JPG")`。

- [ ] **Step 7: 拍照路径常量化(统一用 PHOTO_DIR)**

确认 `_capture_photo` 与 `_start_recording` 不再各自硬编码 `/data/photo/`。`_capture_photo` 中的目录改用 `PHOTO_DIR`:把 `path = f"/data/photo/IMG_..."` 一类拼接改为基于 `PHOTO_DIR` 与 `next_video_path(PHOTO_DIR, "IMG", ".jpg", ...)`(复用同一去重逻辑,避免同秒覆盖)。

- [ ] **Step 8: 语法校验 + 跑全部纯逻辑测试**

Run: `python -c "import ast; ast.parse(open('scripts/camera/app.py',encoding='utf-8').read()); print('OK')"`
Run: `python -m pytest tests/test_video_path.py -v`
Expected: `OK` + 5 passed

- [ ] **Step 9: Commit**

```bash
git add scripts/camera/app.py tests/test_video_path.py
git commit -m "feat(camera): gallery scans photo+video dirs, supports .mp4"
```

---

## Task 8: 板端集成验证(固件路径,唯一有效的录像测试)

**Files:** 无代码改动——部署 hw/lcd.py + scripts/camera/app.py + scripts/camera/video_path.py 全部到板子。

- [ ] **Step 1: 部署并开机**

预期:正常开机→主菜单→进相机预览正常。串口无 pool/MediaManager/ImportError 报错。

- [ ] **Step 2: 录像往返**

切到视频模式,点快门开始录像。预期串口 `[Camera] recording started: /data/video/VID_*.mp4`。录 ~5 秒,顶部红字计时走动且**纯红无黑底**。再点快门停止。预期 `[Camera] recording stopped: /data/video/VID_*.mp4`(不是 `no frames`)。

- [ ] **Step 3: 图库验证**

进图库,预期能看到刚录的 VID_*.mp4(标签 MP4)与之前的照片(JPG),按时间倒序。无 `listdir failed`。

- [ ] **Step 4: 文件落盘 + 可播放验证**

把 /data/video/VID_*.mp4 拉到电脑用播放器打开。预期能播放、画面正常、时长≈录制时长。
- 若文件大小 0 或无法播放但串口显示 stopped ok → 检查是否 `got_first_i`(首 I 帧)逻辑;贴串口回报。
- 若开始录像即报内存/VB 失败 → 触发"内存风险"回退:hw/lcd.py 把 chn1 降到 `set_framesize(width=640, height=480, ...)` 或 320×240,重测拍照+录像。

- [ ] **Step 5: 回归——拍照仍正常**

切回拍照模式拍一张,预期 `[Camera] photo saved: /data/photo/IMG_*.jpg`,图库可见。确认新增 chn2 未破坏既有拍照路径。

- [ ] **Step 6: 全部通过后 Commit(若验证中有微调)**

```bash
git add -A
git commit -m "test(camera): board-verified recording to /data/video"
```

---

## Self-Review

**Spec 覆盖:**
- 真实录像写文件 → Task 4/5/6(encoder+tick+muxer 收尾)✅
- 参考 camer_mode.py 的 vencoder+mp4+chn2 → Task 1(chn2)+ Task 4/5(对齐 _record_begin/_record_tick)✅
- 视频存 /data/video、照片视频分开 → Task 3(VIDEO_DIR/PHOTO_DIR)+ Task 7(图库扫两目录)✅
- chn2 在 MediaManager.init() 前声明 → Task 1 ✅
- (顺带)图库 .avi→.mp4、tuple endswith 已在本会话先行修复,Task 7 测试固化 ✅

**占位符扫描:** 无 TBD/TODO;每个改代码的步骤均有完整代码。

**类型一致性:** `self._rec` dict 的键(encoder/venc_chn/mp4_handle/mp4_track/stream_data/frame_data/save_idr/idr_index/got_first_i/video_start_ts/link/path)在 Task 4 创建、Task 5/6 读取,键名一致 ✅。`_cleanup_recording(encoder, venc_chn, link, mp4_handle)` 签名在 Task 4 定义、Task 6 调用,参数顺序一致 ✅。`files` 三元组 `(name, fullpath, mtime)` 在 Task 7 Step5 产生、Step6 解包一致 ✅。`next_video_path(dir, prefix, ext, t, exists)` 签名 Task 2 定义,Task 4/7 调用一致 ✅。

**已知风险:** VB 池内存(已列回退);具名 import 符号缺失(已给星号导入回退)。
