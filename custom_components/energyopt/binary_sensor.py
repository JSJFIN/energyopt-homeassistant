"""Binary sensor platform for the EnergyOpt integration."""

from __future__ import annotations

from datetime import datetime, time
from typing import Any

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from . import EnergyOptConfigEntry
from .const import DOMAIN
from .coordinator import EnergyOptCoordinator


def _build_device_entities(
    coordinator: EnergyOptCoordinator, entry_id: str, device_id: str
) -> list[EnergyOptShouldRunBinarySensor]:
    """Build the binary sensor entities for a single device."""
    return [EnergyOptShouldRunBinarySensor(coordinator, entry_id, device_id)]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: EnergyOptConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up EnergyOpt binary sensors from a config entry.

    Devices are created for every device in the initial payload, then a
    coordinator listener creates entities for devices that appear in later
    polls (added in the web UI) without requiring an integration reload.
    """
    coordinator = entry.runtime_data
    known_ids: set[str] = set()

    @callback
    def _add_new_devices() -> None:
        """Add entities for any device not seen yet in coordinator data."""
        data = coordinator.data or {}
        devices = data.get("devices", [])
        current_ids = {
            device["id"]
            for device in devices
            if isinstance(device, dict) and device.get("id")
        }
        # Forget departed ids so a removed-then-readded device is recreated.
        known_ids.intersection_update(current_ids)

        new_entities: list[EnergyOptShouldRunBinarySensor] = []
        for device in devices:
            if not isinstance(device, dict):
                continue
            device_id = device.get("id")
            if not device_id or device_id in known_ids:
                continue
            known_ids.add(device_id)
            new_entities.extend(
                _build_device_entities(coordinator, entry.entry_id, device_id)
            )
        if new_entities:
            async_add_entities(new_entities)

    _add_new_devices()
    entry.async_on_unload(coordinator.async_add_listener(_add_new_devices))


class EnergyOptShouldRunBinarySensor(
    CoordinatorEntity[EnergyOptCoordinator], BinarySensorEntity
):
    """Binary sensor indicating whether a device should run now."""

    _attr_has_entity_name = True
    _attr_name = "Should run"
    _attr_icon = "mdi:flash"

    def __init__(
        self,
        coordinator: EnergyOptCoordinator,
        entry_id: str,
        device_id: str,
    ) -> None:
        """Initialize the binary sensor."""
        super().__init__(coordinator)
        self._device_id = device_id
        self._attr_unique_id = f"{entry_id}_{device_id}_should_run"
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
        """Return True whenever the device is present in retained data.

        A failed poll does not make the entity unavailable: the coordinator
        keeps the last known schedule, and state is computed locally from it.
        The entity is only unavailable if there has never been any data.
        """
        return self.coordinator.data is not None and self._get_device() is not None

    def _evaluate(self, device: dict[str, Any]) -> tuple[bool, bool]:
        """Compute (should_run, is_fallback) locally from the retained data."""
        now = dt_util.now()
        payload_fallback = bool(device.get("is_fallback"))

        # An active schedule window covering "now" always wins; a valid
        # upcoming cached window means the schedule is still authoritative.
        has_upcoming = False
        for window in device.get("schedule") or []:
            if not isinstance(window, dict):
                continue
            start = window.get("start")
            end = window.get("end")
            if not (isinstance(start, datetime) and isinstance(end, datetime)):
                continue
            if start <= now < end:
                return True, payload_fallback
            if start > now:
                has_upcoming = True

        # Fall back only when the cached schedule has nothing left to say:
        # prices ran out, or the data is stale AND holds no upcoming window.
        # A mere poll failure must not preempt a still-valid cached window.
        prices_until = self.coordinator.data.get("prices_loaded_until")
        exhausted = isinstance(prices_until, datetime) and prices_until < now
        if exhausted or (self.coordinator.data_stale and not has_upcoming):
            fallback = self._fallback_state(device, now)
            if fallback is not None:
                return fallback, True

        return False, payload_fallback

    @staticmethod
    def _fallback_state(device: dict[str, Any], now: datetime) -> bool | None:
        """Return the fallback on/off state, or None if no fallback configured.

        On iff the local wall-clock time is inside [fallback_start,
        fallback_end). A window whose end is <= start wraps across midnight.
        """
        start = device.get("fallback_start")
        end = device.get("fallback_end")
        if not isinstance(start, time) or not isinstance(end, time):
            return None
        current = now.time()
        if end <= start:
            return current >= start or current < end
        return start <= current < end

    @property
    def is_on(self) -> bool | None:
        """Return whether the device should run now, computed locally."""
        device = self._get_device()
        if device is None:
            return None
        state, _ = self._evaluate(device)
        return state

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return additional device and schedule attributes."""
        device = self._get_device() or {}
        _, is_fallback = self._evaluate(device) if device else (False, False)
        return {
            "device_id": device.get("id"),
            "device_name": device.get("name"),
            "reason": device.get("reason"),
            "next_start": device.get("next_start"),
            "next_end": device.get("next_end"),
            "estimated_cost_eur": device.get("estimated_cost_eur"),
            "schedule": device.get("schedule"),
            "server_should_run": device.get("should_run_now"),
            "is_fallback": is_fallback,
            "last_success": self.coordinator.last_success_at,
            "data_stale": self.coordinator.data_stale,
            "last_update": self.coordinator.data.get("updated_at"),
        }
