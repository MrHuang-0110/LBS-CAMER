# core/status_hud.py — 顶栏状态小字格式化（纯 Python，host 可测）
#
# 2026-08-13 屏幕状态小字：顶栏右侧显示 `30fps 95C 3`（帧率/温度/目标数）。
# 字体约束：body 字体缺 温/目/°/· 等字符（用户选择不重建字体），故状态行
# 用纯 ASCII 通用单位（fps/°C/数字语言无关）；i18n 提供 status.format 模板
# （双语框架就位，语言切换时状态栏内容不变，系统其它文案照常切换）。
#
# 本模块只做格式化纯逻辑，LVGL label 创建与更新在脚本 _build_ui/run 循环。

DEFAULT_FORMAT = "{fps}fps {temp}C {n}"


def _fmt_int(v):
    """取整截断：None/非数 → 0，负 → 0。"""
    try:
        i = int(v)
    except Exception:
        return 0
    return i if i > 0 else 0


def status_text(lang, fps, temp, n):
    """格式化状态行。lang 提供 t("status.format") 模板；无模板/异常用默认。

    Args:
        lang: LangManager 实例（t(key) 接口），可为 None（用默认模板）
        fps: 帧率（float/int，取整）
        temp: 温度（float/int；None/读失败 → "?"）
        n: 目标数（int）
    Returns:
        str: 如 "30fps 95C 3"
    """
    fmt = DEFAULT_FORMAT
    if lang is not None:
        try:
            t = lang.t("status.format")
            if t:
                fmt = t
        except Exception:
            pass
    temp_s = "?" if temp is None else str(_fmt_int(temp))
    try:
        return fmt.format(fps=_fmt_int(fps), temp=temp_s, n=_fmt_int(n))
    except Exception:
        # 模板占位符异常兜底：回退默认模板
        return DEFAULT_FORMAT.format(fps=_fmt_int(fps), temp=temp_s, n=_fmt_int(n))
