# iBG Home Assistant Integration

## Supported scope

This release supports encrypted local communication with iBG gateways. It can
pair with unlocked gateways or reuse a pre-paired local key when a gateway's
local access lock is enabled. It reads the full paginated subdevice inventory
and exposes reviewed fields from PID
`00000000000000000000000005000100` sensors:

- temperature;
- humidity;
- illuminance;
- battery percentage;
- occupancy;
- physical key press events.

PID `00000000000000000000000031130100` seven-channel controllers expose:

- seven read/write circuit switches (`pwr1` through `pwr7`);
- current power and total energy;
- four temperature inputs;
- three-phase voltage and current.

Switch writes are field- and type-whitelisted, and HA updates a switch only
after the controller confirms the requested value.

PID `0000000000000000000000000b150100` DTUs expose:

- two confirmed read/write switches (`pwr1` and `pwr2`);
- three analog input values whose `dateN_type` mode selects mA (`0`) or V (`1`);
- one confirmed 0-10 V number output in 0.1 V steps;
- current power, total energy, and three-phase voltage/current;
- three binary signal inputs.

Numeric DTU writes are range-, step-, and type-whitelisted and are accepted by
HA only after the DTU reports the requested normalized value.

PID `00000000000000000000000093150100` Modbus air conditioners expose:

- one native climate entity with power, cool/heat/dry/fan/auto operating modes;
- automatic, low, medium, and high fan speeds;
- a 16-32 °C target temperature in 1 °C steps;
- current room temperature scaled from the device's tenths-of-a-degree value;
- one diagnostic sensor containing the raw 0-65535 fault code.

Every writable climate field is range- and type-whitelisted. HA accepts a
control only after the device response confirms every requested field.

PID `0000000000000000000000000f160100` Modbus multifunction sensors expose:

- temperature divided by 100 and reported natively in °C;
- carbon dioxide concentration in ppm;
- relative humidity divided by 100 and reported as a percentage;
- illuminance in lx.

The profile is read-only. Modbus addresses, transport results, device
configuration, and unreviewed response fields are not exposed to HA.

PID `00000000000000000000000034150100` Modbus water meters expose:

- cumulative positive flow divided by 100 and reported in m³ with a
  `total_increasing` state class;
- instantaneous volume flow in L/h when the device includes that field.

The profile is read-only. A missing instantaneous-flow field remains unknown;
the integration does not fabricate a zero-flow reading.

PID `0000000000000000000000002b160100` RF water-cooled air-conditioner
panels expose:

- one native climate entity with confirmed power, cool, heat, and fan-only
  operating modes;
- automatic, low, medium, and high fan speeds;
- a 5-35 °C target temperature in 1 °C steps;
- the panel's read-only indoor temperature.

Every writable panel field is range-, enum-, and type-whitelisted. HA accepts
a control only after the device response confirms every requested value.

PID `000000000000000000000000ed140100` Modbus electricity meters expose:

- combined, forward, and reverse active energy in kWh;
- A/B/C phase voltage and AB/BC/AC line voltage;
- A/B/C phase current and harmonic current;
- total and per-phase active and reactive power;
- combined and per-phase power factor plus grid frequency;
- A/B/C phase overload problem sensors;
- disabled-by-default diagnostics for the reported device name, electrical
  parameters, Modbus address, transformer ratio, and read/write result.

All 37 documented fields are type-, range-, and scale-validated. The profile is
strictly read-only. HA uses the stable inventory name plus the reported device
name for display, while registry identifiers remain based on gateway ID, DID,
and field name so multiple meters cannot collide.

PID `000000000000000000000000d10f0100` DLT645 electricity meters expose:

- combined, forward, and reverse active energy in kWh;
- A/B/C phase voltage and current;
- total and per-phase active power;
- combined and per-phase power factor;
- A/B/C phase harmonic current;
- A/B/C phase overload problem sensors;
- disabled-by-default diagnostics for electrical parameters and the DLT645
  address.

All 25 documented entities are type-, range-, and scale-validated. The profile
is strictly read-only: the integration does not expose the firmware's
undocumented relay field or any unreviewed response fields.

PID `0000000000000000000000009b100100` 433 MHz eAC1 smart air-conditioner
panels expose:

- one native climate entity with confirmed power, cool, heat, and fan-only
  operating modes;
- automatic, low, medium, and high fan speeds;
- a 16-30 °C target temperature in 1 °C steps;
- read-only indoor temperature and humidity;
- one confirmed physical-key lock switch;
- one disabled-by-default water-cooled/VRV device-type diagnostic.

Power, fan, mode, target temperature, and key lock are field-, range-, enum-,
and type-whitelisted. HA accepts a control only after the panel response
confirms every requested value. Indoor temperature, humidity, and device type
remain read-only.

PID `000000000000000000000000b5120100` first-generation eSensor-2000 devices
expose:

- temperature divided by 10 and reported in °C;
- relative humidity divided by 10 and reported as a percentage;
- battery percentage and occupancy;
- press and double-press physical-key events.

PID `00000000000000000000000043160100` second-generation eSensor-2000 devices
expose:

- temperature divided by 10 and reported in °C;
- relative humidity divided by 100 and reported as a percentage;
- battery percentage and illuminance in lx;
- occupancy, while the documented unknown value remains unavailable;
- press, double-press, and long-press physical-key events.

Both profiles are read-only. Initial or repeatedly polled key values do not
create false HA events; only a newly observed documented key action is emitted.

PID `000000000000000000000000d7140100` eight-channel light switches expose:

- seven independently confirmed circuit switches (`pwr1` through `pwr7`);
- one confirmed all-on/all-off switch backed by `mpwr`;
- immediate physical-state updates through authenticated iBG pushes.

The integration sends `mpwr=0` for all off and `mpwr=1` for all on. The
device's `mpwr=2` value means keep the individual outputs unchanged, so HA
derives the master switch state from the seven actual circuit states instead
of treating `2` as an on/off state.

PID `00000000000000000000000020110100` single-channel light switches expose:

- one confirmed load switch backed by `pwr1`;
- one confirmed panel-backlight switch backed by `bglight`;
- separate momentary `pressed` event entities for `scenarioswitch_1` and
  `scenarioswitch_2`.

The scene fields are not writable switches. The firmware automatically returns
them to `0`; HA emits an event only for a pushed `0` to `1` transition.

PID `00000000000000000000000021110100` two-channel light switches expose:

- two confirmed load switches backed by `pwr1` and `pwr2`;
- one confirmed panel-backlight switch backed by `bglight`;
- one confirmed all-on/all-off switch backed by `mpwr`;
- separate momentary `pressed` event entities for
  `scenarioswitch_1` through `scenarioswitch_4`.

The `mpwr` field accepts only `0` or `1` when written. A reported `2` means
keep the individual outputs unchanged, so HA derives the master state from
`pwr1` and `pwr2`.

PID `00000000000000000000000022110100` three-channel light switches expose:

- three confirmed load switches backed by `pwr1` through `pwr3`;
- one confirmed panel-backlight switch backed by `bglight`;
- one confirmed all-on/all-off switch backed by `mpwr`;
- separate momentary `pressed` event entities for
  `scenarioswitch_1` through `scenarioswitch_6`.

The `mpwr` field accepts only `0` or `1` when written. A reported `2` means
keep the individual outputs unchanged, so HA derives the master state from
`pwr1` through `pwr3`. Scene fields are read-only and emit an event only for
a pushed `0` to `1` transition.

Gateway credentials, MQTT settings, network keys, raw snapshots, AES session
keys, and unreviewed fields are deliberately excluded. Dynamic addition of new
entity types while HA is running, Zigbee, other Modbus profiles, and other RF
profiles are not part of this release.

## Architecture

The Python library under `src/aiolinknlink` owns discovery, authentication,
packet validation, pagination, normalization, and error isolation. The custom
component under `custom_components/linknlink` owns HA configuration, polling,
device hierarchy, availability, and entities.

HA maintains an authenticated local UDP heartbeat and receives acknowledged
status pushes for immediate physical key events and state updates. It also polls
the gateway every 30 seconds as a fallback. A failed sensor read marks only that
sensor unavailable. A failed gateway exchange triggers one reauthentication
attempt. Subsequent failure marks the coordinator unavailable and preserves the
last state in HA history.

## Development deployment

Requirements:

- HA Container with host networking;
- SSH access to the HA host;
- passwordless `sudo docker` for the deployment user;
- the HA configuration volume at
  `/var/lib/docker/volumes/homeassistant_config/_data`, or an explicitly supplied
  alternative.

From the repository root:

```bash
./scripts/deploy-ha.sh USER@HA_HOST
```

The script prompts through normal SSH authentication, creates a timestamped
backup, stages files, installs both the custom component and library source in
the persistent HA configuration volume, restarts HA, and waits for the
container to return to a running or healthy state. It never stores an SSH
password or iBG key. If HA does not become ready within two minutes, the script
exits unsuccessfully and prints the retained backup location.

In HA, select **Settings → Devices & services → Add integration → LinknLink**,
then enter the iBG LAN address. Leave **Local key** empty for an unlocked
gateway; after successful pairing, the integration stores the negotiated key
in config-entry data for restart-safe reconnection. A locked gateway requires
its existing 32-character hexadecimal local key. The integration never exposes
either form of key through an entity or log message.

## Verification

Run the component import check in an HA image or HA container:

```bash
python scripts/ha-smoke-test.py --source /path/to/aiolinknlink
```

Add `--host IBG_ADDRESS` for a read-only live check. For a locked gateway, set
the `LINKNLINK_IBG_LOCAL_KEY` environment variable. Output contains counts and
field names only; it excludes device identifiers, names, state values, and keys.

Each PID `05000100` device has six entities. Each PID `31130100` device has 19
entities: seven switches and twelve sensors. Each PID `0b150100` DTU has 17
entities: two switches, eleven sensors, three binary sensors, and one number.
Each PID `93150100` Modbus air conditioner has two entities: one climate entity
and one diagnostic fault-code sensor. Each PID `0f160100` Modbus multifunction
sensor has four measurement entities. Each PID `34150100` Modbus water meter
has two entities: cumulative water and instantaneous flow. Each PID `2b160100`
water-cooled air-conditioner panel has one climate entity. Each PID `ed140100`
Modbus electricity meter has 37 entities: 28 measurements, three overload
binary sensors, and six disabled-by-default diagnostic sensors. Counts can
differ on another gateway. Each PID `d10f0100` DLT645 electricity meter has
25 entities: 20 measurements, three overload binary sensors, and two
disabled-by-default diagnostic sensors. Each PID `9b100100` eAC1 panel has four
entities: one climate entity, one humidity sensor, one key-lock switch, and
one disabled-by-default device-type diagnostic sensor. Each PID `b5120100`
first-generation eSensor-2000 has five entities: three measurement sensors, one
occupancy binary sensor, and one physical-key event entity. Each PID `43160100`
second-generation eSensor-2000 has six entities: four measurement sensors, one
occupancy binary sensor, and one physical-key event entity. Each PID `d7140100`
eight-channel light switch has eight switch entities: seven circuits and one
all-on/all-off control.
Each PID `20110100` single-channel light switch has four entities: the load
switch, panel-backlight switch, and two scene-button event entities.
Each PID `21110100` two-channel light switch has eight entities: two load
switches, a panel-backlight switch, an all-on/all-off switch, and four
scene-button event entities.
Each PID `22110100` three-channel light switch has eleven entities: three load
switches, a panel-backlight switch, an all-on/all-off switch, and six
scene-button event entities.

## Upgrade and rollback

Deploying again creates a new backup under:

```text
<ha-config>/backups/linknlink/<UTC timestamp>/
```

To restore one backup:

```bash
./scripts/rollback-ha.sh USER@HA_HOST UTC_TIMESTAMP
```

The backup also records when either installation directory did not exist, so a
rollback of the first installation removes the newly installed directory.

After HA becomes healthy, inspect LinknLink-related startup messages:

```bash
ssh USER@HA_HOST \
  "sudo docker ps --filter name=homeassistant; \
   sudo docker logs --since 5m homeassistant 2>&1 | grep -Ei 'linknlink|traceback|error'"
```

## Uninstall

Remove the LinknLink config entry in HA first. Then back up and remove these two
scoped directories from the HA host and restart HA:

```text
<ha-config>/custom_components/linknlink
<ha-config>/deps/aiolinknlink
```

Do not edit HA `.storage` files manually.

## Recovery checks

Before release, verify these behaviors in the test environment:

1. restart HA and confirm all ten entities return;
2. disconnect one sensor and confirm only that sensor becomes unavailable;
3. power-cycle iBG and confirm the coordinator reconnects;
4. block UDP port 80 temporarily and confirm entities become unavailable;
5. restore network access and confirm states recover without recreating the
   config entry;
6. replace/restart the HA container and confirm the persistent source loader
   still imports `aiolinknlink` from `/config/deps/aiolinknlink/src`.
