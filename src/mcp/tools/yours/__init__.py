"""倒计时器MCP工具模块.

提供延迟执行命令的倒计时器功能，支持AI模型状态查询和反馈
"""

from .manager import get_yours_manager

# 开盖
__all__ = ["get_yours_manager","open_cover", "set_volume", "get_battery","ads_change"]
