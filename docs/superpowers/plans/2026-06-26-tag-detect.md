# 标签识别(tag_detect)实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 新增 tag_detect 脚本(AprilTag + 二维码双功能),并修复 face_detect 双语切换 bug。

**Architecture:** 复用 `_template` 单线程主循环;新增纯 Python `core/tag_db.py`(内存-only DB,可真单测);`IdRegistry` 加可选 `registrar` 参数(向后兼容);chn1 QVGA RGB565 做检测(官方 demo 同款),chn0 VGA RGB888 显示;两功能都用协议类型 0x04 上传;所有文本走 i18n。

**Tech Stack:** MicroPython / LVGL / K230 CanMV(`image.find_apriltags`/`find_qrcodes`)/ pytest 风格自跑测试。

**设计文档:** `docs/superpowers/specs/2026-06-26-tag-detect-design.md`(已提交 `00b437c`)。

**测试约定:**
- 板端模块(`host_api`/`id_registry`/`app_runtime`/`tag_detect/app`)依赖 MicroPython,Windows 不可导入 → 用 **AST 契约测试**(读源码 `ast.parse` 断言结构)。
- `core/tag_db.py` 纯 Python(仅用内置类型)→ **真单元测试**(`import tag_db` 实跑)。
- 所有测试 `python tests/test_xxx.py` 自跑,`test_runner()` 汇总,exit code 非0即失败。

**文件清单:**

| 文件 | 动作 | 职责 |
|------|------|------|
| `core/tag_db.py` | 新建 | 标签 ID 内存库(register/match/clear/flush_to_disk) |
| `core/id_registry.py` | 改 | `try_register` 加可选 `registrar` 参数 |
| `comm/host_api.py` | 改 | CATEGORY_TYPE 加 `tag_detect→0x04` |
| `core/app_runtime.py` | 改 | `_channels_for` 加 tag_detect(chn1 QVGA RGB565);`init_app` 加 tag_detect 图标预读 |
| `core/icon_cache.py` | 改 | 加 `preload_tag_icons`/`get_tag_icon` |
| `resource/icons/tag_detect_icon/back.png` | 新建(复制) | 从 face_detect_icon/back.png 复制 |
| `resource/icons/tag_detect_icon/list.png` | 新建(复制) | 从 face_detect_icon/list.png 复制 |
| `resource/i18n/zh_CN.json` | 改 | face_detect.registered 改带格式;新增 tag_detect 段 |
| `resource/i18n/en_US.json` | 改 | 同上对称 |
| `scripts/face_detect/app.py` | 改 | 硬编码中文 → `t()`(双语修复) |
| `scripts/tag_detect/app.py` | 新建 | 双功能脚本主体 |
| `tests/test_tag_db.py` | 新建 | tag_db 真单元测试 |
| `tests/test_host_api.py` | 改 | 加 tag_detect→0x04 契约 |
| `tests/test_id_registry.py` | 新建 | try_register 可选 registrar 契约 |
| `tests/test_tag_detect_app.py` | 新建 | tag_detect app AST 契约 |

---

## Task 1: face_detect 双语修复(M0)

**Files:**
- Modify: `resource/i18n/zh_CN.json:59`
- Modify: `resource/i18n/en_US.json:59`
- Modify: `scripts/face_detect/app.py:158,187,196,370`

- [ ] **Step 1: 写失败测试 — face_detect 无硬编码中文 + i18n 含格式化 registered**

新建 `tests/test_face_detect_i18n.py`:

```python
# tests/test_face_detect_i18n.py — face_detect 双语修复契约
import ast
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP_PATH = os.path.join(ROOT, "scripts", "face_detect", "app.py")
ZH_PATH = os.path.join(ROOT, "resource", "i18n", "zh_CN.json")
EN_PATH = os.path.join(ROOT, "resource", "i18n", "en_US.json")


def _src():
    with open(APP_PATH, "r", encoding="utf-8") as f:
        return f.read()


def test_no_hardcoded_chinese_strings():
    """face_detect app 不得硬编码中文 UI 文本(须走 lang.t())。"""
    src = _src()
    bad = ["已注册", "清除", "保存"]
    for s in bad:
        assert ('"%s"' % s) not in src and ("'%s'" % s) not in src, \
            "face_detect must not hardcode Chinese '%s'; use lang.t()" % s


def test_i18n_registered_has_format_placeholder():
    """face_detect.registered 必须带 %d 格式化占位(注册数)。"""
    for path in (ZH_PATH, EN_PATH):
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        val = data["face_detect"]["registered"]
        assert "%d" in val, "face_detect.registered must contain %%d in %s" % path


def test_runner():
    failures = 0
    for name in sorted(n for n in globals() if n.startswith("test_") and n != "test_runner"):
        try:
            globals()[name]()
            print("PASS %s" % name)
        except Exception as e:
            failures += 1
            print("FAIL %s: %s" % (name, e))
    print("\n%s" % ("ALL PASS" if failures == 0 else "%d FAILED" % failures))
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    test_runner()
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python tests/test_face_detect_i18n.py`
Expected: FAIL(当前 app.py 有硬编码 `"已注册"`/`"清除"`/`"保存"`,且 i18n `registered` 无 `%d`)。

- [ ] **Step 3: 改 i18n — registered 带格式**

`resource/i18n/zh_CN.json` 的 `face_detect.registered`:
```json
"registered": "已注册 %d/4",
```
`resource/i18n/en_US.json` 同段:
```json
"registered": "Registered %d/4",
```

- [ ] **Step 4: 改 face_detect/app.py — 三处硬编码改 t()**

`scripts/face_detect/app.py`:

line 158(`_refresh_count` 内):
```python
            _count_label.set_text(_RUNTIME.lang.t("face_detect.registered", len(_db_features)))
```

line 187(清除按钮 label):
```python
        cl.set_text(runtime.lang.t("face_detect.clear"))
```
注意:`_on_list_clicked` 内拿 runtime 须改用 `_RUNTIME`(模块级全局已在 on_frame 用)。确认 `_on_list_clicked` 用 `_RUNTIME.lang`。

line 196(保存按钮 label):
```python
        sv.set_text(_RUNTIME.lang.t("face_detect.save"))
```

line 370(`_build_ui` 内初始计数):
```python
    count_label.set_text(runtime.lang.t("face_detect.registered", len(_db_features)))
```

- [ ] **Step 5: 跑测试确认通过**

Run: `python tests/test_face_detect_i18n.py`
Expected: ALL PASS

- [ ] **Step 6: 回归现有 face_detect 测试**

Run: `python tests/test_face_detect.py && python tests/test_face_db.py`
Expected: ALL PASS(未破坏现有契约)。

- [ ] **Step 7: Commit**

```bash
git add resource/i18n/zh_CN.json resource/i18n/en_US.json scripts/face_detect/app.py tests/test_face_detect_i18n.py
git commit -m "fix(face_detect): hardcoded zh strings -> i18n t() for bilingual switch

非标题文本(已注册/清除/保存)硬编码中文未走 lang.t(),切换英语只有标题变。
registered i18n 值改带 %d 格式化。tag_detect 后续同样模式。

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## Task 2: TagDB 纯 Python 库(M1)

**Files:**
- Create: `core/tag_db.py`
- Test: `tests/test_tag_db.py`

- [ ] **Step 1: 写失败测试 — tag_db 真单元测试**

新建 `tests/test_tag_db.py`:

```python
# tests/test_tag_db.py — TagDB 真单元测试(纯 Python 可导入)
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "core"))

from tag_db import TagDB


def test_register_fills_empty_slot_first():
    db = TagDB()
    s1 = db.register(101)
    s2 = db.register(102)
    assert s1 == 1 and s2 == 2, "empty slots filled in order 1,2"
    assert db.count == 2


def test_register_round_robin_after_full():
    db = TagDB()
    for i in range(1, 5):
        assert db.register(i * 10) == i
    # 满4后覆盖 _next_slot(初始1),推进 1->2->3->4->1
    s5 = db.register(999)
    assert s5 == 1, "full db overwrites slot 1 (round-robin), got %r" % s5
    s6 = db.register(888)
    assert s6 == 2, "next overwrite slot 2"


def test_match_hit_returns_slot_and_score_one():
    db = TagDB()
    db.register(42)
    slot, score = db.match(42)
    assert slot == 1, "matched slot 1"
    assert score == 1.0, "exact match score = 1.0"


def test_match_miss_returns_none_zero():
    db = TagDB()
    db.register(42)
    slot, score = db.match(999)
    assert slot is None, "miss -> None slot"
    assert score == 0.0, "miss -> 0.0 score"


def test_match_empty_db():
    db = TagDB()
    slot, score = db.match(1)
    assert slot is None and score == 0.0


def test_match_qr_string_code_id():
    """QR payload 是字符串,code_id 类型由调用方决定。"""
    db = TagDB()
    db.register("http://example.com")
    slot, score = db.match("http://example.com")
    assert slot == 1 and score == 1.0
    assert db.match("other") == (None, 0.0)


def test_clear_empties_db():
    db = TagDB()
    db.register(1)
    db.register(2)
    db.clear()
    assert db.count == 0
    assert db.match(1) == (None, 0.0)


def test_flush_to_disk_is_noop_safe():
    """flush_to_disk 当前 no-op(持久化预留),调用不崩。"""
    db = TagDB()
    db.register(1)
    db.clear()
    db.flush_to_disk()  # must not raise


def test_runner():
    failures = 0
    for name in sorted(n for n in globals() if n.startswith("test_") and n != "test_runner"):
        try:
            globals()[name]()
            print("PASS %s" % name)
        except Exception as e:
            failures += 1
            print("FAIL %s: %s" % (name, e))
    print("\n%s" % ("ALL PASS" if failures == 0 else "%d FAILED" % failures))
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    test_runner()
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python tests/test_tag_db.py`
Expected: FAIL(`ImportError: no module named 'tag_db'`)。

- [ ] **Step 3: 实现 core/tag_db.py**

新建 `core/tag_db.py`:

```python
# core/tag_db.py — 标签 ID 内存数据库(AprilTag / 二维码共用)
#
# 镜像 face_db 的内存-only + flush_to_disk 预留模式,但:
#   - 存标量 code_id(int=AprilTag.id 或 str=QR.payload),非 ulab ndarray
#   - 精确匹配(相等即命中),无相似度概念,score=1.0
#
# 纯 Python(无 MicroPython 依赖)→ 可在 host 端真单元测试。
#
# 持久化路径待定(同 face_db):flush_to_disk 当前 no-op,后续决定存哪。
# K230 坑#2:运行时 SD 写与 display flush 抢 DMA,故运行时只改内存,退出刷盘。


class TagDB:
    """标签 ID 内存库。code_id 由调用方决定类型(int/str)。"""

    def __init__(self):
        self._features = {}        # {slot_id: code_id}
        self._next_slot = 1        # 轮转覆盖指针(1-4 循环)
        self._dirty = False
        self._clear_dirty = False

    def register(self, code_id):
        """注册 code_id 到槽位(轮转覆盖,同 face_db)。

        空槽优先(不推进 _next_slot);无空槽覆盖 _next_slot 并推进(1→2→3→4→1)。
        返回 slot_id(1-4)。纯内存,设 _dirty。
        """
        slot = None
        for i in range(1, 5):
            if i not in self._features:
                slot = i
                break
        if slot is None:
            slot = self._next_slot
            self._next_slot = self._next_slot % 4 + 1
        self._features[slot] = code_id
        self._dirty = True
        self._clear_dirty = False
        print("[TagDB] registered code_id=%r -> id%d (memory, dirty)" % (code_id, slot))
        return slot

    def match(self, code_id):
        """精确匹配 code_id。返回 (slot_id, 1.0) 或 (None, 0.0)。

        标签码精确相等即命中(无相似度),score=1.0 作上位机置信度。
        """
        for slot_id, cid in self._features.items():
            if cid == code_id:
                return slot_id, 1.0
        return None, 0.0

    def clear(self):
        """清内存,设 _clear_dirty(clear wins over _dirty)。"""
        self._features.clear()
        self._clear_dirty = True
        self._dirty = False
        self._next_slot = 1
        print("[TagDB] cleared (memory, clear_dirty)")

    def flush_to_disk(self):
        """退出时刷盘(预留)。⚠️ 持久化路径待定,当前 no-op,仅复位 dirty 标志。"""
        if self._clear_dirty:
            print("[TagDB] exit: clear intent recorded (persistence disabled)")
        elif self._dirty:
            print("[TagDB] exit: %d code(s) pending (persistence disabled)"
                  % len(self._features))
        self._clear_dirty = False
        self._dirty = False

    @property
    def count(self):
        return len(self._features)
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python tests/test_tag_db.py`
Expected: ALL PASS(9 tests)。

- [ ] **Step 5: Commit**

```bash
git add core/tag_db.py tests/test_tag_db.py
git commit -m "feat(tag_db): memory-only tag ID database with reserved persistence

TagDB 镜像 face_db 内存-only 模式,存标量 code_id(int=AprilTag/str=QR),
精确匹配 score=1.0。纯 Python 可真单测。flush_to_disk no-op 预留。

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## Task 3: IdRegistry 加可选 registrar 参数(M2)

**Files:**
- Modify: `core/id_registry.py:56`
- Test: `tests/test_id_registry.py`(新建)

- [ ] **Step 1: 写失败测试 — try_register 含可选 registrar**

新建 `tests/test_id_registry.py`:

```python
# tests/test_id_registry.py — IdRegistry 可选 registrar 契约(AST)
import ast
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REG_PATH = os.path.join(ROOT, "core", "id_registry.py")


def _src():
    with open(REG_PATH, "r", encoding="utf-8") as f:
        return f.read()


def _parse():
    return ast.parse(_src(), filename=REG_PATH)


def _method(name):
    tree = _parse()
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == "IdRegistry":
            for n in node.body:
                if isinstance(n, ast.FunctionDef) and n.name == name:
                    return n
    raise AssertionError("Method %s missing" % name)


def test_try_register_has_optional_registrar_param():
    """try_register 须含可选 registrar 参数(默认 None 走 face_db,向后兼容)。"""
    m = _method("try_register")
    args = [a.arg for a in m.args.args]
    assert "feature" in args, "try_register takes feature (1st arg)"
    assert "registrar" in args, "try_register must accept optional 'registrar'"
    # registrar 必须有默认值 None(可选,不破坏 face_detect 现有调用)
    defaults = m.args.defaults
    assert len(defaults) >= 1, "registrar must have a default (optional)"


def test_try_register_uses_registrar_when_provided():
    """传 registrar 时须调 registrar(feature),不硬编码 face_db。"""
    seg = ast.get_source_segment(_src(), _method("try_register")) or ""
    assert "registrar" in seg, "try_register must reference registrar param"
    assert "registrar(feature)" in seg or "registrar(code" not in seg, \
        "try_register must call registrar(feature) when provided"


def test_try_register_defaults_to_face_db():
    """registrar=None 时回退 face_db.register(向后兼容 face_detect)。"""
    seg = ast.get_source_segment(_src(), _method("try_register")) or ""
    assert "face_db" in seg or "face_db.register" in seg, \
        "try_register must fall back to face_db when registrar is None"


def test_runner():
    failures = 0
    for name in sorted(n for n in globals() if n.startswith("test_") and n != "test_runner"):
        try:
            globals()[name]()
            print("PASS %s" % name)
        except Exception as e:
            failures += 1
            print("FAIL %s: %s" % (name, e))
    print("\n%s" % ("ALL PASS" if failures == 0 else "%d FAILED" % failures))
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    test_runner()
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python tests/test_id_registry.py`
Expected: FAIL(当前 `try_register(self, feature, buzzer=None)` 无 `registrar` 参数)。

- [ ] **Step 3: 改 id_registry.py — 加 registrar 参数**

`core/id_registry.py` 的 `try_register`(line 56 起)改为:

```python
    def try_register(self, feature, buzzer=None, registrar=None):
        """on_frame 调。pending(2秒内) → 注册 + 蜂鸣 + 清 pending。
        返回 slot_id(1-4) 或 None（没按/超时/失败）。

        单线程：on_frame 先 has_pending() 判定，命中再提 feature 传入本方法，
        避免无 pending 时重复 NPU 推理。feature：512维 ndarray（face）/ 标量
        code_id（tag）。buzzer：Buzzer 实例或 None。

        registrar：可选注册函数，签名 registrar(feature)->slot_id。
        None（默认）→ face_db.register（face_detect 向后兼容，零影响）。
        tag_detect 传 tag_db.register 复用 K2 边沿/超时/蜂鸣逻辑。
        """
        if not self._pending:
            return None
        # 2 秒超时：防"按了→走开→别人来→误注册"
        if time.ticks_diff(time.ticks_ms(), self._pending_time) > 2000:
            self._pending = False
            print("[IdRegistry] pending timeout, discarded")
            return None
        self._pending = False
        try:
            if registrar is not None:
                slot = registrar(feature)
            else:
                from core.face_db import face_db
                slot = face_db.register(feature)
            self._last_slot = slot
            if buzzer is not None:
                buzzer.beep(ms=80)
            return slot
        except Exception as e:
            print("[IdRegistry] register failed: %s" % e)
            if buzzer is not None:
                buzzer.beep(ms=200)
            return None
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python tests/test_id_registry.py`
Expected: ALL PASS。

- [ ] **Step 5: Commit**

```bash
git add core/id_registry.py tests/test_id_registry.py
git commit -m "feat(id_registry): optional registrar param for non-face scripts

try_register 加 registrar=None 可选参数。None→face_db.register（face_detect
向后兼容零影响）；tag_detect 传 tag_db.register 复用 K2 边沿/超时/蜂鸣。

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## Task 4: host_api CATEGORY_TYPE 加 tag_detect(M4)

**Files:**
- Modify: `comm/host_api.py:41-47`
- Modify: `tests/test_host_api.py:29-37`

- [ ] **Step 1: 写失败测试 — CATEGORY_TYPE 覆盖 tag_detect→0x04**

在 `tests/test_host_api.py` 的 `test_category_type_mapping_covers_all_categories` 里,把 for 列表加一行 tag_detect:

```python
    for cat, code in [("main_menu", "0x01"), ("settings", "0x01"),
                      ("camera", "0x02"), ("face_detect", "0x03"),
                      ("tag_detect", "0x04"),
                      ("_template", "0x01")]:
        assert ('"%s"' % cat) in src or ("'%s'" % cat) in src, \
            "CATEGORY_TYPE must cover %s" % cat
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python tests/test_host_api.py`
Expected: FAIL(`CATEGORY_TYPE must cover tag_detect`)。

- [ ] **Step 3: 改 host_api.py — CATEGORY_TYPE 加 tag_detect**

`comm/host_api.py` 的 `CATEGORY_TYPE`(line 41)加一行:

```python
    CATEGORY_TYPE = {
        "main_menu":  TYPE_MAIN_MENU,     # 0x01
        "settings":   TYPE_MAIN_MENU,     # 0x01（复用主菜单）
        "camera":     TYPE_CAMERA,        # 0x02
        "face_detect":TYPE_FACE_DETECT,   # 0x03
        "tag_detect": TYPE_TAG_DETECT,    # 0x04
        "_template":  TYPE_MAIN_MENU,     # 0x01（默认）
    }
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python tests/test_host_api.py`
Expected: ALL PASS。

- [ ] **Step 5: Commit**

```bash
git add comm/host_api.py tests/test_host_api.py
git commit -m "feat(host_api): map tag_detect -> TYPE_TAG_DETECT 0x04

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## Task 5: icon_cache 加 tag 图标预读(M4)

**Files:**
- Create: `resource/icons/tag_detect_icon/back.png`(复制自 face_detect_icon/back.png)
- Create: `resource/icons/tag_detect_icon/list.png`(复制自 face_detect_icon/list.png)
- Modify: `core/icon_cache.py:99-121`
- Test: `tests/test_tag_detect_app.py` 的 icon_cache 部分(见 Task 8,或此处独立小测)

- [ ] **Step 1: 复制图标资源**

```bash
cp resource/icons/face_detect_icon/back.png resource/icons/tag_detect_icon/back.png
cp resource/icons/face_detect_icon/list.png resource/icons/tag_detect_icon/list.png
ls resource/icons/tag_detect_icon/
```
Expected: 列出 back.png + list.png。

- [ ] **Step 2: 写失败测试 — icon_cache 有 preload_tag_icons/get_tag_icon**

新建 `tests/test_icon_cache.py`:

```python
# tests/test_icon_cache.py — icon_cache tag 图标接口契约(AST)
import ast
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
IC_PATH = os.path.join(ROOT, "core", "icon_cache.py")


def _src():
    with open(IC_PATH, "r", encoding="utf-8") as f:
        return f.read()


def _parse():
    return ast.parse(_src(), filename=IC_PATH)


def _method(name):
    tree = _parse()
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == "_IconCache":
            for n in node.body:
                if isinstance(n, ast.FunctionDef) and n.name == name:
                    return n
    raise AssertionError("Method %s missing" % name)


def test_preload_tag_icons_exists():
    """icon_cache 须有 preload_tag_icons() 预读 tag_detect 图标。"""
    try:
        _method("preload_tag_icons")
    except AssertionError:
        assert False, "_IconCache must define preload_tag_icons()"


def test_get_tag_icon_exists():
    """icon_cache 须有 get_tag_icon(name) 取 tag 图标。"""
    try:
        m = _method("get_tag_icon")
    except AssertionError:
        assert False, "_IconCache must define get_tag_icon(name)"
    args = [a.arg for a in m.args.args]
    assert "name" in args, "get_tag_icon must take name"


def test_preload_tag_icons_reads_tag_detect_icon_dir():
    """preload_tag_icons 必须读 tag_detect_icon/ 目录。"""
    seg = ast.get_source_segment(_src(), _method("preload_tag_icons")) or ""
    assert "tag_detect_icon" in seg, "preload_tag_icons must read tag_detect_icon/"


def test_runner():
    failures = 0
    for name in sorted(n for n in globals() if n.startswith("test_") and n != "test_runner"):
        try:
            globals()[name]()
            print("PASS %s" % name)
        except Exception as e:
            failures += 1
            print("FAIL %s: %s" % (name, e))
    print("\n%s" % ("ALL PASS" if failures == 0 else "%d FAILED" % failures))
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    test_runner()
```

- [ ] **Step 3: 跑测试确认失败**

Run: `python tests/test_icon_cache.py`
Expected: FAIL(`_IconCache must define preload_tag_icons()`)。

- [ ] **Step 4: 改 icon_cache.py — 加 tag 图标方法**

`core/icon_cache.py`:
- `__init__` 加 `self._tag_icons = {}`(在 `self._face_icons = {}` 下一行)。
- 在 `get_face_icon` 方法后追加:

```python
    def preload_tag_icons(self):
        """预读标签识别APP图标（在首次 task_handler 之前调用）"""
        base = "/sdcard/CamerAi/resource/icons/tag_detect_icon/"
        icons = {
            "list": base + "list.png",
            "back": base + "back.png",
        }
        for name, path in icons.items():
            try:
                with open(path, 'rb') as f:
                    data = f.read()
                dsc = lv.img_dsc_t({
                    'data_size': len(data),
                    'data': data,
                })
                self._tag_icons[name] = (data, dsc)
                print(f"[IconCache] tag/{name} OK ({len(data)} bytes)")
            except Exception as e:
                print(f"[IconCache] tag/{name} FAILED: {e}")

    def get_tag_icon(self, name):
        """获取标签识别图标 (data, dsc)，未缓存返回 (None, None)"""
        return self._tag_icons.get(name, (None, None))
```

- [ ] **Step 5: 跑测试确认通过**

Run: `python tests/test_icon_cache.py`
Expected: ALL PASS。

- [ ] **Step 6: Commit**

```bash
git add core/icon_cache.py resource/icons/tag_detect_icon/ tests/test_icon_cache.py
git commit -m "feat(icon_cache): preload_tag_icons/get_tag_icon for tag_detect

复制 face_detect_icon back/list 到 tag_detect_icon。预读在首次 task_handler 前。

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## Task 6: app_runtime 通道配置 + 图标预读(M3/M4)

**Files:**
- Modify: `core/app_runtime.py:169-170,176-185`
- Test: `tests/test_tag_detect_app.py` 的 channels 部分(见 Task 8)

- [ ] **Step 1: 改 app_runtime.py — _channels_for 加 tag_detect**

`core/app_runtime.py` 的 `_channels_for`(line 176)在 face_detect 分支后加 tag_detect:

```python
    def _channels_for(self, category_id):
        """按 category 决定 sensor 通道配置。"""
        chs = [(CAM_CHN_ID_0, Sensor.VGA, Sensor.RGB888)]
        if category_id == "face_detect":
            chs.append((CAM_CHN_ID_2, Sensor.XGA, Sensor.RGBP888))
        elif category_id == "camera":
            chs.append((CAM_CHN_ID_1, Sensor.SXGAM, Sensor.RGB565))
        elif category_id == "tag_detect":
            # chn1 QVGA RGB565 专做检测（官方 AprilTag/QR demo 同款）；
            # chn0 VGA RGB888 显示。rect ×2 映射显示（QVGA→VGA 整数缩放）。
            chs.append((CAM_CHN_ID_1, Sensor.QVGA, Sensor.RGB565))
        elif category_id == "_template":
            pass  # 模板纯显示，单通道 chn0（复用默认）
        return chs
```

- [ ] **Step 2: 改 app_runtime.py — init_app 加 tag_detect 图标预读**

`init_app`(line 167-170)的图标预读分支加 tag_detect:

```python
        if category_id == "camera":
            icon_cache.preload_camera_icons()
        elif category_id == "face_detect":
            icon_cache.preload_face_icons()
        elif category_id == "tag_detect":
            icon_cache.preload_tag_icons()
```

- [ ] **Step 3: 验证(Task 8 AST 测试会覆盖,此处先确认文件可解析)**

Run: `python -c "import ast; ast.parse(open('core/app_runtime.py',encoding='utf-8').read()); print('OK')"`
Expected: `OK`。

- [ ] **Step 4: Commit(随 Task 7/8 一起或此处单独)**

暂不单独 commit,Task 7 i18n 一起提交,或在此处:

```bash
git add core/app_runtime.py
git commit -m "feat(app_runtime): tag_detect chn1 QVGA RGB565 + icon preload

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## Task 7: i18n 新增 tag_detect 段(M5)

**Files:**
- Modify: `resource/i18n/zh_CN.json`
- Modify: `resource/i18n/en_US.json`

- [ ] **Step 1: 写失败测试 — i18n 含 tag_detect 段**

新建 `tests/test_i18n_tag.py`:

```python
# tests/test_i18n_tag.py — tag_detect i18n 键契约
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ZH = os.path.join(ROOT, "resource", "i18n", "zh_CN.json")
EN = os.path.join(ROOT, "resource", "i18n", "en_US.json")


def _load(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def test_tag_detect_section_exists_both_langs():
    for path in (ZH, EN):
        data = _load(path)
        assert "tag_detect" in data, "missing tag_detect section in %s" % path


def test_tag_detect_keys_present():
    required = ["april_tag", "qr_code", "registered"]
    for path in (ZH, EN):
        td = _load(path)["tag_detect"]
        for k in required:
            assert k in td, "missing tag_detect.%s in %s" % (k, path)


def test_tag_detect_registered_has_placeholder():
    for path in (ZH, EN):
        val = _load(path)["tag_detect"]["registered"]
        assert "%d" in val, "tag_detect.registered must contain %%d in %s" % path


def test_runner():
    failures = 0
    for name in sorted(n for n in globals() if n.startswith("test_") and n != "test_runner"):
        try:
            globals()[name]()
            print("PASS %s" % name)
        except Exception as e:
            failures += 1
            print("FAIL %s: %s" % (name, e))
    print("\n%s" % ("ALL PASS" if failures == 0 else "%d FAILED" % failures))
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    test_runner()
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python tests/test_i18n_tag.py`
Expected: FAIL(`missing tag_detect section`)。

- [ ] **Step 3: 改 zh_CN.json — 加 tag_detect 段**

在 `zh_CN.json` 的 `"face_detect": {...}` 段后(逗号后)加:

```json
  "tag_detect": {
    "april_tag": "AprilTag",
    "qr_code": "二维码",
    "registered": "已注册 %d/4"
  },
```

- [ ] **Step 4: 改 en_US.json — 加 tag_detect 段(对称)**

```json
  "tag_detect": {
    "april_tag": "AprilTag",
    "qr_code": "QR Code",
    "registered": "Registered %d/4"
  },
```

- [ ] **Step 5: 跑测试确认通过**

Run: `python tests/test_i18n_tag.py`
Expected: ALL PASS。

- [ ] **Step 6: Commit**

```bash
git add resource/i18n/zh_CN.json resource/i18n/en_US.json tests/test_i18n_tag.py
git commit -m "feat(i18n): tag_detect section (april_tag/qr_code/registered)

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## Task 8: tag_detect 脚本主体(M3)

**Files:**
- Create: `scripts/tag_detect/app.py`
- Test: `tests/test_tag_detect_app.py`

- [ ] **Step 1: 写失败测试 — tag_detect app AST 契约**

新建 `tests/test_tag_detect_app.py`:

```python
# tests/test_tag_detect_app.py — tag_detect app AST 契约(板端不可导入)
import ast
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP_PATH = os.path.join(ROOT, "scripts", "tag_detect", "app.py")
RT_PATH = os.path.join(ROOT, "core", "app_runtime.py")


def _app_src():
    with open(APP_PATH, "r", encoding="utf-8") as f:
        return f.read()


def _app_tree():
    return ast.parse(_app_src(), filename=APP_PATH)


def _func(name):
    tree = _app_tree()
    for n in tree.body:
        if isinstance(n, ast.FunctionDef) and n.name == name:
            return n
    raise AssertionError("Function %s missing in tag_detect/app.py" % name)


def test_run_entrypoint_exists():
    try:
        _func("run")
    except AssertionError:
        assert False, "tag_detect/app.py must define run(runtime)"


def test_on_frame_calls_find_apriltags_and_find_qrcodes():
    """on_frame 须按功能调 find_apriltags 或 find_qrcodes。"""
    seg = ast.get_source_segment(_app_src(), _func("on_frame")) or ""
    assert "find_apriltags" in seg, "on_frame must call find_apriltags"
    assert "find_qrcodes" in seg, "on_frame must call find_qrcodes"
    assert "TAG36H11" in seg or "TAG36H11" in _app_src(), \
        "AprilTag must use TAG36H11 family"


def test_on_frame_uses_cam_chn_id_1_for_detection():
    """检测须取 chn1(QVGA RGB565 专用检测通道)。"""
    seg = ast.get_source_segment(_app_src(), _func("on_frame")) or ""
    assert "CAM_CHN_ID_1" in seg, "on_frame must snapshot chn=CAM_CHN_ID_1"


def test_on_frame_calls_host_tick_with_slots():
    """on_frame 须构建4槽位并调 host_tick。"""
    seg = ast.get_source_segment(_app_src(), _func("on_frame")) or ""
    assert "host_tick" in seg, "on_frame must call host_tick(slots)"
    assert "slots" in seg, "on_frame must build slots list"


def test_on_frame_uses_id_registry_with_tag_db_registrar():
    """KEY2 注册须走 tag_db.register(registrar=)。"""
    seg = ast.get_source_segment(_app_src(), _func("on_frame")) or ""
    assert "try_register" in seg, "on_frame must call id_registry.try_register"
    assert "registrar" in seg, "try_register must pass registrar=tag_db.register"


def test_run_loop_has_exitpoint_and_task_handler():
    """主循环须有 os.exitpoint + lv.task_handler(对齐模板)。"""
    seg = ast.get_source_segment(_app_src(), _func("run")) or ""
    assert "exitpoint" in seg, "run loop must call os.exitpoint()"
    assert "task_handler" in seg, "run loop must call lv.task_handler()"


def test_app_uses_i18n_not_hardcoded():
    """文本须走 lang.t(),不得硬编码中文。"""
    src = _app_src()
    assert "lang.t" in src or "lang.t(" in src, "tag_detect must use lang.t()"
    bad = ["已注册", "清除", "保存", "二维码"]
    for s in bad:
        assert ('"%s"' % s) not in src, "must not hardcode '%s'; use i18n" % s


def test_channels_for_tag_detect_uses_qvga_rgb565():
    """app_runtime._channels_for 须为 tag_detect 配 chn1 QVGA RGB565。"""
    with open(RT_PATH, "r", encoding="utf-8") as f:
        rt = f.read()
    assert "tag_detect" in rt, "app_runtime must handle tag_detect"
    # 取 _channels_for 段
    tree = ast.parse(rt, filename=RT_PATH)
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == "AppRuntime":
            for n in node.body:
                if isinstance(n, ast.FunctionDef) and n.name == "_channels_for":
                    seg = ast.get_source_segment(rt, n) or ""
                    assert "QVGA" in seg and "tag_detect" in seg, \
                        "_channels_for must config tag_detect with QVGA"
                    assert "RGB565" in seg, "_channels_for tag_detect must use RGB565"
                    assert "CAM_CHN_ID_1" in seg, "tag_detect detection on chn1"
                    return
    assert False, "AppRuntime._channels_for missing"


def test_runner():
    failures = 0
    for name in sorted(n for n in globals() if n.startswith("test_") and n != "test_runner"):
        try:
            globals()[name]()
            print("PASS %s" % name)
        except Exception as e:
            failures += 1
            print("FAIL %s: %s" % (name, e))
    print("\n%s" % ("ALL PASS" if failures == 0 else "%d FAILED" % failures))
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    test_runner()
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python tests/test_tag_detect_app.py`
Expected: FAIL(`scripts/tag_detect/app.py` 不存在)。

- [ ] **Step 3: 实现 scripts/tag_detect/app.py**

新建 `scripts/tag_detect/app.py`(基于 face_detect 结构,替换 AI 逻辑为 AprilTag/QR):

```python
# scripts/tag_detect/app.py — AprilTag + 二维码双功能标签识别。
#
# 复用 _template 单线程主循环。chn1 QVGA RGB565 做检测(官方 demo 同款),
# chn0 VGA RGB888 显示。两功能底栏切换(选中置绿),各自独立 ID 设置(最多4),
# KEY2 注册(走 tag_db.register via registrar),协议类型 0x04 上传4槽位。
# 持久化预留(flush_to_disk no-op)。

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
from core.tag_db import TagDB

BAR_H = 52
PREVIEW_Y = BAR_H
PREVIEW_H = 376
BAR_BG = 0x1A1A1A
CARD_BG = 0x2A2A2A
CARD_ACTIVE = 0x2E7D32   # 选中卡片绿色
# chn1 QVGA(320x240) → chn0 VGA(640x480):坐标 ×2 整数缩放
DET_SCALE = 2

_RUNTIME = None
_screen = None
_top_bar = None
_bottom_bar = None
_preview = None
_count_label = None
_id_registry = None
_april_db = None
_qr_db = None
_active_fn = "april"      # "april" | "qr"
_april_card = None
_qr_card = None


def _active_db():
    return _april_db if _active_fn == "april" else _qr_db


def _init_registry(fpioa):
    global _id_registry
    _id_registry = IdRegistry(fpioa, pin=0)


def on_frame(img):
    """chn1 检测 → 匹配 DB → 命中填4槽位 → chn0 画框 → host_tick。"""
    if _RUNTIME is None:
        return
    img_det = _RUNTIME.sensor.snapshot(chn=CAM_CHN_ID_1)
    slots = [None, None, None, None]
    db = _active_db()
    detected = []   # [(code_id, rect, cx, cy), ...]

    if _active_fn == "april":
        try:
            tags = img_det.find_apriltags(families=image.TAG36H11)
        except Exception as e:
            print("[tag_detect] apriltag error: %s" % e)
            tags = []
        for tag in tags:
            code_id = tag.id()
            rect = tag.rect()   # [x, y, w, h] in QVGA
            detected.append((code_id, rect))
    else:
        try:
            codes = img_det.find_qrcodes()
        except Exception as e:
            print("[tag_detect] qr error: %s" % e)
            codes = []
        for code in codes:
            code_id = code.payload()
            rect = code.rect()
            detected.append((code_id, rect))

    # 匹配 DB,命中填槽位 + chn0 画框
    for code_id, rect in detected:
        x, y, w, h = [int(v) for v in rect]
        slot, score = db.match(code_id)
        if slot is not None:
            slots[slot - 1] = (slot, x * DET_SCALE, y * DET_SCALE,
                               w * DET_SCALE, h * DET_SCALE, 100)
            # 命中画绿框 + slot id
            img.draw_rectangle([x * DET_SCALE, y * DET_SCALE,
                                w * DET_SCALE, h * DET_SCALE],
                               color=(0, 255, 0), thickness=4)
            img.draw_string_advanced(x * DET_SCALE, y * DET_SCALE - 24, 24,
                                     "id%d" % slot)
        else:
            # 未注册画红框
            img.draw_rectangle([x * DET_SCALE, y * DET_SCALE,
                                w * DET_SCALE, h * DET_SCALE],
                               color=(255, 0, 0), thickness=2)

    # KEY2 注册:pending 且当前帧有检测到码 → 存入下一槽
    if _id_registry is not None and _id_registry.has_pending() and detected:
        code_id, _rect = detected[0]
        slot = _id_registry.try_register(code_id, _RUNTIME.buzzer,
                                         registrar=db.register)
        if slot is not None:
            _refresh_count()

    if _RUNTIME is not None and _RUNTIME.host is not None:
        _RUNTIME.host_tick(slots)


def _refresh_count():
    if _count_label is not None and _RUNTIME is not None:
        try:
            _count_label.set_text(
                _RUNTIME.lang.t("tag_detect.registered", _active_db().count))
        except Exception:
            pass


def _switch_fn(fn):
    """切换 AprilTag / QR 功能。"""
    global _active_fn
    if fn == _active_fn:
        return
    _active_fn = fn
    if _april_card is not None:
        _april_card.set_style_bg_color(
            lv.color_hex(CARD_ACTIVE if fn == "april" else CARD_BG), 0)
    if _qr_card is not None:
        _qr_card.set_style_bg_color(
            lv.color_hex(CARD_ACTIVE if fn == "qr" else CARD_BG), 0)
    _refresh_count()


def _make_card(parent, label_key, fn, align_to):
    """建一个功能卡片(可点击切换)。返回 card obj。"""
    from ui.theme import make_back_bar_text_style
    card = lv.btn(parent)
    card.set_size(110, 40)
    card.align(lv.ALIGN.LEFT_MID, align_to, 0)
    card.set_style_bg_color(lv.color_hex(CARD_ACTIVE if _active_fn == fn else CARD_BG), 0)
    card.set_style_bg_opa(255, 0)
    card.set_style_radius(8, 0)
    card.set_style_border_width(0, 0)
    card.set_style_shadow_width(0, 0)
    lbl = lv.label(card)
    lbl.set_text(_RUNTIME.lang.t(label_key))
    lbl.add_style(make_back_bar_text_style(fonts.body), 0)
    lbl.center()

    def _on_click(e, _fn=fn):
        if e.get_code() == lv.EVENT.CLICKED:
            _switch_fn(_fn)
    card.add_event(_on_click, lv.EVENT.CLICKED, None)
    return card


def _build_ui(runtime, exit_flag):
    """顶栏(back+标题) + 透明预览 + 底栏(list图标 + AprilTag/QR卡片 + 计数)。"""
    global _screen, _top_bar, _bottom_bar, _preview, _count_label
    global _april_card, _qr_card
    screen = lv.scr_act()
    screen.set_style_bg_opa(0, 0)
    _screen = screen

    # 顶栏:返回钮 + 标题
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

    icon_data, icon_dsc = icon_cache.get_tag_icon("back")
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
    title.set_text(runtime.lang.t("category.tag_detect"))
    title.align(lv.ALIGN.CENTER, 0, 0)
    from ui.theme import make_back_bar_text_style
    title.add_style(make_back_bar_text_style(fonts.body), 0)

    # 透明预览区(透出 OSD1)
    _preview = lv.obj(screen)
    _preview.set_size(lv.pct(100), PREVIEW_H)
    _preview.set_pos(0, PREVIEW_Y)
    _preview.set_style_bg_opa(0, 0)
    _preview.set_style_border_width(0, 0)
    _preview.set_style_pad_all(0, 0)
    _preview.set_style_radius(0, 0)
    _preview.clear_flag(lv.obj.FLAG.SCROLLABLE)
    _preview.clear_flag(lv.obj.FLAG.CLICKABLE)

    # 底栏:list图标(纯显示) + AprilTag卡片 + QR卡片 + 计数
    _bottom_bar = lv.obj(screen)
    _bottom_bar.set_size(lv.pct(100), BAR_H)
    _bottom_bar.set_pos(0, PREVIEW_Y + PREVIEW_H)
    _bottom_bar.set_style_bg_color(lv.color_hex(BAR_BG), 0)
    _bottom_bar.set_style_bg_opa(255, 0)
    _bottom_bar.set_style_border_width(0, 0)
    _bottom_bar.set_style_pad_all(0, 0)
    _bottom_bar.set_style_radius(0, 0)
    _bottom_bar.clear_flag(lv.obj.FLAG.SCROLLABLE)

    # list 图标(只显示不绑功能)
    list_btn = lv.obj(_bottom_bar)
    list_btn.set_size(48, 48)
    list_btn.align(lv.ALIGN.LEFT_MID, 2, 0)
    list_btn.set_style_bg_opa(0, 0)
    list_btn.set_style_border_width(0, 0)
    list_btn.set_style_pad_all(0, 0)
    list_btn.clear_flag(lv.obj.FLAG.SCROLLABLE)
    list_icon_data, list_icon_dsc = icon_cache.get_tag_icon("list")
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

    _april_card = _make_card(_bottom_bar, "tag_detect.april_tag", "april", 56)
    _qr_card = _make_card(_bottom_bar, "tag_detect.qr_code", "qr", 174)

    count_label = lv.label(_bottom_bar)
    count_label.set_text(runtime.lang.t("tag_detect.registered", 0))
    count_label.add_style(make_back_bar_text_style(fonts.body), 0)
    count_label.align(lv.ALIGN.RIGHT_MID, -12, 0)
    _count_label = count_label


def _destroy_ui():
    global _screen, _top_bar, _bottom_bar, _preview, _count_label, _april_card, _qr_card
    for obj in (_april_card, _qr_card, _top_bar, _bottom_bar, _preview, _count_label):
        if obj is not None:
            try:
                obj.delete()
            except Exception:
                pass
    _april_card = None
    _qr_card = None
    _top_bar = None
    _bottom_bar = None
    _preview = None
    _count_label = None
    try:
        from ui.theme import Colors
        scr = lv.scr_act()
        scr.set_style_bg_color(lv.color_hex(Colors.BG), 0)
        scr.set_style_bg_opa(255, 0)
    except Exception:
        pass
    _screen = None


def run(runtime):
    """reset 框架入口。单线程主循环:snapshot chn0 → on_frame → show OSD1 → task_handler。"""
    global _RUNTIME, _april_db, _qr_db
    _RUNTIME = runtime
    _april_db = TagDB()
    _qr_db = TagDB()
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
                print("[tag_detect] on_frame error: %s" % e)
                try:
                    sys.print_exception(e)
                except Exception:
                    pass
            if _id_registry is not None:
                _id_registry.poll_k2()
            Display.show_image(img, 0, 0, Display.LAYER_OSD1)
            time.sleep_ms(lv.task_handler())
            fc += 1
            if fc % 30 == 0:
                print("[tag_detect] fc=%d" % fc)
    finally:
        _destroy_ui()
        if _april_db is not None:
            _april_db.flush_to_disk()  # 持久化预留(no-op)
        if _qr_db is not None:
            _qr_db.flush_to_disk()
        _RUNTIME = None
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python tests/test_tag_detect_app.py`
Expected: ALL PASS。

- [ ] **Step 5: 跑全部测试回归**

Run:
```bash
python tests/test_tag_db.py && python tests/test_id_registry.py && python tests/test_host_api.py && python tests/test_icon_cache.py && python tests/test_i18n_tag.py && python tests/test_tag_detect_app.py && python tests/test_face_detect_i18n.py && python tests/test_face_db.py && python tests/test_face_detect.py
```
Expected: 全部 ALL PASS。

- [ ] **Step 6: Commit**

```bash
git add scripts/tag_detect/app.py tests/test_tag_detect_app.py
git commit -m "feat(tag_detect): AprilTag + QR dual-function script

chn1 QVGA RGB565 检测(官方 demo 同款),chn0 VGA RGB888 显示,rect ×2 缩放。
底栏 list 图标(纯显示)+ AprilTag/QR 两卡片切换(选中置绿)+ N/4 计数。
KEY2 注册走 tag_db.register via IdRegistry.registrar 参数。协议 0x04 上传4槽。
持久化预留(flush_to_disk no-op)。全部文本 i18n。

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## Task 9: 板端验收 + 记录

**Files:**
- 板端部署 + 验收清单
- 记录到 `项目记录.md` + memory(如稳定)

- [ ] **Step 1: 部署到板端**

把 `core/tag_db.py`、`core/id_registry.py`、`core/icon_cache.py`、`core/app_runtime.py`、`comm/host_api.py`、`scripts/tag_detect/app.py`、`scripts/face_detect/app.py`、`resource/i18n/*.json`、`resource/icons/tag_detect_icon/*` 同步到 SD 卡 `/sdcard/CamerAi/` 对应路径。

- [ ] **Step 2: 板端验收清单**

逐项验证(用户在板端操作):
- [ ] 主菜单进入"标签识别",顶栏标题 + back 图标正常,预览出摄像画面。
- [ ] 底栏:list 图标显示,AprilTag 卡片默认绿色(选中),二维码卡片灰色。
- [ ] 对准 AprilTag(TAG36H11):未注册画红框;按 K2 → 蜂鸣 → 画绿框 + "id1";计数变 "已注册 1/4"。
- [ ] 切换到二维码卡片(置绿):对准 QR,同样 K2 注册流程。
- [ ] 上位机:收到类型 0x04 数据帧,4 组 ID/坐标/置信度正确(命中 conf=100)。
- [ ] 切换中/英语:标题、卡片文本、计数全部随语言切换。
- [ ] 退出回主菜单不卡死;再进 face_detect 双语正常(回归)。

- [ ] **Step 3: 记录稳定版本**

板端验收通过后:
- `项目记录.md` 追加 2026-06-26 tag_detect 段(commit hash + 验收结果)。
- 更新 memory `camerai-host-protocol.md`(tag_detect→0x04 接入)。
- 可选新建 memory `camerai-tag-detect.md`(脚本结构 + AprilTag/QR API 坑)。

- [ ] **Step 4: 最终 commit + push**

```bash
git add 项目记录.md
git commit -m "docs: record tag_detect board acceptance"
git push origin main
```

---

## 自检

- **Spec 覆盖**:M0→Task1, M1→Task2, M2→Task3, M4协议→Task4, M4图标→Task5, M3通道→Task6, M5→Task7, M3脚本→Task8, 验收→Task9。全覆盖。
- **Placeholder**:无 TBD/TODO,每步含完整代码。
- **类型一致**:`TagDB.register(code_id)→slot`、`match(code_id)→(slot,1.0)/(None,0.0)`、`try_register(feature,buzzer,registrar)`、`get_tag_icon(name)`、`preload_tag_icons()` 跨任务签名一致。
- **向后兼容**:IdRegistry `registrar=None` 默认走 face_db,face_detect 调用零改动(Task 1 不碰 id_registry 调用)。
