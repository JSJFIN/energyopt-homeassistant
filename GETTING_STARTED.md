# EnergyOpt — Getting started

EnergyOpt runs your devices (water boiler, car charger, heating…) during the
cheapest electricity hours automatically. You configure everything in a web
browser — no YAML, no scripting.

**You need:** a spot-price electricity contract (Finland), and either
Home Assistant (with HACS) **or** a Shelly Plus/Pro smart relay.

---

## 1. Create your account

1. Go to **https://energyopt.ailabra.org** and click **Sign up**.
2. After signing in, a site called **"My home"** is created for you
   automatically (Finland price area, VAT 25.5 % pre-filled).

## 2. Set your electricity details (Settings)

Open **Settings** in the top menu:

- **Seller margin** (c/kWh): from your electricity contract, e.g. 0.45.
- **VAT**: already 25.5 %, leave as is.
- **Electrical** (optional but nice): your main fuse size (e.g. 25 A) and
  phases — the **Plan** page will then warn you if you schedule more power
  than your fuse can handle.

## 3. Add a device

**Devices → Add device.** Pick what it is — the choice fills in sensible
defaults you can adjust:

- **Water boiler** → "cheapest 2 hours between 00:00 and 07:00"
- **Circulation pump** → "cheapest 15 minutes of every hour"
- **Generic switch / Shelly switch** → you choose the rule

Tips:
- Set the device's **power in kW** (it's on the nameplate) — cost estimates
  and the Plan page need it.
- Set a **Fallback time** (e.g. 01:00–04:00): if price data is ever
  unavailable, the device still runs during that window instead of staying
  cold.

Check the **Plan** page: you'll see your devices' runs placed in the valleys
of the price curve. That's the whole idea working.

## 4a. Connect Home Assistant

1. In HA: **HACS → ⋮ menu → Custom repositories** → add
   `https://github.com/JSJFIN/energyopt-homeassistant` (type: Integration).
2. Install **EnergyOpt**, then restart Home Assistant.
3. **Settings → Devices & Services → Add Integration → EnergyOpt** and enter:
   - Base URL: `https://energyopt.ailabra.org`
   - API key: create one in the web UI under **Integration → Create key**
     (copy it immediately — it's shown only once)
   - Site ID: shown on the same Integration page
4. Your devices appear with entities like
   `binary_sensor.water_boiler_should_run`, next start/end times, and a
   plain-language reason sensor.
5. To actually switch something, import the blueprint (one click):

   [![Import blueprint](https://my.home-assistant.io/badges/blueprint_import.svg)](https://my.home-assistant.io/redirect/blueprint_import/?blueprint_url=https%3A%2F%2Fraw.githubusercontent.com%2FJSJFIN%2Fenergyopt-homeassistant%2Fmain%2Fblueprints%2Fautomation%2Fenergyopt%2Fcontrol_switch_from_schedule.yaml)

   Then create an automation from it: pick the EnergyOpt sensor and the
   switch it should control. Done. (The blueprint doesn't come with HACS —
   blueprints are always imported separately.)

New devices you add in the web UI appear in HA by themselves within a few
minutes.

## 4b. Or connect a Shelly directly (no Home Assistant needed)

1. Add the device in the web UI with type **Shelly switch**.
2. You land on the **Shelly script** page: click
   **Create key and generate script** and copy the script.
3. Open your Shelly's own web page (its IP address) →
   **Scripts → Add script** → paste → **Save**, enable it, and switch on
   **Run on startup**. (Needs a Shelly Plus/Pro/Gen3 — Gen1 doesn't support
   scripts.)
4. The Shelly now checks EnergyOpt every minute and switches itself.

## 5. Test it

On the device's Shelly script page (or ask Jukka to show the override):
press **Force ON (5 min)** — within about a minute your relay/switch turns
on, and the override expires by itself. That's the whole chain verified.

## Good to know

- New prices arrive every afternoon (~14:15); schedules extend to tomorrow
  automatically.
- The **reason** text always explains what's happening ("Next cheap window
  01:00–03:00 tomorrow.").
- If the internet or the service is down, Home Assistant keeps switching on
  the last known schedule, and after that your fallback time takes over —
  the cloud never controls your devices directly.
- Sign-in may show a small "development mode" notice — harmless, it
  disappears when the service gets its production login keys.

Problems? Tell Jukka what the reason sensor says — it usually explains
itself.
