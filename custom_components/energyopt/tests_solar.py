"""Executable, stdlib-only tests for ``solar.evaluate_solar`` (spec cases S1-S15).

There is no Home Assistant runtime here, so this is the verification harness for
the pure decision logic. Run directly:

    python3 homeassistant/custom_components/energyopt/tests_solar.py

It loads the sibling ``solar.py`` by path so it works regardless of how the
package is (or isn't) importable.
"""

from __future__ import annotations

import importlib.util
import os
import sys
from datetime import datetime, timedelta, timezone

_HERE = os.path.dirname(os.path.abspath(__file__))
_SPEC = importlib.util.spec_from_file_location(
    "energyopt_solar", os.path.join(_HERE, "solar.py")
)
solar = importlib.util.module_from_spec(_SPEC)
assert _SPEC and _SPEC.loader
# Register before exec so dataclass annotation resolution can find the module.
sys.modules["energyopt_solar"] = solar
_SPEC.loader.exec_module(solar)

SolarConfig = solar.SolarConfig
SolarState = solar.SolarState
evaluate_solar = solar.evaluate_solar

NOW = datetime(2026, 7, 13, 12, 0, 0, tzinfo=timezone.utc)


def ago(minutes: float) -> datetime:
    """Return the timestamp ``minutes`` before NOW."""
    return NOW - timedelta(minutes=minutes)


# Device A: grid_negative_export, power 2 kW.
DEV_A = SolarConfig(
    use_solar=True,
    entity_id="sensor.grid_power",
    source_type="grid_negative_export",
    start_w=2000,
    stop_w=100,
    min_on_minutes=10,
    min_off_minutes=5,
    device_power_w=2000,
)

# Device A' : same as A but positive-export convention.
DEV_A_POS = SolarConfig(
    use_solar=True,
    entity_id="sensor.grid_power",
    source_type="grid_positive_export",
    start_w=2000,
    stop_w=100,
    min_on_minutes=10,
    min_off_minutes=5,
    device_power_w=2000,
)

# Device P: production, default stop_w=1000.
DEV_P = SolarConfig(
    use_solar=True,
    entity_id="sensor.pv_power",
    source_type="production",
    start_w=2000,
    stop_w=1000,
    min_on_minutes=10,
    min_off_minutes=5,
    device_power_w=2000,
)


_RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    """Record a single assertion result."""
    _RESULTS.append((name, bool(condition), detail))


# --- S1-S15 ---------------------------------------------------------------


def s1_start_on_export() -> None:
    d = evaluate_solar(DEV_A, "-2500", NOW, SolarState(on=False, since=None))
    check(
        "S1 start on export",
        d.solar_on and d.state.since == NOW and d.excess_w == 2500.0,
        f"on={d.solar_on} since={d.state.since} excess={d.excess_w}",
    )


def s3_own_consumption_dip() -> None:
    d = evaluate_solar(DEV_A, "50", NOW, SolarState(on=True, since=ago(3)))
    check("S3 own-consumption dip stays on", d.solar_on, f"on={d.solar_on}")


def s4_stop_boundary() -> None:
    d = evaluate_solar(DEV_A, "100", NOW, SolarState(on=True, since=ago(12)))
    check("S4 stop boundary stays on (strict >)", d.solar_on, f"on={d.solar_on}")


def s5_cloud_dip_in_min_on() -> None:
    d = evaluate_solar(DEV_A, "500", NOW, SolarState(on=True, since=ago(3)))
    check("S5 cloud dip within min_on stays on", d.solar_on, f"on={d.solar_on}")


def s6_stop_after_min_on() -> None:
    d = evaluate_solar(DEV_A, "500", NOW, SolarState(on=True, since=ago(12)))
    check(
        "S6 stop after min_on",
        not d.solar_on and d.state.since == NOW,
        f"on={d.solar_on} since={d.state.since}",
    )


def s7_min_off_blocks_restart() -> None:
    d = evaluate_solar(DEV_A, "-2500", NOW, SolarState(on=False, since=ago(2)))
    check("S7 min_off blocks restart", not d.solar_on, f"on={d.solar_on}")


def s8_min_off_expired() -> None:
    d = evaluate_solar(DEV_A, "-2500", NOW, SolarState(on=False, since=ago(6)))
    check(
        "S8 min_off expired -> on",
        d.solar_on and d.state.since == NOW,
        f"on={d.solar_on} since={d.state.since}",
    )


def s9_grid_positive_export() -> None:
    d = evaluate_solar(DEV_A_POS, "2500", NOW, SolarState(on=False, since=None))
    check("S9 grid_positive_export starts", d.solar_on, f"on={d.solar_on}")


def s10_production_hold() -> None:
    d = evaluate_solar(DEV_P, "1500", NOW, SolarState(on=True, since=ago(12)))
    check("S10 production hold band stays on", d.solar_on, f"on={d.solar_on}")


def s11_production_stop() -> None:
    d = evaluate_solar(DEV_P, "900", NOW, SolarState(on=True, since=ago(12)))
    check("S11 production stop", not d.solar_on, f"on={d.solar_on}")


def s12_production_no_start_in_band() -> None:
    d = evaluate_solar(DEV_P, "1500", NOW, SolarState(on=False, since=None))
    check("S12 production no-start in band", not d.solar_on, f"on={d.solar_on}")


def s13_unavailable_min_on_holds() -> None:
    since = ago(4)
    d = evaluate_solar(DEV_A, "unavailable", NOW, SolarState(on=True, since=since))
    expected_hold = since + timedelta(minutes=10)
    check(
        "S13 unavailable within min_on holds on",
        d.solar_on and d.hold_until == expected_hold and d.excess_w is None,
        f"on={d.solar_on} hold={d.hold_until} excess={d.excess_w}",
    )


def s14_unavailable_min_on_done() -> None:
    d = evaluate_solar(DEV_A, "unknown", NOW, SolarState(on=True, since=ago(15)))
    check(
        "S14 unavailable, min_on expired -> off",
        not d.solar_on and d.state.since == NOW,
        f"on={d.solar_on} since={d.state.since}",
    )


def s15_restart_amnesia() -> None:
    d = evaluate_solar(DEV_A, "-2500", NOW, SolarState(on=False, since=None))
    check(
        "S15 restart amnesia -> on immediately",
        d.solar_on and d.state.since == NOW,
        f"on={d.solar_on} since={d.state.since}",
    )


def main() -> int:
    """Run all cases and print a summary; return 0 on full pass."""
    for fn in (
        s1_start_on_export,
        s3_own_consumption_dip,
        s4_stop_boundary,
        s5_cloud_dip_in_min_on,
        s6_stop_after_min_on,
        s7_min_off_blocks_restart,
        s8_min_off_expired,
        s9_grid_positive_export,
        s10_production_hold,
        s11_production_stop,
        s12_production_no_start_in_band,
        s13_unavailable_min_on_holds,
        s14_unavailable_min_on_done,
        s15_restart_amnesia,
    ):
        fn()

    passed = sum(1 for _, ok, _ in _RESULTS if ok)
    total = len(_RESULTS)
    for name, ok, detail in _RESULTS:
        mark = "PASS" if ok else "FAIL"
        line = f"[{mark}] {name}"
        if not ok and detail:
            line += f"  <- {detail}"
        print(line)
    print(f"\n{passed}/{total} passed")
    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
