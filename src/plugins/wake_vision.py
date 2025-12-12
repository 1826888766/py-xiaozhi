import asyncio
import time
from typing import Any, Optional

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
        self.ros_topic_name: str = "/handel_person"
        self.debug_draw: bool = False
        self._ros_mode: Optional[str] = None
        self._sub = None
        self._running: bool = False
        self._paused: bool = False
        self._loop_task: Optional[asyncio.Task] = None
        self._last_detect_time: float = 0.0
        self._detected_person: bool = False
        self._consecutive_hits: int = 0
        self._last_wake_time: float = 0.0
        self._main_loop: Optional[asyncio.AbstractEventLoop] = None

    async def setup(self, app: Any) -> None:
        self.app = app
        self._main_loop = asyncio.get_running_loop()
        self._ros_mode = self.app.ros_mode
        if not self.app.ros_ok:
            logger.warning(f"[{self.name}] ROS not initialized properly")
            return

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
        if self._ros_mode == "ros2":
            from sensor_msgs.msg import String
            def _ros2_person_callback(msg: String):
                self._last_detect_time = time.time()
                self._detected_person = msg.data == "person"

            self._sub = self.app.ros2_subscribe(
                msg_type=String,
                topic=self.ros_topic_name,
                callback=_ros2_person_callback,
            )
        if self._ros_mode == "ros1":
            import rospy
            from sensor_msgs.msg import String
            def _ros1_person_callback(msg: String):
                self._last_detect_time = time.time()
                self._detected_person = msg.data == "person"

            self._sub = rospy.Subscriber(
                self.ros_topic_name,
                String,
                _ros1_person_callback,
            )
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
