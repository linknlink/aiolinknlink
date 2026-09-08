# HACS release candidate status

Date: 2026-09-08

## Included in the current candidate

The `feature/hacs-integration` candidate is based on the validated
`feature/ehome` line and includes:

- iBG2 SE discovery, authentication, session-key persistence, and push updates.
- The validated iBG subdevice profiles listed in `README.md`.
- eHome/EHUB sensor support and the related Home Assistant entities.
- The previously validated local eMotion Ultra and Ultra2 support present on
  the `feature/ehome` line.
- A self-contained Home Assistant package under
  `custom_components/linknlink`.

## Deliberately not included yet

The following branches require protocol-layer adaptation rather than a
whole-branch merge:

- `feature/emotion-pro`
- `feature/emotion-max`
- `feature/emotion-remotes`
- `feature/emotion-ultra1`

Their implementations remain available for the next device-specific porting
steps. They must not be advertised as part of this release candidate until
their shared client changes, Home Assistant entities, and hardware validation
are integrated.

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
