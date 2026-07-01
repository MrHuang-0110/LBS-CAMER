# Camera Protocol Remap Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move camera AI protocol types from gesture onward out of the host firmware's legacy-command conflict range: gesture=0x10, body=0x11, object_classify=0x12, image_classify=0x13.

**Architecture:** K230 remains the source of binary camera ID frames; only type constants change. STM32 host firmware updates its camera mode enum and protocol data-case dispatch so 0x10~0x13 enter the existing `refsh_camer()` path without touching legacy 0x08 version ACK or 0x09 link command handling.

**Tech Stack:** K230 MicroPython (`comm/host_api.py`, AST tests); STM32H723 C firmware (`camer.h`, `portagree.c`, Keil build); shared binary UART protocol.

---

## File Structure

### K230 project: `e:\LBS-CAMER-AI`

- Modify: `comm/host_api.py`
  - Change only type constants:
    - `TYPE_GESTURE_DETECT = 0x10`
    - `TYPE_BODY_DETECT = 0x11`
    - `TYPE_OBJECT_CLASSIFY = 0x12`
    - `TYPE_IMAGE_CLASSIFY = 0x13`
  - Keep `CATEGORY_TYPE` keys unchanged.
  - Keep the temporary low-frequency `[HostAPI] tick category=... msg_type=...` diagnostic print for board validation.
- Modify: `通讯协议.txt`
  - Update the protocol type table for gesture/body/object_classify/image_classify.
- Modify: `tests/test_host_api.py`
  - Add/extend AST assertions that the four constants use `0x10~0x13`.
- Modify: `tests/test_gesture_detect_ast.py`, `tests/test_body_detect_ast.py`, `tests/test_object_classify_detect_ast.py`
  - Update comments/expectations mentioning old protocol numbers to the new numbers.

### STM32 host project: `E:\LBS-NEW-AI`

- Modify: `Drivers/DataFile/camer/camer.h`
  - Give every camera mode enum an explicit numeric value.
  - Preserve 0x01~0x07; set gesture/body/object-body/photo to 0x10~0x13.
- Modify: `Drivers/DataFile/portAgree/portagree.c`
  - Add `case 0x11`, `case 0x12`, `case 0x13` to the sensor data dispatch case.
  - Keep existing `case 0x10` in the data dispatch case.
  - Keep existing `case 0x08` version ACK and `case DEV_PORT_LINKE` behavior unchanged.

---

### Task 1: K230 protocol constants and tests

**Files:**
- Modify: `e:\LBS-CAMER-AI\tests\test_host_api.py`
- Modify: `e:\LBS-CAMER-AI\comm\host_api.py`
- Modify: `e:\LBS-CAMER-AI\通讯协议.txt`
- Modify: `e:\LBS-CAMER-AI\tests\test_gesture_detect_ast.py`
- Modify: `e:\LBS-CAMER-AI\tests\test_body_detect_ast.py`
- Modify: `e:\LBS-CAMER-AI\tests\test_object_classify_detect_ast.py`

- [ ] **Step 1: Write the failing K230 constant test**

Append this test to `e:\LBS-CAMER-AI\tests\test_host_api.py` before `test_runner()`:

```python
def test_camera_ai_protocol_constants_remapped_to_0x10_range():
    """手势/人体/物体分类/图像分类必须避开主机旧命令 0x08/0x09/0x0A。"""
    src = _src()
    tree = ast.parse(src, filename=HOST_API_PATH)
    cls = _class_node(tree, "HostAPI")
    values = {}
    for n in cls.body:
        if isinstance(n, ast.Assign):
            for t in n.targets:
                if isinstance(t, ast.Name):
                    if t.id in ("TYPE_GESTURE_DETECT", "TYPE_BODY_DETECT",
                                "TYPE_OBJECT_CLASSIFY", "TYPE_IMAGE_CLASSIFY"):
                        assert isinstance(n.value, ast.Constant), "%s must be a literal" % t.id
                        values[t.id] = n.value.value
    assert values.get("TYPE_GESTURE_DETECT") == 0x10
    assert values.get("TYPE_BODY_DETECT") == 0x11
    assert values.get("TYPE_OBJECT_CLASSIFY") == 0x12
    assert values.get("TYPE_IMAGE_CLASSIFY") == 0x13
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
cd e:/LBS-CAMER-AI && python tests/test_host_api.py
```

Expected: FAIL on `test_camera_ai_protocol_constants_remapped_to_0x10_range`, because current constants are still `0x08/0x09/0x0A/0x0B`.

- [ ] **Step 3: Implement K230 constant remap**

In `e:\LBS-CAMER-AI\comm\host_api.py`, replace:

```python
    TYPE_GESTURE_DETECT = 0x08
    TYPE_BODY_DETECT    = 0x09
    TYPE_OBJECT_CLASSIFY = 0x0A
    TYPE_IMAGE_CLASSIFY  = 0x0B
```

with:

```python
    TYPE_GESTURE_DETECT = 0x10
    TYPE_BODY_DETECT    = 0x11
    TYPE_OBJECT_CLASSIFY = 0x12
    TYPE_IMAGE_CLASSIFY  = 0x13
```

Keep `CATEGORY_TYPE` keys unchanged.

- [ ] **Step 4: Update K230 protocol docs**

In `e:\LBS-CAMER-AI\通讯协议.txt`, replace the type table tail:

```text
道路识别:0x07
手势识别:0x08
人体识别:0x09
物体分类:0x0A
图像分类:0x0B
```

with:

```text
道路识别:0x07
手势识别:0x10
人体识别:0x11
物体分类:0x12
图像分类:0x13
```

- [ ] **Step 5: Update K230 AST test comments/strings**

In `e:\LBS-CAMER-AI\tests\test_gesture_detect_ast.py`, replace user-facing comment/assertion text containing `0x08` with `0x10`.

In `e:\LBS-CAMER-AI\tests\test_body_detect_ast.py`, replace user-facing comment/assertion text containing `0x09` with `0x11`.

In `e:\LBS-CAMER-AI\tests\test_object_classify_detect_ast.py`, replace user-facing comment/assertion text containing `0x0A` with `0x12`.

Do not change script behavior in these test files unless a test explicitly asserts the numeric value.

- [ ] **Step 6: Run K230 tests**

Run:

```bash
cd e:/LBS-CAMER-AI && python tests/test_host_api.py && python tests/test_gesture_detect_ast.py && python tests/test_body_detect_ast.py && python tests/test_object_classify_detect_ast.py
```

Expected: all tests pass.

---

### Task 2: STM32 host camera enum and data dispatch

**Files:**
- Modify: `E:\LBS-NEW-AI\Drivers\DataFile\camer\camer.h`
- Modify: `E:\LBS-NEW-AI\Drivers\DataFile\portAgree\portagree.c`

- [ ] **Step 1: Write minimal host-side static verification script**

Create temporary verification file `E:\LBS-NEW-AI\verify_camera_protocol_remap.py`:

```python
from pathlib import Path

root = Path(__file__).resolve().parent
camer_h = (root / "Drivers/DataFile/camer/camer.h").read_text(encoding="utf-8", errors="ignore")
portagree_c = (root / "Drivers/DataFile/portAgree/portagree.c").read_text(encoding="utf-8", errors="ignore")

required_enum = {
    "CAMER_MENU_TYPE = 0x01": "menu",
    "CAMER_MODE_TYPE = 0x02": "camera",
    "CAMER_FACE_TYPE = 0x03": "face",
    "CAMER_LABE_TYPE = 0x04": "label",
    "CAMER_OBJECT_TYPE = 0x05": "object",
    "CAMER_COLOR_TYPE = 0x06": "color",
    "CAMER_WAY_TYPE = 0x07": "road",
    "CAMER_GESTURE_TYPE = 0x10": "gesture",
    "CAMER_BODY_TYPE = 0x11": "body",
    "CAMER_OBJECT_BODY_TYPE = 0x12": "object classify",
    "CAMER_PHOTO_TYPE = 0x13": "image classify",
}
for snippet, name in required_enum.items():
    assert snippet in camer_h, "missing enum mapping for %s: %s" % (name, snippet)

for snippet in ("case 0x10:", "case 0x11:", "case 0x12:", "case 0x13:"):
    assert snippet in portagree_c, "missing data dispatch %s" % snippet

assert "case 0x08:" in portagree_c, "legacy version ACK case 0x08 must remain"
assert "case DEV_PORT_LINKE:" in portagree_c, "legacy link command 0x09 must remain"
print("PASS camera protocol remap verification")
```

- [ ] **Step 2: Run verification to confirm it fails**

Run:

```bash
cd E:/LBS-NEW-AI && python verify_camera_protocol_remap.py
```

Expected: FAIL, because `camer.h` still uses implicit enum values and `portagree.c` does not contain `case 0x11/0x12/0x13` in data dispatch.

- [ ] **Step 3: Implement explicit host camera enum values**

In `E:\LBS-NEW-AI\Drivers\DataFile\camer\camer.h`, replace:

```c
typedef enum{ 
  CAMER_MENU_TYPE = 1,
	CAMER_MODE_TYPE,
	CAMER_FACE_TYPE,
    CAMER_LABE_TYPE,
    CAMER_OBJECT_TYPE,
    CAMER_COLOR_TYPE,
    CAMER_WAY_TYPE,
    CAMER_GESTURE_TYPE,
    CAMER_BODY_TYPE,
    CAMER_OBJECT_BODY_TYPE,
    CAMER_PHOTO_TYPE
}CAMER_MODE;
```

with:

```c
typedef enum{ 
  CAMER_MENU_TYPE = 0x01,
	CAMER_MODE_TYPE = 0x02,
	CAMER_FACE_TYPE = 0x03,
    CAMER_LABE_TYPE = 0x04,
    CAMER_OBJECT_TYPE = 0x05,
    CAMER_COLOR_TYPE = 0x06,
    CAMER_WAY_TYPE = 0x07,
    CAMER_GESTURE_TYPE = 0x10,
    CAMER_BODY_TYPE = 0x11,
    CAMER_OBJECT_BODY_TYPE = 0x12,
    CAMER_PHOTO_TYPE = 0x13
}CAMER_MODE;
```

Preserve the file encoding and surrounding style.

- [ ] **Step 4: Implement host data dispatch cases**

In `E:\LBS-NEW-AI\Drivers\DataFile\portAgree\portagree.c`, replace the data dispatch case line:

```c
case 0x01:case 0x02:case 0x03:case 0x04:case 0x05:case 0x06:case 0x07:case 0x10:case 0x0C:case 0x0D:
```

with:

```c
case 0x01:case 0x02:case 0x03:case 0x04:case 0x05:case 0x06:case 0x07:
case 0x10:case 0x11:case 0x12:case 0x13:
case 0x0C:case 0x0D:
```

Do not remove or move:

```c
case DEV_PORT_LINKE:port_linke(portIndex,id,data);break;
```

Do not remove or move:

```c
case 0x08:
```

- [ ] **Step 5: Run host static verification**

Run:

```bash
cd E:/LBS-NEW-AI && python verify_camera_protocol_remap.py
```

Expected:

```text
PASS camera protocol remap verification
```

- [ ] **Step 6: Remove temporary verification script**

Delete `E:\LBS-NEW-AI\verify_camera_protocol_remap.py` after it passes. The repo has no test suite, so this script is a temporary TDD guard only.

---

### Task 3: Cross-project manual validation checklist

**Files:**
- No code changes.

- [ ] **Step 1: Deploy K230 file**

Copy:

```text
e:\LBS-CAMER-AI\comm\host_api.py
```

to board:

```text
/sdcard/CamerAi/comm/host_api.py
```

Reboot K230.

- [ ] **Step 2: Build and flash STM32 host firmware**

Open:

```text
E:\LBS-NEW-AI\MDK-ARM\STM32H723.uvprojx
```

Build target `STM32H723` in Keil and flash/update the controller using the project's normal process.

- [ ] **Step 3: Validate gesture detect**

Run `gesture_detect` on K230. Expected K230 serial diagnostic:

```text
[HostAPI] tick category=gesture_detect msg_type=0x10 slots=data
```

Expected host JSON includes camera mode `16`:

```json
{"port":4,"camer":{"mode":16,"id1":0}}
```

The JSON may contain more fields (`x/y/w/h/pp`, `id2~id4`); the required proof is `mode:16` instead of `mode:0`.

- [ ] **Step 4: Validate body detect**

Run `body_detect` on K230. Expected K230 serial diagnostic:

```text
[HostAPI] tick category=body_detect msg_type=0x11 slots=data
```

Expected host JSON includes `mode:17`.

- [ ] **Step 5: Validate object classify**

Run `object_classify` on K230. Expected K230 serial diagnostic:

```text
[HostAPI] tick category=object_classify msg_type=0x12 slots=data
```

Expected host JSON includes `mode:18`.

- [ ] **Step 6: Validate no regression for road detect**

Run `road_detect`. Expected host JSON still includes `mode:7` and still updates normally.

---

## Self-Review

- Spec coverage: Covers K230 constants/docs/tests, STM32 enum/dispatch, and deployment validation for gesture/body/object_classify/road regression.
- Placeholder scan: No TBD/TODO/fill-later placeholders.
- Type consistency: Uses `gesture=0x10`, `body=0x11`, `object_classify=0x12`, `image_classify=0x13` consistently in K230, STM32, tests, and docs.
- Scope note: This is one protocol compatibility change spanning two repositories. It is intentionally not split because both sides must change together to avoid an unusable intermediate protocol.
