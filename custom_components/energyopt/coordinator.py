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

from .const import (
    DOMAIN,
    SELF_CONTROLLED_TYPES,
    STALE_MULTIPLIER,
    TICK_INTERVAL_SECONDS,
)
from .solar import SolarConfig, SolarDecision, SolarState, evaluate_solar

_LOGGER = logging.getLogger(__name__)

REQUEST_TIMEOUT = 30

# Default solar timers when the payload omits them (see solar_excess_spec.md §2).
_DEFAULT_MIN_ON_MINUTES = 10
_DEFAULT_MIN_OFF_MINUTES = 5


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
        # Solar ownership lives here (see solar_excess_spec.md): the coordinator
        # is the single evaluator. ``_solar_states`` is per-device persistent
        # memory (lost on restart); ``_solar_cache`` is a per-cycle decision
        # cache invalidated whenever ``_eval_generation`` is bumped, so the state
        # machine advances exactly once per cycle no matter how many entities ask.
        self._solar_states: dict[str, SolarState] = {}
        self._solar_cache: dict[str, SolarDecision | None] = {}
        self._eval_generation = 0
        self._solar_cache_generation = -1

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

    @callback
    def async_update_listeners(self) -> None:
        """Bump the eval generation, then notify listeners.

        This is the single choke point through which every cycle passes — a
        successful poll, a failed poll (both end in ``_async_refresh`` calling
        this), and the 60 s tick. Bumping the generation here invalidates the
        per-cycle solar cache so the state machine advances exactly once per
        cycle, on the first ``get_solar`` of the new generation.
        """
        self._eval_generation += 1
        super().async_update_listeners()

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

    # --- Solar ownership --------------------------------------------------

    def _build_solar_config(self, device: dict[str, Any]) -> SolarConfig | None:
        """Build a SolarConfig from the device payload, or None if not applicable.

        Codes defensively: payload keys may be absent. Returns None when solar
        is off, the device is self-controlled, or required fields are missing;
        the validity of thresholds is enforced by ``evaluate_solar`` itself.
        """
        if not device.get("use_solar"):
            return None
        if device.get("type") in SELF_CONTROLLED_TYPES:
            return None
        entity_id = device.get("solar_entity_id")
        source_type = device.get("solar_source_type")
        if not entity_id or not source_type:
            return None

        try:
            power_kw = float(device.get("power_kw") or 0)
        except (TypeError, ValueError):
            power_kw = 0.0
        device_power_w = round(power_kw * 1000)

        start_w = device.get("solar_start_w")
        if start_w is None:
            start_w = device_power_w
        stop_w = device.get("solar_stop_w")
        if stop_w is None:
            if source_type == "production":
                stop_w = max(100, round(int(start_w) * 0.5)) if start_w else 0
            else:
                stop_w = 100
        min_on = device.get("solar_min_on_minutes")
        min_on = _DEFAULT_MIN_ON_MINUTES if min_on is None else min_on
        min_off = device.get("solar_min_off_minutes")
        min_off = _DEFAULT_MIN_OFF_MINUTES if min_off is None else min_off

        try:
            return SolarConfig(
                use_solar=True,
                entity_id=str(entity_id),
                source_type=str(source_type),
                start_w=int(start_w),
                stop_w=int(stop_w),
                min_on_minutes=int(min_on),
                min_off_minutes=int(min_off),
                device_power_w=int(device_power_w),
            )
        except (TypeError, ValueError):
            return None

    def _compute_solar(self, device: dict[str, Any]) -> SolarDecision | None:
        """Read the configured sensor and step the local solar state machine.

        Returns None when solar is not configured / invalid / self-controlled
        (the config-building step). A configured device whose sensor is
        unavailable is still an evaluation (§3) and yields a decision, not None.
        """
        device_id = device.get("id")
        config = self._build_solar_config(device)
        if config is None:
            if device_id is not None:
                self._solar_states.pop(device_id, None)
            return None

        # Read the sensor locally; guard against hass not being attached yet.
        sensor_value: str | None = None
        if self.hass is not None:
            state = self.hass.states.get(config.entity_id)
            if state is not None:
                sensor_value = state.state

        prior = self._solar_states.get(device_id, SolarState())
        decision = evaluate_solar(config, sensor_value, dt_util.now(), prior)
        if device_id is not None:
            self._solar_states[device_id] = decision.state
        return decision

    def get_solar(self, device: dict[str, Any]) -> SolarDecision | None:
        """Return the per-cycle solar decision for a device.

        None when solar is not configured / invalid / self-controlled. The
        state machine is advanced at most once per eval generation: the first
        caller in a cycle computes and caches; later callers read the cache.
        """
        if self._solar_cache_generation != self._eval_generation:
            self._solar_cache = {}
            self._solar_cache_generation = self._eval_generation
        device_id = device.get("id")
        if device_id is not None and device_id in self._solar_cache:
            return self._solar_cache[device_id]
        decision = self._compute_solar(device)
        if device_id is not None:
            self._solar_cache[device_id] = decision
        return decision

    def solar_reason(self, device: dict[str, Any], final_on: bool) -> str | None:
        """Compose the short human explanation of the solar contribution, or None.

        ``final_on`` is the entity's resolved on/off after the precedence ladder;
        it distinguishes a device actually carried by solar from one whose solar
        excess is suppressed (disabled / override-off).
        """
        solar = self.get_solar(device)
        if solar is None:
            return None
        source = device.get("solar_source_type")
        verb = "producing" if source == "production" else "exporting"
        excess = (
            f" ({verb} {solar.excess_w / 1000:.1f} kW)"
            if isinstance(solar.excess_w, (int, float)) and solar.excess_w > 0
            else ""
        )
        if solar.solar_on:
            if not final_on:
                return "Solar excess available (suppressed)"
            if solar.hold_until is not None and not excess:
                # Carried by the minimum-run timer through a dip.
                until = dt_util.as_local(solar.hold_until)
                return f"Running on solar minimum-run until {until:%H:%M}."
            return f"Running on excess solar{excess}."
        if solar.hold_until is not None:
            until = dt_util.as_local(solar.hold_until)
            return f"Solar paused (minimum off-time until {until:%H:%M})."
        return None

    @staticmethod
    def schedule_window_status(
        device: dict[str, Any], now: datetime
    ) -> tuple[bool, bool]:
        """Return ``(covers_now, has_upcoming)`` for the cached schedule windows.

        ``covers_now`` is True when an active window contains ``now``;
        ``has_upcoming`` is True when a window starts in the future. Shared by
        the should-run binary sensor (both flags) and the reason sensor
        (``covers_now`` only) so the window logic is not duplicated.
        """
        covers_now = False
        has_upcoming = False
        for window in device.get("schedule") or []:
            if not isinstance(window, dict):
                continue
            start = window.get("start")
            end = window.get("end")
            if not (isinstance(start, datetime) and isinstance(end, datetime)):
                continue
            if start <= now < end:
                covers_now = True
            elif start > now:
                has_upcoming = True
        return covers_now, has_upcoming
