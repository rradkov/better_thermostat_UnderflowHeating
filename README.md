

[![GitHub issues](https://img.shields.io/github/issues/rradkov/better_thermostat_UnderflowHeating?style=for-the-badge)](https://github.com/rradkov/better_thermostat_UnderflowHeating/issues)
[![Version](https://img.shields.io/badge/Version-1.9.3.1.5-009688?style=for-the-badge)](https://github.com/rradkov/better_thermostat_UnderflowHeating/releases)

# Better Thermostat — UnderflowHeating fork

This repository is a modified fork of [KartoffelToby/better_thermostat](https://github.com/KartoffelToby/better_thermostat), with **underfloor heating as its primary focus**.

UnderflowHeating is designed primarily for Home Assistant installations centered on hydronic underfloor heating. The heat source can be a heat pump, electric boiler, gas boiler, pellet or other solid-fuel boiler, or another heating source. The system can also include fan-coil convectors and/or air conditioners for additional heating or cooling.

### What this fork is for

- **Underfloor heating first:** Warm Floor control helps prevent the floor from fully cooling between heating calls and improves recovery of slow hydronic systems.
- **Underfloor heating + fan-coils:** Use fan-coil convectors as the fast room-heating or cooling companion to the underfloor system, regardless of whether they share the same heat source.
- **Source-independent:** The integration focuses on the underfloor system and room climate behavior; the type of boiler, heat pump, or other heat source is not a limitation.
- **Underfloor heating + air conditioners:** Combine floor heating with an air conditioner for fast recovery and efficient heating/cooling.
- **Radiators and TRVs:** The original Better Thermostat functionality remains available as additional compatibility for traditional radiator-based systems.

The fork modifications are maintained by Rumen Radkov and were introduced from October 2026 onward. The original copyright and license notices are preserved. This project, including the fork modifications, is released under the [GNU Affero General Public License v3.0](LICENSE).

Original project: <https://github.com/KartoffelToby/better_thermostat>

The source code for every published fork release is available in this repository. Fork-specific support and issue tracking are available in the [fork issue tracker](https://github.com/rradkov/better_thermostat_UnderflowHeating/issues).

## Latest fork release: 1.9.3.1.5

This release improves recovery after a controlled heater, outdoor sensor, or related device becomes unavailable and later returns:

- **Added:** a fresh control pass is queued when a controlled heater recovers, even if the first recovered state does not contain a new temperature or setpoint.
- **Added:** the same recovery trigger for the optional outdoor sensor.
- **Changed:** an unavailable optional outdoor input no longer stops Warm Floor or replaces the last valid heating decision with stale data.
- **Fixed:** recovery paths that could leave Warm Floor or normal heating waiting for a future unrelated state change after a device reconnects.
- **Verified:** focused recovery, weather, and TRV event tests pass; the implementation remains heat-source and installation neutral.

Upgrade note: after installing this release, restart Home Assistant once so the custom integration is loaded from the new release tree.

**For more info visit: <https://better-thermostat.org/>**

## Requirements

- Minimum required Home Assistant version: `2026.7.2`
  (_continuously tested against Home Assistant_ [![Tested Home Assistant version](https://img.shields.io/badge/dynamic/regex?url=https%3A%2F%2Fraw.githubusercontent.com%2Frradkov%2Fbetter_thermostat_UnderflowHeating%2Fmain%2Fpyproject.toml&search=homeassistant%3E%3D%28%5B0-9.%5D%2B%29&replace=%241&label=&color=009688)](pyproject.toml))

### Companion UI

We've created a companion UI element which can display more information than the default thermostat element in Home Assistant. Check it out via HACS: [better-thermostat-ui-card](https://github.com/KartoffelToby/better-thermostat-ui-card)

- If you have a question or need help please create a new [discussion](https://github.com/rradkov/better_thermostat_UnderflowHeating/discussions) or check if your question is already answered
- If you have a suggestion, found a bug, or want to add a new device or function create a new [issue](https://github.com/rradkov/better_thermostat_UnderflowHeating/issues)
- If you want to contribute to this project create a new [pull request](https://github.com/rradkov/better_thermostat_UnderflowHeating/pulls)

### Features

This fork brings intelligent control to **underfloor heating first**, with optional fan-coil and air-conditioner climate devices for additional heating or cooling. The heat-source technology remains independent of the integration:

- Uses a room temperature sensor away from the active heat emitter or HVAC unit for more representative room control
- Coordinates compatible Home Assistant climate entities around the underfloor-heating system, including fan-coils, air conditioners, radiators, and TRVs
- Makes connected thermostat and climate entities available for convenient Home Assistant and Google Home control
- Lets windows and doors pause heating or cooling without requiring a separate automation
- Uses weather forecasts or an outdoor temperature sensor to adjust heating and cooling behavior
- Performs valve maintenance automatically where a connected valve supports it, helping prevent valves from sticking closed
- Supports grouping multiple TRVs where radiator valves are part of the installation
- Improves temperature regulation with adaptive control and energy-aware heating/cooling behavior
- Learns and persists dynamic preset temperatures, so the baseline/"no preset" target survives restarts
- **Advanced Control Algorithms**: Choose between MPC, PID, TPI, AI Time Based or simple target temperature matching for precise climate control.
- **Selectable Presets**: Configure which preset modes are available for each thermostat during setup.
- **Warm Floor mode** *(fork-only)*: Keeps underfloor heating from fully idling between heat calls, using the same heat-loss/heating-power/solar telemetry BT already tracks — no new thermal model, no fully-cold slab to recover from.
- **Boost on window reopen** *(fork-only)*: Reuses your configured secondary climate device (a fan-coil, air conditioner, or similar) to actively recover the room after a window closes, symmetrically in either direction — a heat boost if the room got too cold, a cool boost if it got too warm.

See [Underfloor heating (Warm Floor) & Boost](docs/Configuration/configuration.md#underfloor-heating-warm-floor-mode) in the configuration guide for details.

### Advanced Control Algorithms

Better Thermostat now supports multiple advanced control strategies to optimize your heating:

- **MPC (Model Predictive Control)**: Uses a physical model of your room and radiator to predict future temperature changes and optimize valve opening.
- **PID Controller**: A classic Proportional-Integral-Derivative controller that learns your room's characteristics to maintain a stable temperature. It features auto-tuning (currently in beta) to automatically find the best parameters (Kp, Ki, Kd) for your room.
- **TPI (Time Proportional Integral)**: A control method that cycles the valve on and off (or modulates it) to maintain a stable temperature, reducing overshoot.
- **AI Time Based**: Uses a custom algorithm based on simple measurements and calculations (not actual AI) to calculate the required heating power and adjusts the TRV calibration to achieve it. This improves upon the standard TRV internal algorithm.

**Not sure which to pick? A quick guide:**

| Algorithm | Best For | Trade-off |
|-----------|----------|-----------|
| **TPI** | A robust default — steady across room types, gentle on valve & battery, good with multiple radiators | Slightly looser setpoint tracking than PID/MPC |
| **PID** | Tight setpoint tracking in a stable, well-characterised room | More valve activity; varies more by room; autotune is still in beta |
| **MPC** | Anticipating solar / known disturbances when the room is modelled well | Most valve activity; can over-react with several radiators; needs tuning |
| **AI Time Based** | Quick setup, no tuning | Looser comfort, weaker in complex rooms |

Start with **TPI** or **AI Time Based**. Try **PID** for tighter tracking in a stable room, or **MPC** for disturbance anticipation if you're willing to tune.

These modes can be selected in the advanced configuration of the device.

### Preset Temperature Configuration

Preset temperatures are now fully configurable via dedicated `number` entities.

How it works:

1. During setup or configuration, you can select which **Presets** you want to enable for this thermostat.
2. For each enabled preset mode (e.g. Eco, Comfort, Sleep), a corresponding `number` entity is created (e.g., `number.better_thermostat_preset_eco`).
3. These entities are located in the **Configuration** category of the device.
4. You can adjust the temperature for each preset directly using these number sliders.
5. The values are automatically persisted across Home Assistant restarts.
6. Changing a preset temperature via the number entity immediately updates the thermostat if that preset is currently active.

Default starting values:

```text
Away:            16.0 °C
Boost:           24.0 °C
Comfort:         21.0 °C
Eco:             19.0 °C
Home:            20.0 °C
Sleep:           18.0 °C
Activity:        22.0 °C
```

### Which hardware do we support?

We support all thermostats which are compatible with Home Assistant as long as they are shown up as a climate entity.

**_Integrations that are tested_**

- Zigbee2Mqtt
- Deconz
- Tado
- generic_thermostat

### How to setup

Install this integration via HACS or copy the files from the [latest release](https://github.com/rradkov/better_thermostat_UnderflowHeating/releases/latest)

Configuration details can be found in the [documentation](docs/Configuration/configuration.md) or on our website: [better-thermostat.org](https://better-thermostat.org/configuration)

Some nice-to-know config tips for the configuration.yaml

#### Example Window/Door - Sensor config

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

#### Combine multiple TRV to one (Group)

No worry, Better Thermostat supports grouping out of the box

---

