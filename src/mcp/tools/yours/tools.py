"""小车MCP工具函数.

提供给MCP服务器调用的异步工具函数（优化版：去除阻塞，适配asyncio）。
- 不在协程里调用阻塞型rospy.sleep / wait_for_message / requests
- 用 threading.Event 与ROS回调解耦，并用 asyncio.to_thread 在后台线程等待
- TTS与HTTP上报均使用 fire-and-forget，不阻塞事件循环
"""

from __future__ import annotations

import asyncio
import json
import threading
from typing import Any, Dict, Optional

import aiohttp
from std_msgs.msg import UInt32, String
from yours_ai.utils.logging_config import get_logger

logger = get_logger(__name__)

# -----------------------------
# 线程安全的共享状态 & 事件
# -----------------------------
_open_status_lock = threading.Lock()
open_status: Optional[int] = None
is_whait_open: bool = False  # 保持你的原字段名，避免外部依赖坏掉

_open_event = threading.Event()     # 盖子打开事件（open_status==0）
_motion_event = threading.Event()   # 任务状态反馈事件（/scheduled_tasks/ctrl_status）
_motion_success: bool = False       # 任务是否成功


# -----------------------------
# 工具：无阻塞地调度协程（无事件循环则开线程跑）
# -----------------------------


def _fire_and_forget(coro: asyncio.coroutines) -> None:
    """在当前事件循环中调度协程；若无事件循环则新开线程执行 asyncio.run。"""
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(coro)
    except RuntimeError:
        threading.Thread(target=lambda: asyncio.run(coro), daemon=True).start()


def _notify(message: str) -> None:
    """执行TTS播报（fire-and-forget，不阻塞）。"""
    from yours_ai.application import Application

    app = Application.get_instance()
    if not getattr(app, "running", False):
        return
    _log_info(f"TTS: {message}")
    _fire_and_forget(app._send_text_tts(message))


# -----------------------------
# ROS 兼容层与回调
# -----------------------------

def _create_publisher(topic: str, msg_type: Any):
    app = Application.get_instance()
    if not app.ros_ok:
        return None
    if app.ros_mode == "ros1":
        import rospy
        return rospy.Publisher(topic, msg_type, queue_size=1)
    if app.ros_mode == "ros2":
        import rclpy
        _QoSProfile = rclpy.qos.QoSProfile if rclpy.qos is not None else None
        qos = _QoSProfile(depth=10) if _QoSProfile is not None else 10

        _ros2_node = app.get_ros_node()
        return _ros2_node.create_publisher(msg_type, topic, qos)
    return None


def _create_subscription(topic: str, msg_type: Any, callback):
    app = Application.get_instance()
    if not app.ros_ok:
        return None
    if app.ros_mode == "ros1":
        import rospy
        return rospy.Subscriber(topic, msg_type, callback)
    if app.ros_mode == "ros2":
        import rclpy
        _QoSProfile = rclpy.qos.QoSProfile if rclpy.qos is not None else None
        qos = _QoSProfile(depth=10) if _QoSProfile is not None else 10

        _ros2_node = app.get_ros_node()
        return _ros2_node.create_subscription(msg_type, topic, callback, qos)
    return None


async def _wait_for_message(topic: str, msg_type: Any, timeout: Optional[float] = None):
    app = Application.get_instance()
    if not app.ros_ok:
        raise RuntimeError("ROS unavailable")
    if app.ros_mode == "ros1":
        import rospy
        return await asyncio.to_thread(rospy.wait_for_message, topic, msg_type, timeout)
    if app.ros_mode == "ros2":
        _ros2_node = app.get_ros_node()
        event = threading.Event()
        holder: Dict[str, Any] = {"msg": None}

    def _cb(msg):
        holder["msg"] = msg
        event.set()
    sub = _create_subscription(topic, msg_type, _cb)
    try:
        if timeout is not None:
            got = await asyncio.wait_for(asyncio.to_thread(event.wait), timeout)
        else:
            got = await asyncio.to_thread(event.wait)
    except asyncio.TimeoutError:
        got = False
    finally:
        try:
            if sub is not None and _ros2_node is not None:
                _ros2_node.destroy_subscription(sub)
        except Exception:
            pass
    if not got:
        raise asyncio.TimeoutError()
    return holder["msg"]


def open_status_callback(msg: UInt32) -> None:
    global open_status
    with _open_status_lock:
        prev = open_status
        open_status = int(msg.data)

    # 非等待期：状态从非开 -> 开，播报提示
    if not is_whait_open and open_status == 0 and prev != 0:
        _notify("盖子已打开")

    # 打开则唤醒等待者
    if open_status == 0:
        _open_event.set()


def command_callback(msg_data: String) -> None:
    global _motion_success
    try:
        data = (msg_data.data or "").strip().lower()
        _motion_success = (data == "success")
    except Exception:
        _motion_success = False
    finally:
        _motion_event.set()


# -----------------------------
# ROS 通信
# -----------------------------
open_status_sub = _create_subscription("/yours_base/locks_status", UInt32, open_status_callback)
ctrl_status_sub = _create_subscription("/scheduled_tasks/ctrl_status", String, command_callback)

lock_publish = _create_publisher("/yours_base/locks_ctrl", UInt32)
ctrl_publish = _create_publisher("/scheduled_tasks/ctrl", String)


# -----------------------------
# HTTP 上报（使用异步HTTP客户端）
# -----------------------------
async def _notify_http() -> None:
    """上报盖子已经打开。使用异步HTTP客户端。"""
    try:
        robot_serial = ""
        try:
            with open("/home/nvidia/.yours_robot/robot_params.json", "r") as f:
                robot_param = json.load(f)
                robot_serial = robot_param.get("DeviceName", "")
        except Exception as e:
            logger.warning(f"读取 robot_params.json 失败: {e}")

        async with aiohttp.ClientSession() as session:
            async with session.post(
                "https://jp.yours.xyz/api/robot/log_locks",
                json={"from": "mcp", "robotSerial": robot_serial, "event_info": "open_lid"},
                timeout=aiohttp.ClientTimeout(total=3)
            ) as response:
                response.raise_for_status()
                logger.info("上报锁控状态成功")
    except Exception as e:
        logger.error(f"上报锁控状态失败: {e}")


# -----------------------------
# 工具方法（全部为异步且非阻塞）
# -----------------------------
async def open_lid(args: Dict[str, Any]) -> str:
    """
    打开小车的盖子。非阻塞：所有阻塞操作都放到线程执行。
    返回 JSON 字符串。
    """
    global is_whait_open, open_status
    try:
        if lock_publish is None:
            return json.dumps({"success": False, "message": "ROS不可用"}, ensure_ascii=False)
        is_whait_open = True
        _open_event.clear()
        with _open_status_lock:
            open_status = None

        # 重试发布打开指令：0（上电）-> 3（开盖脉冲）
        max_attempts = 5
        for _ in range(max_attempts):
            msg = UInt32()
            msg.data = 0
            lock_publish.publish(msg)
            await asyncio.sleep(0.05)
            msg = UInt32()
            msg.data = 3
            lock_publish.publish(msg)

            # 最多等1.2秒看是否已开
            try:
                opened = await asyncio.wait_for(
                    asyncio.to_thread(_open_event.wait),
                    1.2,
                )
                if opened:
                    break
            except asyncio.TimeoutError:
                pass

        if _open_event.is_set() or (open_status == 0):
            _fire_and_forget(_notify_http())
            return json.dumps({"open_cover": "success"}, ensure_ascii=False, indent=2)

        error_msg = "打开小车的盖子失败"
        logger.error(f"[TimerTools] {error_msg}")
        return json.dumps({"success": False, "message": error_msg}, ensure_ascii=False)

    except Exception as e:
        error_msg = f"打开小车的盖子失败: {e}"
        logger.error(f"[TimerTools] {error_msg}", exc_info=True)
        return json.dumps({"success": False, "message": error_msg}, ensure_ascii=False)
    finally:
        is_whait_open = False


async def robot_opertion(args: Dict[str, Any]) -> str:
    """
    操作小车运行状态: types ∈ {'resume','pause'}
    非阻塞：使用 threading.Event + to_thread 等待状态。
    返回 JSON 字符串。
    """
    try:
        types = str(args.get("types", "")).strip().lower()
        logger.info(f"robot_opertion car {types}")
        if types not in {"resume", "pause"}:
            return json.dumps({"success": False, "message": f"未知指令: {types}"}, ensure_ascii=False)

        global _motion_success
        _motion_success = False
        _motion_event.clear()

        if ctrl_publish is None:
            return json.dumps({"success": False, "message": "ROS不可用"}, ensure_ascii=False)
        msg = String()
        msg.data = types
        ctrl_publish.publish(msg)
        # 可选：重复一次以提高鲁棒性
        await asyncio.sleep(0.05)
        ctrl_publish.publish(msg)

        # 最长等待2秒收到 ctrl_status
        try:
            got = await asyncio.wait_for(
                asyncio.to_thread(_motion_event.wait),
                2.0
            )
        except asyncio.TimeoutError:
            got = False

        if not got:
            return json.dumps({"success": True, "message": "没有任务需要执行"}, ensure_ascii=False)

        if _motion_success:
            key = "pause" if types == "pause" else "resume"
            return json.dumps({key: "success"}, ensure_ascii=False, indent=2)

        return json.dumps({"success": False, "message": "上游返回失败"}, ensure_ascii=False)

    except Exception as e:
        logger.exception("[robot_opertion] 执行异常")
        return json.dumps({"success": False, "message": f"异常: {e}"}, ensure_ascii=False)


async def ads_change(args: Dict[str, Any]) -> str:
    """切换广告。使用异步HTTP客户端。"""
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                "http://nx_robot:nx_robot@127.0.0.1:15672/api/exchanges/%2F/robot/publish",
                json={
                    "properties": {},
                    "routing_key": "eye",
                    "payload": "{\"event\": \"ads_next\", \"body\": {\"type\": 1}}",
                    "payload_encoding": "string",
                },
                timeout=aiohttp.ClientTimeout(total=3)
            ) as response:
                response_text = await response.json()
                logger.info(f"切换广告请求响应: {response_text}")
                return json.dumps({"success": True, "message": "切换广告成功"}, ensure_ascii=False)

    except Exception as e:
        error_msg = f"切换广告失败: {e}"
        logger.error(f"[YoursTools] {error_msg}", exc_info=True)
        return json.dumps({"success": False, "message": error_msg}, ensure_ascii=False)


async def set_volume(args: Dict[str, Any]) -> str:
    """设置音量。非阻塞：使用线程执行耗时操作。

    - 参数：
    - volume: 音量值, 0-100之间的整数。
    - plus: 增加音量
    - minus: 减少音量
    """
    try:
        import os
        home_dir = os.path.expanduser("~")
        volume_file_path = os.path.join(home_dir, "volume.txt")
        try:
            with open(volume_file_path, "r") as f:
                volume = int(f.read().strip())
        except Exception:
            # 如果文件不存在 设置默认音量 100%
            volume = 80

        if args.get("plus", False):
            volume += 10
        elif args.get("minus", False):
            volume -= 10
        else:
            volume = int(args.get("volume", volume))
        volume = max(0, min(100, volume))  # 限制范围

        def _work():
            os.system(f"pactl set-sink-volume @DEFAULT_SINK@ {volume}%")

        await asyncio.to_thread(_work)
        # 写入主目录下的 volume.txt 文件
        home_dir = os.path.expanduser("~")
        volume_file_path = os.path.join(home_dir, "volume.txt")
        try:
            with open(volume_file_path, "w") as f:
                f.write(str(volume))
        except Exception as e:
            logger.error(f"写入音量文件失败: {e}")

        return json.dumps({"success": True, "message": f"设置音量为 {volume}"}, ensure_ascii=False)

    except Exception as e:
        error_msg = f"设置音量失败: {e}"
        logger.error(f"[YoursTools] {error_msg}", exc_info=True)
        return json.dumps({"success": False, "message": error_msg}, ensure_ascii=False)


async def get_battery(args: Dict[str, Any]) -> str:
    """获取电池信息：在后台线程等待ROS消息，避免阻塞事件循环。"""
    try:
        from yours_msgs.msg import YoursBatteryStatus

        battery_status = await _wait_for_message(
            "/yours_base/battery_status",
            YoursBatteryStatus,
            3.0,
        )

        return json.dumps(
            {
                "success": True,
                "message": "获取电量成功",
                "battery": float(battery_status.rsoc),
                "current": float(battery_status.current),
                "voltage": float(battery_status.voltage),
            },
            ensure_ascii=False,
        )

    except Exception as e:
        error_msg = f"获取电量失败: {e}"
        logger.error(f"[YoursTools] {error_msg}", exc_info=True)
        return json.dumps({"success": False, "message": error_msg}, ensure_ascii=False)
