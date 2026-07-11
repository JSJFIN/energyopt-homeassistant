"""Sensor platform for the EnergyOpt integration."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import EnergyOptConfigEntry
from .const import DOMAIN, SELF_CONTROLLED_TYPES
from .coordinator import EnergyOptCoordinator


@dataclass(frozen=True, kw_only=True)
class EnergyOptDeviceSensorDescription(SensorEntityDescription):
    """Describes an EnergyOpt per-device sensor."""

    value_fn: Callable[[dict[str, Any]], Any]


@dataclass(frozen=True, kw_only=True)
class EnergyOptSiteSensorDescription(SensorEntityDescription):
    """Describes an EnergyOpt site-level sensor."""

    value_fn: Callable[[dict[str, Any]], Any]


DEVICE_SENSORS: tuple[EnergyOptDeviceSensorDescription, ...] = (
    EnergyOptDeviceSensorDescription(
        key="next_start",
        translation_key="next_start",
        # "begins/ends" (not "start/end") so alphabetical entity lists show
        # the start before the end.
        name="Next run begins",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=lambda device: device.get("next_start"),
    ),
    EnergyOptDeviceSensorDescription(
        key="next_end",
        translation_key="next_end",
        name="Next run ends",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=lambda device: device.get("next_end"),
    ),
    EnergyOptDeviceSensorDescription(
        key="reason",
        translation_key="reason",
        name="Reason",
        icon="mdi:information-outline",
        value_fn=lambda device: device.get("reason"),
    ),
    EnergyOptDeviceSensorDescription(
        key="estimated_cost",
        translation_key="estimated_cost",
        name="Estimated cost",
        icon="mdi:currency-eur",
        native_unit_of_measurement="EUR",
        suggested_display_precision=2,
        value_fn=lambda device: device.get("estimated_cost_eur"),
    ),
)

SITE_SENSORS: tuple[EnergyOptSiteSensorDescription, ...] = (
    EnergyOptSiteSensorDescription(
        key="prices_loaded_until",
        translation_key="prices_loaded_until",
        name="Prices loaded until",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=lambda data: data.get("prices_loaded_until"),
    ),
    EnergyOptSiteSensorDescription(
        key="status",
        translation_key="status",
        name="Status",
        icon="mdi:heart-pulse",
        value_fn=lambda data: data.get("status"),
    ),
)


def _build_device_entities(
    coordinator: EnergyOptCoordinator, entry_id: str, device_id: str
) -> list[SensorEntity]:
    """Build the per-device sensor entities for a single device."""
    return [
        EnergyOptDeviceSensor(coordinator, entry_id, device_id, description)
        for description in DEVICE_SENSORS
    ]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: EnergyOptConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up EnergyOpt sensors from a config entry.

    Per-device sensors are created for the initial payload, then a
    coordinator listener creates sensors for devices that appear in later
    polls (added in the web UI) without requiring an integration reload.
    Site-level sensors are created once here.
    """
    coordinator = entry.runtime_data
    known_ids: set[str] = set()

    @callback
    def _add_new_devices() -> None:
        """Add sensors for any device not seen yet in coordinator data."""
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

        new_entities: list[SensorEntity] = []
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
            EnergyOptSiteSensor(coordinator, entry.entry_id, description)
            for description in SITE_SENSORS
        ]
    )
    entry.async_on_unload(coordinator.async_add_listener(_add_new_devices))


class EnergyOptDeviceSensor(
    CoordinatorEntity[EnergyOptCoordinator], SensorEntity
):
    """A per-device EnergyOpt sensor."""

    entity_description: EnergyOptDeviceSensorDescription
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: EnergyOptCoordinator,
        entry_id: str,
        device_id: str,
        description: EnergyOptDeviceSensorDescription,
    ) -> None:
        """Initialize the device sensor."""
        super().__init__(coordinator)
        self.entity_description = description
        self._device_id = device_id
        self._attr_unique_id = f"{entry_id}_{device_id}_{description.key}"
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

        A failed poll keeps the last known values instead of flipping the
        entity unavailable; only a total absence of data is unavailable.
        """
        return self.coordinator.data is not None and self._get_device() is not None

    @property
    def native_value(self) -> datetime | str | float | None:
        """Return the sensor value."""
        device = self._get_device()
        if device is None:
            return None
        return self.entity_description.value_fn(device)


class EnergyOptSiteSensor(
    CoordinatorEntity[EnergyOptCoordinator], SensorEntity
):
    """A site-level EnergyOpt sensor."""

    entity_description: EnergyOptSiteSensorDescription
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: EnergyOptCoordinator,
        entry_id: str,
        description: EnergyOptSiteSensorDescription,
    ) -> None:
        """Initialize the site sensor."""
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
    def native_value(self) -> datetime | str | float | None:
        """Return the sensor value.

        The site status reports "stale" while the last successful poll is
        older than the stale window, otherwise the server-reported status.
        """
        if self.entity_description.key == "status" and self.coordinator.data_stale:
            return "stale"
        return self.entity_description.value_fn(self.coordinator.data)
