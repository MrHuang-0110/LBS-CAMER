# core/ — 应用框架层
# 状态机、配置、多语言、事件总线、脚本调度、字体管理

from core.app import AppState, app_state
from core.config_manager import ConfigManager
from core.lang import LangManager
from core.event_bus import EventBus
from core.font_manager import FontManager
from core.plugin_loader import PluginLoader
from core.script_runner import ScriptRunner
