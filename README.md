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

Site-level: prices-loaded-until timestamp and a status sensor.

Devices added or removed in the web UI appear/disappear in Home Assistant
automatically within one poll interval — no reload needed. Devices of the
**Shelly switch** type never appear here: they control themselves via the
generated script, and one device should have exactly one controller.

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
with an optional manual override and minimum on-time. One-click import:

[![Import blueprint](https://my.home-assistant.io/badges/blueprint_import.svg)](https://my.home-assistant.io/redirect/blueprint_import/?blueprint_url=https%3A%2F%2Fraw.githubusercontent.com%2FJSJFIN%2Fenergyopt-homeassistant%2Fmain%2Fblueprints%2Fautomation%2Fenergyopt%2Fcontrol_switch_from_schedule.yaml)

(or paste the raw file URL into Settings → Automations → Blueprints → Import;
note that HACS installs the integration only — blueprints are always
imported separately, that's a Home Assistant limitation).

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
