# Changelog

## 0.5.0 (unreleased)

- Add model-specific local protocol support for eMotion Max, eMotion Max 2,
  and eMotion Max 3.
- Read environment state through KeyValue frames on Max and virtual peripheral
  frames on Max 2 and Max 3.
- Add device-verified radar configuration and local UDP position subscription
  support for all three generations.
- Use the wire-order MAC and 80-byte terminal structure required by legacy BLC
  pairing, and accept a provisioning-time local control key for locked units.
- Keep this version unreleased until the claimed generation has passed
  discovery, authentication, state, control, restart, and IP-change tests on a
  real device.

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
