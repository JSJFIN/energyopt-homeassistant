"""Persistent price snapshots; dashboard reads and day rollover are local."""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from itertools import pairwise
from typing import Any
from zoneinfo import ZoneInfo

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.storage import Store


@dataclass(frozen=True)
class CachedPrice:
    start: datetime
    end: datetime
    price_cents_kwh: float

    def as_dict(self) -> dict[str, Any]:
        return {
            "start": self.start.isoformat(),
            "end": self.end.isoformat(),
            "price_cents_kwh": self.price_cents_kwh,
        }


def _datetime(value: Any) -> datetime:
    if not isinstance(value, str):
        raise TypeError("Expected an ISO datetime")
    result = datetime.fromisoformat(value)
    if result.tzinfo is None:
        raise ValueError("Price timestamps must include a timezone")
    return result.astimezone(UTC)


def _versions(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        raise TypeError("Invalid price day versions")
    for day, version in value.items():
        date.fromisoformat(day)
        if not isinstance(version, str) or not version:
            raise ValueError("Invalid price day version")
    return dict(value)


class PriceCache:
    def __init__(self, hass: HomeAssistant, base_url: str, site_id: str) -> None:
        identity = hashlib.sha256(f"{base_url}:{site_id}".encode()).hexdigest()
        self._store = Store(hass, 1, f"energyopt.prices.{identity}")
        self.timezone = "Europe/Helsinki"
        self.slots: list[CachedPrice] = []
        self.day_versions: dict[str, str] = {}
        self.last_refreshed_at: datetime | None = None
        self.retry_at: datetime | None = None
        self.failures = 0
        self.last_error: str | None = None
        self.expected_timezone: str | None = None
        self.expected_versions: dict[str, str] | None = None

    @property
    def loaded(self) -> bool:
        return self.last_refreshed_at is not None

    async def async_load(self) -> None:
        try:
            data = await self._store.async_load()
            if not data:
                return
            if not isinstance(data, dict):
                raise TypeError("Invalid saved price cache")
            if data.get("last_refreshed_at"):
                self.accept(data, _datetime(data["last_refreshed_at"]))
            self.failures = max(0, int(data.get("failures", 0)))
            self.retry_at = (
                _datetime(data["retry_at"]) if data.get("retry_at") else None
            )
            self.last_error = data.get("last_error")
        except (OSError, HomeAssistantError, ValueError, TypeError, KeyError):
            # A broken cache must not prevent schedule control from loading.
            self.slots = []
            self.day_versions = {}
            self.last_refreshed_at = None
            self.retry_at = None
            self.failures = 0
            self.last_error = None

    async def async_save(self) -> None:
        await self._store.async_save(
            {
                "timezone": self.timezone,
                "prices": [slot.as_dict() for slot in self.slots],
                "price_day_versions": self.day_versions,
                "last_refreshed_at": self.last_refreshed_at.isoformat()
                if self.last_refreshed_at
                else None,
                "retry_at": self.retry_at.isoformat() if self.retry_at else None,
                "failures": self.failures,
                "last_error": self.last_error,
            }
        )

    def observe(self, schedule: dict[str, Any]) -> None:
        timezone = schedule.get("timezone", self.timezone)
        ZoneInfo(timezone)
        versions = schedule.get("price_day_versions")
        parsed = _versions(versions) if versions is not None else None
        self.expected_timezone = timezone
        self.expected_versions = parsed

    def needs_refresh(self, now: datetime) -> bool:
        if self.expected_timezone is None:
            return False
        if self.expected_versions is None:
            # Older backends allow one initial snapshot and manual refreshes,
            # without turning missing version metadata into constant polling.
            return not self.loaded
        today = now.astimezone(ZoneInfo(self.expected_timezone)).date()
        wanted = {today.isoformat(), (today + timedelta(days=1)).isoformat()}
        return any(
            day in wanted
            and (
                self.expected_timezone != self.timezone
                or self.day_versions.get(day) != version
                or not any(
                    slot.start.astimezone(ZoneInfo(self.timezone)).date().isoformat()
                    == day
                    for slot in self.slots
                )
            )
            for day, version in self.expected_versions.items()
        )

    def accept(self, data: dict[str, Any], now: datetime) -> None:
        """Validate the complete response before replacing the previous cache."""
        if not isinstance(data, dict):
            raise TypeError("Invalid price snapshot")
        timezone = data["timezone"]
        ZoneInfo(timezone)
        prices = data["prices"]
        if not isinstance(prices, list):
            raise TypeError("Invalid price list")
        slots = []
        for price in prices:
            start, end = _datetime(price["start"]), _datetime(price["end"])
            value = float(price["price_cents_kwh"])
            if start >= end or not math.isfinite(value):
                raise ValueError("Invalid price slot")
            slots.append(CachedPrice(start, end, value))
        slots.sort(key=lambda slot: slot.start)
        if any(a.end > b.start for a, b in pairwise(slots)):
            raise ValueError("Overlapping price slots")
        versions = _versions(data.get("price_day_versions", {}))
        self.timezone = timezone
        self.slots = slots
        self.day_versions = versions
        self.last_refreshed_at = now.astimezone(UTC)

    def current_price(self, now: datetime) -> float | None:
        if self.expected_timezone not in (None, self.timezone):
            return None
        return next(
            (
                slot.price_cents_kwh
                for slot in self.slots
                if slot.start <= now < slot.end
            ),
            None,
        )

    def dashboard_data(self, now: datetime) -> dict[str, Any]:
        timezone = self.expected_timezone or self.timezone
        tz = ZoneInfo(timezone)
        today = now.astimezone(tz).date()
        result: dict[str, Any] = {
            "timezone": timezone,
            "price_unit": "c/kWh",
            "last_refreshed_at": self.last_refreshed_at,
            "last_error": self.last_error,
        }
        for label, day in (("today", today), ("tomorrow", today + timedelta(days=1))):
            slots = [
                slot for slot in self.slots if slot.start.astimezone(tz).date() == day
            ]
            if self.expected_timezone not in (None, self.timezone):
                slots = []
            start = datetime.combine(day, time.min, tz).astimezone(UTC)
            end = datetime.combine(day + timedelta(days=1), time.min, tz).astimezone(
                UTC
            )
            cursor = start
            for slot in slots:
                if slot.start != cursor:
                    break
                cursor = slot.end
            result[label] = [slot.as_dict() for slot in slots]
            result[f"{label}_date"] = day.isoformat()
            result[f"{label}_available"] = bool(slots) and cursor == end
        return result
