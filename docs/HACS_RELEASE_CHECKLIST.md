# LinknLink HACS release checklist

## Repository and package

- [ ] `hacs.json` exists at the repository root.
- [ ] `custom_components/linknlink/manifest.json` is valid.
- [ ] The manifest version matches `pyproject.toml`.
- [ ] The bundled `custom_components/linknlink/aiolinknlink` library matches
      `src/aiolinknlink`.
- [ ] `python3 scripts/validate-hacs.py` passes.
- [ ] `python3 -m pytest tests/test_hacs_bundle.py` passes on Python 3.11+.

## Home Assistant

- [ ] Install from HACS into a clean Home Assistant instance.
- [ ] Restart Home Assistant and confirm the integration loads without import
      errors.
- [ ] Add at least one iBG, eHome, and eMotion device where hardware is
      available.
- [ ] Restart Home Assistant and confirm config entries reconnect.
- [ ] Confirm multiple gateways do not collide in the device registry.

## Device support policy

- [ ] Every device included in the release has automated tests.
- [ ] Every writable field has an independent read-back check.
- [ ] Unsupported or unverified device branches are not advertised as
      production-ready.
- [ ] No local keys, passwords, or device-identifying captures are committed.

## Release

- [ ] Update `CHANGELOG.md`.
- [ ] Update `manifest.json` and `pyproject.toml` to the same version.
- [ ] Create a Git tag in the form `v<version>`.
- [ ] Test the tagged revision through HACS before announcing it.
