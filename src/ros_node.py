import threading
from src.utils.logging_config import get_logger
logger = get_logger(__name__)
class RosNode:
    _instance = None
    _lock = threading.Lock()

    @classmethod
    def get_instance(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = RosNode()
        return cls._instance
    def __init__(self):
        if RosNode._instance is not None:
            logger.error("尝试创建RosNode的多个实例")
            raise Exception("RosNode是单例类，请使用get_instance()获取实例")
        RosNode._instance = self
        self.ros_ok = False
        self.ros_mode = None
        self.ros_node_name = "yours_ai"

    def _setup_ros(self) -> None:
        logger.info(f"initializing rospy node: {self.ros_node_name}")

        try:
            import rospy
            self.ros_mode = "ros1"
            rospy.init_node(self.ros_node_name, disable_signals=True)
            logger.info(f"auto-initialized rospy node: {self.ros_node_name}")
            self.ros_ok = True
            return
        except ImportError:
            pass
        except Exception as e:
            logger.error(f"ROS1 setup error: {e}", exc_info=True)

        try:
            import rclpy
            from rclpy.node import Node
            from rclpy.executors import SingleThreadedExecutor

            self.ros_mode = "ros2"
            if not rclpy.utilities.is_initialized():
                rclpy.init()
                self._rclpy_inited = True

            self._rcl_node = Node(self.ros_node_name)
            self._rcl_executor = SingleThreadedExecutor()
            self._rcl_executor.add_node(self._rcl_node)

            self._rcl_thread = threading.Thread(target=self._rcl_executor.spin, name=f"{self.ros_node_name}-ros2-spin", daemon=True)
            self._rcl_thread.start()

            self.ros_ok = True
        except ImportError as e:
            pass
        except Exception as e:
            logger.error(f"ROS2 setup error: {e}", exc_info=True)