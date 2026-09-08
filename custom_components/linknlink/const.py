"""Constants for the LinknLink integration."""

from homeassistant.const import Platform

DOMAIN = "linknlink"
CONF_HOST = "host"
CONF_LOCAL_KEY = "local_key"
PLATFORMS = (
    Platform.SENSOR,
    Platform.BINARY_SENSOR,
    Platform.EVENT,
    Platform.SWITCH,
    Platform.NUMBER,
    Platform.CLIMATE,
)
UPDATE_INTERVAL_SECONDS = 30


def resolve_local_key_hex(configured_key_hex: str, negotiated_key: bytes | None) -> str:
    """Prefer a configured local key, otherwise retain the negotiated key."""
    if configured_key_hex:
        return configured_key_hex
    return negotiated_key.hex() if negotiated_key is not None else ""
