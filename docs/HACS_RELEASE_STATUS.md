# HACS release candidate status

Date: 2026-09-09
Target release: `0.19.1`

## Included in the current candidate

The `feature/hacs-integration` candidate is based on the validated
`feature/ehome` line and includes:

- iBG2 SE discovery, authentication, session-key persistence, and push updates.
- The validated iBG subdevice profiles listed in `README.md`.
- eHome, eTHS, and eHub host sensor support with related Home Assistant
  entities.
- eHub infrared remote support.
- eMotion, eMotion Pro, eMotion Ultra/Ultra1, eMotion Ultra2, and eMotion Max
  protocol adapters, HA entities, and automated tests.
- eHomeHA/eRemoteHA local infrared remote support.
- A self-contained Home Assistant package under
  `custom_components/linknlink`.
- The bundled client library takes precedence over any stale development
  checkout under `/config/deps/aiolinknlink`.

These profiles are included in the code candidate, but production
advertisement still depends on recording real-device validation for each
model. A passing protocol test alone does not prove compatibility with every
firmware revision.

## Not yet release-complete

- eHub RF learning, frequency scan, study/cancel-study, and RF replay have
  protocol constants and raw-send support, but are not exposed as HA services
  until a real learn/send sequence is validated.
- Clean-instance HACS installation and multi-gateway restart/recovery checks
  still need to be recorded.

## Checks completed locally

- HACS layout validation passed.
- Python 3.11 bytecode compilation passed.
- Python 3.11 pytest passed: 300 tests passed, 3 skipped.
- Ruff check and format validation passed.
- mypy passed for all 16 source modules.
- Source and bundled client-library contents are byte-identical.
- Git worktree is clean after each committed step.

## Checks still requiring a Home Assistant or hardware environment

The automated Python checks run in an isolated Python 3.11 environment.
The remaining release checks require a clean Home Assistant instance and
physical devices: HACS installation, restart/recovery, multi-gateway registry
collision checks, and per-profile field validation.
