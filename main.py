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


def _read_next_script():
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

    fpioa = FPIOA()
    runtime = AppRuntime()
    runtime.init_menu(fpioa)

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
    menu.preload_icons()
    menu.show()
    print("[CamerAi] main menu running")
    while True:
        os.exitpoint()
        _th = lv.task_handler()
        time.sleep_ms(_th if _th > 0 else 5)


def run_script(category_id):
    from core.app_runtime import AppRuntime
    print("[CamerAi] run_script start: %s" % category_id)
    fpioa = FPIOA()
    runtime = AppRuntime()
    runtime.init_app(category_id, fpioa)
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
    _clear_next_script()
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
