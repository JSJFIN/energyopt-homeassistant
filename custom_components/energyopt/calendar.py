"""Calendar platform for the EnergyOpt integration."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from homeassistant.components.calendar import CalendarEntity, CalendarEvent
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from . import EnergyOptConfigEntry
from .const import CONF_ENABLE_CALENDARS, DOMAIN, SELF_CONTROLLED_TYPES
from .coordinator import EnergyOptCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: EnergyOptConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up EnergyOpt calendars from a config entry.

    One calendar is created per (non-self-controlled) device in the initial
    payload, then a coordinator listener creates calendars for devices that
    appear in later polls without requiring an integration reload. Calendars
    are optional: when disabled in the entry options, none are created and
    previously created ones are removed (options changes reload the entry,
    so toggling takes effect immediately).
    """
    if not entry.options.get(CONF_ENABLE_CALENDARS, True):
        ent_reg = er.async_get(hass)
        for reg_entry in er.async_entries_for_config_entry(ent_reg, entry.entry_id):
            if reg_entry.unique_id.endswith("_calendar"):
                ent_reg.async_remove(reg_entry.entity_id)
        return

    coordinator = entry.runtime_data
    known_ids: set[str] = set()

    @callback
    def _add_new_devices() -> None:
        """Add a calendar for any device not seen yet in coordinator data."""
        data = coordinator.data or {}
        devices = data.get("devices", [])
        current_ids = {
            device["id"]
            for device in devices
            if isinstance(device, dict)
            and device.get("id")
            and device.get("type") not in SELF_CONTROLLED_TYPES
        }
        # Forget departed ids so a removed-then-readded device is recreated.
        known_ids.intersection_update(current_ids)

        new_entities: list[EnergyOptDeviceCalendar] = []
        for device in devices:
            if not isinstance(device, dict):
                continue
            device_id = device.get("id")
            if not device_id or device_id in known_ids:
                continue
            if device.get("type") in SELF_CONTROLLED_TYPES:
                continue
            known_ids.add(device_id)
            new_entities.append(
                EnergyOptDeviceCalendar(coordinator, entry.entry_id, device_id)
            )
        if new_entities:
            async_add_entities(new_entities)

    _add_new_devices()
    entry.async_on_unload(coordinator.async_add_listener(_add_new_devices))


class EnergyOptDeviceCalendar(
    CoordinatorEntity[EnergyOptCoordinator], CalendarEntity
):
    """A calendar of scheduled run windows for a single device."""

    _attr_has_entity_name = True
    _attr_name = "Schedule"

    def __init__(
        self,
        coordinator: EnergyOptCoordinator,
        entry_id: str,
        device_id: str,
    ) -> None:
        """Initialize the device calendar."""
        super().__init__(coordinator)
        self._device_id = device_id
        self._attr_unique_id = f"{entry_id}_{device_id}_calendar"
        device = self._get_device() or {}
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{entry_id}_{device_id}")},
            name=self._device_name,
            manufacturer="EnergyOpt",
            model=device.get("type") or "device",
        )

    def _get_device(self) -> dict[str, Any] | None:
        """Return the device dict from coordinator data, if present."""
        for device in self.coordinator.data.get("devices", []):
            if device.get("id") == self._device_id:
                return device
        return None

    @property
    def _device_name(self) -> str:
        """Return the current device name, falling back to the id."""
        device = self._get_device()
        if device and device.get("name"):
            return device["name"]
        return self._device_id

    @property
    def available(self) -> bool:
        """Return True whenever the device is present in retained data."""
        return self.coordinator.data is not None and self._get_device() is not None

    def _windows(self) -> list[tuple[datetime, datetime]]:
        """Return schedule windows as (start, end) with tz-aware datetimes only.

        Absent, malformed, or naive datetimes are skipped: a CalendarEvent
        requires timezone-aware boundaries.
        """
        device = self._get_device() or {}
        windows: list[tuple[datetime, datetime]] = []
        for window in device.get("schedule") or []:
            if not isinstance(window, dict):
                continue
            start = window.get("start")
            end = window.get("end")
            if not (isinstance(start, datetime) and isinstance(end, datetime)):
                continue
            if start.tzinfo is None or end.tzinfo is None:
                continue
            windows.append((start, end))
        windows.sort(key=lambda w: w[0])
        return windows

    def _make_event(self, start: datetime, end: datetime) -> CalendarEvent:
        """Build a CalendarEvent for a single run window."""
        return CalendarEvent(
            start=start,
            end=end,
            summary=f"{self._device_name} runs",
        )

    @property
    def event(self) -> CalendarEvent | None:
        """Return the current or next scheduled run window."""
        now = dt_util.now()
        for start, end in self._windows():
            if end > now:
                return self._make_event(start, end)
        return None

    async def async_get_events(
        self,
        hass: HomeAssistant,
        start_date: datetime,
        end_date: datetime,
    ) -> list[CalendarEvent]:
        """Return all run windows overlapping the requested range."""
        events: list[CalendarEvent] = []
        for start, end in self._windows():
            # Overlap iff the window starts before the range ends and ends
            # after the range starts.
            if start < end_date and end > start_date:
                events.append(self._make_event(start, end))
        return events
