"""Diagnostics support for the EnergyOpt integration."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from . import EnergyOptConfigEntry
from .const import CONF_API_KEY

TO_REDACT = {CONF_API_KEY}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: EnergyOptConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    coordinator = entry.runtime_data
    update_interval = coordinator.update_interval
    poll_interval = (
        update_interval.total_seconds() if update_interval is not None else None
    )

    return {
        "entry_data": async_redact_data(entry.data, TO_REDACT),
        "entry_options": dict(entry.options),
        "last_success_at": coordinator.last_success_at,
        "data_stale": coordinator.data_stale,
        "poll_interval": poll_interval,
        # The schedule payload contains no secrets.
        "data": coordinator.data,
    }
