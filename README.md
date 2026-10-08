# EnergyOpt for Home Assistant

**Cheapest electricity automation — without YAML.**

EnergyOpt computes optimal run windows for your devices from day-ahead spot
prices (15-minute resolution) and exposes them as ready-to-use Home Assistant
entities. Configure rules like *"run during the cheapest 2 hours overnight"*,
*"split 4 hours into the cheapest blocks"*, or *"run the cheapest 15 minutes
of every hour"* in the EnergyOpt web UI — this integration turns them into
sensors your automations can use.

## Entities

Per device:

- `binary_sensor.<device>_should_run` — on during scheduled windows
- `sensor.<device>_next_run_begins` / `_next_run_ends` — timestamps
- `sensor.<device>_reason` — plain-language explanation
  ("Next cheap window 01:00–03:00 tomorrow.")
- `sensor.<device>_estimated_cost` — EUR for the next run
- `calendar.<device>_schedule` — upcoming run windows as calendar events

Site-level:

- `sensor.<site>_price_now` — current spot price (c/kWh)
- `sensor.<site>_prices_loaded_until` — timestamp prices are loaded through
- `sensor.<site>_status` — health/status (`stale` when data is old)
- `binary_sensor.<site>_prices_loaded` — on when day-ahead prices are loaded
  (off while data is stale)
- `binary_sensor.<site>_cheap_now` / `_expensive_now` — on when the current
  price is in the cheap / expensive band

Devices added or removed in the web UI appear/disappear in Home Assistant
automatically within one poll interval — no reload needed. Schedule
calendars are optional (integration options, on by default). Devices of the
**Shelly switch** type never appear here: they control themselves via the
generated script, and one device should have exactly one controller.

The integration exposes an **options** flow (adjust the poll interval) and a
**reconfigure** flow (update base URL, API key, or site ID) from its entry in
Settings → Devices & Services, and provides **diagnostics** downloads with the
API key redacted.

## Installation (HACS)

1. HACS → three-dot menu → **Custom repositories** → add
   `https://github.com/JSJFIN/energyopt-homeassistant` (type: Integration).
2. Install **EnergyOpt**, restart Home Assistant.
3. Settings → Devices & Services → **Add Integration** → EnergyOpt.
4. Enter the base URL (`https://energyopt.ailabra.org`), your API key, and
   site ID — both from the web UI's *Integration* page.

Manual install: copy `custom_components/energyopt` into your HA
`custom_components/` folder and restart.

## Automation blueprint

[`control_switch_from_schedule.yaml`](blueprints/automation/energyopt/control_switch_from_schedule.yaml)
turns a switch (or input_boolean) on/off following the should-run sensor,
with optional boost, pause, and minimum on-time. One-click import:

[![Import blueprint](https://my.home-assistant.io/badges/blueprint_import.svg)](https://my.home-assistant.io/redirect/blueprint_import/?blueprint_url=https%3A%2F%2Fraw.githubusercontent.com%2FJSJFIN%2Fenergyopt-homeassistant%2Fmain%2Fblueprints%2Fautomation%2Fenergyopt%2Fcontrol_switch_from_schedule.yaml)

(or paste the raw file URL into Settings → Automations → Blueprints → Import;
note that HACS installs the integration only — blueprints are always
imported separately, that's a Home Assistant limitation).

### Boost until the next cheap period

For extra hot water before guests or sauna, turn on **Boost**: the device
runs immediately, even outside its cheap hours. At the next scheduled cheap
period, the boost toggle resets to off and normal control resumes. The device
stays on during that cheap period and turns off normally afterward.

Requires integration **0.4.3 or newer** and the updated blueprint. For each
device, create two helpers in **Settings → Devices & services → Helpers**:

1. A **Toggle**, for example `input_boolean.water_heater_boost`.
2. A **Date and/or time** helper with **both date and time** enabled, for
   example `input_datetime.water_heater_boost_expires`.

Select them in the automation's **Boost until next cheap period** and
**Boost expires at** inputs. Put the toggle and expiry helper on your dashboard.
Use separate helpers for every device, and do not set YAML `initial` values:
Home Assistant needs to restore their saved states after a restart.

The expiry is saved when boost starts. A newly published earlier cheap period
can shorten it; changes never extend it automatically. With no known cheap
period, boost expires after **four hours**. Solar and fallback activity do not
reset boost. Turning the toggle off cancels boost and resumes normal control.
If the schedule sensor is unavailable when boost ends, the device switches
off immediately instead of remaining forced on.
Starting boost during a cheap period immediately clears the toggle, because
normal control is already running the device.

**Pause automatic control** is the former manual-override input: it leaves
the switch untouched and takes priority over boost. Boost still expires while
paused. Clearing pause immediately reconciles the switch. Minimum on-time is
measured from when the switch actually turned on; boost cancels a pending OFF.

To update an existing installation, update EnergyOpt through HACS and restart
Home Assistant, then **re-import the blueprint** in Settings → Automations &
scenes → Blueprints. Edit the existing automation to select the new helpers.
Existing automations without boost helpers continue to follow their sensor.

Blueprint regression tests use a separate Home Assistant runtime. Run these
commands from the [energyopt monorepo](https://github.com/JSJFIN/energyopt) root:

```sh
python3 -m venv /tmp/energyopt-ha-tests
/tmp/energyopt-ha-tests/bin/pip install -r homeassistant/tests/requirements.txt
/tmp/energyopt-ha-tests/bin/pytest homeassistant/tests
```

## Excess solar

Devices with **use excess solar** enabled run whenever your site has surplus
solar power, on top of their price schedule. The device's `should_run` sensor
turns on if *either* a cheap-price window is active *or* there is enough solar
excess — the two reasons are independent, and solar works even while cloud data
is stale (it needs no cloud data at all).

The configured power sensor is read **locally** in Home Assistant, so there is
no cloud round-trip: EnergyOpt reacts to a change in surplus within about
**60 s** (the same tick that re-evaluates schedule windows). Short cloud dips
are smoothed by minimum on/off timers — defaults **10 min minimum on** and
**5 min minimum off** — so the device doesn't flap as passing clouds cross the
sun. Start/stop use asymmetric thresholds so a device that consumes its own
surplus once running doesn't immediately switch itself back off.

Extra attributes on the `should_run` sensor expose the current state:
`solar_active` (solar is a reason the device is on now), `solar_excess_w`
(signed surplus in watts, or null when the sensor has no reading), and
`solar_hold_until` (when the active min-on/min-off timer expires).

The `reason` sensor reflects solar when solar is driving the device: it shows
the solar explanation (or appends it to the price reason when a price window is
also active) instead of only the server's price-schedule reason.

## Offline behavior

Entities keep working from the last fetched schedule during cloud outages:
window boundaries flip on time locally, and if the schedule runs out
entirely, the optional per-device fallback time window takes over. The
status sensor shows `stale` while data is old. The cloud never controls
your devices — Home Assistant always switches locally.

## Links

- **New here? Full walkthrough: [GETTING_STARTED.md](GETTING_STARTED.md)**
- Web UI / account: https://energyopt.ailabra.org
- Issues: https://github.com/JSJFIN/energyopt-homeassistant/issues
