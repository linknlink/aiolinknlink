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
water-cooled air-conditioner panel has one climate entity. Counts can differ on
another gateway.

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
