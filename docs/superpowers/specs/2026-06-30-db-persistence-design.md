# 识别脚本注册数据持久化设计(子项目 A)

> 创建日期:2026-06-30
> 范围:face_db / tag_db / object_db / color_db 四个 DB 的 JSON 持久化(注册即写 + 安全读取)
> 不含:返回钮重构(子项目 B,另开)

## 背景

四个识别脚本(face/tag/object/color)的用户注册数据当前**内存-only**,重启丢失。各 DB 的 `flush_to_disk` / `load` 均为 no-op 预留。

用户反馈:之前运行脚本卡死,根因是某功能"每次启动 open 失败抛异常"——即坑#18 变体(open ENOENT 污染)。本次持久化**必须严防 open ENOENT**,所有读盘用 os.stat 预检查。

## 目标

- 注册数据写进 SD 卡,每次启动读取并匹配,重启后保留历史注册。
- KEY2 注册成功后**立即写盘**(注册即写)。
- 严防 open ENOENT 坑:所有读盘 os.stat 预检查,文件不存在跳过。
- 写盘在 on_frame 内、task_handler 之前(安全窗口,避坑#2)。

## 非目标

- 不改返回钮(子项目 B)。
- 不改注册/匹配/清除的内存逻辑(只加持久化层)。
- 不改 DB 的槽位语义(仍 1-4 轮转)。
- 不做注册即写的性能优化(单次注册才写,非每帧)。

## 存储

路径:`/sdcard/CamerAi/data/<db>.json`

| DB | 文件 | 内容 |
|---|---|---|
| face_db | `data/face_db.json` | `{"next_slot": N, "slots": {"1": [float,...], "2": [...]}}` |
| tag_db(AprilTag) | `data/tag_april.json` | `{"next_slot": N, "slots": {"1": code_id, ...}}` |
| tag_db(QR) | `data/tag_qr.json` | 同上 |
| object_db | `data/object_db.json` | `{"next_slot": N, "slots": {"1": class_id, ...}}` |
| color_db | `data/color_db.json` | `{"next_slot": N, "slots": {"1": {"threshold": [6值], "lab": [L,A,B], "rgb": N}, ...}}` |

face 特征向量(ulab 数组)序列化为 list[float],反序列化用 `ulab.numpy.array(list, dtype=ulab.numpy.float)` 转回。

tag/object/color 数据小,直接 JSON。

## 组件设计

### 每个 DB 新增/实现两个方法

```python
def load_from_disk(self, path):
    """启动加载。os.stat 预检查,文件不存在跳过(避 open ENOENT 坑#18)。"""
    try:
        os.stat(path)
    except Exception:
        return  # 文件不存在,空库
    try:
        with open(path, 'r') as f:
            data = json.load(f)
        # 反序列化到内存结构
        ...
    except Exception as e:
        print("[%s] load failed: %s" % (..., e))

def flush_to_disk(self, path):
    """注册即写。open(path,'w') 文件不存在会创建,不抛 ENOENT。"""
    try:
        with open(path, 'w') as f:
            json.dump(self._serialize(), f)
    except Exception as e:
        print("[%s] flush failed: %s" % (..., e))
```

### face_db 特殊处理

- `init_features()`(启动加载钩子)内调 `load_from_disk(FACE_DB_PATH)`。
- 特征 ulab 数组 ↔ list 转换:`feat.tolist()` / `ulab.numpy.array(lst, dtype=ulab.numpy.float)`。
- `_next_slot` 并入 JSON(取代独立 `.next_slot` 文件)。
- register 后调 `flush_to_disk`(注册即写)。原 exit 的 flush_to_disk 保留(兜底)。

### tag/object/color_db

- 各脚本 run() 开头(DB 实例化后、主循环前)调 `db.load_from_disk(path)`。
- on_frame 里 try_register 成功后调 `db.flush_to_disk(path)`(注册即写)。
- tag_detect 两个实例分别用 `tag_april.json` / `tag_qr.json`。

### 路径常量

各 DB 模块顶部定义:
```python
import os
_DB_PATH = "/sdcard/CamerAi/data/face_db.json"  # 各 DB 不同
```
`/sdcard/CamerAi/data/` 目录若不存在,首次 flush 时 `open("w")` 会自动建文件(目录需预存在或 os.mkdir)。MicroPython `open("w")` 不建目录,需确保 data 目录存在——启动时 `os.stat(data_dir)` 不存在则 `os.mkdir`。

## 数据流

```
启动: run() → db.load_from_disk(path)
  → os.stat(path) 不存在 → 空库(不 open,避 ENOENT)
  → os.stat 存在 → open read → json.load → 反序列化到内存

注册: on_frame → KEY2 → try_register → db.register(内存) → db.flush_to_disk(path)
  → open(path,'w') → json.dump(serialize)  [on_frame 内,task_handler 前,安全窗口]

清除: on_frame → list浮层 clear → db.clear(内存) → db.flush_to_disk(path)
  → 写空库 {"next_slot":1,"slots":{}}
```

## 错误处理与 K230 约束

- **红线:读盘必须 os.stat 预检查**(避 open ENOENT 坑#18)。这是本次任务的核心约束,用户之前卡死即此。
- 写盘 `open("w")` 不抛 ENOENT(文件不存在会创建),但目录不存在会抛——启动确保 data 目录存在。
- 写盘在 on_frame 内、task_handler 之前(坑#2 安全窗口)。
- 所有文件 I/O 包 try/except,失败打印不崩(注册数据丢失可接受,卡死不可接受)。
- face 特征反序列化失败(损坏/维度不符)→ 跳过该槽,不崩。
- 不在 LVGL 回调/flush_cb 内读写盘。

## 测试策略

### PC 侧单元测试(纯 Python,无 K230 依赖)

- `tests/test_face_db_persist.py`:flush_to_disk 写 JSON → load_from_disk 读回 → 特征向量 round-trip 一致(list↔ulab 转换)。需 stub ulab 或用纯 list 测试序列化逻辑。
- `tests/test_tag_db_persist.py` / `test_object_db_persist.py` / `test_color_db_persist.py`:round-trip 一致。
- `tests/test_db_persist_enoent.py`:load_from_disk 对不存在路径**不抛异常、不 open**(用 os.stat 预检查的契约)。

### 板端验证

1. 注册若干条 → 重启 → 启动加载 → 识别能匹配到已注册(重启不丢)。
2. clear → 重启 → 空库。
3. 首次运行(data 目录/文件不存在)→ 不卡死(os.stat 预检查生效)。
4. 滚动/挂机主菜单 GC 后仍不死(确认持久化未引入新 open ENOENT)。

## 验收标准

- 四个脚本注册数据重启后保留。
- 首次运行(data 文件不存在)不卡死。
- 注册即写,断电不丢(注册后立即写盘)。
- clear 后重启为空库。
- 主菜单 GC 后仍不死(无新增 open ENOENT 路径)。
- PC 侧 round-trip + ENOENT 测试全 PASS。

## 实施顺序

1. face_db 持久化(load/flush + ulab 转换 + init_features 接入)。
2. tag_db / object_db / color_db 持久化(结构简单,模式同 face)。
3. 各脚本 app.py 接入 load_from_disk(启动)+ flush_to_disk(注册即写)。
4. data 目录启动确保存在。
5. PC 测试 + 板端验证。
