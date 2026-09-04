import asyncio
from aiolinknlink import UltraClient


async def main() -> None:
    client = UltraClient(discovery_timeout=3, auth_timeout=5, command_timeout=5)
    device = await client.discover_host("192.168.3.31")
    print("DEVICE", device)
    session = await client.connect(device)
    print("AUTH", session.auth_status, hex(session.auth_device_type), session.auth_error)
    print("STATE", await client.get_emotion_state(session))


asyncio.run(main())
