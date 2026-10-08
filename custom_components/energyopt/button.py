"""An explicit, rate-limited refresh for the site's local price snapshot."""

from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import EnergyOptConfigEntry
from .const import DOMAIN
from .coordinator import EnergyOptCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: EnergyOptConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    async_add_entities(
        [EnergyOptRefreshPricesButton(entry.runtime_data, entry.entry_id)]
    )


class EnergyOptRefreshPricesButton(
    CoordinatorEntity[EnergyOptCoordinator], ButtonEntity
):
    _attr_has_entity_name = True
    _attr_name = "Refresh prices"
    _attr_icon = "mdi:refresh"
    _attr_translation_key = "refresh_prices"

    def __init__(self, coordinator: EnergyOptCoordinator, entry_id: str) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry_id}_site_refresh_prices"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{entry_id}_site")},
            name=f"EnergyOpt site {coordinator.data.get('site_id', 'site')}",
            manufacturer="EnergyOpt",
            model="site",
        )

    @property
    def available(self) -> bool:
        return self.coordinator.data is not None or self.coordinator.prices.loaded

    async def async_press(self) -> None:
        await self.coordinator.async_refresh_prices()
