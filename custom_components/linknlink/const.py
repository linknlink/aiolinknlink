"""Constants for the LinknLink integration."""

from homeassistant.const import Platform

DOMAIN = "linknlink"
CONF_HOST = "host"
PLATFORMS = (Platform.SENSOR, Platform.BINARY_SENSOR, Platform.EVENT)
UPDATE_INTERVAL_SECONDS = 30
