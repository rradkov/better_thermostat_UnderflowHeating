---
title: Configuration
description: Create and configure a Better Thermostat device.
slug: configuration
---

## Create a new Better Thermostat device

**Go to: `Settings` -> `Devices & Services` -> `Integrations` -> `+ Add Integration` -> `Better Thermostat`**

or click on the button below:

[![Open your Home Assistant instance and start setting up a new integration.](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=better_thermostat)

## Configuration

## First step

![first step](../assets/setup_1.png)

**Name** This is a required field. It is the name of the virtual climate. It is used as an entity key name.

**The real thermostat** This is a required field. This is the real climate entity you want to control with BT, if you have more than one climate in your room you can select multiple climate entities, fill out the first field and a second one will appear.

**Temperature sensor** This is a required field. This is the temperature sensor you want to use to control the real climate entity. It's used to get a more accurate temperature reading than the sensor in the real climate entity because you can place it in the middle of the room and not close to the radiator.

**Humidity sensor** This is an optional field. For now, the humidity is only used to display it in the UI. In the future, it will be used to make a better calculation of the temperature or set it up to a *feels-like* temperature.

**If you have an outdoor sensor...** This field is optional. If you have an outdoor sensor you can use it to get the outdoor temperatures, this is used to set the thermostat on or off if the threshold (the last option in this screen) is reached. It uses a mean of the last 3 days and checks it every morning at 5:00 AM.

**Window Sensor** This is an optional field. If you have a window sensor you can use it to turn off the thermostat if the window is open and turn it on again when the window is closed. If you have more than one window in a room, you can also select window groups (see the GitHub page for more info).

### Example window/door sensor config

```yaml
group:
  livingroom_windows:
    name: Livingroom Windows
    icon: mdi:window-open-variant
    all: false
    entities:
      - binary_sensor.openclose_1
      - binary_sensor.openclose_2
      - binary_sensor.openclose_3
```

**Your weather entity for outdoor temperature** This is an optional field. It should be empty if you have an outdoor sensor. This is the weather entity you want to use to get the outdoor temperature. It uses the mean of the last 3 days and checks it every morning at 5:00 AM.

**Window delay** This is an optional field. If you don't want to turn off the thermostat instantly when the window is open, you can set a delay. This goes in both directions, so if you want to turn it on again after the window is closed, you can set a delay here too.

**The outdoor temperature threshold** This is an optional field. If you have an outdoor sensor or a weather entity, you can set a threshold. If the outdoor temperature is higher than the threshold, the thermostat will be turned off. If the outdoor temperature is lower than the threshold, the thermostat will be turned on. If you don't have an outdoor sensor or a weather entity, this field will be ignored.

**Tolerance** This is an optional field. It helps prevent the thermostat from turning on and off too often. Here is an example of how it works: If you set the target temperature to 20.0 and the tolerance to 0.3 for example. Then BT will heat to 20.0 and then go to idle until the temperature drops again to 19.7 and then it will heat again to 20.0. If you configured a cooler, the tolerance delays the switch-on instead of advancing it: with a cooling target of 24.0 and a tolerance of 0.3, BT starts cooling once the temperature reaches 24.3 and keeps cooling until it is back below 24.0. The cooling band is never narrower than 0.2, so with a tolerance of 0.1 cooling still starts at 24.1, but it keeps running until the temperature is back below 23.9.

## Second step

![second step](../assets/config_2.png)

**Calibration Type** This is a required field. How the calibration should be applied on the TRV (Target temp or offset)

- ***Target Temperature Based***: Apply the calibration to the target temperature.

- ***Offset Based***: Apply the calibration to the offset. This will not be an option if your TRV doesn't support offset mode.

**Enhanced Compatibility:**

- **NUMBER entities**: Traditional numeric offset controls (most TRVs)
- **SELECT entities**: Dropdown-based offset selection (e.g., HomeMatic IP with predefined offset values like "1.5k", "2.0k")
- **Automatic Detection**: Better Thermostat automatically detects and supports both entity types

**HomeMatic IP/CCU Integration:**

When using HomeMatic IP or CCU thermostats, Better Thermostat automatically detects and uses SELECT entities for temperature offset calibration. These entities provide predefined offset values (like "1.5k", "2.0k", "2.5k") and are fully supported for offset-based calibration.

**⚠️ Important Setup Required:** For HomeMatic IP/CCU devices, the `temperature_offset` SELECT entity is hidden by default. You must explicitly enable it before it can be used with Better Thermostat:

1. Navigate to your HomeMatic integration settings
2. Go to **"Advanced settings"**
3. Click **"UN-IGNORE parameters"**
4. Enable the temperature offset parameter for your device model. The exact parameter path varies by model, for example:
   - **`TEMPERATURE_OFFSET:MASTER@HM-CC-RT-DN`** (HomeMatic Classic)
   - **`TEMPERATURE_OFFSET:MASTER@HM-CC-RT-DN-BoM`** (HomeMatic Classic BoM variant)
   - **`TEMPERATURE_OFFSET:MASTER@HmIP-eTRV`** (HomeMatic IP)

   Check your device manual or the HomeMatic integration's device parameter list for the correct parameter name.

Only after this activation, the `select.{room}_temperature_offset` entity becomes available for Better Thermostat to use. This setup is confirmed for the HACS integration "Homematic(IP) Local for OpenCCU" - it may differ slightly for other HomeMatic integrations.

**Calibration Mode**  This is a required field. It determines how the calibration should be calculated

Better Thermostat offers several algorithms to control your heating:

- ***Normal***: Simple and reliable - uses your external sensor to correct the TRV's internal sensor
- ***Aggressive***: Pushes the TRV harder for faster heating (good for slow-heating rooms)
- ***AI Time Based***: **[Recommended]** Learns your room's heating patterns and adapts automatically
- ***MPC Predictive***: Advanced algorithm that predicts temperature changes for optimal efficiency
- ***PID Controller***: Classic control method that responds well to varying heating conditions
- ***TPI Controller***: Simple duty-cycle based control for consistent heating

**→ [Learn more about each algorithm and which one to choose](algorithms)**

**Quick guide:**

- Start with AI Time Based, the default
- Switch to Aggressive if the room heats slowly
- Switch to MPC Predictive if the temperature overshoots
- Switch to PID Controller if you want fine control
- On HomeMatic IP/CCU, offset-based calibration handles SELECT entities automatically

**Overheating protection** This should only be checked if you have any problems with strong overheating.

**If your TRV can't handle the off mode, you can enable this to use target temp 5° instead** If your TRV model doesn't have an off mode, BT will use the min target temp of this device instead, this option is only needed if you have problems, known models that don't have an off mode are auto-detected by BT.

**If auto means heat for your TRV and you want to swap it** Some climates in HA use the mode auto for default heating, and a boost when mode is heat. This isn't what we want, so if this is the case for you, check this option.

**Ignore all inputs on the TRV like a child lock** If this option is enabled, all changes on the real TRV, even over HA, will be ignored or reverted, only input from the BT entity is accepted.

**If you use HomematicIP you should enable this...** If your entity is a HomematicIP entity this option should be enabled, to prevent a duty cycle overload.

## Underfloor heating (Warm Floor mode)

Underfloor heating (UFH) has much higher thermal inertia than a radiator TRV: once the room is satisfied and BT backs a heater fully off, the slab keeps cooling for a long time, so recovering from a fully-cold floor is slow and energy-costly. Warm Floor mode keeps a low, continuous background heat level in UFH-flagged heaters instead of letting them fully idle. The passive backoff floor never pushes the room air above target; the opt-in sustain push (see below, off by default) pushes only the setpoint sent to the physical heater a small amount above target — the target temperature shown on the card never changes.

This works with any `climate.*` heater, including Home Assistant's own **Generic Thermostat** helper (external sensor + on/off relay) — the most common way to wire up UFH — with no extra plumbing.

**Enable Underfloor Heating** (first step, top-level toggle) — turn this on if any heater in this Better Thermostat instance is underfloor heating. Leave it off if every heater is a radiator; it changes nothing until at least one heater is flagged below. Also reveals **Show all calibration modes** (see below).

Once enabled, each heater gets extra fields on the advanced (second) step:

**Heating type** — `Under` (default, once this field is shown), `Radiator`, `Fan-coil / Convector`, or `Air Conditioner / HVAC`. Only heaters set to `Under` get the sustaining-floor behavior described here; the other three labels exist so a room that mixes underfloor heating with a secondary device can identify each heater correctly, and today behave identically to `Radiator` (no Warm Floor effect).

**Warm Floor level** — `1 · Eco`, `2 · Balanced` (default), `3 · Keep it Hot`, or `Custom`. A single dial that sets both **Warm Floor max backoff** and **Warm Floor sustain push** together:

| Level | Max backoff | Sustain push |
|---|---|---|
| 1 · Eco | 3.0°C | 0.0°C (off) |
| 2 · Balanced | 1.5°C | 0.0°C (off) |
| 3 · Keep it Hot | 0.5°C | 0.3°C |

Choosing `Custom` reveals both raw °C fields below so you can set them directly instead of using a preset.

**Warm Floor max backoff (°C)** — how far below the target temperature the sustaining floor is allowed to drift during an idle cycle. Range `0.2–5.0°C`. The *actual* backoff used each cycle is automatically scaled down (never up) from this configured maximum based on:

- **Heat loss / heating power** (or, for MPC-calibrated heaters, the learned MPC Loss/Gain and MPC Insulation coefficients) — a leaky room or a weak emitter gets a *tighter* floor, since it can't afford to drift far without a slow, costly recovery.
- **Solar gain** (MPC-calibrated heaters only, once learned) — if the room's current comfort is being propped up by sunlight rather than delivered heat, the floor stays close to target so it doesn't need to catch up once the sun's contribution fades.
- **The room's real-time trend** — the floor never raises while the room is still actively gaining, and tightens immediately if the room is falling faster than expected.

**Warm Floor sustain push (°C)** — off (`0.0`) by default. On four of the eight calibration modes (**AI Time Based**, **External Sensor Offset Only**, **Aggressive**, **No Calibration**), a non-valve heater's setpoint is already pinned at exactly target once idle, so the backoff floor above never has anything to raise — it's a structural no-op. Sustain push closes that gap: once the room has actually reached target and the heater is idle, it pushes the setpoint *sent to the heater* up to this much above target, scaled down the same way as the backoff amount. Range `0.0–1.5°C`. Never fires while the room is still gaining, while another heater on this entity is under active direct-valve control, or while the external temperature sensor's last update is more than ~20 minutes old (a stale sensor is treated the same as "still gaining," not as "safe to push"). **MPC Predictive**, **(AI) MPC v2**, **TPI Controller**, and **PID Controller** already compute a graduated setpoint and were never affected by this gap — sustain push can still engage there too on the rare cycle where they land exactly on target, but has nothing to do most of the time.

**Show all calibration modes** (top-level toggle, shown once Underfloor Heating is enabled) — off by default. While off, a heater flagged `Under` only offers **MPC Predictive**, **(AI) MPC v2**, **TPI Controller**, and **PID Controller** in its **Calibration mode** dropdown — the four modes proven to work correctly on a non-valve UFH heater without relying on sustain push. Turn this on to see all 8 modes again; a valve-capable UFH actuator (e.g. a TRV head on a manifold) works correctly on any of them regardless of this setting.

This never overrides an open window/door, a `call_for_heat=false` weather/outdoor gate, or BT being switched off — Warm Floor only ever raises what would otherwise be a fully-idle cycle, it never fights the normal heating computation or forces heat the room doesn't need.

A **Warm Floor Status** diagnostic sensor is created per instance, showing `active`/`idle`/`disabled` plus the current sustaining setpoint, backoff amount, and sustain push amount.

## Boost on window reopen

If you've configured a **Cooler** entity (top of the first step), you can also use it for fast recovery after a window reopens — most fan-coil/hydronic units and heat pumps already run in both directions, so the same device that cools in summer can boost-heat a cold room back to temperature after airing it out.

**Enable Boost on window reopen** — shown once a Cooler entity is configured. When on, if the room temperature drifts by more than the configured threshold while a window is open, closing the window arms a boost: colder than the threshold arms a **heat** boost, warmer arms a **cool** boost (checked against what the Cooler entity actually reports supporting). Either way it targets your normal target temperature, temporarily taking the Cooler entity over from its normal duty. Once the boost ends, the device is restored to exactly the mode, temperature, and fan setting it had the moment the boost took over — not simply switched off — so a device that was mid-way through its own independent cooling run when boost interrupted it picks that run back up rather than losing it.

**Boost temperature-drift threshold (°C)** — default `5.0°C`, range `0.1–15.0°C`.

**Use a different threshold for cooling** — off by default (one threshold covers both directions). Turn this on to set a separate, lower or higher **Boost cooling threshold (°C)** for the warm-day case, independent of the heat-boost threshold.

**Boost fan speed** — only shown if the Cooler entity reports fan modes. Optionally forces the device's fan speed (e.g. to maximum) while boosting, restored automatically once the boost ends. Leave as "Don't change fan mode" to skip this.

**Boost anticipation (minutes)** — default `5`, range `0–30`. How many minutes of residual heating/cooling the device keeps delivering into the room after being told to stop; used to stop the boost *before* the room actually reaches target, avoiding overshoot in either direction. When the room's own learned solar gain is available, it's factored in too — a heat boost on a sunny day can stop a little earlier, a cool boost needs a little more margin.

**Boost safety timeout (minutes)** — default `60`, range `5–240`. Forces the boost off after this long regardless of temperature, as a guard against a stuck sensor or a device that isn't actually acting.

**Boost cooldown (minutes)** — default `15`, range `0–120`. Minimum time after a boost ends before another one can arm, so a room that keeps drifting past the threshold (e.g. repeated window in/out) doesn't short-cycle the shared device.

A **Boost Status** diagnostic sensor is created per instance, showing `off`/`heating`/`cooling` plus how long the current boost has been running.

**Manually testing it** — the `better_thermostat.test_boost` service arms a boost cycle on demand (pick `heat` or `cool`) so you can confirm the wiring — target device, fan mode, threshold reachability — without needing to actually open and close a window.
