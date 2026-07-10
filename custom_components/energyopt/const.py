"""Constants for the EnergyOpt integration."""

DOMAIN = "energyopt"

CONF_BASE_URL = "base_url"
CONF_API_KEY = "api_key"
CONF_SITE_ID = "site_id"
CONF_POLL_INTERVAL = "poll_interval"

DEFAULT_BASE_URL = "https://energyopt.ailabra.org"
DEFAULT_SITE_ID = "demo"
DEFAULT_POLL_INTERVAL = 300

# Data is considered stale once the last successful poll is older than this
# multiple of the poll interval. Stale data still drives entity state (from the
# last known schedule / fallback) instead of flipping entities unavailable.
STALE_MULTIPLIER = 3

# How often the coordinator nudges entities to re-evaluate time-based state
# (schedule window boundaries, staleness) between polls, in seconds.
TICK_INTERVAL_SECONDS = 60
