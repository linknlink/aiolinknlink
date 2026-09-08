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

## Device branches requiring deliberate adaptation

The following branches change the shared client architecture and must not be
merged wholesale into the HACS branch:

- `feature/emotion-pro`
- `feature/emotion-max`
- `feature/emotion-remotes`
- `feature/emotion-ultra1`

Their commits contain useful protocol implementations and tests, but they
replace common files such as `client.py`, `models.py`, and
`protocol/dna.py`. Each supported model must therefore be ported into the
current client/profile architecture, with its Home Assistant entities and
hardware validation reviewed before release.

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
