"""Pure, Home-Assistant-independent excess-solar decision logic.

This module contains no ``homeassistant`` imports and no I/O: it is a stateless
function over ``(config, sensor_value, now, prior)`` so that it can be unit
tested outside a HA runtime (see ``tests_solar.py``) and so that two
independent implementations of the spec produce identical output for identical
inputs. See ``docs/solar_excess_spec.md`` for the authoritative semantics.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

# Known values for ``SolarConfig.source_type``.
GRID_NEGATIVE_EXPORT = "grid_negative_export"
GRID_POSITIVE_EXPORT = "grid_positive_export"
PRODUCTION = "production"

_GRID_TYPES = (GRID_NEGATIVE_EXPORT, GRID_POSITIVE_EXPORT)
_KNOWN_SOURCE_TYPES = (GRID_NEGATIVE_EXPORT, GRID_POSITIVE_EXPORT, PRODUCTION)

# HA state strings that mean "no reading" rather than a number.
_NO_SIGNAL_STRINGS = frozenset({"", "unknown", "unavailable", "none"})


@dataclass(frozen=True)
class SolarConfig:
    """Per-device solar configuration, derived from the cloud payload."""

    use_solar: bool
    entity_id: str
    source_type: str  # grid_negative_export | grid_positive_export | production
    start_w: int
    stop_w: int
    min_on_minutes: int
    min_off_minutes: int
    device_power_w: int


@dataclass(frozen=True)
class SolarState:
    """The only persisted memory: prior state fed back as next ``prior``.

    ``since`` is the timestamp of the last on<->off transition; ``None`` means
    fresh state (e.g. after a HA restart), in which case no min-off/min-on
    deadline is in force.
    """

    on: bool = False
    since: datetime | None = None


@dataclass(frozen=True)
class SolarDecision:
    """Result of one evaluation. ``state`` is fed back as the next ``prior``."""

    solar_on: bool
    state: SolarState
    excess_w: float | None
    hold_until: datetime | None


def _is_valid(config: SolarConfig) -> bool:
    """Return True when the config is complete and internally consistent.

    Invalid config yields a permanently-off decision (never raises).
    """
    if not config.use_solar:
        return False
    if not config.entity_id:
        return False
    if config.source_type not in _KNOWN_SOURCE_TYPES:
        return False
    if config.start_w <= 0:
        return False
    if config.min_on_minutes < 0 or config.min_off_minutes < 0:
        return False
    if config.source_type in _GRID_TYPES:
        # stop_w is an allowed grid-import tolerance in watts.
        if config.stop_w < 0:
            return False
    else:  # production: needs a real hysteresis band below start_w.
        if config.stop_w >= config.start_w:
            return False
    return True


def _parse_sensor(value: str | float | None) -> float | None:
    """Parse a raw HA state into a float, or None when there is no signal."""
    if value is None:
        return None
    # bool is an int subclass; a boolean sensor state is not a power reading.
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if text.lower() in _NO_SIGNAL_STRINGS:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _raw_conditions(
    config: SolarConfig, parsed: float | None
) -> tuple[float | None, bool, bool]:
    """Return ``(excess_w, raw_start, raw_stop)`` for the parsed sensor value.

    ``excess_w`` is the signed value reported to HA (export for grid types,
    production for the production type), or None when there is no signal. No
    signal is treated as "excess gone": ``raw_start=False, raw_stop=True``.
    """
    if parsed is None:
        return None, False, True

    if config.source_type == GRID_NEGATIVE_EXPORT:
        export_w = -parsed
    elif config.source_type == GRID_POSITIVE_EXPORT:
        export_w = parsed
    else:  # PRODUCTION
        production_w = parsed
        raw_start = production_w >= config.start_w
        raw_stop = production_w < config.stop_w
        return production_w, raw_start, raw_stop

    import_w = -export_w
    raw_start = export_w >= config.start_w
    raw_stop = import_w > config.stop_w
    return export_w, raw_start, raw_stop


def evaluate_solar(
    config: SolarConfig,
    sensor_value: str | float | None,
    now: datetime,
    prior: SolarState,
) -> SolarDecision:
    """Compute the local solar decision. Pure and total: never raises."""
    if not _is_valid(config):
        return SolarDecision(False, SolarState(), None, None)

    parsed = _parse_sensor(sensor_value)
    excess_w, raw_start, raw_stop = _raw_conditions(config, parsed)

    min_on = timedelta(minutes=config.min_on_minutes)
    min_off = timedelta(minutes=config.min_off_minutes)

    if prior.on:
        min_on_deadline = prior.since + min_on if prior.since is not None else None
        if raw_stop and (min_on_deadline is None or now >= min_on_deadline):
            new_state = SolarState(on=False, since=now)  # STOP; min_off begins
        else:
            new_state = SolarState(on=True, since=prior.since)  # hold ON
    else:  # prior off
        min_off_deadline = prior.since + min_off if prior.since is not None else None
        if raw_start and (min_off_deadline is None or now >= min_off_deadline):
            new_state = SolarState(on=True, since=now)  # START; min_on begins
        else:
            new_state = SolarState(on=False, since=prior.since)  # blocked / no sun

    hold_until = _hold_until(new_state, now, min_on, min_off)
    return SolarDecision(new_state.on, new_state, excess_w, hold_until)


def _hold_until(
    state: SolarState, now: datetime, min_on: timedelta, min_off: timedelta
) -> datetime | None:
    """Return the active min-on/min-off deadline, or None if nothing constrains.

    While ON the constraining deadline is ``since + min_on``; while OFF it is
    ``since + min_off``. Once a deadline has passed (or ``since`` is None) the
    current state is no longer timer-constrained, so this returns None.
    """
    if state.since is None:
        return None
    deadline = state.since + (min_on if state.on else min_off)
    return deadline if now < deadline else None
