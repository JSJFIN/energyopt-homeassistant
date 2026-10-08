"""Local, persistent load-shedding decisions; never switch devices here."""

from __future__ import annotations

import hashlib
import logging
import math
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from homeassistant.const import EVENT_STATE_REPORTED
from homeassistant.core import HomeAssistant, State, callback, valid_entity_id
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.event import (
    async_track_point_in_utc_time,
    async_track_state_change_event,
)
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class GridGuardConfig:
    sensor: str
    stop_w: float = 16000
    resume_w: float = 14000
    pause_minutes: float = 10
    max_age_seconds: float = 120
    import_direction: str = "positive"

    def __post_init__(self) -> None:
        if (
            not isinstance(self.sensor, str)
            or not valid_entity_id(self.sensor)
            or not self.sensor.startswith("sensor.")
        ):
            raise ValueError("Select a power sensor")
        values = (self.stop_w, self.resume_w, self.pause_minutes, self.max_age_seconds)
        if any(isinstance(v, bool) or not math.isfinite(v) for v in values):
            raise ValueError("Grid guard values must be finite numbers")
        if not 0 <= self.resume_w < self.stop_w:
            raise ValueError("Resume power must be lower than stop power")
        if self.pause_minutes <= 0 or self.max_age_seconds <= 0:
            raise ValueError("Pause duration and maximum reading age must be positive")
        if self.import_direction not in ("positive", "negative"):
            raise ValueError("Invalid grid import direction")


@dataclass(frozen=True)
class GridGuardState:
    blocked_until: datetime | None = None


@dataclass(frozen=True)
class GridGuardDecision:
    blocked: bool
    blocked_until: datetime | None
    import_w: float | None
    reason: str | None


def read_import_power(
    config: GridGuardConfig, state: State | None, now: datetime
) -> tuple[float | None, str | None]:
    if state is None or state.state in ("unknown", "unavailable"):
        return None, "Grid power sensor is unavailable"
    if state.attributes.get("restored"):
        return None, "Waiting for a live grid power reading"
    age = (now - state.last_reported).total_seconds()
    if age < 0:
        return None, "Grid power reading has a future timestamp"
    if age >= config.max_age_seconds:
        return None, "Grid power reading is stale"
    unit = state.attributes.get("unit_of_measurement")
    if unit not in ("W", "kW"):
        return None, "Grid power sensor must report W or kW"
    try:
        power = float(state.state)
    except (TypeError, ValueError):
        return None, "Grid power reading is invalid"
    if not math.isfinite(power):
        return None, "Grid power reading is invalid"
    if unit == "kW":
        power *= 1000
    if not math.isfinite(power):
        return None, "Grid power reading is invalid"
    if config.import_direction == "negative":
        power = -power
    return max(0.0, power), None


def evaluate_grid_guard(
    config: GridGuardConfig,
    prior: GridGuardState,
    power: float | None,
    error: str | None,
    now: datetime,
) -> tuple[GridGuardState, GridGuardDecision]:
    """Latch overload once; release only after time AND safe power criteria."""
    deadline = prior.blocked_until
    if power is None:
        return prior, GridGuardDecision(True, deadline, None, error)
    if deadline is None and power > config.stop_w:
        deadline = now + timedelta(minutes=config.pause_minutes)
    if deadline is not None:
        if now < deadline:
            reason = "Grid-power pause; waiting for the minimum pause to finish"
        elif power > config.resume_w:
            reason = "Grid-power pause; waiting for import to fall to the resume limit"
        else:
            return GridGuardState(), GridGuardDecision(False, None, power, None)
        return GridGuardState(deadline), GridGuardDecision(
            True, deadline, power, reason
        )
    return prior, GridGuardDecision(False, None, power, None)


class GridPowerGuard:
    """One evaluator for all devices; deadlines and reading expiry use timers."""

    def __init__(
        self,
        hass: HomeAssistant,
        base_url: str,
        site_id: str,
        options: dict[str, Any],
        notify: Callable[[], None],
    ) -> None:
        self.hass = hass
        self.configs = {
            device_id: GridGuardConfig(**settings)
            for device_id, settings in options.items()
        }
        self.states = {device_id: GridGuardState() for device_id in self.configs}
        self.decisions: dict[str, GridGuardDecision] = {}
        identity = hashlib.sha256(f"{base_url}:{site_id}".encode()).hexdigest()
        self._store = Store(hass, 1, f"energyopt.grid_guard.{identity}")
        self._notify = notify
        self._unsubs: list[Callable[[], None]] = []
        self._unsub_timer: Callable[[], None] | None = None
        self._started = False

    async def async_start(self) -> None:
        if self._started or not self.configs:
            return
        try:
            saved = await self._store.async_load()
            if saved is not None:
                if not isinstance(saved, dict):
                    raise TypeError("Invalid saved grid guard state")
                restored = {}
                for device_id in self.configs:
                    value = saved.get(device_id)
                    deadline = datetime.fromisoformat(value) if value else None
                    if deadline is not None and deadline.tzinfo is None:
                        raise ValueError("Grid guard deadline must include a timezone")
                    restored[device_id] = GridGuardState(deadline)
                self.states = restored
        except (OSError, HomeAssistantError, KeyError, TypeError, ValueError):
            _LOGGER.warning(
                "Grid guard cache could not be loaded; applying a fresh pause"
            )
            self.states = {
                key: GridGuardState(
                    dt_util.utcnow() + timedelta(minutes=c.pause_minutes)
                )
                for key, c in self.configs.items()
            }
        self._started = True
        sensors = {config.sensor for config in self.configs.values()}

        @callback
        def reading_updated(event: Any) -> None:
            if self.refresh():
                self._notify()

        @callback
        def is_grid_sensor(event_data: dict[str, Any]) -> bool:
            return event_data["entity_id"] in sensors

        self._unsubs = [
            async_track_state_change_event(self.hass, sensors, reading_updated),
            self.hass.bus.async_listen(
                EVENT_STATE_REPORTED, reading_updated, event_filter=is_grid_sensor
            ),
        ]
        self.refresh()
        self._store.async_delay_save(self._saved_state)

    @callback
    def refresh(self) -> bool:
        if not self._started:
            return False
        now = dt_util.utcnow()
        previous = self.decisions
        changed = False
        self.decisions = {}
        future = []
        for key, config in self.configs.items():
            reading = self.hass.states.get(config.sensor)
            power, error = read_import_power(config, reading, now)
            state, decision = evaluate_grid_guard(
                config, self.states[key], power, error, now
            )
            changed |= state != self.states[key]
            self.states[key] = state
            self.decisions[key] = decision
            if state.blocked_until is not None and state.blocked_until > now:
                future.append(state.blocked_until)
            if reading is not None:
                stale_at = reading.last_reported + timedelta(
                    seconds=config.max_age_seconds
                )
                if stale_at > now:
                    future.append(stale_at)
        if changed:
            self._store.async_delay_save(self._saved_state)
        if self._unsub_timer is not None:
            self._unsub_timer()
            self._unsub_timer = None
        if future:

            @callback
            def timer(now: datetime) -> None:
                self._unsub_timer = None
                if self.refresh():
                    self._notify()

            self._unsub_timer = async_track_point_in_utc_time(
                self.hass, timer, min(future)
            )
        return previous != self.decisions

    @callback
    def decision(self, device_id: str) -> GridGuardDecision | None:
        if device_id not in self.configs:
            return None
        return self.decisions.get(
            device_id,
            GridGuardDecision(True, None, None, "Grid-power guard is initializing"),
        )

    @callback
    def _saved_state(self) -> dict[str, str | None]:
        return {
            key: state.blocked_until.isoformat() if state.blocked_until else None
            for key, state in self.states.items()
        }

    async def async_shutdown(self) -> None:
        for unsub in self._unsubs:
            unsub()
        self._unsubs = []
        if self._unsub_timer is not None:
            self._unsub_timer()
            self._unsub_timer = None
        if self._started:
            self._started = False
            try:
                await self._store.async_save(self._saved_state())
            except (OSError, HomeAssistantError):
                _LOGGER.warning("Unable to save the grid guard cooldowns")
