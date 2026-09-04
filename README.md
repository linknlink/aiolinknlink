# aiolinknlink

`aiolinknlink` is an asynchronous Python client for direct local communication with LinknLink eMotion devices.

The library implements LinknLink DNA discovery, authentication, encrypted UDP transport, eMotion presence-sensor state and configuration, Ultra2 local multi-target radar position subscriptions, environmental and occupancy state reads, and device-verified radar configuration. It communicates directly with devices on the local network and does not require a cloud service or MQTT broker.

The radar_env eMotion variant is identified by PID `0000000000000000000000007bac0000`. It exposes only presence (`pir_detected`), absence delay (`delaytime1`), sensitivity (`level_of_sensitivity`), and firmware version (`fwVer`); Ultra2 environmental and zone-position entities are not created for this model.

An Ultra2 stores one local UDP position destination. Running another position subscriber for the same device redirects updates away from the current subscriber.

Supported radar configuration includes sensitivity, trigger speed, installation mode, installation height, installation direction, Z-axis detection limits, the default absence delay, and Zone 1-4 absence delays. Every write API performs a separate device status read and raises an error when the read-back does not match.

Temperature and humidity require the optional sensor power cable. Environment reads refresh the device's ESPHome entity list so connecting or disconnecting the cable is detected after the device restarts.

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

For a radar_env eMotion, use the same discovery and authentication flow, then call
`get_emotion_state()`. Configuration setters perform a separate read-back:

```python
device = await client.discover_host("192.168.3.31")
session = await client.connect(device)
state = await client.get_emotion_state(session)
print(state.occupied, state.absence_delay, state.sensitivity)
await client.set_emotion_absence_delay(session, 30)
await client.set_emotion_sensitivity(session, 1)
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
