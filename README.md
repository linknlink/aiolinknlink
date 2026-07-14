# aiolinknlink

`aiolinknlink` is an asynchronous Python client for direct local communication with LinknLink eMotion Ultra2 devices.

The library implements LinknLink DNA discovery, authentication, encrypted UDP transport, local multi-target radar position subscriptions, and device-verified radar configuration. It communicates directly with devices on the local network and does not require a cloud service or MQTT broker.

An Ultra2 stores one local UDP position destination. Running another position subscriber for the same device redirects updates away from the current subscriber.

Supported radar configuration includes sensitivity, trigger speed, installation mode, installation height, cable direction, Z-axis detection limits, the default absence delay, and Zone 1-4 absence delays. Every write API performs a separate device status read and raises an error when the read-back does not match.

## Requirements

- Python 3.11 or newer
- An eMotion Ultra2 already connected to Wi-Fi
- The client and device on the same local network

## Example

```python
import asyncio

from aiolinknlink import UltraClient, UltraPositionSubscription


async def main() -> None:
    client = UltraClient()
    device = await client.discover_host("192.168.1.8")
    session = await client.connect(device)
    subscription = UltraPositionSubscription(
        client,
        session,
        callback=lambda update: print(update.targets),
    )
    await subscription.start()
    try:
        await subscription.wait_confirmed(60)
        radar_status = await subscription.get_radar_status()
        print(radar_status.sensitivity)
        print(radar_status.z_range)
        await asyncio.sleep(60)
    finally:
        await subscription.stop()


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
