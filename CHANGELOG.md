# Changelog

## 0.3.0

- Limit the supported product scope to eMotion Ultra2.
- Replace ESPHome state polling with a direct encrypted local UDP position subscription.
- Add typed multi-target coordinates, nearest horizontal and three-dimensional distances, position expiry, subscription renewal, session renewal, retry backoff, and deterministic socket cleanup.
- Add device-read radar configuration models and write APIs for sensitivity, trigger speed, installation mode, height, cable direction, Z-axis limits, the default absence delay, and Zone 1-4 absence delays.
- Require an independent device status read-back after every radar configuration write.
- Remove the legacy eMotion Ultra and ESPHome API dependencies.

## 0.2.0

- Add eMotion Ultra2 state reads through its ESPHome local API.

## 0.1.0

- Add LinknLink DNA discovery, authentication, encrypted UDP transport, and initial eMotion Ultra support.
