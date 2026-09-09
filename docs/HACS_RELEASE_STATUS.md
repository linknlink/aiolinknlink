# HACS release candidate status

Date: 2026-09-08

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

These profiles are included in the code candidate, but production
advertisement still depends on recording real-device validation for each
model. A passing protocol test alone does not prove compatibility with every
firmware revision.

## Not yet release-complete

- eHub RF learning, frequency scan, study/cancel-study, and RF replay have
  protocol constants and raw-send support, but are not exposed as HA services
  until a real learn/send sequence is validated.
- Full Python 3.11+ test, Ruff, mypy, and Home Assistant integration checks
  still need to run in a dependency-complete environment.
- Clean-instance HACS installation and multi-gateway restart/recovery checks
  still need to be recorded.

## Checks completed locally

- HACS layout validation passed.
- Python 3.11 bytecode compilation passed.
- Source and bundled client-library contents are byte-identical.
- Git worktree is clean after each committed step.

## Checks still requiring the CI environment

The development machine currently has Python 3.9 as its default interpreter,
while the project requires Python 3.11 or newer. Full pytest, Ruff, mypy, and
Home Assistant platform tests must therefore run in the repository CI matrix
or another Python 3.11+ environment.
