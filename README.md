# aiolinknlink

`aiolinknlink` is an asynchronous Python client for direct local communication with LinknLink devices.

The library implements LinknLink DNA discovery, authentication, encrypted UDP transport, local multi-target radar position subscriptions, environmental and occupancy state reads, and device-verified radar configuration. It communicates directly with devices on the local network and does not require a cloud service or MQTT broker.

An Ultra2 stores one local UDP position destination. Running another position subscriber for the same device redirects updates away from the current subscriber.

Supported radar configuration includes sensitivity, trigger speed, installation mode, installation height, installation direction, Z-axis detection limits, the default absence delay, and Zone 1-4 absence delays. Every write API performs a separate device status read and raises an error when the read-back does not match.

Temperature and humidity require the optional sensor power cable. Environment reads refresh the device's ESPHome entity list so connecting or disconnecting the cable is detected after the device restarts.

## eMotionPro conditional support

The `feature/emotion-pro` branch supports two source-derived local protocol
variants. Type `0x6FAC` uses KeyValue frames; type `0xB9AC` uses persisted
`0xACD9` radar and optional `0xACDC` temperature/humidity subdevices. Both
variants expose only temperature, humidity, occupancy, and the absence delay.
The KeyValue variant stores whole minutes while the radar variant stores
seconds. Every absence-delay write requires a separate status read-back.

The local V2 JSON handler does not process air-conditioner control fields, so
this library does not expose climate or air-conditioner control for eMotionPro.

These variants remain conditional and must not be released until each claimed
type joins a normal home LAN, responds to DNA discovery, authenticates, and
passes state, control, restart, and IP-change tests without any external
service.

Legacy BLC firmware can lock local pairing after setup. When discovery reports
`device.is_locked`, applications must securely retain the 16-byte local control
key returned during Wi-Fi provisioning and pass it as `local_key`. This library
does not perform Wi-Fi provisioning.

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
