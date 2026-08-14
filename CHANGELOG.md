# Changelog

## Unreleased

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
