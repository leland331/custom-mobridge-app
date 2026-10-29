from .connection import ConnectionScreen
from .configuration import ConfigurationScreen
from .dsp import DspScreen
from .firmware import FirmwareScreen
from .filesystem import FilesystemScreen
from .bus_tools import BusToolsScreen
from .debug_console import DebugConsoleScreen
from .diagnostics_health import DiagnosticsHealthScreen
from .script import ScriptScreen

__all__ = ['ConnectionScreen', 'ConfigurationScreen', 'DspScreen', 'FirmwareScreen',
           'FilesystemScreen', 'BusToolsScreen', 'DebugConsoleScreen',
           'DiagnosticsHealthScreen', 'ScriptScreen']
from .channel_map import ChannelMapScreen
