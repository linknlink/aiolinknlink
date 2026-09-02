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
| eMotionPro | `feature/emotion-pro` | Complete | `0xB9AC` token authentication, all four states and absence-delay control confirmed; `0x6FAC` unverified | Do not release |

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
| eMotionPro | Temperature, humidity, occupancy and absence delay | Variant-specific minute/second absence delay with read-back | No climate or air-conditioner control |

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
| eMotionPro radar variant | `0xB9AC` with radar `0xACD9` | Implementation and automated tests | Factory-reset reprovisioning, LAN join, specified-host discovery, token authentication, reauthentication, temperature, humidity, occupancy change and absence-delay write/restore confirmed; power-cycle/IP recovery remains unverified | Do not release |
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
  name `eMotion Ultra`, and responds to specified-host and directed-broadcast
  discovery. Repeated broadcast reliability remains to be measured.
- Its discovery payload reports status flags `0x02` and local lock state `1`.
  The correct wire-order MAC reaches the terminal-add handler, which returns
  message `0x03E9`, status `0xFFFF`, and no key because pairing is locked.
- Firmware source proves that legacy terminal pairing uses an 80-byte aligned
  structure. A 100-byte request is treated as the authenticated newer packet
  form, so the library now uses the 80-byte form and the reversed wire MAC.
- Firmware source also proves that Wi-Fi provisioning returns the persistent
  16-byte local control key as `token`. The current unit was provisioned before
  that token was captured, so the provided-key path is automated but not yet
  hardware verified.
- Firmware source identifies Ultra first-generation as the `cm_ha` product with
  a `0xACDB` 60 GHz radar. The earlier `0xACD9` observations came from a
  `0xB9AC` `rm_radar` product whose firmware identifies it as an eMotionPro
  variant; those observations are not Ultra evidence.
- Optional temperature/humidity, configuration writes, local position,
  power-cycle recovery and IP-change recovery all remain unverified. Hardware
  validation requires reprovisioning the unit while securely capturing its
  local token; the token must never be committed as a fixture.

Remote validation must cover learning, polling until a code is available,
sending the learned code, reconnecting, and command persistence after both the
device and Home Assistant restart. Command deletion is a Home Assistant storage
operation, not a device protocol command.

The KeyValue `0x6FAC` eMotionPro can proceed only if a real current-generation
device can join a normal home LAN, be discovered, authenticate, read all four
fields and confirm an absence-delay write. The subdevice-based `0xB9AC` variant
requires temperature and humidity only when its optional climate peripheral is
listed. Otherwise the corresponding variant remains unsupported.

The observed `0xB9AC` variant identifies itself as `rm_radar`, joins a normal
home LAN and returns a persistent local token during Wi-Fi provisioning. After
a factory reset, encrypted provisioning returned a new token; specified-host
discovery, provided-token authentication and explicit reauthentication all
succeeded with that token. Authenticated commands use `0xE3AC`/`0x006A`.
Occupancy was observed changing from false to true. The reported absence delay
was changed from 60 to 61 seconds, independently read back, then restored to 60
seconds and read back again. A later repeat encountered one transient timeout
after the restore write; a fresh independent session confirmed that the device
was at the original 60-second value. A bounded post-write retry was then added
without relaxing ACK or value validation; the full change and restore sequence
passed on the device. A reconstructed provided-token session also completed a
fresh state command. The persisted peripheral list returns status `-1` on this
firmware even though direct derived-DID reads of the `0xACDC` peripheral return
valid temperature and humidity. The adapter now uses the derived DID as an
optional fallback, and both values were observed changing across real samples
without affecting radar availability. The public Pro adapter deliberately
excludes illuminance and all climate control even though this firmware family
has internal implementations. Factory-reset reprovisioning does not prove
normal power-cycle persistence. Device power-cycle, IP-change recovery and
physical absence-delay timing confirmation remain unverified, so this variant
is not release-ready.

## Deliberately unsupported

- Legacy RM Monitor type `0x1B52`.
- Firmware-created dynamic infrared virtual devices.
- Any capability requiring a non-local service.
