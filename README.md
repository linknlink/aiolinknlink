# aiolinknlink

`aiolinknlink` is an asynchronous Python client for direct local communication
with LinknLink eMotion Ultra and Ultra2 devices.

The library implements LinknLink DNA discovery, authentication, encrypted UDP
transport, and the eMotion gateway/subdevice protocol. It communicates directly
with devices on the local network and does not require
`linknlink-device-bridge`, DeviceHub, MQTT, or a cloud service.

## Requirements

- Python 3.11 or newer
- An eMotion Ultra/Ultra2 already connected to Wi-Fi
- The client and device on the same local network

## Example

```python
import asyncio

from aiolinknlink import UltraClient


async def main() -> None:
    client = UltraClient()
    devices = await client.discover()
    if not devices:
        return

    session = await client.connect(devices[0])
    state = await client.refresh(session)
    print(state.values)


asyncio.run(main())
```

## Development

```bash
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
.venv/bin/pytest
.venv/bin/ruff check .
.venv/bin/mypy src
```

## License

Apache License 2.0.

