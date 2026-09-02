# aiolinknlink

`aiolinknlink` is an asynchronous Python client for direct local communication with LinknLink eMotion Ultra devices.

The library implements LinknLink DNA discovery, authentication, encrypted UDP transport, local multi-target radar position subscriptions, environmental and occupancy state reads, and device-verified radar configuration. It communicates directly with devices on the local network and does not require a cloud service or MQTT broker.

An Ultra with local position support stores one UDP position destination. Running another position subscriber for the same device redirects updates away from the current subscriber.

Radar configuration is filtered by the fields returned by the device. Radar firmware can expose sensitivity, trigger speed, installation mode, installation height, installation direction, Z-axis detection limits, the default absence delay, and Zone 1-4 absence delays. Every write API performs a separate device status read and raises an error when the read-back does not match.

Ultra2 temperature and humidity require the optional sensor power cable. Environment reads refresh the device's ESPHome entity list so connecting or disconnecting the cable is detected after the device restarts. First-generation Ultra devices report their persisted radar, illuminance, and optional temperature/humidity peripherals through the encrypted DNA protocol.

First-generation devices use a 60 GHz `0xACDB` radar. Position, calculated distance, and target speed are not available through its verified local interface, so `get_runtime_capabilities()` does not advertise them for this model.

First-generation firmware automatically locks local pairing after initial setup. The Wi-Fi provisioning response contains a persistent 16-byte local control key. Applications must store that key securely and pass it as `local_key` when `device.is_locked` is true. The library does not configure Wi-Fi or retrieve cloud credentials.

Call `get_runtime_capabilities()` after authentication and use its result when exposing features. Model metadata is the maximum possible capability set; runtime capabilities are narrowed by the device's online persisted peripherals.

If a configured device no longer responds at its stored address, call `rediscover(device)`. The client retries a directed broadcast on the previous address's subnet and returns only a device with the same MAC, allowing the caller to persist its new address before reconnecting.

## Requirements

- Python 3.11 or newer
- An eMotion Ultra or Ultra2 already connected to Wi-Fi
- The client and device on the same local network

## Example

```python
import asyncio

from aiolinknlink import DeviceCapability, UltraClient, UltraPositionSubscription


async def main() -> None:
    client = UltraClient()
    device = await client.discover_host("198.51.100.8")
    session = await client.connect(device)
    print((await client.get_environment_state(session)).values)
    print(await client.get_radar_status(session))

    capabilities = await client.get_runtime_capabilities(session)
    if DeviceCapability.LOCAL_UDP in capabilities:
        subscription = UltraPositionSubscription(
            client,
            session,
            callback=lambda update: print(update.targets),
        )
        await subscription.start()
        try:
            await subscription.wait_confirmed(60)
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
