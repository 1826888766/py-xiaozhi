import argparse
import asyncio
import signal
import sys
import threading
from src.application import Application
from src.utils.logging_config import get_logger, setup_logging

logger = get_logger(__name__)


def parse_args():
    """
    解析命令行参数.
    """
    parser = argparse.ArgumentParser(description="小智Ai客户端")
    parser.add_argument(
        "--mode",
        choices=["gui", "cli"],
        default="cli",
        help="运行模式：gui(图形界面) 或 cli(命令行)",
    )
    parser.add_argument(
        "--protocol",
        choices=["mqtt", "websocket"],
        default="websocket",
        help="通信协议：mqtt 或 websocket",
    )
    parser.add_argument(
        "--skip-activation",
        action="store_true",
        help="跳过激活流程，直接启动应用（仅用于调试）",
    )
    return parser.parse_args()


async def handle_activation(mode: str) -> bool:
    """处理设备激活流程，依赖已有事件循环.

    Args:
        mode: 运行模式，"gui"或"cli"

    Returns:
        bool: 激活是否成功
    """
    try:
        from src.core.system_initializer import SystemInitializer
 

        logger.info("开始设备激活流程检查...")

        system_initializer = SystemInitializer()
        # 统一使用 SystemInitializer 内的激活处理，GUI/CLI 自适应
        result = await system_initializer.handle_activation_process(mode=mode)
        success = bool(result.get("is_activated", False))
        logger.info(f"激活流程完成，结果: {success}")
        return success
    except Exception as e:
        logger.error(f"激活流程异常: {e}", exc_info=True)
        return False



async def start_app(mode: str, protocol: str, skip_activation: bool) -> int:
    """
    启动应用的统一入口（在已有事件循环中执行）.
    """
    logger.info("启动小智AI客户端")
    # 启动广告队列

    # 处理激活流程
    if not skip_activation:
        activation_success = await handle_activation(mode)
        if not activation_success:
            logger.error("设备激活失败，程序退出")
            return 1
    else:
        logger.warning("跳过激活流程（调试模式）")

    # 创建并启动应用程序
    app = Application.get_instance()
    
    threading.Thread(target=app._setup_ros).start()

    return await app.run(mode=mode, protocol=protocol)


if __name__ == "__main__":
    exit_code = 1
    try:
        args = parse_args()
        setup_logging()
        ### ros 初始化 节点不可重复初始化
        # 统一设置信号处理：忽略 macOS 上可能出现的 SIGTRAP，避免“trace trap”导致进程退出
        try:
            if hasattr(signal, "SIGINT"):
                # 交由 qasync/Qt 处理 Ctrl+C；保持默认或后续由 GUI 层处理
                pass
            if hasattr(signal, "SIGTERM"):
                # 允许进程收到终止信号时走正常关闭路径
                pass
            if hasattr(signal, "SIGTRAP"):
                signal.signal(signal.SIGTRAP, signal.SIG_IGN)
        except Exception:
            # 某些平台/环境不支持设置这些信号，忽略即可
            pass

        exit_code = asyncio.run(
                start_app(args.mode, args.protocol, args.skip_activation)
            )

    except KeyboardInterrupt:
        logger.info("程序被用户中断")
        exit_code = 0
    except Exception as e:
        logger.error(f"程序异常退出: {e}", exc_info=True)
        exit_code = 1
    finally:
        sys.exit(exit_code)
