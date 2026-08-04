# core/tag_scan.py — 标签检测结果排序/截断/id 映射（纯 Python，无板端依赖）
#
# tag_detect 每帧全屏扫描后调用:排序(最左=目标1) → 截断 25 → id 编码 → 坐标缩放。
# 纯内置类型,host 可真单元测试。MAX_SLOTS 与 comm/host_api.MAX_ID_SLOTS 对齐。

MAX_SLOTS = 25


def build_slots(detected, qr_mode=False, scale=2, return_codes=False):
    """把检测列表转为上传槽位列表。

    Args:
        detected: list[(code_id, x, y, w, h)] — code_id 为 int(AprilTag) 或
                  str(QR payload);坐标均为检测通道(QVGA)整数。
        qr_mode: True=QR 功能(id 取排序后序号 1..N);False=AprilTag(id 取码值)。
        scale: 坐标缩放系数(QVGA→VGA 用 2)。
        return_codes: True 时返回 (slots, codes),codes 为与 slots 同序的
                  原始 code_id(排序后未截断,AprilTag=码值/QR=payload,
                  供屏幕显示;避免调用方按未排序 detected 取 ID 造成错位)。

    Returns:
        list[(id_val, x*scale, y*scale, w*scale, h*scale, 100)]:
        按 (x, y) 升序(最左边=目标1),截断前 MAX_SLOTS 个。
        AprilTag 码值 >255 固定输出 255(协议 id 字段 1 字节)。
        return_codes=True 时返回 (slots, codes) 元组。
    """
    ordered = sorted(detected, key=lambda t: (t[1], t[2]))
    ordered = ordered[:MAX_SLOTS]
    slots = []
    codes = []
    for i, item in enumerate(ordered):
        code_id = item[0]
        codes.append(code_id)
        x, y, w, h = item[1], item[2], item[3], item[4]
        if qr_mode:
            id_val = i + 1
        else:
            id_val = code_id if code_id <= 255 else 255
        slots.append((id_val, x * scale, y * scale, w * scale, h * scale, 100))
    if return_codes:
        return slots, codes
    return slots
