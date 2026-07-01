# core/object_classify_lock.py — 物体分类锁定/点选纯 Python 逻辑
#
# 锁定跟踪:点击某检测框 → 其特征存为 locked_feature;后续每帧在检测特征列表里
# 用余弦相似度找最匹配的那个作为锁定目标(score≥阈值才继续锁,否则丢失解锁)。
# 点选:屏幕触摸坐标(VGA 显示空间)→ 命中的检测框索引(最小包含矩形)。
#
# 纯 Python(复用 object_classify_db.cosine_score)→ host 端可单测。

from core.object_classify_db import cosine_score, OBJECT_CLASSIFY_MATCH_THRESHOLD


def select_lock_index(locked_feature, features, threshold=OBJECT_CLASSIFY_MATCH_THRESHOLD):
    """在检测特征列表 features 中找与 locked_feature 余弦最相似的。

    Args:
        locked_feature: 锁定特征(plain list)或 None。
        features: list[feature],本帧各检测框的特征(与 det_boxes 等长同序)。
        threshold: score=cos/2+0.5 的命中阈值(默认 0.75,同 DB)。
    Returns:
        (index, score):最匹配的特征在 features 中的索引与 score;无/低于阈值 → (None, 0.0)。
    """
    if not features or locked_feature is None:
        return None, 0.0
    best_i = None
    best_score = 0.0
    for i, f in enumerate(features):
        sc = cosine_score(locked_feature, f)
        if sc > best_score:
            best_score = sc
            best_i = i
    if best_score < threshold:
        return None, 0.0
    return best_i, best_score


def pick_box_at_point(boxes, px, py):
    """屏幕点 (px,py) 命中哪个检测框。

    Args:
        boxes: list of (x, y, w, h),显示空间(VGA 640×480)矩形。
        px, py: 触摸点(显示空间)。
    Returns:
        命中矩形中最小面积(最具体)者的索引;未命中 → None。
    """
    best_i = None
    best_area = None
    for i, (x, y, w, h) in enumerate(boxes):
        if x <= px <= x + w and y <= py <= y + h:
            a = w * h
            if best_area is None or a < best_area:
                best_area = a
                best_i = i
    return best_i
