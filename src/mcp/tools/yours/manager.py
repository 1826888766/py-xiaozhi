"""小车管理器.

负责小车的初始化、配置和MCP工具注册
"""

from typing import Any, Dict

from src.utils.logging_config import get_logger

from .tools import (
    open_lid,
    get_battery,
    ads_change,
    robot_opertion,
    set_volume,
    get_audio_play
)

logger = get_logger(__name__)


class YoursToolManager:
    """
    小车管理器.
    """

    def __init__(self):
        """
        初始化小车管理器.
        """
        self._initialized = False
        logger.info("[YoursManager] 小车管理器初始化")

    def init_tools(self, add_tool, PropertyList, Property, PropertyType):
        """
        初始化并注册所有小车.
        """
        try:
            logger.info("[YoursManager] 开始注册小车")

            # 注册开盖工具
            self._register_open_cover_tool(
                add_tool, PropertyList, Property, PropertyType
            )

            # 注册电池工具
            self._register_battery_tool(
                add_tool, PropertyList, Property, PropertyType
            )

            # 注册广告工具
            self._register_ads_tool(
                add_tool, PropertyList, Property, PropertyType
            )

            # 注册音量工具
            self._register_volume_tool(
                add_tool, PropertyList, Property, PropertyType
            )

            # 注册机器人操作工具
            self._register_robot_opertion_tool(
                add_tool, PropertyList, Property, PropertyType
            )

            # 注册音频播放工具
            self._register_audio_play_tool(
                add_tool, PropertyList, Property, PropertyType
            )

            self._initialized = True
            logger.info("[YoursManager] 小车注册完成")

        except Exception as e:
            logger.error(f"[YoursManager] 小车注册失败: {e}", exc_info=True)
            raise

    def _register_audio_play_tool(
        self, add_tool, PropertyList, Property, PropertyType
    ):
        """
        注册音频播放工具.
        """
        audio_play_props = PropertyList()
        add_tool(
            (
                "yours.get_audio_play",
                "Play audio task. 按照返回的内容回复，不可再次封装",
                audio_play_props,
                get_audio_play
            )
        )
        logger.debug("[YoursManager] 注册音频播放工具成功")

    def _register_open_cover_tool(
        self, add_tool, PropertyList, Property, PropertyType
    ):
        """
        注册开盖工具.
        """

        add_tool(
            (
                "yours.open_lid",
                "Open lid",
                PropertyList(),
                open_lid
            )
        )
        logger.debug("[YoursManager] 注册开盖工具成功")
    
    def _register_battery_tool(
        self, add_tool, PropertyList, Property, PropertyType
    ):
        """
        注册电池工具.
        """
        add_tool(
            (
                "yours.get_battery",
                "Get the battery information of the car. ",
                PropertyList(),
                get_battery
            )
        )
        logger.debug("[YoursManager] 注册电池工具成功")
    def _register_volume_tool(
        self, add_tool, PropertyList, Property, PropertyType
    ):
        """
        注册音量工具.
        """
        volume_props = PropertyList(
            [
                Property("volume", PropertyType.INTEGER),
                Property("plus", PropertyType.BOOLEAN),
                Property("minus", PropertyType.BOOLEAN),
            ]
        )
        add_tool(
            (
                "yours.set_volume",
                "Set the volume of the car. 设置音量为指定值 增大音量 减小音量. "
                "params: "
                "1 volume: (10,150] "
                "2 plus: increase volume "
                "3 minus: decrease volume ",
                volume_props,
                set_volume
            )
        )
        logger.debug("[YoursManager] 注册音量工具成功")
        
    def _register_ads_tool(
        self, add_tool, PropertyList, Property, PropertyType
    ):
        """
        注册广告工具.
        """
        add_tool(
            (
                "yours.ads_change",
                "Change the ad of the car. ",
                PropertyList(),
                ads_change
            )
        )
        logger.debug("[YoursManager] 注册广告工具成功")
    
    def _register_robot_opertion_tool(
        self, add_tool, PropertyList, Property, PropertyType
    ):
        """
        注册机器人操作工具.
        """
        robot_props = PropertyList(
            [
                Property("types", PropertyType.STRING),
            ]
        )
        add_tool(
            (
                "yours.robot_opertion",
                "Operate the robot. "
                "params: 1 types: pause | resume ",
                robot_props,
                robot_opertion
            )
        )
        logger.debug("[YoursManager] 注册机器人操作工具成功")

# 全局管理器实例
_yours_tools_manager = None


def get_yours_manager() -> YoursToolManager:
    """
    获取小车管理器单例.
    """
    global _yours_tools_manager
    if _yours_tools_manager is None:
        _yours_tools_manager = YoursToolManager()
        logger.debug("[YoursManager] 创建小车工具管理器实例")
    return _yours_tools_manager
