###
# 广告相关工具函数
# 链接rabbitmq队列

import asyncio
import os
import json
from typing import Any, Optional, Tuple, Dict, Callable

import aio_pika
from src.utils.logging_config import get_logger
from src.utils.volume_controller import VolumeController
logger = get_logger(__name__)


class AdsUtils:
    """
    广告相关工具函数.
    """

    def __init__(self) -> None:
        self.queue_name = "ai"
        self.exchange_name = "robot"
        self.routing_key = "server"
        self.host = os.getenv("RMQ_HOST", "127.0.0.1")
        self.port = int(os.getenv("RMQ_PORT", "5672"))
        self.vhost = os.getenv("RMQ_VHOST", "/")
        self.username = os.getenv("RMQ_USER", "nx_robot")
        self.password = os.getenv("RMQ_PASS", "nx_robot")

        self._connection: Optional[aio_pika.RobustConnection] = None
        self._channel: Optional[aio_pika.Channel] = None
        self._on_ads_change: Optional[Callable] = None
        self.volume_controller = VolumeController()

        self.advert_site_id = None
        self.cancel_event = None

    async def connect(self) -> aio_pika.RobustConnection:
        return await aio_pika.connect_robust(
            host=self.host,
            port=self.port,
            virtualhost=self.vhost,
            login=self.username,
            password=self.password,
        )

    async def start(self) -> None:
        self._connection = await self.connect()
        self._channel = await self._connection.channel()
        await self._channel.set_qos(prefetch_count=1)
        _, queue = await self.link_exchange(self._channel)
        await queue.consume(self._on_message, no_ack=False)
        # 等待一个“保持运行”的事件，外部 cancel 时退出
        self._keep_running = asyncio.Event()
        await self._keep_running.wait()
    
    async def stop(self):
        self._stop_event.set()

    async def link_exchange(self, channel: aio_pika.Channel) -> Tuple[aio_pika.Exchange, aio_pika.Queue]:
        exchange = await channel.declare_exchange(
            self.exchange_name,
            aio_pika.ExchangeType.TOPIC,
            durable=False,
        )
        queue = await channel.declare_queue(
            self.queue_name,
            durable=False,
            auto_delete=True,
        )
        await queue.bind(exchange, routing_key=self.routing_key)
        logger.info(f"队列 {self.queue_name} 已绑定到交换机 {self.exchange_name}，路由键 {self.routing_key}")
        return exchange, queue

    async def handle_message(self, msg: Dict[str, Any]) -> None:
        """
        处理广告变更消息，会取消当前正在执行的任务并启动新任务
        """

        action = msg["event"]
        body = msg["body"]

        if action == "volume_update":
            logger.warning(f"音量变更事件: {action}")
            volume = body
            logger.info(f"音量变更为: {volume}")
            ## 修改 Ubuntu 音量
            self.volume_controller.set_volume(int(volume))
            ## 写入主目录下的 volume.txt 文件
            home_dir = os.path.expanduser("~")
            volume_file_path = os.path.join(home_dir, "volume.txt")
            try:
                with open(volume_file_path, "w") as f:
                    f.write(str(volume))
            except Exception as e:
                logger.error(f"写入音量文件失败: {e}")
            return
        if action == "ads_switch":
            logger.warning(f"广告切换事件: {action}")
            self.advert_site_id = body["advert_site_id"]
            self.cancel_event = asyncio.Event()
            try:
                await self.handle_activation(self.advert_site_id, self.cancel_event)
            except asyncio.CancelledError:
                logger.info("激活任务被取消")
            except Exception as e:
                logger.error(f"激活任务执行失败: {e}")
    
    async def handle_activation(self, advert_site_id: str, cancel_event: asyncio.Event) -> None:
        """
        处理广告切换事件，会取消当前正在执行的任务并启动新任务
        """
        logger.info(f"开始处理广告切换事件: {advert_site_id}")
        # 取消当前正在执行的任务
        try:
            from src.core.system_initializer import SystemInitializer
            from src.application import Application

            app = Application.get_instance()
            logger.info("开始设备激活流程检查...")
            app.advert_site_id = advert_site_id
            system_initializer = SystemInitializer()
            if cancel_event and cancel_event.is_set():
                    logger.info("激活流程被取消")
                    return False
            
            # 统一使用 SystemInitializer 内的激活处理，GUI/CLI 自适应
            try:
                await app.abort_speaking('广告切换: ' + advert_site_id)
                await app.close()
                await asyncio.sleep(1)
            except Exception as e:
                logger.error(f"取消语音播放失败: {e}")
            while True:
                # 检查是否收到取消信号
                await system_initializer.handle_unbind_process()
                result = await system_initializer.handle_activation_process(mode="cli")
                success = bool(result.get("is_activated", False))
                if success:
                    await app.start_auto_conversation()
                    await app.protocol.send_wake_word_detected("唤醒")
                    break
                else:
                    logger.error(f"激活失败，结果: {result}")
                    # 重试
                
            logger.info(f"激活流程完成，结果: {success}")
            return success
        except asyncio.CancelledError:
            logger.info("激活流程被取消")
            return False
        except Exception as e:
            logger.error(f"激活流程异常: {e}", exc_info=True)
            return False

    async def _on_message(self, message: aio_pika.IncomingMessage) -> None:
        try:
            payload = json.loads(message.body.decode("utf-8"))
        except Exception as e:
            logger.exception("JSON 解析失败，reject 不重回队列。body=%r err=%s", message.body[:256], e)
            await message.reject(requeue=False)
            return

        try:
            await self.handle_message(payload)
        except (asyncio.TimeoutError, ConnectionError) as e:
            # 可能是暂态错误，允许重投
            logger.exception("暂态失败，nack 重回队列。msg=%s err=%s", payload, e)
            await message.nack(requeue=True)
            return
        except Exception as e:
            # 业务不可恢复错误：丢弃，避免毒消息阻塞
            logger.exception("不可恢复错误，reject 不重回队列。msg=%s err=%s", payload, e)
            await message.reject(requeue=False)
            return
        try:
            await message.ack()
        except Exception as e:
            logger.error(f"确认消息失败: {e}")

    async def close(self) -> None:
        try:
            if self._channel:
                await self._channel.close()
        except Exception:
            pass
        try:
            if self._connection:
                await self._connection.close()
        except Exception:
            pass

    async def run_forever(self) -> None:
        try:
            await self.start()
        finally:
            await self.close()

