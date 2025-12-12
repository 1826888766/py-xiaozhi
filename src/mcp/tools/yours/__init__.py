"""倒计时器MCP工具模块.

提供延迟执行命令的倒计时器功能，支持AI模型状态查询和反馈
"""

from .manager import get_yours_manager

from .tools import (
    open_lid,
    get_battery,
    ads_change,
    robot_opertion,
    set_volume
)
# 开盖
__all__ = ["get_yours_manager","robot_opertion","open_lid", "set_volume", "get_battery","ads_change"]
