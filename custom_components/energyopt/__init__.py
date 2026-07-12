"""The EnergyOpt integration."""

from __future__ import annotations

from functools import partial

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr, entity_registry as er

from .const import (
    SELF_CONTROLLED_TYPES,
    CONF_API_KEY,
    CONF_BASE_URL,
    CONF_POLL_INTERVAL,
    CONF_SITE_ID,
    DEFAULT_POLL_INTERVAL,
)
from .coordinator import EnergyOptCoordinator

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.CALENDAR,
    Platform.SENSOR,
]

type EnergyOptConfigEntry = ConfigEntry[EnergyOptCoordinator]


async def async_setup_entry(
    hass: HomeAssistant, entry: EnergyOptConfigEntry
) -> bool:
    """Set up EnergyOpt from a config entry."""
    # Options (editable via the options flow) take precedence over the value
    # captured at initial setup in entry.data.
    poll_interval = entry.options.get(
        CONF_POLL_INTERVAL,
        entry.data.get(CONF_POLL_INTERVAL, DEFAULT_POLL_INTERVAL),
    )
    coordinator = EnergyOptCoordinator(
        hass,
        base_url=entry.data[CONF_BASE_URL],
        api_key=entry.data[CONF_API_KEY],
        site_id=entry.data[CONF_SITE_ID],
        poll_interval=poll_interval,
    )

    await coordinator.async_config_entry_first_refresh()

    # Cancel the coordinator's between-poll ticker when the entry unloads.
    entry.async_on_unload(coordinator.async_shutdown)

    entry.runtime_data = coordinator

    # Reload the entry when options change (e.g. a new poll interval).
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Prune entities/devices for devices that disappear from the schedule.
    entry.async_on_unload(
        coordinator.async_add_listener(
            partial(_prune_removed_devices, hass, entry, coordinator)
        )
    )

    return True


# Entity key suffixes used by the two platforms; must stay in sync with
# binary_sensor.py and sensor.py DEVICE_SENSORS. Device slugs contain
# underscores ("pump_2"), so prefix matching against device ids is ambiguous —
# the id is recovered by stripping a known key suffix instead.
_DEVICE_ENTITY_KEYS = (
    "should_run",
    "next_start",
    "next_end",
    "reason",
    "estimated_cost",
    "calendar",
)


def _device_id_from_unique_id(unique_id: str, entry_id: str) -> str | None:
    """Extract the device id from ``{entry_id}_{device_id}_{key}``; None if not ours."""
    prefix = f"{entry_id}_"
    if not unique_id.startswith(prefix):
        return None
    remainder = unique_id[len(prefix) :]
    for key in _DEVICE_ENTITY_KEYS:
        suffix = f"_{key}"
        if remainder.endswith(suffix) and len(remainder) > len(suffix):
            return remainder[: -len(suffix)]
    return None


@callback
def _prune_removed_devices(
    hass: HomeAssistant,
    entry: EnergyOptConfigEntry,
    coordinator: EnergyOptCoordinator,
) -> None:
    """Remove registry entries for devices no longer in the payload.

    Runs after each coordinator update. Pruning happens only when the last
    poll succeeded and produced a devices list, so a failed or stale poll
    never removes entities for a still-configured device. Site-level entities
    (``{entry_id}_site_*``) are always preserved.
    """
    if not coordinator.last_update_success:
        return
    data = coordinator.data
    if not isinstance(data, dict) or "devices" not in data:
        return
    devices = data.get("devices")
    if not isinstance(devices, list):
        return

    current_ids = {
        device["id"]
        for device in devices
        if isinstance(device, dict)
        and device.get("id")
        and device.get("type") not in SELF_CONTROLLED_TYPES
    }
    site_prefix = f"{entry.entry_id}_site_"

    ent_reg = er.async_get(hass)
    for reg_entry in er.async_entries_for_config_entry(ent_reg, entry.entry_id):
        unique_id = reg_entry.unique_id
        if unique_id.startswith(site_prefix):
            continue
        device_id = _device_id_from_unique_id(unique_id, entry.entry_id)
        if device_id is None or device_id in current_ids:
            continue
        ent_reg.async_remove(reg_entry.entity_id)

    # Drop device-registry entries left without any entities so the HA device
    # page for a removed device disappears too.
    dev_reg = dr.async_get(hass)
    remaining = er.async_entries_for_config_entry(ent_reg, entry.entry_id)
    used_device_ids = {e.device_id for e in remaining if e.device_id}
    for device in dr.async_entries_for_config_entry(dev_reg, entry.entry_id):
        if device.id not in used_device_ids:
            dev_reg.async_remove_device(device.id)


async def _async_update_listener(
    hass: HomeAssistant, entry: EnergyOptConfigEntry
) -> None:
    """Reload the entry when its options change."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(
    hass: HomeAssistant, entry: EnergyOptConfigEntry
) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
