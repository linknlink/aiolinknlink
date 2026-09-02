# aiolinknlink

`aiolinknlink` is an asynchronous Python client for direct local communication with LinknLink devices.

The library implements LinknLink DNA discovery, authentication, encrypted UDP transport, local multi-target radar position subscriptions, environmental and occupancy state reads, and device-verified radar configuration. It communicates directly with devices on the local network and does not require a cloud service or MQTT broker.

An Ultra2 stores one local UDP position destination. Running another position subscriber for the same device redirects updates away from the current subscriber.

Supported radar configuration includes sensitivity, trigger speed, installation mode, installation height, installation direction, Z-axis detection limits, the default absence delay, and Zone 1-4 absence delays. Every write API performs a separate device status read and raises an error when the read-back does not match.

Temperature and humidity require the optional sensor power cable. Environment reads refresh the device's ESPHome entity list so connecting or disconnecting the cable is detected after the device restarts.

## Infrared remote development support

The `feature/emotion-remotes` branch adds local infrared support for eHomeHA
(`0x85AC`) and eRemoteHA (`0x90AC`). Discovery uses the local DNA protocol;
infrared operations use JSON-RPC over TCP port 502.

`LinknLinkRemoteClient` maintains the device's single supported TCP connection
and serializes all requests. It supports starting and stopping learning, polling
for a learned Base64 infrared frame, sending a validated frame, and a combined
`learn_code()` operation. The library intentionally does not store command
names. Home Assistant command persistence and deletion belong to the later
standard `remote` entity implementation.

This support is not release-ready until learning, sending, reconnecting, and
device restart behavior have been verified on the corresponding real model.

## Requirements

- Python 3.11 or newer
- A supported LinknLink device already connected to Wi-Fi
- The client and device on the same local network

## Example

```python
import asyncio

from aiolinknlink import UltraClient, UltraPositionSubscription


async def main() -> None:
    client = UltraClient()
    device = await client.discover_host("198.51.100.8")
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
