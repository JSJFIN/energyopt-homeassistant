"""Binary sensor platform for the EnergyOpt integration."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, time
from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from . import EnergyOptConfigEntry
from .const import DOMAIN, SELF_CONTROLLED_TYPES
from .coordinator import EnergyOptCoordinator


def _nullable_bool(value: Any) -> bool | None:
    """Return None for a null/missing payload value, else its truthiness."""
    if value is None:
        return None
    return bool(value)


@dataclass(frozen=True, kw_only=True)
class EnergyOptSiteBinarySensorDescription(BinarySensorEntityDescription):
    """Describes an EnergyOpt site-level binary sensor.

    ``is_on_fn`` receives the payload and the coordinator staleness flag and
    returns True/False, or None to report an unknown state.
    """

    is_on_fn: Callable[[dict[str, Any], bool], bool | None]


SITE_BINARY_SENSORS: tuple[EnergyOptSiteBinarySensorDescription, ...] = (
    EnergyOptSiteBinarySensorDescription(
        key="prices_loaded",
        translation_key="prices_loaded",
        name="Prices loaded",
        icon="mdi:database-check",
        # Stale cache must not claim prices are fine.
        is_on_fn=lambda data, stale: False
        if stale
        else bool(data.get("prices_loaded")),
    ),
    EnergyOptSiteBinarySensorDescription(
        key="cheap_now",
        translation_key="cheap_now",
        name="Cheap now",
        icon="mdi:arrow-down-bold-circle",
        is_on_fn=lambda data, stale: _nullable_bool(data.get("cheap_now")),
    ),
    EnergyOptSiteBinarySensorDescription(
        key="expensive_now",
        translation_key="expensive_now",
        name="Expensive now",
        icon="mdi:arrow-up-bold-circle",
        is_on_fn=lambda data, stale: _nullable_bool(data.get("expensive_now")),
    ),
)


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
    Site-level binary sensors are created once here.
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
            if isinstance(device, dict)
            and device.get("id")
            and device.get("type") not in SELF_CONTROLLED_TYPES
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
            if device.get("type") in SELF_CONTROLLED_TYPES:
                # Self-controlled (e.g. Shelly-script) devices get no HA
                # entities: one controller per device.
                continue
            known_ids.add(device_id)
            new_entities.extend(
                _build_device_entities(coordinator, entry.entry_id, device_id)
            )
        if new_entities:
            async_add_entities(new_entities)

    _add_new_devices()
    async_add_entities(
        [
            EnergyOptSiteBinarySensor(coordinator, entry.entry_id, description)
            for description in SITE_BINARY_SENSORS
        ]
    )
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
        """Compute (should_run, is_fallback) locally from the retained data.

        Precedence ladder (solar_excess_spec.md §1): disabled → off; else
        schedule_on OR solar_on → on; else fallback. Manual override is not yet
        sourced in this integration and is intentionally omitted.
        """
        now = dt_util.now()
        payload_fallback = bool(device.get("is_fallback"))

        # Precedence rung 1 (docs/solar_excess_spec.md): an active manual
        # override beats schedule, solar, and fallback alike. Forced ON is
        # already expressed as a synthetic schedule window; this guard is what
        # makes forced OFF hold against solar.
        override_until = device.get("override_until")
        if (
            device.get("override_state") in ("on", "off")
            and isinstance(override_until, datetime)
            and override_until > now
        ):
            return device.get("override_state") == "on", False

        # Disabled devices never run, sun or not (belt-and-suspenders: the
        # payload only carries this flag when the backend sends it).
        if device.get("enabled") is False:
            return False, False

        # An active schedule window covering "now" always wins; a valid
        # upcoming cached window means the schedule is still authoritative.
        covers_now, has_upcoming = self.coordinator.schedule_window_status(device, now)
        if covers_now:
            return True, payload_fallback

        # Solar sits above fallback: it depends on no cloud data, so a stale
        # site with sun runs on solar_on and never reaches the fallback rung.
        solar = self.coordinator.get_solar(device)
        if solar is not None and solar.solar_on:
            return True, False

        # Fall back only when the cached schedule has nothing left to say and
        # solar is not carrying the device: prices ran out, or the data is
        # stale AND holds no upcoming window. A mere poll failure must not
        # preempt a still-valid cached window.
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
        final_on, is_fallback = self._evaluate(device) if device else (False, False)
        solar = self.coordinator.get_solar(device) if device else None
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
            # Solar adds attributes; it never rewrites the server reason (§6).
            "solar_active": bool(solar is not None and solar.solar_on and final_on),
            "solar_excess_w": solar.excess_w if solar is not None else None,
            "solar_hold_until": solar.hold_until if solar is not None else None,
            "solar_reason": self.coordinator.solar_reason(device, final_on)
            if device
            else None,
        }


class EnergyOptSiteBinarySensor(
    CoordinatorEntity[EnergyOptCoordinator], BinarySensorEntity
):
    """A site-level EnergyOpt binary sensor."""

    entity_description: EnergyOptSiteBinarySensorDescription
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: EnergyOptCoordinator,
        entry_id: str,
        description: EnergyOptSiteBinarySensorDescription,
    ) -> None:
        """Initialize the site binary sensor."""
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{entry_id}_site_{description.key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{entry_id}_site")},
            name=f"EnergyOpt site {coordinator.data.get('site_id', 'site')}",
            manufacturer="EnergyOpt",
            model="site",
        )

    @property
    def available(self) -> bool:
        """Return True whenever data is retained, even after a failed poll."""
        return self.coordinator.data is not None

    @property
    def is_on(self) -> bool | None:
        """Return the site state, or None when the payload value is absent."""
        data = self.coordinator.data
        if data is None:
            return None
        return self.entity_description.is_on_fn(data, self.coordinator.data_stale)
