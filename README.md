# aiolinknlink

`aiolinknlink` is an asynchronous Python client for direct local communication with LinknLink devices.

The library implements LinknLink DNA discovery, authentication, encrypted UDP transport, local multi-target radar position subscriptions, environmental and occupancy state reads, and device-verified radar configuration. It communicates directly with devices on the local network and does not require a cloud service or MQTT broker.

An Ultra2 stores one local UDP position destination. Running another position subscriber for the same device redirects updates away from the current subscriber.

Supported radar configuration includes sensitivity, trigger speed, installation mode, installation height, installation direction, Z-axis detection limits, the default absence delay, and Zone 1-4 absence delays. Every write API performs a separate device status read and raises an error when the read-back does not match.

Temperature and humidity require the optional sensor power cable. Environment reads refresh the device's ESPHome entity list so connecting or disconnecting the cable is detected after the device restarts.

The iBG client supports compact DNA discovery and authentication, optional
pre-paired local keys for locked gateways, and paginated subdevice inventory.
PID `05000100` environment/occupancy sensors, PID `31130100` seven-channel
controllers, and PID `0b150100` DTUs are supported. DTUs expose two confirmed
switches, three mode-aware analog inputs, a confirmed 0-10 V output, three
signal inputs, and electrical measurements. A persistent authenticated heartbeat
receives and acknowledges local state pushes. Gateway configuration values and
credentials are never included in public state.

## Requirements

- Python 3.11 or newer
- A supported eMotion Ultra2 or iBG2 SE connected to the local network
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

Read supported iBG subdevice states:

```python
import asyncio

from aiolinknlink import IbgClient, IbgPushSubscription


async def main() -> None:
    client = IbgClient()
    gateway = await client.discover_host("192.168.1.10")
    session = await client.connect(gateway)
    subdevices = await client.list_subdevices(session)
    states = await client.read_supported_states(session, subdevices)
    print(states)
    subscription = IbgPushSubscription(
        client,
        session,
        subdevices,
        callback=lambda state: print(state.values),
    )
    await subscription.start()
    try:
        await asyncio.sleep(60)
    finally:
        await subscription.stop()


asyncio.run(main())
```

Locked gateways can reuse their existing 16-byte local key without changing the
gateway lock setting:

```python
session = await client.connect(gateway, local_key=bytes.fromhex(local_key_hex))
```

Seven-channel controller writes are restricted to `pwr1` through `pwr7` and
must be confirmed by the device response:

```python
state = await client.set_subdevice_state(session, controller, {"pwr1": True})
```

DTU writes are restricted to boolean `pwr1`/`pwr2` values and a `voltage`
output from 0.0 through 10.0 V in 0.1 V steps. The wire value is scaled by ten,
and the normalized read-back must confirm the request:

```python
state = await client.set_subdevice_state(session, dtu, {"pwr1": True, "voltage": 7.5})
```

For development testing, the repository also contains a Home Assistant custom
integration in `custom_components/linknlink`. Copy that directory into the HA
configuration's `custom_components` directory and install this checkout of
`aiolinknlink` in the HA Python environment.

The reproducible HA Container deployment, upgrade, rollback, verification, and
recovery procedure is documented in [docs/IBG_INTEGRATION.md](docs/IBG_INTEGRATION.md).

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
