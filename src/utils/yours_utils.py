import asyncio
import aiohttp
from src.utils.config_manager import ConfigManager

async def device_unbind(config_manager):
    """处理解绑流程，根据需要创建解绑界面."""
    timeout = aiohttp.ClientTimeout(total=10)
    if config_manager == None:
        config_manager = ConfigManager()
    proxy = config_manager.get_config("SYSTEM_OPTIONS.PROXY")
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(
                "https://ads.yours.xyz/index.php/api/ai/unbind",
                proxy=proxy,
                data={
                    "device_id": config_manager.get_config("SYSTEM_OPTIONS.DEVICE_ID"),
                    "robotSerial": config_manager.get_config("SYSTEM_OPTIONS.ROBOT_ID"),
                }
            ) as resp:
                text = await resp.text()
                data = {
                    "device_id": config_manager.get_config("SYSTEM_OPTIONS.DEVICE_ID"),
                    "robotSerial": config_manager.get_config("SYSTEM_OPTIONS.ROBOT_ID"),
                }
                print(f"解绑响应: {data} ")
                print(f"解绑响应: {text}")
                return resp.status == 200
    except asyncio.CancelledError:
        print("解绑流程被取消")        
        return False
    except Exception as e:
        print(f"解绑请求失败: {e}")
        return False

async def device_bind(code,config_manager):
    """处理解绑流程，根据需要创建解绑界面."""
    from src.application import Application
    if config_manager == None:
        config_manager = ConfigManager()
    proxy = config_manager.get_config("SYSTEM_OPTIONS.PROXY")
    async with aiohttp.ClientSession() as session:
        async with session.post("https://ads.yours.xyz/index.php/api/ai/bind", proxy=proxy, data={
            "device_id": config_manager.get_config("SYSTEM_OPTIONS.DEVICE_ID"),
            "advert_site_id": Application.get_instance().advert_site_id,
            "robotSerial": config_manager.get_config("SYSTEM_OPTIONS.ROBOT_ID"),
            "verificationCode": ''.join(code),
        }) as resp:
            text = await resp.text()
            print(f"激活响应: {text}")
            return resp.status == 200