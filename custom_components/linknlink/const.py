"""Constants for the LinknLink integration."""

from homeassistant.const import Platform

DOMAIN = "linknlink"
CONF_HOST = "host"
CONF_LOCAL_KEY = "local_key"
PLATFORMS = (Platform.SENSOR, Platform.BINARY_SENSOR, Platform.EVENT, Platform.SWITCH, Platform.NUMBER)
UPDATE_INTERVAL_SECONDS = 30
