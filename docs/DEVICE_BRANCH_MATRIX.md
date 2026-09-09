# Device branch and HACS release matrix

The HACS release is built from one integration branch. Device feature branches
remain available for development, but they are not independently installable
through HACS.

## Current integration baseline

| Branch | Role | HACS status |
| --- | --- | --- |
| `main` | Stable upstream baseline | Not yet HACS-ready |
| `feature/hacs-integration` | HACS packaging and release candidate | Current working branch |
| `feature/ehome` | Most complete validated iBG/eHome feature line | Integrated into the HACS branch |
| `feature/ibg-integration-hardening` | iBG hardening line with eMotion changes reverted | Not used as the release base |

## Device branch disposition

The following device branches have been deliberately ported into
`feature/hacs-integration`; they must not be merged wholesale in the future
because they replace shared protocol files:

| Branch | Ported scope | Current status |
| --- | --- | --- |
| `feature/emotion-pro` | eMotion Pro and Pro radar protocol, entities, and tests | Code and automated tests integrated; field validation tracked separately |
| `feature/emotion-max` | eMotion Max protocol variants, entities, and tests | Code and automated tests integrated; field validation tracked separately |
| `feature/emotion-remotes` | Local infrared remote protocol and HA remote entity | Code and automated tests integrated; real learn/send validation tracked separately |
| `feature/emotion-ultra1` | First-generation eMotion Ultra protocol and entities | Code and automated tests integrated; field validation tracked separately |

The current HACS branch is the source of truth for these devices. The feature
branches remain historical development references and should only be used to
port a missing fix after reviewing it against the shared client/profile
architecture.

## Current support layers

| Layer | Included profiles | Verification state |
| --- | --- | --- |
| iBG gateway and reviewed iBG subdevices | iBG2 SE plus the documented RF, Modbus, DLT645, DTU, lighting, sensor, and air-conditioner profiles | Hardware validation completed for the devices previously verified in the handover; regression testing remains |
| eMotion local devices | eMotion, eMotion Pro, Ultra, Ultra1, Ultra2, and Max variants | Protocol tests and HA entity tests integrated; hardware validation must be recorded per model |
| eHome/eTHS/eHub | eHome, eTHS, eHub host sensors, and eHub infrared remote | Protocol tests and HA entity tests integrated; eHub RF capabilities remain incomplete |
| eHomeHA/eRemoteHA | Local infrared remote devices | Protocol and HA remote tests integrated; real learn/send validation remains |

## Release policy

Only a device profile with passing automated tests and completed real-device
validation should be advertised in a HACS release. An unverified profile may
remain on its feature branch or behind an explicit experimental flag, but it
must not be silently exposed as production-ready.

Before every release:

1. Select the device commits to include.
2. Port or cherry-pick them into `feature/hacs-integration`.
3. Run the full test and HACS validation suite.
4. Update `CHANGELOG.md`, `manifest.json`, and `pyproject.toml`.
5. Tag the tested commit and publish it through HACS.
