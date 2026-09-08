# aiolinknlink

`aiolinknlink` is an asynchronous Python client for direct local communication with LinknLink devices.

The library implements LinknLink DNA discovery, authentication, encrypted UDP transport, local multi-target radar position subscriptions, environmental and occupancy state reads, and device-verified radar configuration. It communicates directly with devices on the local network and does not require a cloud service or MQTT broker.

The included Home Assistant custom integration automatically distinguishes
supported iBG gateways from eMotion Ultra2 devices by their local discovery
identity. Ultra2 entities include temperature, humidity, illuminance, Wi-Fi
signal, occupancy, target counts, per-zone presence/counts, multi-target
coordinates and nearest distances, plus device-confirmed radar configuration
controls. Existing iBG entries without a stored product-family marker remain
compatible.

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
- A supported eMotion Ultra, eMotion Ultra2, or iBG2 SE connected to the local network
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

Supported iBG profiles include the RF environment/occupancy sensor, the
seven-channel controller, the DTU, PID `93150100` Modbus air conditioners, PID
`0f160100` Modbus multifunction sensors, PID `34150100` Modbus water meters,
PID `ed140100` Modbus electricity meters, PID `d10f0100` DLT645 electricity
meters, PID `2b160100` RF water-cooled air-conditioner panels, PID `9b100100`
433 MHz eAC1 smart air-conditioner panels, PID `b5120100` first-generation
eSensor-2000 sensors, and PID `43160100` second-generation eSensor-2000
environmental sensors. The Home Assistant
integration exposes both air-conditioner profiles as native climate entities
with confirmed power, mode, fan-speed, and target-temperature writes.
Multifunction sensors expose temperature, carbon dioxide, humidity, and
illuminance measurements. Water meters expose cumulative consumption and
instantaneous volume flow when reported by the device. Electricity meters
expose active energy, phase and line voltage, phase current, active and
reactive power, power factor, harmonic current, frequency, overload, and
reviewed Modbus diagnostics. DLT645 electricity meters expose the documented
active energy, phase voltage/current, active power, power factor, harmonic
current, overload, electrical-parameter, and address fields. Both
electricity-meter profiles are read-only.
eAC1 panels expose a native climate entity with confirmed power, mode,
fan-speed, and 16-30 °C target-temperature controls, plus indoor humidity, a
confirmed key-lock switch, and a device-type diagnostic.
First-generation eSensor-2000 devices expose temperature, humidity, battery,
occupancy, and physical key events for press and double-press. Second-generation
devices additionally expose illuminance and long-press events.
PID `d7140100` eight-channel light switches expose seven individual circuit
switches plus one all-on/all-off switch. The integration sends only `0` or `1`
for `mpwr`; a reported `2` means keep the existing circuit states and is never
misrepresented as an on/off value.
PID `20110100` single-channel light switches expose a confirmed `pwr1` load
switch, a confirmed `bglight` panel-backlight switch, and two separate
momentary scene-button event entities for `scenarioswitch_1` and
`scenarioswitch_2`.
PID `21110100` two-channel light switches expose confirmed `pwr1`, `pwr2`,
`bglight`, and `mpwr` controls, plus four separate momentary scene-button
event entities. As with the eight-channel profile, `mpwr=2` means keep the
current channel states and is derived from the actual channel outputs.
PID `22110100` three-channel light switches expose confirmed `pwr1` through
`pwr3`, `bglight`, and `mpwr` controls, plus six separate momentary
scene-button event entities. A reported `mpwr=2` is likewise derived from the
three actual channel outputs.

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

The repository also contains a self-contained Home Assistant custom
integration in `custom_components/linknlink`. It is intended to be installed
through HACS; the client library is bundled inside the integration, so an HA
host does not need a separate checkout or manual Python path configuration.
For local development, the same directory can be copied into the HA
configuration's `custom_components` directory.

When changing `src/aiolinknlink`, refresh the HACS copy with:

```bash
python3 scripts/sync-bundled-library.py
python3 scripts/validate-hacs.py
```

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
