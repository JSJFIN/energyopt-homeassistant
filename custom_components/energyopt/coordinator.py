"""DataUpdateCoordinator for the EnergyOpt integration."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta
import logging
from typing import Any

import aiohttp

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .const import DOMAIN, STALE_MULTIPLIER, TICK_INTERVAL_SECONDS

_LOGGER = logging.getLogger(__name__)

REQUEST_TIMEOUT = 30


class EnergyOptCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Poll the EnergyOpt schedule endpoint and expose parsed data."""

    def __init__(
        self,
        hass: HomeAssistant,
        base_url: str,
        api_key: str,
        site_id: str,
        poll_interval: int,
    ) -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=poll_interval),
        )
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._site_id = site_id
        self._session = async_get_clientsession(hass)
        self.last_success_at: datetime | None = None
        self._unsub_ticker: Callable[[], None] | None = None

    @property
    def schedule_url(self) -> str:
        """Return the schedule endpoint URL for the configured site."""
        return f"{self._base_url}/v1/sites/{self._site_id}/schedule"

    @property
    def data_stale(self) -> bool:
        """Return True if the last successful poll is older than the stale window."""
        if self.last_success_at is None or self.update_interval is None:
            return False
        age = dt_util.utcnow() - self.last_success_at
        return age > self.update_interval * STALE_MULTIPLIER

    async def async_config_entry_first_refresh(self) -> None:
        """Do the first refresh, then start the between-poll ticker."""
        await super().async_config_entry_first_refresh()
        self._start_ticker()

    def _start_ticker(self) -> None:
        """Start a periodic tick so entities re-evaluate time-based state."""
        if self._unsub_ticker is not None:
            return
        self._unsub_ticker = async_track_time_interval(
            self.hass,
            self._handle_tick,
            timedelta(seconds=TICK_INTERVAL_SECONDS),
        )

    @callback
    def _handle_tick(self, now: datetime) -> None:
        """Nudge entities to recompute state between polls."""
        self.async_update_listeners()

    async def async_shutdown(self) -> None:
        """Cancel the ticker and shut down the coordinator."""
        if self._unsub_ticker is not None:
            self._unsub_ticker()
            self._unsub_ticker = None
        await super().async_shutdown()

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch and parse the latest schedule payload."""
        headers = {"Authorization": f"Bearer {self._api_key}"}
        try:
            async with self._session.get(
                self.schedule_url,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT),
            ) as response:
                if response.status in (401, 403):
                    raise UpdateFailed("Invalid authentication")
                response.raise_for_status()
                data: dict[str, Any] = await response.json()
        except aiohttp.ClientError as err:
            raise UpdateFailed(f"Error communicating with EnergyOpt API: {err}") from err

        parsed = self._parse(data)
        self.last_success_at = dt_util.utcnow()
        return parsed

    @staticmethod
    def _parse(data: dict[str, Any]) -> dict[str, Any]:
        """Parse ISO datetime strings and fallback times into objects.

        All new keys are optional; older backends omit them and are handled
        by the ``isinstance`` guards.
        """
        for device in data.get("devices", []):
            for key in ("next_start", "next_end", "override_until"):
                value = device.get(key)
                if isinstance(value, str):
                    device[key] = dt_util.parse_datetime(value)
            for window in device.get("schedule") or []:
                if not isinstance(window, dict):
                    continue
                for key in ("start", "end"):
                    value = window.get(key)
                    if isinstance(value, str):
                        window[key] = dt_util.parse_datetime(value)
            for key in ("fallback_start", "fallback_end"):
                value = device.get(key)
                if isinstance(value, str):
                    device[key] = dt_util.parse_time(value)
        for key in ("prices_loaded_until", "updated_at"):
            value = data.get(key)
            if isinstance(value, str):
                data[key] = dt_util.parse_datetime(value)
        return data
