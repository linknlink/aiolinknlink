"""Constants for the LinknLink integration."""

from homeassistant.const import Platform

DOMAIN = "linknlink"
CONF_HOST = "host"
CONF_LOCAL_KEY = "local_key"
CONF_DEVICE_TYPE = "device_type"
DEVICE_TYPE_IBG = "ibg"
DEVICE_TYPE_EMOTION = "emotion"
DEVICE_TYPE_ULTRA = "ultra"
DEVICE_TYPE_ULTRA2 = "ultra2"
DEVICE_TYPE_EHOME = "ehome"
DEVICE_TYPE_EHUB = "ehub"
DEVICE_TYPE_REMOTE = "remote"
DEVICE_TYPE_ETHS = "eths"
DEVICE_TYPE_ZHA_QUIRK = "zha_quirk"

# ZHA quirk auto-injection paths
QUIRKS_DIR = "/config/custom_components/linknlink/zha_quirks"
CONFIGURATION_YAML = "/config/configuration.yaml"

PLATFORMS = (
    Platform.SENSOR,
    Platform.BINARY_SENSOR,
    Platform.EVENT,
    Platform.SWITCH,
    Platform.NUMBER,
    Platform.CLIMATE,
    Platform.REMOTE,
)
UPDATE_INTERVAL_SECONDS = 30
UNAVAILABLE_RETRY_INTERVAL_SECONDS = 300


def resolve_local_key_hex(configured_key_hex: str, negotiated_key: bytes | None) -> str:
    """Prefer a configured local key, otherwise retain the negotiated key."""
    if configured_key_hex:
        return configured_key_hex
    return negotiated_key.hex() if negotiated_key is not None else ""
