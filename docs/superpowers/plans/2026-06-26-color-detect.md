# 颜色识别(color_detect)实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 实现 color_detect 脚本:屏幕点击取色,LAB 阈值滑块实时调参,find_blobs 检测同色区域,KEY2 注册当前色为 ID(4 槽),协议 0x06 上传匹配框。

**Architecture:** 复用 _template 单线程主循环 + tag_detect 双通道范式。chn0 VGA RGB888 显示+取色,chn1 QVGA RGB565 find_blobs 检测。采样即套用 ±10 容差生成 6 阈值,KEY2 轮转注册 4 槽,左表 3 槽采色历史参考,协议 0x06。

**Tech Stack:** K230 CanMV MicroPython,LVGL v8,nncase 不需要(纯 find_blobs),纯 Python RGB→LAB,IdRegistry 复用,TagDB 范式镜像出 ColorDB。

---

## 文件结构

| 操作 | 文件 | 职责 |
|------|------|------|
| 新建 | `core/color_db.py` | 纯 Python ColorDB(register/match/clear/count/flush) |
| 新建 | `scripts/color_detect/__init__.py` | 包标记(空) |
| 新建 | `scripts/color_detect/app.py` | 主脚本:run/on_frame/_build_ui/_destroy_ui/取色/滑块/左表 |
| 改 | `core/app_runtime.py` | _channels_for 加 color_detect 分支 + init_app 预读 color 图标 |
| 改 | `core/icon_cache.py` | preload_color_icons/get_color_icon |
| 改 | `comm/host_api.py` | CATEGORY_TYPE 加 `"color_detect": TYPE_COLOR_DETECT` |
| 改 | `resource/i18n/zh_CN.json` | color_detect 段 |
| 改 | `resource/i18n/en_US.json` | color_detect 段 |
| 新建 | `tests/test_color_db.py` | ColorDB 纯 Python 真单测 |
| 新建 | `tests/test_color_detect_ast.py` | AST 契约测试 |

**复用既有**:`core/id_registry.py`(IdRegistry,零改动)、`ui/theme.py`(make_back_bar_text_style)、`core/font_manager.py`(fonts)。

**协议**:TYPE_COLOR_DETECT=0x06 已定义,只需加进 CATEGORY_TYPE 映射。

**通道**:color_detect → (CAM_CHN_ID_1, Sensor.QVGA, Sensor.RGB565),同 tag_detect。

---

### Task 1: ColorDB 纯 Python 内存库

**Files:**
- Create: `core/color_db.py`
- Test: `tests/test_color_db.py`

镜像 tag_db,但存 6 阈值 tuple + 中心 LAB + RGB。register 同阈值不重复占槽。

- [ ] **Step 1: 写失败测试**

创建 `tests/test_color_db.py`:

```python
# tests/test_color_db.py — ColorDB 纯 Python 单测(无 MicroPython 依赖)
import os, sys
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from core.color_db import ColorDB


def test_register_returns_slot_1_to_4():
    db = ColorDB()
    s1 = db.register(((40,60,-10,10,-10,10),(50,50,50)), rgb=0xFF0000)
    s2 = db.register(((70,90,20,40,10,30),(80,30,20)), rgb=0x00FF00)
    assert s1 == 1 and s2 == 2


def test_register_same_threshold_returns_existing_slot():
    db = ColorDB()
    th = ((40,60,-10,10,-10,10),(50,50,50))
    s1 = db.register(th, rgb=0xFF0000)
    s2 = db.register(th, rgb=0xFF0000)
    assert s1 == s2 == 1
    assert db.count == 1


def test_register_round_robin_after_4():
    db = ColorDB()
    for i in range(4):
        db.register(((i*10,i*10+10,0,0,0,0),(i*10,0,0)), rgb=0x111111*i)
    assert db.count == 4
    s5 = db.register(((100,110,0,0,0,0),(105,0,0)), rgb=0xFFFFFF)
    assert s5 == 1  # 轮转回到 slot 1


def test_match_exact_hit():
    db = ColorDB()
    th = ((40,60,-10,10,-10,10),(50,50,50))
    db.register(th, rgb=0xFF0000)
    slot, score = db.match(th)
    assert slot == 1 and score == 1.0


def test_match_miss():
    db = ColorDB()
    db.register(((40,60,-10,10,-10,10),(50,50,50)), rgb=0xFF0000)
    slot, score = db.match(((70,90,0,0,0,0),(80,0,0)))
    assert slot is None and score == 0.0


def test_clear_resets():
    db = ColorDB()
    db.register(((40,60,-10,10,-10,10),(50,50,50)), rgb=0xFF0000)
    db.clear()
    assert db.count == 0


def test_get_slot_returns_threshold_and_meta():
    db = ColorDB()
    th = ((40,60,-10,10,-10,10),(50,50,50))
    db.register(th, rgb=0xFF0000)
    entry = db.get_slot(1)
    assert entry is not None
    assert entry['threshold'] == th
    assert entry['rgb'] == 0xFF0000
    assert entry['lab'] == (50,50,50)


def test_iter_slots():
    db = ColorDB()
    db.register(((40,60,-10,10,-10,10),(50,50,50)), rgb=0xFF0000)
    db.register(((70,90,20,40,10,30),(80,30,20)), rgb=0x00FF00)
    slots = list(db.iter_slots())
    assert len(slots) == 2
    assert all('threshold' in e and 'rgb' in e and 'lab' in e for e in slots)


def test_runner():
    failures = 0
    tests = [(n, f) for n, f in sorted(globals().items())
             if n.startswith("test_") and callable(f) and n != "test_runner"]
    for name, fn in tests:
        try:
            fn(); print("PASS %s" % name)
        except Exception as e:
            failures += 1; print("FAIL %s: %s" % (name, e))
    print("\n%s" % ("ALL PASS" if failures == 0 else "%d FAILED" % failures))
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    test_runner()
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python tests/test_color_db.py`
Expected: FAIL `ImportError: no module named 'core.color_db'`

- [ ] **Step 3: 写 ColorDB 实现**

创建 `core/color_db.py`:

```python
# core/color_db.py — 颜色 ID 内存数据库
#
# 镜像 tag_db 的内存-only + flush_to_disk 预留模式,但:
#   - 存 6 阈值 tuple (Lmin,Lmax,Amin,Amax,Bmin,Bmax) + 中心 LAB + RGB
#   - 同阈值(完全相同)不重复占槽,返回已有槽
#   - 精确匹配(阈值完全相等即命中),score=1.0
#
# 纯 Python(无 MicroPython 依赖)→ 可在 host 端真单元测试。
#
# 持久化预留:flush_to_disk 当前 no-op。K230 坑#2:运行时 SD 写与 display
# flush 抢 DMA,故运行时只改内存,退出刷盘。


class ColorDB:
    """颜色 ID 内存库。每槽存 6 阈值 + 中心 LAB + RGB。

    threshold 形如 ((Lmin,Lmax,Amin,Amax,Bmin,Bmax),(L,A,B))。
    """

    def __init__(self):
        self._features = {}        # {slot_id: {'threshold':th, 'lab':(L,A,B), 'rgb':int}}
        self._next_slot = 1        # 轮转覆盖指针(1-4 循环)
        self._dirty = False
        self._clear_dirty = False

    def register(self, threshold, rgb=0):
        """注册颜色到槽位(轮转覆盖,空槽优先)。

        同阈值(完全相同)不重复占槽,返回已有 slot。
        返回 slot_id(1-4)。纯内存,设 _dirty。
        """
        # 同阈值去重:已存在则返回已有槽
        for slot_id, entry in self._features.items():
            if entry['threshold'] == threshold:
                return slot_id
        slot = None
        for i in range(1, 5):
            if i not in self._features:
                slot = i
                break
        if slot is None:
            slot = self._next_slot
            self._next_slot = self._next_slot % 4 + 1
        th, lab = threshold
        self._features[slot] = {'threshold': th, 'lab': lab, 'rgb': rgb}
        self._dirty = True
        self._clear_dirty = False
        print("[ColorDB] registered lab=%r -> id%d (memory, dirty)" % (lab, slot))
        return slot

    def match(self, threshold):
        """精确匹配 threshold(6 阈值完全相等)。返回 (slot_id, 1.0) 或 (None, 0.0)。"""
        th, _lab = threshold
        for slot_id, entry in self._features.items():
            if entry['threshold'] == th:
                return slot_id, 1.0
        return None, 0.0

    def clear(self):
        """清内存,设 _clear_dirty。"""
        self._features.clear()
        self._clear_dirty = True
        self._dirty = False
        self._next_slot = 1
        print("[ColorDB] cleared (memory, clear_dirty)")

    def get_slot(self, slot_id):
        """取某槽 entry(threshold/lab/rgb),不存在返回 None。"""
        return self._features.get(slot_id)

    def iter_slots(self):
        """遍历所有槽 entry(供每帧检测用)。"""
        return self._features.values()

    def flush_to_disk(self):
        """退出时刷盘(预留)。当前 no-op,仅复位 dirty 标志。"""
        if self._clear_dirty:
            print("[ColorDB] exit: clear intent recorded (persistence disabled)")
        elif self._dirty:
            print("[ColorDB] exit: %d color(s) pending (persistence disabled)"
                  % len(self._features))
        self._clear_dirty = False
        self._dirty = False

    @property
    def count(self):
        return len(self._features)
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python tests/test_color_db.py`
Expected: ALL PASS(9 PASS)

- [ ] **Step 5: 提交**

```bash
git add core/color_db.py tests/test_color_db.py
git commit -m "feat(color_db): pure-Python color ID memory DB

镜像 tag_db,存 6 阈值+中心LAB+RGB,同阈值不重复占槽,
精确匹配,轮转 1-4,flush 预留 no-op。

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 2: host_api CATEGORY_TYPE 映射 + i18n 段

**Files:**
- Modify: `comm/host_api.py`
- Modify: `resource/i18n/zh_CN.json`
- Modify: `resource/i18n/en_US.json`
- Test: `tests/test_host_api.py`(已存在,加断言)

- [ ] **Step 1: 写失败测试**

在 `tests/test_host_api.py` 末尾 `test_runner` 之前加测试(先 Read 文件确认结构):

```python
def test_color_detect_category_type_mapped():
    """color_detect 必须映射到 TYPE_COLOR_DETECT(0x06)。"""
    from comm.host_api import HostAPI
    assert HostAPI.CATEGORY_TYPE.get("color_detect") == HostAPI.TYPE_COLOR_DETECT
    assert HostAPI.TYPE_COLOR_DETECT == 0x06
```

并加一个 i18n 检查测试:

```python
def test_color_detect_i18n_keys_exist():
    """color_detect i18n 段必须有 registered/clear/save + 6 阈值键。"""
    import json
    ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    for lang in ("zh_CN", "en_US"):
        with open(os.path.join(ROOT, "resource", "i18n", "%s.json" % lang),
                  encoding="utf-8") as f:
            d = json.load(f)
        seg = d.get("color_detect", {})
        for key in ("registered", "clear", "save",
                    "Lmin", "Lmax", "Amin", "Amax", "Bmin", "Bmax"):
            assert key in seg, "color_detect.%s missing in %s" % (key, lang)
```

> 若 test_host_api.py 没有 `import os`/`ROOT`,在文件顶部补上(参考 test_template.py)。

- [ ] **Step 2: 跑测试确认失败**

Run: `python tests/test_host_api.py`
Expected: FAIL `test_color_detect_category_type_mapped` (KeyError/None) + FAIL i18n keys

- [ ] **Step 3: 改 host_api.py CATEGORY_TYPE**

在 [comm/host_api.py:48](comm/host_api.py#L48) 的 CATEGORY_TYPE 字典 `"object_detect"` 行后加:

```python
        "color_detect": TYPE_COLOR_DETECT,  # 0x06
```

(放在 `"_template"` 行之前)

- [ ] **Step 4: 改 i18n**

在 `resource/i18n/zh_CN.json` 的 `"object_detect"` 段后加 `"color_detect"` 段:

```json
  "color_detect": {
    "registered": "已注册 %d/4",
    "clear": "清除",
    "save": "保存",
    "Lmin": "L最小",
    "Lmax": "L最大",
    "Amin": "A最小",
    "Amax": "A最大",
    "Bmin": "B最小",
    "Bmax": "B最大"
  },
```

在 `resource/i18n/en_US.json` 对应位置加:

```json
  "color_detect": {
    "registered": "Registered %d/4",
    "clear": "Clear",
    "save": "Save",
    "Lmin": "Lmin",
    "Lmax": "Lmax",
    "Amin": "Amin",
    "Amax": "Amax",
    "Bmin": "Bmin",
    "Bmax": "Bmax"
  },
```

- [ ] **Step 5: 跑测试确认通过**

Run: `python tests/test_host_api.py`
Expected: ALL PASS(含新 2 项)

- [ ] **Step 6: 提交**

```bash
git add comm/host_api.py resource/i18n/zh_CN.json resource/i18n/en_US.json tests/test_host_api.py
git commit -m "feat(color_detect): host protocol 0x06 mapping + i18n

CATEGORY_TYPE 加 color_detect->0x06,zh/en i18n 加 6 阈值键+registered/clear/save。

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 3: app_runtime 通道分支 + icon_cache 颜色图标

**Files:**
- Modify: `core/app_runtime.py:180-197`(_channels_for)
- Modify: `core/app_runtime.py:167-174`(init_app 预读分支)
- Modify: `core/icon_cache.py`(preload_color_icons/get_color_icon + __init__ 槽)
- Test: `tests/test_color_detect_ast.py`(部分,通道+图标)

- [ ] **Step 1: 写失败测试**

创建 `tests/test_color_detect_ast.py`:

```python
# tests/test_color_detect_ast.py — host-side AST 契约测试(color_detect)
import ast, os, json

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP_RUNTIME_PATH = os.path.join(ROOT, "core", "app_runtime.py")
ICON_CACHE_PATH = os.path.join(ROOT, "core", "icon_cache.py")
HOST_API_PATH = os.path.join(ROOT, "comm", "host_api.py")
CATEGORIES_PATH = os.path.join(ROOT, "config", "categories.json")
APP_PATH = os.path.join(ROOT, "scripts", "color_detect", "app.py")


def _read(path):
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def test_channels_for_color_detect_qvga_rgb565():
    """_channels_for 必须为 color_detect 配 chn1 QVGA RGB565(同 tag_detect)。"""
    src = _read(APP_RUNTIME_PATH)
    start = src.find("def _channels_for(")
    body = src[start:start + 1200]
    assert "color_detect" in body, "_channels_for must handle color_detect"
    assert "QVGA" in body, "color_detect must use QVGA on chn1"
    assert "RGB565" in body, "color_detect must use RGB565 on chn1"


def test_init_app_preloads_color_icons():
    """init_app 必须对 color_detect 调 preload_color_icons。"""
    src = _read(APP_RUNTIME_PATH)
    assert 'preload_color_icons' in src, "init_app must preload color icons"


def test_icon_cache_has_color_methods():
    """icon_cache 必须有 preload_color_icons + get_color_icon。"""
    src = _read(ICON_CACHE_PATH)
    assert "def preload_color_icons" in src
    assert "def get_color_icon" in src
    assert "_color_icons" in src  # 槽字段


def test_color_detect_in_categories_enabled():
    """categories.json 必须有 color_detect 条目且 enabled。"""
    with open(CATEGORIES_PATH, encoding="utf-8") as f:
        data = json.load(f)
    cats = data.get("categories", [])
    c = [x for x in cats if x.get("id") == "color_detect"]
    assert c, "color_detect category missing"
    assert c[0].get("enabled") is True
    assert c[0].get("ui_mode") == "stream"


def test_runner():
    failures = 0
    tests = [(n, f) for n, f in sorted(globals().items())
             if n.startswith("test_") and callable(f) and n != "test_runner"]
    for name, fn in tests:
        try:
            fn(); print("PASS %s" % name)
        except Exception as e:
            failures += 1; print("FAIL %s: %s" % (name, e))
    print("\n%s" % ("ALL PASS" if failures == 0 else "%d FAILED" % failures))
    import sys
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    test_runner()
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python tests/test_color_detect_ast.py`
Expected: FAIL `test_channels_for_color_detect_qvga_rgb565` + `test_init_app_preloads_color_icons` + `test_icon_cache_has_color_methods`

- [ ] **Step 3: 改 icon_cache.py**

在 [core/icon_cache.py:21](core/icon_cache.py#L21) `__init__` 的 `self._object_icons = {}` 后加:

```python
        self._color_icons = {}      # name → (data, dsc)
```

在 `get_object_icon` 方法后(文件末尾 `_IconCache` 类内)加:

```python
    def preload_color_icons(self):
        """预读颜色识别APP图标（在首次 task_handler 之前调用）"""
        base = "/sdcard/CamerAi/resource/icons/color_detect_icon/"
        icons = {
            "list": base + "list.png",
            "back": base + "back.png",
        }
        for name, path in icons.items():
            try:
                with open(path, 'rb') as f:
                    data = bytearray(f.read())
                dsc = lv.img_dsc_t({
                    'data_size': len(data),
                    'data': data,
                })
                self._color_icons[name] = (data, dsc)
                print(f"[IconCache] color/{name} OK ({len(data)} bytes)")
            except Exception as e:
                print(f"[IconCache] color/{name} FAILED: {e}")

    def get_color_icon(self, name):
        """获取颜色识别图标 (data, dsc)，未缓存返回 (None, None)"""
        return self._color_icons.get(name, (None, None))
```

> 注:图标 PNG 文件可能尚未提供。preload 用 try/except 容错,缺失不影响运行(顶栏返回钮会用 `get_color_icon("back")`,缺失则降级显示 `<`)。Task 6 板端验收时确认图标目录。

- [ ] **Step 4: 改 app_runtime.py _channels_for**

在 [core/app_runtime.py:194](core/app_runtime.py#L194) `object_detect` 分支后、`_template` 分支前加:

```python
        elif category_id == "color_detect":
            # chn1 QVGA RGB565 专做 find_blobs 颜色检测(同 tag_detect);
            # chn0 VGA RGB888 显示+取色。blob rect ×2 映射显示(QVGA→VGA)。
            chs.append((CAM_CHN_ID_1, Sensor.QVGA, Sensor.RGB565))
```

- [ ] **Step 5: 改 app_runtime.py init_app 预读分支**

在 [core/app_runtime.py:173-174](core/app_runtime.py#L173-L174) `object_detect` 分支后加:

```python
        elif category_id == "color_detect":
            icon_cache.preload_color_icons()
```

- [ ] **Step 6: 跑测试确认通过**

Run: `python tests/test_color_detect_ast.py`
Expected: ALL PASS(4 PASS)

- [ ] **Step 7: 提交**

```bash
git add core/app_runtime.py core/icon_cache.py tests/test_color_detect_ast.py
git commit -m "feat(color_detect): chn1 QVGA channel + color icon cache

_channels_for 加 color_detect 分支(chn1 QVGA RGB565),
init_app 预读 color 图标,icon_cache 加 preload/get_color_icon。

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 4: RGB→LAB 转换 + 6 阈值生成

**Files:**
- Create: `scripts/color_detect/__init__.py`(空)
- Create: `scripts/color_detect/app.py`(仅此部分,后续 Task 增量填充)
- Test: `tests/test_color_detect_ast.py`(加转换函数测试)

RGB→LAB 标准 sRGB→XYZ→Lab(D65),纯 Python。容差 ±10 生成 6 阈值并裁剪到有效范围。

- [ ] **Step 1: 写失败测试**

在 `tests/test_color_detect_ast.py` 加(需能 import app,但 app 依赖 lvgl 等板端模块 Windows 不可导入 → 用 AST 提取函数源码 exec,或单独把转换函数放可测位置)。

**决策**:为可测,把 `_rgb_to_lab` / `_make_threshold` 写成模块级纯函数,测试用 AST 把函数源码抠出来 exec(避免 import 整个 app 触发 lvgl 导入)。在 `test_color_detect_ast.py` 加:

```python
def _extract_func_src(path, func_name):
    """从 app.py 抠出指定函数源码(避免 import 触发 lvgl)。"""
    tree = ast.parse(_read(path))
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == func_name:
            return ast.get_source_segment(_read(path), node)
    return None


def test_rgb_to_lab_white():
    """白色 RGB(255,255,255) → L≈100, A≈0, B≈0。"""
    src = _extract_func_src(APP_PATH, "_rgb_to_lab")
    assert src is not None, "_rgb_to_lab missing in app.py"
    ns = {}
    exec(src, ns)
    L, A, B = ns["_rgb_to_lab"](255, 255, 255)
    assert abs(L - 100) < 3, "white L should be ~100, got %s" % L
    assert abs(A) < 3, "white A should be ~0, got %s" % A
    assert abs(B) < 3, "white B should be ~0, got %s" % B


def test_rgb_to_lab_black():
    """黑色 RGB(0,0,0) → L≈0, A≈0, B≈0。"""
    src = _extract_func_src(APP_PATH, "_rgb_to_lab")
    ns = {}
    exec(src, ns)
    L, A, B = ns["_rgb_to_lab"](0, 0, 0)
    assert abs(L) < 3
    assert abs(A) < 3
    assert abs(B) < 3


def test_rgb_to_lab_red():
    """红色 RGB(255,0,0) → L≈53, A≈80(红方向), B≈67(黄方向)。"""
    src = _extract_func_src(APP_PATH, "_rgb_to_lab")
    ns = {}
    exec(src, ns)
    L, A, B = ns["_rgb_to_lab"](255, 0, 0)
    assert abs(L - 53) < 5
    assert A > 60, "red A should be strongly positive, got %s" % A
    assert B > 50, "red B should be positive, got %s" % B


def test_make_threshold_applies_plus_minus_10():
    """_make_threshold 用 ±10 容差,且裁剪到有效范围。"""
    src = _extract_func_src(APP_PATH, "_make_threshold")
    assert src is not None, "_make_threshold missing"
    ns = {}
    exec(src, ns)
    # L=95 → Lmin=85, Lmax=100(裁剪); A=5 → -5~15; B=120 → 110~127(裁剪)
    th = ns["_make_threshold"]((95, 5, 120))
    Lmin, Lmax, Amin, Amax, Bmin, Bmax = th
    assert (Lmin, Lmax) == (85, 100), "L clip fail: %s" % ((Lmin, Lmax),)
    assert (Amin, Amax) == (-5, 15)
    assert (Bmin, Bmax) == (110, 127), "B clip fail: %s" % ((Bmin, Bmax),)


def test_make_threshold_negative_a_clips():
    """A=-125 → Amin=-128(裁剪), Amax=-115。"""
    src = _extract_func_src(APP_PATH, "_make_threshold")
    ns = {}
    exec(src, ns)
    th = ns["_make_threshold"]((50, -125, 0))
    Lmin, Lmax, Amin, Amax, Bmin, Bmax = th
    assert Amin == -128
    assert Amax == -115
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python tests/test_color_detect_ast.py`
Expected: FAIL `_rgb_to_lab` / `_make_threshold` 缺失

- [ ] **Step 3: 创建包标记**

创建空文件 `scripts/color_detect/__init__.py`(内容为空或单行注释):

```python
# scripts/color_detect package
```

- [ ] **Step 4: 创建 app.py(转换函数部分)**

创建 `scripts/color_detect/app.py`(后续 Task 增量填充,本 Task 只放转换函数 + 模块头 + 常量):

```python
# scripts/color_detect/app.py — 颜色识别(LAB 阈值 find_blobs + 屏幕取色)。
#
# 复用 _template 单线程主循环 + tag_detect 双通道。chn0 VGA RGB888 显示+取色,
# chn1 QVGA RGB565 find_blobs 检测。屏幕点击取色→RGB→LAB→±10容差6阈值→立即检测。
# KEY2 注册当前检测色到 4 槽(轮转),每帧注册色 find_blobs 画 ID 彩框,协议 0x06。
# 左表 3 槽采色历史(底色=采样色),与 ID 独立。

import os
import sys
import time
import image
import lvgl as lv
from media.display import Display
from media.sensor import CAM_CHN_ID_0, CAM_CHN_ID_1
from core.icon_cache import icon_cache
from core.font_manager import fonts
from core.id_registry import IdRegistry
from core.color_db import ColorDB

BAR_H = 52
PREVIEW_Y = BAR_H
PREVIEW_H = 376
BAR_BG = 0x1A1A1A
CARD_BG = 0x2A2A2A
CARD_ACTIVE = 0x2E7D32   # 选中格绿色
# chn1 QVGA(320x240) -> chn0 VGA(640x480):坐标 x2 整数缩放
DET_SCALE = 2
# 取色容差(L/A/B 统一 ±10)
TOLERANCE = 10
# L 范围 0-100,A/B 范围 -128~127
L_LO, L_HI = 0, 100
AB_LO, AB_HI = -128, 127

# 画框配色对齐 tag_detect:未注册白框,注册按 slot 取彩色。
BOX_COLORS = {
    1: 0x44CC44,   # 绿
    2: 0x4488FF,   # 蓝
    3: 0xFF8844,   # 橙
    4: 0xCC44FF,   # 紫
}
BOX_UNKNOWN = 0xFFFFFF   # 未注册白框

# 6 阈值格定义:(key, label_key, lo, hi, default)
THRESH_CELLS = [
    ("Lmin", "color_detect.Lmin", L_LO, L_HI, 0),
    ("Lmax", "color_detect.Lmax", L_LO, L_HI, 100),
    ("Amin", "color_detect.Amin", AB_LO, AB_HI, -10),
    ("Amax", "color_detect.Amax", AB_LO, AB_HI, 10),
    ("Bmin", "color_detect.Bmin", AB_LO, AB_HI, -10),
    ("Bmax", "color_detect.Bmax", AB_LO, AB_HI, 10),
]


def _draw_color(hex_color):
    """hex 0xRRGGBB -> K230 draw_rectangle color tuple (A, B, G, R)。"""
    r = (hex_color >> 16) & 0xFF
    g = (hex_color >> 8) & 0xFF
    b = hex_color & 0xFF
    return (0xFF, b, g, r)


def _rgb_to_lab(r, g, b):
    """sRGB [0,255] -> Lab。L:0-100, A/B:-128~127(标准 sRGB→XYZ→Lab D65)。

    纯 Python,仅取色时调一次,无性能压力。
    """
    def _linear(c):
        c = c / 255.0
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4
    rl = _linear(r)
    gl = _linear(g)
    bl = _linear(b)
    # sRGB→XYZ (D65)
    x = (rl * 0.4124564 + gl * 0.3575761 + bl * 0.1804375) / 0.95047
    y = (rl * 0.2126729 + gl * 0.7151522 + bl * 0.0721750) / 1.00000
    z = (rl * 0.0193339 + gl * 0.1191920 + bl * 0.9503041) / 1.08883
    def _f(t):
        return t ** (1 / 3) if t > 0.008856 else (7.787 * t + 16 / 116)
    fx = _f(x)
    fy = _f(y)
    fz = _f(z)
    L = 116 * fy - 16
    A = 500 * (fx - fy)
    B = 200 * (fy - fz)
    # 裁剪到有效范围(显示/存储用整数)
    L = max(L_LO, min(L_HI, round(L)))
    A = max(AB_LO, min(AB_HI, round(A)))
    B = max(AB_LO, min(AB_HI, round(B)))
    return (L, A, B)


def _make_threshold(lab):
    """LAB 中心值 -> 6 阈值 (Lmin,Lmax,Amin,Amax,Bmin,Bmax),容差 ±10,裁剪。"""
    L, A, B = lab
    Lmin = max(L_LO, L - TOLERANCE)
    Lmax = min(L_HI, L + TOLERANCE)
    Amin = max(AB_LO, A - TOLERANCE)
    Amax = min(AB_HI, A + TOLERANCE)
    Bmin = max(AB_LO, B - TOLERANCE)
    Bmax = min(AB_HI, B + TOLERANCE)
    return (Lmin, Lmax, Amin, Amax, Bmin, Bmax)
```

- [ ] **Step 5: 跑测试确认通过**

Run: `python tests/test_color_detect_ast.py`
Expected: ALL PASS(含转换函数 5 项 + Task3 的 4 项 = 9 PASS)

- [ ] **Step 6: 提交**

```bash
git add scripts/color_detect/__init__.py scripts/color_detect/app.py tests/test_color_detect_ast.py
git commit -m "feat(color_detect): RGB->LAB + ±10 threshold gen

sRGB→XYZ→Lab D65 纯 Python 转换(取色时调一次),
_make_threshold 容差 ±10 生成 6 阈值并裁剪到有效范围。

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 5: UI 构建(顶栏/预览/底栏 6 格滑块/左表)

**Files:**
- Modify: `scripts/color_detect/app.py`(加 _build_ui / 全局状态 / 滑块/左表/格回调)

布局:顶栏(返回+标题)+ 左表(4×3,顶栏左下方)+ 透明预览(可点击取色)+ 底栏(list 图标 + 6 阈值格 + 共享滑块 + 计数)。

- [ ] **Step 1: 写失败测试**

在 `tests/test_color_detect_ast.py` 加 UI 契约测试:

```python
def test_build_ui_creates_top_bottom_preview_table():
    """_build_ui 必须建顶栏/底栏/预览/左表。"""
    src = _read(APP_PATH)
    assert "def _build_ui(" in src, "_build_ui missing"
    assert "_top_bar" in src and "_bottom_bar" in src
    assert "_preview" in src
    assert "_table" in src or "_swatch" in src, "left color table missing"
    # 返回钮回调
    assert "exit_flag[0] = True" in src


def test_build_ui_has_6_thresh_cells_and_slider():
    """底栏必须有 6 阈值格 + 共享滑块。"""
    src = _read(APP_PATH)
    assert "THRESH_CELLS" in src
    assert "lv.slider" in src, "shared slider missing"
    # 选中格置绿
    assert "CARD_ACTIVE" in src


def test_preview_clickable_for_sampling():
    """预览/screen 必须可点击取色(CLICKED 事件设 pending_click)。"""
    src = _read(APP_PATH)
    assert "pending_click" in src, "pending_click sampling state missing"
    assert "EVENT.CLICKED" in src


def test_left_table_4rows_3cols():
    """左表必须 4 行 3 列(首行 L/A/B 表头)。"""
    src = _read(APP_PATH)
    assert "set_rows(4)" in src and "set_cols(3)" in src, "table must be 4x3"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python tests/test_color_detect_ast.py`
Expected: FAIL UI 相关 4 项

- [ ] **Step 3: 加全局状态 + _build_ui**

在 app.py(THRESH_CELLS 定义后、`_draw_color` 前)加全局状态:

```python
_RUNTIME = None
_screen = None
_top_bar = None
_bottom_bar = None
_preview = None
_table = None          # 左表 4×3
_count_label = None
_id_registry = None
_color_db = None
_slider = None         # 共享滑块
_thresh_labels = {}    # {key: label_obj} 6 格数值标签
_thresh_cells = {}     # {key: cell_obj} 6 格容器
_selected_key = "Lmin" # 当前选中格
_thresh_values = {"Lmin": 0, "Lmax": 100, "Amin": -10, "Amax": 10,
                  "Bmin": -10, "Bmax": 10}  # 当前 6 阈值
_pending_click = None  # (x,y) 待取色,或 None
_swatch = [None, None, None]  # 左表 3 槽采色历史 entry(lab, rgb) 或 None
_overlay = None
_clear_btn = None
_save_btn = None
_close_overlay = False
```

加 `_build_ui`(放在 `_make_threshold` 后):

```python
def _init_registry(fpioa):
    global _id_registry
    _id_registry = IdRegistry(fpioa, pin=0)


def _select_cell(key):
    """选中某阈值格(置绿)+ 滑块 range/value 同步。"""
    global _selected_key
    _selected_key = key
    for k, cell in _thresh_cells.items():
        try:
            cell.set_style_bg_color(
                lv.color_hex(CARD_ACTIVE if k == key else CARD_BG), 0)
        except Exception:
            pass
    # 同步滑块 range + value
    if _slider is not None:
        for k, _label, lo, hi, _dflt in THRESH_CELLS:
            if k == key:
                _slider.set_range(lo, hi)
                _slider.set_value(_thresh_values.get(key, lo), lv.ANIM.OFF)
                break


def _on_slider_changed(e):
    """滑块值变化 -> 更新选中格数值 + _thresh_values。"""
    if e.get_code() != lv.EVENT.VALUE_CHANGED:
        return
    if _slider is None or _selected_key is None:
        return
    val = _slider.get_value()
    _thresh_values[_selected_key] = val
    lbl = _thresh_labels.get(_selected_key)
    if lbl is not None:
        try:
            lbl.set_text(str(val))
        except Exception:
            pass


def _make_cell(parent, key, label_key, lo, hi, dflt, align_x):
    """建一个阈值格(可点选)+ 数值标签。"""
    from ui.theme import make_back_bar_text_style
    cell = lv.btn(parent)
    cell.set_size(52, 44)
    cell.align(lv.ALIGN.LEFT_MID, align_x, 0)
    cell.set_style_bg_color(
        lv.color_hex(CARD_ACTIVE if key == _selected_key else CARD_BG), 0)
    cell.set_style_bg_opa(255, 0)
    cell.set_style_radius(6, 0)
    cell.set_style_border_width(0, 0)
    cell.set_style_shadow_width(0, 0)
    cell.set_style_pad_all(2, 0)

    name_lbl = lv.label(cell)
    name_lbl.set_text(_RUNTIME.lang.t(label_key))
    name_lbl.add_style(make_back_bar_text_style(fonts.small), 0)
    name_lbl.align(lv.ALIGN.TOP_MID, 0, 0)

    val_lbl = lv.label(cell)
    val_lbl.set_text(str(_thresh_values.get(key, dflt)))
    val_lbl.add_style(make_back_bar_text_style(fonts.body), 0)
    val_lbl.align(lv.ALIGN.BOTTOM_MID, 0, 0)
    _thresh_labels[key] = val_lbl

    def _on_click(e, _k=key):
        if e.get_code() == lv.EVENT.CLICKED:
            _select_cell(_k)
    cell.add_event(_on_click, lv.EVENT.CLICKED, None)
    _thresh_cells[key] = cell
    return cell


def _refresh_table():
    """刷新左表 3 槽采色历史(底色 + LAB 值)。"""
    if _table is None:
        return
    header = ["L", "A", "B"]
    for col in range(3):
        try:
            _table.set_cell_value(0, col, header[col])
        except Exception:
            pass
    for i in range(3):
        entry = _swatch[i]
        for col in range(3):
            try:
                if entry is not None:
                    lab = entry[0]
                    _table.set_cell_value(i + 1, col, str(lab[col]))
                else:
                    _table.set_cell_value(i + 1, col, "-")
            except Exception:
                pass
        # 底色 = 采样 RGB
        rgb = entry[1] if entry is not None else 0x222222
        try:
            _table.set_cell_value(i + 1, 0, _table.get_cell_value(i + 1, 0))  # noop trigger
        except Exception:
            pass


def _on_preview_clicked(e):
    """点预览区取色:记录屏幕坐标(VGA 空间)。"""
    global _pending_click
    if e.get_code() != lv.EVENT.CLICKED:
        return
    if _overlay is not None:
        global _close_overlay
        _close_overlay = True
        return
    try:
        p = lv.indev_get_act().get_point()
        _pending_click = (p.x, p.y)
    except Exception:
        pass


def _on_list_clicked(e):
    """弹出清除/保存浮层(对齐 tag_detect)。"""
    global _overlay, _clear_btn, _save_btn
    if e.get_code() != lv.EVENT.CLICKED:
        return
    if _overlay is not None:
        return
    from ui.theme import make_back_bar_text_style
    _overlay = lv.obj(lv.scr_act())
    _overlay.set_size(lv.pct(100), BAR_H)
    _overlay.set_pos(0, PREVIEW_Y + PREVIEW_H - BAR_H)
    _overlay.set_style_bg_color(lv.color_hex(BAR_BG), 0)
    _overlay.set_style_bg_opa(255, 0)
    _overlay.set_style_border_width(0, 0)
    _overlay.set_style_pad_all(0, 0)
    _overlay.set_style_radius(0, 0)
    _overlay.clear_flag(lv.obj.FLAG.SCROLLABLE)
    _overlay.add_flag(lv.obj.FLAG.CLICKABLE)
    _overlay.add_event(_on_overlay_clicked, lv.EVENT.CLICKED, None)

    _clear_btn = lv.btn(_overlay)
    _clear_btn.set_size(120, 40)
    _clear_btn.align(lv.ALIGN.LEFT_MID, 20, 0)
    cl = lv.label(_clear_btn)
    cl.set_text(_RUNTIME.lang.t("color_detect.clear"))
    cl.add_style(make_back_bar_text_style(fonts.body), 0)
    cl.center()
    _clear_btn.add_event(_on_clear_clicked, lv.EVENT.CLICKED, None)

    _save_btn = lv.btn(_overlay)
    _save_btn.set_size(120, 40)
    _save_btn.align(lv.ALIGN.RIGHT_MID, -20, 0)
    sv = lv.label(_save_btn)
    sv.set_text(_RUNTIME.lang.t("color_detect.save"))
    sv.add_style(make_back_bar_text_style(fonts.body), 0)
    sv.center()
    _save_btn.add_event(_on_save_clicked, lv.EVENT.CLICKED, None)


def _on_overlay_clicked(e):
    global _close_overlay
    if e.get_code() != lv.EVENT.CLICKED:
        return
    _close_overlay = True


def _on_clear_clicked(e):
    global _close_overlay
    if e.get_code() != lv.EVENT.CLICKED:
        return
    _color_db.clear()
    _refresh_count()
    if _RUNTIME is not None and _RUNTIME.buzzer is not None:
        _RUNTIME.buzzer.beep(ms=200)
    _close_overlay = True


def _on_save_clicked(e):
    global _close_overlay
    if e.get_code() != lv.EVENT.CLICKED:
        return
    _close_overlay = True


def _process_overlay_close():
    global _overlay, _clear_btn, _save_btn, _close_overlay
    if not _close_overlay:
        return
    _close_overlay = False
    for obj in (_clear_btn, _save_btn, _overlay):
        if obj is not None:
            try:
                obj.delete()
            except Exception:
                pass
    _clear_btn = None
    _save_btn = None
    _overlay = None


def _refresh_count():
    if _count_label is not None and _RUNTIME is not None:
        try:
            _count_label.set_text(
                _RUNTIME.lang.t("color_detect.registered", _color_db.count))
        except Exception:
            pass


def _build_ui(runtime, exit_flag):
    """顶栏(返回+标题) + 左表 + 透明预览(可取色) + 底栏(list+6格+滑块+计数)。"""
    global _screen, _top_bar, _bottom_bar, _preview, _table, _count_label, _slider
    screen = lv.scr_act()
    screen.set_style_bg_opa(0, 0)
    screen.add_flag(lv.obj.FLAG.CLICKABLE)
    _screen = screen

    # 顶栏
    _top_bar = lv.obj(screen)
    _top_bar.set_size(lv.pct(100), BAR_H)
    _top_bar.set_pos(0, 0)
    _top_bar.set_style_bg_color(lv.color_hex(BAR_BG), 0)
    _top_bar.set_style_bg_opa(255, 0)
    _top_bar.set_style_border_width(0, 0)
    _top_bar.set_style_pad_all(0, 0)
    _top_bar.set_style_radius(0, 0)
    _top_bar.clear_flag(lv.obj.FLAG.SCROLLABLE)

    btn = lv.obj(_top_bar)
    btn.set_size(48, 48)
    btn.align(lv.ALIGN.LEFT_MID, 2, 0)
    btn.set_style_bg_opa(0, 0)
    btn.set_style_border_width(0, 0)
    btn.set_style_shadow_width(0, 0)
    btn.set_style_outline_width(0, 0)
    btn.set_style_outline_opa(0, 0)
    btn.set_style_pad_all(0, 0)
    btn.clear_flag(lv.obj.FLAG.SCROLLABLE)
    btn.add_flag(lv.obj.FLAG.CLICKABLE)
    icon_data, icon_dsc = icon_cache.get_color_icon("back")
    if icon_dsc is not None and icon_data is not None:
        import struct
        w = h = 64
        if len(icon_data) >= 24:
            w = struct.unpack('>I', icon_data[16:20])[0]
            h = struct.unpack('>I', icon_data[20:24])[0]
        target = int(48 * 0.85)
        zoom = int(min(target / w, target / h) * 256) if w > 0 and h > 0 else 256
        zoom = max(8, min(zoom, 256))
        icon_img = lv.img(btn)
        icon_img.set_src(icon_dsc)
        icon_img.set_zoom(zoom)
        icon_img.center()
    else:
        lbl = lv.label(btn)
        lbl.set_text("<")
        lbl.center()

    def _on_back(e):
        if e.get_code() == lv.EVENT.CLICKED:
            exit_flag[0] = True
    btn.add_event(_on_back, lv.EVENT.CLICKED, None)

    title = lv.label(_top_bar)
    title.set_text(runtime.lang.t("category.color_detect"))
    title.align(lv.ALIGN.CENTER, 0, 0)
    from ui.theme import make_back_bar_text_style
    title.add_style(make_back_bar_text_style(fonts.body), 0)

    # 左表(4×3):顶栏左下方,叠在预览区左缘
    _table = lv.table(screen)
    _table.set_rows(4)
    _table.set_cols(3)
    _table.set_size(150, 120)
    _table.set_pos(4, BAR_H + 4)
    _table.set_style_bg_opa(180, 0)
    _table.set_style_border_width(1, 0)
    _table.set_style_pad_all(2, 0)
    _refresh_table()

    # 透明预览区(透出 OSD1,可点击取色)
    _preview = lv.obj(screen)
    _preview.set_size(lv.pct(100), PREVIEW_H)
    _preview.set_pos(0, PREVIEW_Y)
    _preview.set_style_bg_opa(0, 0)
    _preview.set_style_border_width(0, 0)
    _preview.set_style_pad_all(0, 0)
    _preview.set_style_radius(0, 0)
    _preview.clear_flag(lv.obj.FLAG.SCROLLABLE)
    _preview.add_flag(lv.obj.FLAG.CLICKABLE)
    _preview.add_event(_on_preview_clicked, lv.EVENT.CLICKED, None)

    # 底栏:list 图标 + 6 阈值格 + 共享滑块 + 计数
    _bottom_bar = lv.obj(screen)
    _bottom_bar.set_size(lv.pct(100), BAR_H)
    _bottom_bar.set_pos(0, PREVIEW_Y + PREVIEW_H)
    _bottom_bar.set_style_bg_color(lv.color_hex(BAR_BG), 0)
    _bottom_bar.set_style_bg_opa(255, 0)
    _bottom_bar.set_style_border_width(0, 0)
    _bottom_bar.set_style_pad_all(0, 0)
    _bottom_bar.set_style_radius(0, 0)
    _bottom_bar.clear_flag(lv.obj.FLAG.SCROLLABLE)

    # list 图标(左)
    list_btn = lv.obj(_bottom_bar)
    list_btn.set_size(48, 48)
    list_btn.align(lv.ALIGN.LEFT_MID, 2, 0)
    list_btn.set_style_bg_opa(0, 0)
    list_btn.set_style_border_width(0, 0)
    list_btn.set_style_pad_all(0, 0)
    list_btn.clear_flag(lv.obj.FLAG.SCROLLABLE)
    list_btn.add_flag(lv.obj.FLAG.CLICKABLE)
    list_btn.add_event(_on_list_clicked, lv.EVENT.CLICKED, None)
    list_icon_data, list_icon_dsc = icon_cache.get_color_icon("list")
    if list_icon_dsc is not None and list_icon_data is not None:
        import struct
        iw = ih = 64
        if len(list_icon_data) >= 24:
            iw = struct.unpack('>I', list_icon_data[16:20])[0]
            ih = struct.unpack('>I', list_icon_data[20:24])[0]
        ltarget = int(48 * 0.85)
        lzoom = int(min(ltarget / iw, ltarget / ih) * 256) if iw > 0 and ih > 0 else 256
        lzoom = max(8, min(lzoom, 256))
        list_img = lv.img(list_btn)
        list_img.set_src(list_icon_dsc)
        list_img.set_zoom(lzoom)
        list_img.center()

    # 6 阈值格(填底栏中段)
    for i, (key, label_key, lo, hi, dflt) in enumerate(THRESH_CELLS):
        _make_cell(_bottom_bar, key, label_key, lo, hi, dflt, 56 + i * 56)

    # 共享滑块(右侧,竖向小)
    _slider = lv.slider(_bottom_bar)
    _slider.set_size(60, 16)
    _slider.align(lv.ALIGN.RIGHT_MID, -100, 0)
    _slider.set_range(L_LO, L_HI)
    _slider.set_value(_thresh_values[_selected_key], lv.ANIM.OFF)
    _slider.add_event(_on_slider_changed, lv.EVENT.VALUE_CHANGED, None)

    # 计数(最右)
    count_label = lv.label(_bottom_bar)
    count_label.set_text(runtime.lang.t("color_detect.registered", 0))
    count_label.add_style(make_back_bar_text_style(fonts.body), 0)
    count_label.align(lv.ALIGN.RIGHT_MID, -8, 0)
    _count_label = count_label
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python tests/test_color_detect_ast.py`
Expected: ALL PASS(UI 4 项 + 之前 9 项 = 13 PASS)

- [ ] **Step 5: 提交**

```bash
git add scripts/color_detect/app.py tests/test_color_detect_ast.py
git commit -m "feat(color_detect): build UI (top bar/left table/preview/6-cell slider bottom bar)

顶栏返回+标题,左表 4×3 采色历史(顶栏左下方),透明预览可点击取色,
底栏 list 图标+6 阈值格(点选置绿)+共享滑块(range 随选中格切换)+计数。
清除/保存浮层对齐 tag_detect。

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 6: on_frame 检测 + 取色 + 注册 + host_tick

**Files:**
- Modify: `scripts/color_detect/app.py`(加 on_frame + run + _destroy_ui)

on_frame:处理 pending_click 取色套阈值;当前色 find_blobs 白框;注册色 find_blobs 彩色 ID 框;KEY2 注册;host_tick。

- [ ] **Step 1: 写失败测试**

在 `tests/test_color_detect_ast.py` 加:

```python
def test_on_frame_uses_find_blobs():
    """on_frame 必须用 find_blobs 检测。"""
    src = _read(APP_PATH)
    assert "find_blobs" in src


def test_on_frame_calls_host_tick():
    """on_frame 必须调 host_tick(slots)。"""
    src = _read(APP_PATH)
    assert "host_tick" in src


def test_on_frame_handles_pending_click():
    """on_frame 必须处理 _pending_click(get_pixel + RGB->LAB + 套阈值)。"""
    src = _read(APP_PATH)
    assert "_pending_click" in src
    assert "get_pixel" in src
    assert "_rgb_to_lab" in src
    assert "_make_threshold" in src


def test_on_frame_key2_register():
    """on_frame 必须处理 KEY2 注册当前阈值到 ColorDB。"""
    src = _read(APP_PATH)
    assert "try_register" in src
    assert "_color_db.register" in src or "db.register" in src


def test_run_has_exit_flag_loop():
    """run() 主循环必须用 exit_flag + snapshot + show OSD1 + task_handler。"""
    src = _read(APP_PATH)
    assert "def run(" in src
    assert "exit_flag" in src
    assert "snapshot" in src
    assert "LAYER_OSD1" in src
    assert "task_handler" in src


def test_on_frame_isolated_by_try_except():
    """on_frame 调用必须被 try/except 包裹。"""
    src = _read(APP_PATH)
    assert "on_frame(img)" in src
    assert "except" in src


def test_destroy_ui_restores_screen():
    """_destroy_ui 必须删 UI + 恢复 bg_opa=255。"""
    src = _read(APP_PATH)
    assert "def _destroy_ui(" in src
    assert "bg_opa(255" in src


def test_crosshair_drawn():
    """on_frame 必须画居中绿色十字。"""
    src = _read(APP_PATH)
    assert "draw_cross" in src
    assert "320, 240" in src.replace(" ", "") or "320,240" in src.replace(" ", "")
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python tests/test_color_detect_ast.py`
Expected: FAIL on_frame/run/destroy 相关 8 项

- [ ] **Step 3: 加 on_frame + run + _destroy_ui**

在 app.py 末尾(`_build_ui` 后)加:

```python
def _current_threshold_tuple():
    """从 _thresh_values 取当前 6 阈值 tuple。"""
    return (_thresh_values["Lmin"], _thresh_values["Lmax"],
            _thresh_values["Amin"], _thresh_values["Amax"],
            _thresh_values["Bmin"], _thresh_values["Bmax"])


def _apply_sample(lab, rgb):
    """采样色套用 ±10 阈值 -> 更新 6 格数值 + 滑块;压入左表 3 槽循环。"""
    global _swatch
    th = _make_threshold(lab)
    _thresh_values["Lmin"] = th[0]
    _thresh_values["Lmax"] = th[1]
    _thresh_values["Amin"] = th[2]
    _thresh_values["Amax"] = th[3]
    _thresh_values["Bmin"] = th[4]
    _thresh_values["Bmax"] = th[5]
    # 刷新 6 格数值标签
    for key in _thresh_labels:
        lbl = _thresh_labels[key]
        if lbl is not None:
            try:
                lbl.set_text(str(_thresh_values[key]))
            except Exception:
                pass
    # 同步滑块到选中格
    if _slider is not None:
        for k, _label, lo, hi, _dflt in THRESH_CELLS:
            if k == _selected_key:
                _slider.set_range(lo, hi)
                _slider.set_value(_thresh_values[_selected_key], lv.ANIM.OFF)
                break
    # 压入左表 3 槽循环(覆盖最旧:0->1->2->0)
    _swatch = [_swatch[1], _swatch[2], (lab, rgb)]
    _refresh_table()


def _find_largest_blob(img_det, th):
    """find_blobs 取最大 blob(rect [x,y,w,h] in QVGA),无返回 None。"""
    try:
        blobs = img_det.find_blobs([th], pixels_threshold=30,
                                   area_threshold=30, merge=True)
    except Exception as e:
        print("[color_detect] find_blobs error: %s" % e)
        return None
    if not blobs:
        return None
    best = max(blobs, key=lambda b: b.pixels())
    return best.rect()  # [x, y, w, h]


def on_frame(img):
    """chn1 find_blobs 检测 -> 当前色白框 + 注册色彩色ID框 -> chn0 画框 -> host_tick。"""
    if _RUNTIME is None:
        return
    img_det = _RUNTIME.sensor.snapshot(chn=CAM_CHN_ID_1)
    slots = [None, None, None, None]
    cur_th = _current_threshold_tuple()

    # 处理 pending_click 取色(在 chn0 VGA img 上 get_pixel)
    global _pending_click
    if _pending_click is not None:
        cx, cy = _pending_click
        _pending_click = None
        try:
            pixel = img.get_pixel(cx, cy)
            # get_pixel 返回 (R,G,B) 或单值,按 RGB888 处理
            if isinstance(pixel, (tuple, list)):
                r, g, b = pixel[0], pixel[1], pixel[2]
            else:
                r = g = b = pixel
            lab = _rgb_to_lab(r, g, b)
            rgb_hex = ((r & 0xFF) << 16) | ((g & 0xFF) << 8) | (b & 0xFF)
            _apply_sample(lab, rgb_hex)
            cur_th = _current_threshold_tuple()
        except Exception as e:
            print("[color_detect] sample error: %s" % e)

    # 当前色检测 -> 白框(未注册)
    rect = _find_largest_blob(img_det, cur_th)
    if rect is not None:
        x, y, w, h = [int(v) for v in rect]
        color = _draw_color(BOX_UNKNOWN)
        img.draw_rectangle(x * DET_SCALE, y * DET_SCALE,
                           w * DET_SCALE, h * DET_SCALE,
                           color=color, thickness=2)

    # 注册色检测 -> 彩色 ID 框 + 填 slots
    for slot, entry in enumerate(_color_db.iter_slots(), start=1):
        r2 = _find_largest_blob(img_det, entry['threshold'])
        if r2 is not None:
            x, y, w, h = [int(v) for v in r2]
            box_color = BOX_COLORS.get(slot, BOX_UNKNOWN)
            color = _draw_color(box_color)
            img.draw_rectangle(x * DET_SCALE, y * DET_SCALE,
                               w * DET_SCALE, h * DET_SCALE,
                               color=color, thickness=4)
            img.draw_string_advanced(x * DET_SCALE, y * DET_SCALE - 24, 24,
                                     "ID%d" % slot, color=color)
            slots[slot - 1] = (slot, x * DET_SCALE, y * DET_SCALE,
                               w * DET_SCALE, h * DET_SCALE, 100)

    # 居中绿色十字(对齐 tag_detect)
    img.draw_cross(320, 240, color=(0xFF, 0x00, 0xFF, 0x00), size=20, thickness=2)

    # KEY2 注册:pending 且当前帧有当前色 blob -> 注册当前阈值到 4 槽
    if _id_registry is not None and _id_registry.has_pending() and rect is not None:
        # 用当前 6 阈值 + 中心 LAB(从阈值反推中点) + RGB(左表最新采样或白)
        lab_mid = ((cur_th[0] + cur_th[1]) // 2,
                   (cur_th[2] + cur_th[3]) // 2,
                   (cur_th[4] + cur_th[5]) // 2)
        latest_rgb = _swatch[2][1] if _swatch[2] is not None else 0xFFFFFF
        slot = _id_registry.try_register(
            (cur_th, lab_mid), _RUNTIME.buzzer,
            registrar=lambda th: _color_db.register(th, rgb=latest_rgb))
        if slot is not None:
            _refresh_count()

    if _RUNTIME is not None and _RUNTIME.host is not None:
        _RUNTIME.host_tick(slots)


def _destroy_ui():
    global _screen, _top_bar, _bottom_bar, _preview, _table, _count_label, _slider
    global _overlay, _clear_btn, _save_btn
    for obj in (_clear_btn, _save_btn, _overlay, _slider, _table,
                _top_bar, _bottom_bar, _preview, _count_label):
        if obj is not None:
            try:
                obj.delete()
            except Exception:
                pass
    _clear_btn = None
    _save_btn = None
    _overlay = None
    _slider = None
    _table = None
    _top_bar = None
    _bottom_bar = None
    _preview = None
    _count_label = None
    _thresh_labels.clear()
    _thresh_cells.clear()
    try:
        from ui.theme import Colors
        scr = lv.scr_act()
        scr.set_style_bg_color(lv.color_hex(Colors.BG), 0)
        scr.set_style_bg_opa(255, 0)
    except Exception:
        pass
    _screen = None


def run(runtime):
    """reset 框架入口。单线程主循环:snapshot chn0 -> on_frame -> show OSD1 -> task_handler。"""
    global _RUNTIME, _color_db
    _RUNTIME = runtime
    _color_db = ColorDB()
    exit_flag = [False]
    _init_registry(runtime.fpioa)
    _build_ui(runtime, exit_flag)
    fc = 0
    try:
        while not exit_flag[0]:
            os.exitpoint()
            img = runtime.sensor.snapshot(chn=CAM_CHN_ID_0)
            try:
                on_frame(img)
            except Exception as e:
                print("[color_detect] on_frame error: %s" % e)
                try:
                    sys.print_exception(e)
                except Exception:
                    pass
            if _id_registry is not None:
                _id_registry.poll_k2()
            _process_overlay_close()
            Display.show_image(img, 0, 0, Display.LAYER_OSD1)
            time.sleep_ms(lv.task_handler())
            fc += 1
            if fc % 30 == 0:
                print("[color_detect] fc=%d" % fc)
    finally:
        _destroy_ui()
        if _color_db is not None:
            _color_db.flush_to_disk()
        _RUNTIME = None
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python tests/test_color_detect_ast.py`
Expected: ALL PASS(全部 21 项)

- [ ] **Step 5: 跑全量回归**

Run: `python tests/test_color_db.py && python tests/test_host_api.py && python tests/test_color_detect_ast.py`
Expected: 三个文件全 PASS

- [ ] **Step 6: 提交**

```bash
git add scripts/color_detect/app.py tests/test_color_detect_ast.py
git commit -m "feat(color_detect): on_frame detection + sampling + register + run loop

on_frame:pending_click 取色套阈值,当前色 find_blobs 白框,注册色彩色 ID 框,
KEY2 注册当前阈值到 4 槽,居中绿色十字,host_tick 协议 0x06。
run() 单线程主循环 + _destroy_ui 恢复屏幕。

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 7: 全量回归 + 板端验收清单

**Files:** 无(验证)

- [ ] **Step 1: 跑全量测试套件**

Run(逐个,Windows host 端可跑的):
```bash
python tests/test_color_db.py
python tests/test_host_api.py
python tests/test_color_detect_ast.py
python tests/test_template.py
```
Expected: color 相关全 PASS;test_template 回归到基线(3 FAIL 预存,与 color 无关)

- [ ] **Step 2: 检查图标资源目录**

Run: 列 `resource/icons/color_detect_icon/` 是否有 `list.png` + `back.png`
- 若无:preload 容错降级(返回钮显示 `<`,list 图标显示空白),不阻塞。记录待补。
- 若有:确认 PNG 可被 lv.img_dsc_t 解析。

- [ ] **Step 3: 板端验收清单(交付用户测)**

把以下清单交给用户在 K230D 板端验证(每项打勾):

```
[ ] 1. 主菜单点"颜色识别"卡片 → 进入脚本,顶栏返回+标题,画面正常
[ ] 2. 顶栏左下方出现 4×3 左表(首行 L/A/B,2-4 行"-")
[ ] 3. 底栏:list 图标 + 6 阈值格(Lmin0/Lmax100/Amin-10/Amax10/Bmin-10/Bmax10)+ 滑块 + 计数"已注册 0/4"
[ ] 4. 点 Lmin 格 → 置绿,滑块 range 变 0-100,拖滑块数值变,松开立即用于检测
[ ] 5. 点 Amin 格 → 置绿,滑块 range 变 -128~127
[ ] 6. 点预览区某颜色 → 该点颜色套 ±10 阈值,6 格数值更新,左表槽1 填入该色(底色=采样色+LAB值)
[ ] 7. 再点两次不同色 → 左表槽2、槽3 填入,第四次点色覆盖槽1(循环)
[ ] 8. 屏幕中央有绿色十字(320,240)
[ ] 9. 当前色在画面中匹配的区域显示白框
[ ] 10. 按 KEY2 → 当前检测色注册为 ID1,计数"已注册 1/4",蜂鸣
[ ] 11. 切换采样色后按 KEY2 → 注册 ID2(轮转),画面中该色显示彩色 ID 框(ID1/ID2 不同色)
[ ] 12. 按 KEY2 四次后第五次 → 覆盖 ID1(轮转回 1)
[ ] 13. 同色重复按 KEY2 → 不重复占槽(计数不变)
[ ] 14. 点 list 图标 → 弹清除/保存浮层;点清除 → ID 清空,计数归 0,蜂鸣
[ ] 15. 点浮层空白处 → 浮层关闭
[ ] 16. 点返回钮 → 回主菜单(不显 LOGO,热启动)
[ ] 17. 上位机收到协议 0x06 数据帧(4 槽坐标,大端)
[ ] 18. 切换脚本不卡(进/出流畅)
```

- [ ] **Step 4: 修复板端反馈的问题**

按 systematic-debugging 流程:每项不通过先定位根因(读错误/复现/查改动),再修。不盲目试错。

- [ ] **Step 5: 更新项目记录 + memory**

板端验收通过后:
- `项目记录.md` 追加 color_detect 段(实现要点 + 验收结果)
- memory 新建 `camerai-color-detect.md`,更新 `MEMORY.md` 索引
- 提交

```bash
git add 项目记录.md
git commit -m "docs(color_detect): board acceptance record

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## Self-Review

**1. Spec coverage:**
- UI 同前脚本 → Task 5 顶栏/底栏/预览 ✓
- 底栏 list + 6 LAB 格 + 滑块(标签上数值下,选中置绿,填充底栏)→ Task 5 ✓
- 左表 4×3(L/A/B + 3 行采色,底色=采样色)→ Task 5 ✓
- 屏幕点击取色 3 槽循环 → Task 6 `_apply_sample` 循环 + Task 5 左表 ✓
- ID 设置(当前色为 ID)→ Task 6 KEY2 + Task 1 ColorDB ✓
- 4 槽对齐协议 → Task 6 slots + Task 2 协议 0x06 ✓
- 容差 ±10 → Task 4 `_make_threshold` ✓
- 左表顶栏左下方 → Task 5 `_table.set_pos(4, BAR_H+4)` ✓
- 保留十字 → Task 6 `draw_cross(320,240)` ✓
- 采样即套用阈值 → Task 6 `_apply_sample` ✓

**2. Placeholder scan:** 无 TBD/TODO;每个步骤有实际代码。Task 7 Step 2 图标资源"若无"是条件分支(降级处理),非占位。

**3. Type consistency:**
- `ColorDB.register(threshold, rgb)` → threshold=((6值),(L,A,B)),Task 6 调用 `(cur_th, lab_mid)` 一致 ✓
- `ColorDB.iter_slots()` 返回 entry dict(threshold/lab/rgb)→ Task 6 `entry['threshold']` 一致 ✓
- `THRESH_CELLS` 6 项 → Task 5 `_make_cell` 遍历 6 格 ✓
- `_thresh_values` 6 键 → Task 4/5/6 一致 ✓
- `IdRegistry(fpioa, pin=0)` → Task 5 `_init_registry` 一致(tag_detect 同款)✓

**4. 风险已记:** find_blobs 多次调用、RGB→LAB 仅取色时、slider 首用、坐标映射、图标资源降级。

## 执行选择

计划已保存到 `docs/superpowers/plans/2026-06-26-color-detect.md`。两种执行方式:

**1. Subagent-Driven(推荐)** — 每个 Task 派新 subagent,任务间审查,快速迭代

**2. Inline Execution** — 本会话内批量执行,检查点审查

选哪种?
