# CamerAi — 视觉 AI 触控终端
#
# reset 切换架构：main.py 是启动器（永不被覆写），读 .next_script 决定
# 跑主菜单还是目标脚本。点卡片写 .next_script + machine.reset()，脚本
# 进程独立 init（app_runtime），跑完清 .next_script + reset 回主菜单。
#
# 对齐官方综合例程 DemoScriptRunner（machine.reset 切换），用更安全的
# .next_script 标记替代覆写 main.py。
#
# 硬件平台：正点原子 K230D BOX（ST7701 640×480）

import lvgl as lv
import time
import os
import sys
from machine import FPIOA
import machine

_PROJECT_ROOT = "/sdcard/CamerAi"
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

NEXT_SCRIPT_PATH = "/sdcard/CamerAi/.next_script"
# 热启动标记：脚本返回主菜单前的 reset 写入此文件，run_menu 据此跳过开机 LOGO。
# 仅真正上电/断电重启时此文件不存在 → 显示 LOGO。避免每次从脚本返回都重显 LOGO。
WARM_BOOT_PATH = "/sdcard/CamerAi/.warm_boot"


def _read_next_script():
    """读 .next_script。用 os.stat 预检查避免 open() ENOENT 异常污染 K230 状态。

    ⚠️ 坑#18 变体(2026-06-30 定位):开机 main() 调本函数,open() 对不存在的
    .next_script 抛 ENOENT 异常,污染 K230 FATFS/全局状态,后续 Display/MediaManager/
    LVGL 在 GC 后撞上污染状态卡死(主菜单 GC 后死机的真根因)。probe 当 main.py 直跑
    不死,正是因为它不读 .next_script、没有这次 open 异常。

    解法(同坑#18):os.stat 预检查,文件不存在直接返回 None,不触发 open 异常。
    """
    try:
        os.stat(NEXT_SCRIPT_PATH)
    except Exception:
        return None
    try:
        with open(NEXT_SCRIPT_PATH, "r") as f:
            cid = f.read().strip()
        return cid if cid else None
    except Exception:
        return None


def _write_next_script(category_id):
    try:
        with open(NEXT_SCRIPT_PATH, "w") as f:
            f.write(category_id)
    except Exception as e:
        print("[CamerAi] write .next_script failed: %s" % e)


def _clear_next_script():
    try:
        os.remove(NEXT_SCRIPT_PATH)
    except Exception:
        pass


def _on_remote_switch(category, option=None):
    """主机远程切换脚本回调(HostAPI 解析命令帧后调用)。

    category=None → 回主菜单(清 .next_script + reset)。
    category=str  → 进对应脚本(写 .next_script + reset)。
    option 非 None → 标签识别快捷切换(mode 0x14/0x15):先把子功能写入
    .tag_fn(tag_detect 启动时按此记忆启动),再按原链路切脚本。
    复用菜单点击路径(_write_next_script + machine.reset),与本地点击一致。
    """
    print("[CamerAi] remote switch -> %s" % ("main_menu" if category is None else category))
    if option is not None:
        try:
            from core.tag_mode import write_tag_fn
            write_tag_fn(option)
            print("[CamerAi] tag fn -> %s" % option)
        except Exception as e:
            print("[CamerAi] write .tag_fn failed: %s" % e)
    if category is None:
        _clear_next_script()
    else:
        _write_next_script(category)
    machine.reset()


def _is_warm_boot():
    """是否热启动(reset 回菜单,非上电)。读取并删除标记(一次性消费)。"""
    try:
        os.stat(WARM_BOOT_PATH)
        os.remove(WARM_BOOT_PATH)
        return True
    except Exception:
        return False


def _mark_warm_boot():
    """脚本返回主菜单前调用,标记本次 reset 为热启动(跳过开机 LOGO)。"""
    try:
        with open(WARM_BOOT_PATH, "w") as f:
            f.write("1")
    except Exception as e:
        print("[CamerAi] write .warm_boot failed: %s" % e)


def _load_script(category_id):
    from core.config_manager import ConfigManager
    config = ConfigManager()
    config.load()
    cat = config.get_category(category_id)
    if cat is None:
        print("[CamerAi] category not found: %s" % category_id)
        return None
    script_name = cat.get("script", category_id)
    try:
        mod = __import__("scripts.%s.app" % script_name, None, None, ["run"])
        return mod
    except Exception as e:
        print("[CamerAi] load script %s failed: %s" % (script_name, e))
        import sys as _sys
        _sys.print_exception(e)
        return None


def run_menu():
    from core.app_runtime import AppRuntime
    from ui.main_menu import MainMenu
    from ui.boot_splash import BootSplash

    fpioa = FPIOA()
    runtime = AppRuntime()
    runtime.init_menu(fpioa)

    # 注册主机远程切换脚本回调(主菜单态也允许被远程切走)
    if runtime.host is not None:
        runtime.host.register_switch_handler(_on_remote_switch)

    # 显式设默认屏幕纯黑背景 + radius0 + border0：消除 LVGL 默认主题四角白点
    # （BootSplash 原用全屏纯黑 obj 覆盖默认 screen；缺失后默认样式四角露白）。
    _scr = lv.scr_act()
    _scr.set_style_bg_color(lv.color_hex(0x000000), 0)
    _scr.set_style_bg_opa(255, 0)
    _scr.set_style_border_width(0, 0)
    _scr.set_style_radius(0, 0)

    def on_card_click(category_id):
        print("[CamerAi] launch: %s" % category_id)
        if runtime.buzzer is not None:
            runtime.buzzer.beep(ms=50)
        _write_next_script(category_id)
        # 校验标记确实写入（排查 reset 后是否真的进脚本模式）
        try:
            with open(NEXT_SCRIPT_PATH, "r") as _f:
                print("[CamerAi] .next_script=%r, resetting..." % _f.read())
        except Exception as _e:
            print("[CamerAi] verify .next_script failed: %s" % _e)
        machine.reset()

    menu = MainMenu(runtime.config, runtime.buzzer, runtime.lang,
                    on_card_click=on_card_click)
    # preload_icons 必须在 BootSplash 之前：首次 task_handler 前的文件 I/O 安全窗口
    menu.preload_icons()
    # 开机 LOGO：仅真正上电/断电重启时显示（.warm_boot 不存在）。
    # 从脚本返回主菜单是热启动(reset)，run_script 末尾已写 .warm_boot → 跳过 LOGO。
    if not _is_warm_boot():
        # BootSplash 内部 open logo 在首次 task_handler 前，安全；阻塞显示后清理
        BootSplash(runtime.buzzer).show()
    menu.show()
    print("[CamerAi] main menu running")
    while True:
        try:
            os.exitpoint()
            _th = lv.task_handler()
            menu.diag_after_task_handler()
            menu.tick()  # 延迟吸附到期检查(连续滑动时手势优先,松手后才吸附)
            runtime.host_tick()
            time.sleep_ms(_th if _th > 0 else 5)
        except Exception as e:
            print("[CamerAi] run_menu loop error: %s" % e)
            import sys as _sys
            _sys.print_exception(e)
            raise  # 外层 main() 的 BaseException 兜底




def run_script(category_id):
    from core.app_runtime import AppRuntime
    print("[CamerAi] run_script start: %s" % category_id)
    fpioa = FPIOA()
    runtime = AppRuntime()
    runtime.init_app(category_id, fpioa)
    # 注册主机远程切换脚本回调(脚本态允许被远程切走/回菜单)
    if runtime.host is not None:
        runtime.host.register_switch_handler(_on_remote_switch)
    print("[CamerAi] loading script module...")
    mod = _load_script(category_id)
    print("[CamerAi] script module=%s has_run=%s" % (mod is not None,
                                                     hasattr(mod, "run") if mod else False))
    if mod is not None and hasattr(mod, "run"):
        try:
            print("[CamerAi] calling mod.run(runtime)...")
            mod.run(runtime)
        except Exception as e:
            print("[CamerAi] script run error: %s" % e)
            import sys as _sys
            _sys.print_exception(e)
    else:
        print("[CamerAi] script has no run(): %s" % category_id)
    try:
        runtime.cleanup()
    except Exception as e:
        print("[CamerAi] cleanup error: %s" % e)
    _clear_next_script()
    # 标记热启动：reset 回菜单后 run_menu 据此跳过开机 LOGO（仅上电才显 LOGO）。
    _mark_warm_boot()
    machine.reset()


def main():
    print("=" * 40)
    print("  CamerAi v0.2.0 (reset-switch)")
    print("=" * 40)

    next_script = _read_next_script()
    if next_script:
        print("[CamerAi] script mode: %s" % next_script)
        run_script(next_script)
    else:
        print("[CamerAi] menu mode")
        run_menu()


try:
    main()
except BaseException as e:
    print("[CamerAi] fatal: %s" % e)
    import sys
    sys.print_exception(e)
    try:
        os.remove(NEXT_SCRIPT_PATH)
    except Exception:
        pass
finally:
    print("[CamerAi] shutdown")
