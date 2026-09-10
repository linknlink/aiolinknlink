# Changelog

## Unreleased

- Ship the Zigbee eMotion Air ZHA quirk in
  `custom_components/linknlink/zha_quirks/` so HACS keeps it up to date.
  Point ZHA's `custom_quirks_path` at that directory to use it. The quirk
  requires eMotion Air firmware V1.2.7+ and reports button actions on the
  proprietary `0xFC01` cluster.
  The quirk also exposes the `0xFC00` air config cluster (radar frequency,
  sensitivity, absence timeout, illuminance / temp-humidity intervals, and
  illuminance thresholds) as number / switch / select entities.

## 0.19.5 - 2026-09-10

- Corrected eMotion Max V1 capabilities to match its radar-only hardware: it no
  longer exposes temperature, humidity, illuminance, or Wi-Fi signal entities.

## 0.19.4 - 2026-09-10

- Read eMotion Pro illuminance from the derived OPT3004 virtual peripheral even
  when the device's virtual-peripheral list reports `status=-1`, matching the
  Ultra and Max behavior.
- Use the legacy BLC authentication transport for first-generation eMotion Ultra
  devices so firmware that rejects the full-header pairing packet with status
  `0xFFF9` can still be paired.

## 0.19.3 - 2026-09-10

- Add the missing eMotion Pro illuminance entity and parse `envlux`, `lux`, or
  `illuminance` values from standard Pro status responses.
- Read optional OPT3004 illuminance peripherals on eMotion Pro Radar devices.
- Return the first successful protocol discovery result without waiting for an
  unrelated protocol probe to time out.
- Retry temporarily unavailable eMotion devices every five minutes and restore
  the normal 30-second polling interval after recovery.

## 0.19.2 - 2026-09-10

- Prefer the eTHS threshold-register signature when discovering eTHS and eHub
  devices so eTHS hosts are not classified as eHub.
- Prevent eHub and eTHS coordinators from being passed to iBG-only event,
  switch, and climate platforms.
- Keep the bundled HACS client library synchronized with the source library.

## 0.19.1 - 2026-09-09

- Prefer the bundled HACS client library over a stale `/config/deps`
  development checkout so eHub and eTHS discovery cannot be misclassified by
  older protocol code.

## 0.19.0 - 2026-09-09

- Add legacy eMotion Ultra (`PID 0000000000000000000000009cac0000`, DNA type
  `0x9CAC`) detection and gateway-state environment entities. Legacy Ultra
  entries use a separate capability path and do not expose Ultra2-only
  ESPHome/radar/position controls; pre-paired 16-byte session keys are accepted
  when the device refuses a new local pairing.
- Add eMotion Ultra2 auto-detection to the Home Assistant config flow while
  preserving existing iBG entries and their local-key handling.
- Add Ultra2 temperature, humidity, illuminance, Wi-Fi signal, occupancy,
  total/fenced/Zone 1-4 target counts, and Zone 1-4 presence entities.
- Add maintained local UDP multi-target position subscriptions with target
  coordinates, nearest horizontal distance, and nearest three-dimensional
  distance entities.
- Add device-confirmed Ultra2 radar controls for sensitivity, trigger speed,
  installation mode, height, direction, Z-axis limits, default absence delay,
  and Zone 1-4 absence delays.
- Add PID `20110100` single-channel light switches with confirmed `pwr1` and
  panel-backlight (`bglight`) switches, plus separate momentary scene-button
  events for `scenarioswitch_1` and `scenarioswitch_2`.
- Add PID `21110100` two-channel light switches with confirmed `pwr1`, `pwr2`,
  `bglight`, and `mpwr` controls, plus four momentary scene-button events.
- Add PID `22110100` three-channel light switches with confirmed `pwr1` through
  `pwr3`, `bglight`, and `mpwr` controls, plus six momentary scene-button events.
- Add local iBG2 SE discovery, compact DNA authentication, paginated subdevice
  inventory, and read-only sensor state polling.
- Add strict safe-state normalization for PID `05000100` temperature, humidity,
  illuminance, battery, and occupancy data.
- Add a development Home Assistant custom integration with UI configuration,
  coordinator polling, device registry hierarchy, and sensor entities.
- Validate both DID and PID on status responses and isolate offline or failing
  sensors from the rest of a gateway refresh.
- Add failure/recovery tests plus credential-free HA Container deployment,
  backup, rollback, smoke-test, and operations documentation.
- Add authenticated iBG local status push with heartbeat renewal,
  acknowledgement, reauthentication, and polling fallback.
- Expose the PID `05000100` physical key as a Home Assistant event entity;
  only a confirmed `2` to `1` edge emits a `pressed` event.
- Support locked iBG gateways through an optional pre-paired local key without
  changing the gateway lock configuration.
- Add PID `31130100` seven-channel controllers with seven confirmed HA switches
  and twelve scaled power, energy, temperature, voltage, and current sensors.
- Create HA entities from per-PID capability catalogs so unrelated device types
  no longer receive inapplicable entities.
- Generate distinct seven-channel controller entity names from each circuit,
  temperature input, and electrical phase parameter.
- Name the seven controllable channels Switch 1 through Switch 7 to match their
  Home Assistant entity type.
- Add PID `0b150100` DTUs with two confirmed switches, three mode-aware analog
  inputs, a confirmed 0-10 V output, three signal inputs, and electrical sensors.
- Add PID `93150100` Modbus air conditioners as native HA climate entities with
  confirmed power, operating-mode, fan-speed, and target-temperature controls,
  current temperature, and a diagnostic fault-code sensor.
- Add PID `0f160100` Modbus multifunction sensors with temperature, carbon
  dioxide, humidity, and illuminance measurements using documented scaling.
- Add PID `34150100` Modbus water meters with cumulative water consumption in
  cubic meters and optional instantaneous flow in liters per hour.
- Add PID `2b160100` RF water-cooled air-conditioner panels as native HA
  climate entities with confirmed power, cooling/heating/fan-only modes,
  four fan speeds, 5-35 °C target control, and indoor temperature.
- Persist the negotiated iBG session key after unlocked first-time pairing so
  the integration can reconnect after HA or container restarts.
- Add read-only PID `ed140100` Modbus electricity meters with 28 scaled
  electrical measurements, three phase-overload sensors, six reviewed
  diagnostics, and collision-safe names for multiple meters.
- Add PID `9b100100` 433 MHz eAC1 smart air-conditioner panels with a native HA
  climate entity, confirmed power/mode/fan/temperature writes, indoor humidity,
  a confirmed key-lock switch, and a device-type diagnostic.
- Add read-only PID `43160100` eSensor-2000 devices with documented temperature
  and humidity scaling, battery, illuminance, occupancy, and press,
  double-press, and long-press HA events.
- Add read-only PID `b5120100` first-generation eSensor-2000 devices with
  documented temperature and humidity scaling, battery, occupancy, and press
  and double-press HA events, while keeping PID `43160100` as the distinct
  second-generation profile.
- Add PID `d7140100` eight-channel light switches with seven independently
  confirmed circuit switches and one confirmed all-on/all-off switch. Treat
  the reported `mpwr=2` value as "keep current outputs" and derive the HA
  master state from the seven actual circuits.
- Fix PID `d7140100` all-on/all-off writes being reported as failed when the
  SET response confirms `mpwr=0/1` before its individual `pwr1` through `pwr7`
  fields finish updating.
- Add read-only PID `d10f0100` DLT645 electricity meters with 20 documented
  electrical measurements, three phase-overload problem sensors, and two
  disabled-by-default diagnostics. Relay control and unreviewed fields remain
  deliberately unavailable.

## 0.3.2

- Refresh ESPHome entity capabilities on every state read so optional
  temperature and humidity hardware is detected after a device restart.
- Reauthenticate and retry radar operations once after a DNA session expires.

## 0.3.1

- Add direct local reads for eMotion Ultra temperature, humidity, illuminance,
  occupancy, target counts, fenced-zone counts, zone states, and Wi-Fi signal.
- Restrict ESPHome API state mapping to known non-sensitive Ultra entities.

## 0.3.0

- Limit the supported product scope to eMotion Ultra2.
- Replace the previous state-polling path with a direct encrypted local UDP position subscription.
- Add typed multi-target coordinates, nearest horizontal and three-dimensional distances, position expiry, subscription renewal, session renewal, retry backoff, and deterministic socket cleanup.
- Add device-read radar configuration models and write APIs for sensitivity, trigger speed, installation mode, height, installation direction, Z-axis limits, the default absence delay, and Zone 1-4 absence delays.
- Require an independent device status read-back after every radar configuration write.
- Remove APIs for unsupported earlier hardware and third-party protocol dependencies.

## 0.2.0

- Add experimental eMotion Ultra2 state reads.

## 0.1.0

- Add LinknLink DNA discovery, authentication, and encrypted UDP transport.
