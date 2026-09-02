# Changelog

## 0.4.0

- Add first-generation eMotion Ultra DNA discovery and authentication.
- Add direct local reads for 60 GHz radar presence, four zones, target count,
  illuminance, and optional temperature/humidity peripherals.
- Reuse device-verified radar configuration APIs without changing the existing
  Ultra2 API.
- Add immutable model capability metadata and generic LinknLink API aliases.
- Add runtime capability filtering for radar and optional peripherals.
- Parse legacy discovery lock state, use the wire-order MAC and the 80-byte
  legacy terminal pairing structure.
- Allow a provisioning-time local control key to open an already locked
  first-generation device without changing the Ultra2 call path.
- Document that first-generation local responses do not contain target speed.
- Do not advertise unverified first-generation position, distance, local UDP,
  or target-speed capabilities.
- Add MAC-based rediscovery with directed-broadcast retries for devices whose
  DHCP address changes.

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
