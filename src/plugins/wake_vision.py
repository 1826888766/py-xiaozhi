import asyncio
import json
import time
import threading
from typing import Any

from src.constants.constants import AbortReason
from src.plugins.base import Plugin
from src.utils.logging_config import get_logger

logger = get_logger(__name__)


class WakeVisionPlugin(Plugin):
    name = "wake_vision"
    priority = 30  # 依赖 AudioPlugin

    def __init__(self) -> None:
        super().__init__()
        self.app = None
        self.detector = None
        self.min_consecutive_hits: int = 2
        self.cooldown_sec: float = 0.5
        self.no_person_timeout: float = 0.5
        self.ros_auto_init_node: bool = True
        self.ros_node_name: str = "visual_wake_dbg"
        self.ros_topic_name: str = "/handel_person"
        self.debug_draw: bool = False
        self._ros_ok: bool = False
        self._ros_mode: str | None = None
        self._sub = None
        self._rcl_node = None
        self._rcl_executor = None
        self._rcl_thread: threading.Thread | None = None
        self._rclpy_inited: bool = False
        self._running: bool = False
        self._paused: bool = False
        self._loop_task: asyncio.Task | None = None
        self._last_detect_time: float = 0.0
        self._detected_person: bool = False
        self._consecutive_hits: int = 0
        self._last_wake_time: float = 0.0
        self._main_loop: asyncio.AbstractEventLoop | None = None

    async def setup(self, app: Any) -> None:
        self.app = app
        self._main_loop = asyncio.get_running_loop()
        try:
            import rospy
            from std_msgs.msg import String as ROS1String

            self._ros_mode = "ros1"
            if not rospy.core.is_initialized() and self.ros_auto_init_node:
                rospy.init_node(self.ros_node_name, anonymous=True, disable_signals=True)
                logger.info(f"[{self.name}] auto-initialized rospy node: {self.ros_node_name}")

            def _on_person_msg_ros1(msg: ROS1String):
                try:
                    _ = json.loads(msg.data)
                    if self._main_loop and self._main_loop.is_running():
                        self._main_loop.call_soon_threadsafe(self._mark_detected)
                    else:
                        self._mark_detected()
                except Exception as e:
                    logger.warning(f"[{self.name}] invalid JSON in {self.ros_topic_name}: {e}")

            self._sub = rospy.Subscriber(self.ros_topic_name, ROS1String, _on_person_msg_ros1)
            self._ros_ok = True
            logger.info(f"[{self.name}] subscribed to {self.ros_topic_name}")
            return
        except ImportError:
            pass
        except Exception as e:
            logger.error(f"[{self.name}] ROS1 setup error: {e}", exc_info=True)

        try:
            import rclpy
            from rclpy.node import Node
            from std_msgs.msg import String as ROS2String
            from rclpy.executors import SingleThreadedExecutor

            self._ros_mode = "ros2"
            if not rclpy.utilities.is_initialized():
                rclpy.init()
                self._rclpy_inited = True

            self._rcl_node = Node(self.ros_node_name)

            def _on_person_msg_ros2(msg: ROS2String):
                try:
                    _ = json.loads(msg.data)
                    if self._main_loop and self._main_loop.is_running():
                        self._main_loop.call_soon_threadsafe(self._mark_detected)
                    else:
                        self._mark_detected()
                except Exception as e:
                    logger.warning(f"[{self.name}] invalid JSON in {self.ros_topic_name}: {e}")

            self._sub = self._rcl_node.create_subscription(ROS2String, self.ros_topic_name, _on_person_msg_ros2, 10)
            self._rcl_executor = SingleThreadedExecutor()
            self._rcl_executor.add_node(self._rcl_node)

            def _spin():
                try:
                    self._rcl_executor.spin()
                except Exception as e:
                    logger.error(f"[{self.name}] ROS2 executor error: {e}")

            self._rcl_thread = threading.Thread(target=_spin, name=f"{self.name}-ros2-spin", daemon=True)
            self._rcl_thread.start()

            self._ros_ok = True
            logger.info(f"[{self.name}] subscribed to {self.ros_topic_name}")
        except ImportError as e:
            pass
        except Exception as e:
            logger.error(f"[{self.name}] ROS2 setup error: {e}", exc_info=True)

    async def start(self) -> None:
        if not self._ros_ok:
            raise RuntimeError(f"[{self.name}] ROS not initialized properly")

        if self._running:
            logger.warning(f"[{self.name}] already running")
            return

        self._consecutive_hits = 0
        self._last_wake_time = 0.0
        self._running = True
        self._paused = False
        self._loop_task = asyncio.create_task(self._run_loop())
        logger.info(f"[{self.name}] started")
    
    async def _run_loop(self) -> None:
        try:
            while self._running:
                if self._paused:
                    await asyncio.sleep(0.1)
                    continue

                now = time.time()
                if now - self._last_detect_time > self.no_person_timeout:
                    self._detected_person = False

                if self._detected_person:
                    self._consecutive_hits += 1
                else:
                    self._consecutive_hits = 0

                # 判断是否触发 wake
                if (
                    self._detected_person
                    and self._consecutive_hits >= self.min_consecutive_hits
                    and (now - self._last_wake_time) > self.cooldown_sec
                ):
                    self._last_wake_time = now
                    self._consecutive_hits = 0
                    await self._fire_wake()

                await asyncio.sleep(0.1)

        except asyncio.CancelledError:
            logger.info(f"[{self.name}] loop cancelled")
        except Exception as e:
            logger.exception(f"[{self.name}] loop error: {e}")

    async def stop(self) -> None:
        self._running = False
        if self._loop_task:
            await asyncio.sleep(0)
            try:
                self._loop_task.cancel()
            except Exception:
                pass
            self._loop_task = None
        logger.info(f"[{self.name}] stopped")

    async def shutdown(self) -> None:
        try:
            if self._ros_mode == "ros1" and self._sub is not None:
                try:
                    self._sub.unregister()
                except Exception as e:
                    logger.warning(f"[{self.name}] ros1 unsubscribe error: {e}")
            if self._ros_mode == "ros2":
                try:
                    if self._rcl_executor:
                        try:
                            self._rcl_executor.shutdown()
                        except Exception:
                            pass
                    if self._rcl_node and self._sub is not None:
                        try:
                            self._rcl_node.destroy_subscription(self._sub)
                        except Exception:
                            pass
                    if self._rclpy_inited:
                        try:
                            import rclpy
                            rclpy.shutdown()
                        except Exception:
                            pass
                except Exception as e:
                    logger.warning(f"[{self.name}] ros2 shutdown error: {e}")
        except Exception:
            pass

    async def _on_detected(self, wake_word, full_text):
        # 检测到唤醒词：切到自动对话（根据 AEC 自动选择实时/自动停）
        try:
            # 若正在说话，交给应用的打断/状态机处理
            if hasattr(self.app, "device_state") and hasattr(
                self.app, "start_auto_conversation"
            ):
                if self.app.is_speaking():
                    await self.app.abort_speaking(AbortReason.WAKE_WORD_DETECTED)
                    audio_plugin = self.app.plugins.get_plugin("audio")
                    if audio_plugin and audio_plugin.codec:
                        await audio_plugin.codec.clear_audio_queue()
                else:
                    await self.app.start_auto_conversation()
        except Exception as e:
            logger.error(f"处理唤醒词检测失败: {e}", exc_info=True)

    async def _fire_wake(self) -> None:
        try:
            await self._on_detected(None, "")
        except Exception as e:
            logger.error(f"[{self.name}] fire wake error: {e}")

    def _mark_detected(self) -> None:
        self._last_detect_time = time.time()
        self._detected_person = True

    def _on_error(self, error):
        try:
            logger.error(f"唤醒词检测错误: {error}")
            if hasattr(self.app, "set_chat_message"):
                self.app.set_chat_message("assistant", f"[唤醒词错误] {error}")
        except Exception as e:
            logger.error(f"处理唤醒词错误回调失败: {e}")
