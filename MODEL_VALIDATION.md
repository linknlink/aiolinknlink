# Multi-model validation status

This document separates source-derived implementation from real-device proof.
A model is not release-ready until every required hardware check passes.

## Branch status

| Model family | Branch | Automated status | Real-device status | Release status |
| --- | --- | --- | --- | --- |
| Shared model foundation | `feature/device-model-foundation` | Complete | Not applicable | Internal development base |
| eMotion Ultra (1st generation) | `feature/emotion-ultra1` | Complete | Discovery confirmed; authentication rejected by current unit | Do not release |
| eMotion Max / Max 2 / Max 3 | `feature/emotion-max` | Complete | No generation verified | Do not release |
| eHomeHA / eRemoteHA | `feature/emotion-remotes` | Complete | No model verified | Do not release |
| eMotionPro | `feature/emotion-pro` | Complete | Normal-LAN eligibility unverified | Conditional; do not release |

Versions `0.4.0` through `0.7.0` are provisional branch versions. They must be
integrated in release order and revalidated before publication.

## Claimed local capabilities

| Model | State reads | Controls | Explicit exclusions |
| --- | --- | --- | --- |
| eMotion Ultra2 | Environment, occupancy, counts, zones, X/Y/Z and calculated distance | Radar configuration and local position subscription | No locally reported target speed |
| eMotion Ultra (1st generation) | Optional temperature/humidity, illuminance, occupancy, count, four zones, X/Y/Z and calculated distance | Sensitivity, trigger speed, installation settings, Z range, absence delays and local position subscription | No locally reported target speed |
| eMotion Max | Temperature, humidity, illuminance, occupancy, count, four zones, X/Y/Z and calculated distance | KeyValue radar configuration, absence delays and local position subscription | No locally reported target speed |
| eMotion Max 2 / Max 3 | Temperature, humidity, illuminance, occupancy, count, four zones, X/Y/Z and calculated distance | Virtual-peripheral radar configuration, absence delays and local position subscription | No locally reported target speed |
| eHomeHA / eRemoteHA | Learned infrared frame polling | Start/stop learning and send infrared frame | No dynamic virtual HVAC, light, fan, cover or switch devices |
| eMotionPro | Temperature, humidity, occupancy and absence delay | Whole-minute absence delay with read-back | No climate or air-conditioner control |

## Hardware validation matrix

| Model or variant | Wire type | Implementation evidence | Hardware status | Publication decision |
| --- | --- | --- | --- | --- |
| Ultra first-generation | `0x9CAC` with radar `0xACDB` | Source-derived and automated | Specified-host discovery confirmed; authentication and all capabilities unverified | Do not release |
| eMotion Max | `0x9EAC` | KeyValue implementation and automated tests | No device verified | Do not release |
| eMotion Max 2 | `0xD6AC` | Subdevice implementation and automated tests | No device verified | Do not release |
| eMotion Max 3 | `0xDEAC` | Subdevice implementation and automated tests | No device verified | Do not release |
| eHomeHA | `0x85AC` | TCP JSON-RPC implementation and automated tests | Learning, sending and persistence unverified | Do not release |
| eRemoteHA | `0x90AC` | TCP JSON-RPC implementation and automated tests | Learning, sending and persistence unverified | Do not release |
| eMotionPro | `0x6FAC` | KeyValue implementation and automated tests | Normal home-LAN eligibility and all controls unverified | Conditional; do not release |
| eMotionPro radar variant | `0xB9AC` with radar `0xACD9` | Firmware source and partial local reads | Correct Pro adapter and all controls unverified | Do not release |
| Legacy RM Monitor | `0x1B52` | Deliberately excluded | Obsolete generation | Abandoned |

## Required hardware evidence

For every model claimed in a release:

- Broadcast and specified-host discovery identify PID, MAC, model and firmware.
- Local authentication succeeds without an external service.
- Authentication recovers after session expiry, power loss and an IP change.
- Every claimed state is observed changing on the real device at least once.
- Every control is independently read back and physically confirmed.
- Missing optional hardware does not make unrelated state unavailable.
- Unsupported entities are not created.
- Test captures contain no credentials or identifying device data.

Ultra first-generation validation must cover its `0xACDB` radar, illuminance,
occupancy, count, four zones, X/Y/Z coordinates, calculated distance,
sensitivity, trigger speed, installation mode, height, direction, Z range,
absence delays and the local position subscription. Optional `0xACDC`
temperature and humidity must be tested only when that subdevice is listed.
Target speed remains unsupported unless a real local payload proves otherwise.

### Ultra first-generation observations

- The real Ultra first-generation unit reports device type `0x9CAC`, product
  name `eMotion Ultra`, and responds to specified-host discovery. Broadcast
  discovery has been intermittent and remains a release blocker.
- Authentication currently returns a short rejection frame for `0x9CAC`,
  `0xD7AC`, and `0xE3AC` with both compact BLC and full DNA framing, including
  when the client binds UDP port 80. No state or control claim is hardware
  verified until this is resolved.
- Firmware source identifies Ultra first-generation as the `cm_ha` product with
  a `0xACDB` 60 GHz radar. The earlier `0xACD9` observations came from a
  `0xB9AC` `rm_radar` product whose firmware identifies it as an eMotionPro
  variant; those observations are not Ultra evidence.
- Optional temperature/humidity, configuration writes, local position,
  power-cycle recovery and IP-change recovery all remain unverified.

Remote validation must cover learning, polling until a code is available,
sending the learned code, reconnecting, and command persistence after both the
device and Home Assistant restart. Command deletion is a Home Assistant storage
operation, not a device protocol command.

eMotionPro can proceed only if a real current-generation device can join a
normal home LAN, be discovered, authenticate, read all four fields and confirm
an absence-delay write. Otherwise this model remains unsupported.

## Deliberately unsupported

- Legacy RM Monitor type `0x1B52`.
- Firmware-created dynamic infrared virtual devices.
- Any capability requiring a non-local service.
