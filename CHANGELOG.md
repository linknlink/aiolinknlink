# Changelog

## 0.7.0 (unreleased)

- Add conditional local KeyValue support for `0x6FAC` eMotionPro devices.
- Add `0xB9AC` subdevice-protocol support with its validated `0x9CAC`
  authentication compatibility and `0xE3AC` command header.
- Read temperature, humidity, occupancy, and absence delay, with explicit unit
  normalization.
- Add whole-minute and whole-second absence-delay writes, selected by protocol,
  with an independent status read-back.
- Retry the radar variant's independent read-back once after its observed
  post-write settling interval, while preserving strict ACK and value checks.
- Use the wire-order MAC and 80-byte terminal structure required by legacy BLC
  pairing, and accept a provisioning-time local control key for locked units.
- Deliberately exclude climate control because the local V2 JSON handler does
  not implement it.
- Keep this version unreleased until normal-LAN provisioning, discovery,
  authentication, restart, and state/control behavior pass real-device tests.

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
