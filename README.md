

[![GitHub issues](https://img.shields.io/github/issues/rradkov/better_thermostat_UnderflowHeating?style=for-the-badge)](https://github.com/rradkov/better_thermostat_UnderflowHeating/issues)
[![Version](https://img.shields.io/badge/Version-1.9.3.1.3-009688?style=for-the-badge)](https://github.com/rradkov/better_thermostat_UnderflowHeating/releases)

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

This integration brings some smartness to your connected radiator (and, in this fork, underfloor heating) setup:

- Uses a temperature sensor far away from the radiators to measure the real room temperature
- Makes your TRVs fully compatible with Google Home
- Let your windows disable your heating (avoid programming this via an automation)
- Your weather forecast provider will turn your heat on/off
- Or an outside air temperature sensor can do this as well
- Does some valve maintenance automatically, to avoid them getting stuck closed over the summer
- Group multiple TRVs to one (e.g. for a room with multiple radiators)
- Enhance the default TRV Algorithm with some smartness to reduce energy consumption
- Dynamic preset temperature learning & persistence (baseline/"no preset" remembers the last temperature you set and survives restarts)
- **Advanced Control Algorithms**: Choose between MPC, PID, TPI, AI Time Based or simple target temperature matching for precise control.
- **Selectable Presets**: Configure which preset modes are available for your thermostat during setup.
- **Warm Floor mode** *(fork-only)*: Keeps underfloor heating from fully idling between heat calls, using the same heat-loss/heating-power/solar telemetry BT already tracks — no new thermal model, no fully-cold slab to recover from.
- **Boost on window reopen** *(fork-only)*: Optionally reuses a configured secondary climate device (a fan-coil, heat pump, air conditioner, or similar) to actively recover the room after a window closes, symmetrically in either direction — a heat boost if the room got too cold, a cool boost if it got too warm. Its diagnostic state distinguishes disabled, configured-idle, pending, heating, and cooling; it never drives Warm Floor relays or assumes a specific installation entity.

See [Underfloor heating (Warm Floor) & Boost](docs/Configuration/configuration.md#underfloor-heating-warm-floor-mode) in the configuration guide for details.

### Warm Floor additions in this fork

Warm Floor is designed for slow underfloor actuators and remains heat-source-neutral. It can be used with a heat pump, boiler, district heating, electric boiler, buffer tank, or another water-heating source.

- **Safety boundary:** Warm Floor maintenance is allowed when the room temperature is at or above the Better Thermostat target (`current_temperature >= target_temperature`). Below target, normal heating remains in control and Warm Floor sends no maintenance command. At exact target, Auto uses the live diagnostics to maintain a near-zero slope and keep the slab warm.
- **Dynamic Eco, Balanced, and Keep it Hot levels:** The three levels are adaptive baselines, not fixed clock schedules. ON and OFF durations are recalculated from temperature slope, short/one-hour EMA, heat loss, heating power, solar/residual warming, target margin, and learned MPC data.
- **Target-centred maintenance:** At or above target, Warm Floor reduces heating as the room rises above the target band, uses the actuator's four-minute minimum ON travel, and lengthens the pause dynamically. Cooling slope or a falling short EMA can restore maintenance before the room reaches the target; below target, normal Better Thermostat heating remains in control.
- **Actuator-aware duty cycle:** ON and OFF phases respect the configured mechanical travel minimum of slow electrothermal underfloor actuators, so short pulses are not treated as useful heating.
- **Optional outdoor compensation:** If `outdoor_sensor` is configured, Warm Floor uses its current temperature as a bounded heat-loss hint. If it is unavailable, the configured `weather_entity` temperature is used when available. These are reused from the normal Better Thermostat configuration; no second outdoor sensor setting is required.
- **Optional water-temperature compensation:** If `flow_temp_sensor` is configured, its live value is used as a bounded heat-capacity hint. If it is unavailable, `flow_temp_static_c` is used as a manual estimate. If neither is configured, the previous telemetry-only behavior remains active.
- **Auto level:** The new `Auto` level smoothly selects an Eco-like, Balanced, or Keep-it-Hot baseline from live demand, including room response, outdoor conditions, and optional water temperature.
- **Explainable diagnostics:** Warm Floor status includes the current decision, duty phase, reason, demand factor, actuator phase, optional outdoor/water values and their sources, and the resolved Auto policy.
- **Multi-heater zones:** Each configured underfloor heater is evaluated independently. Relays may alternate when safe, while every selected pulse still respects actuator travel time and must produce a real maintenance opportunity.

The optional inputs are capability-based. Users without an outdoor sensor, weather entity, or water-temperature sensor can still use Warm Floor normally. Existing BT configuration fields are reused: `outdoor_sensor`, `weather_entity`, `flow_temp_sensor`, and `flow_temp_static_c`.

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

