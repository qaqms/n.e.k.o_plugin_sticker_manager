"""有状态层：表情库文件存储（library）、发送链路（sender）、工具注册心跳（tool_watch）
与存在感注入（awareness）。"""

from .awareness import Awareness
from .lanlan import LanlanResolver, latest_lanlan
from .library import Library, LibraryResult
from .sender import Sender, SendResult
from .tool_watch import TOOL_WATCH_INTERVAL_SEC, ToolWatch, missing_tool_names

__all__ = [
    "Awareness",
    "LanlanResolver",
    "Library",
    "LibraryResult",
    "Sender",
    "SendResult",
    "TOOL_WATCH_INTERVAL_SEC",
    "ToolWatch",
    "latest_lanlan",
    "missing_tool_names",
]
