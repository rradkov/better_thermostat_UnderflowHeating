---
title: Experimental secondary-device Boost
description: Development reference, unavailable in the official Warm Floor release.
---

This reference describes the retained implementation for work on `dev`. It is
not a production setup guide. Release `1.9.3.1.6` disables this feature, including
previously enabled configurations. Its service and status sensor are not registered.
The standard Boost temperature preset remains available.

Boost is optional and deliberately separate from Warm Floor: it never drives a Warm Floor relay, a switch, or the Better Thermostat's normal heater. It only commands the configured Secondary climate entity. There is no installation-specific default device.

If you've configured a **Secondary climate device** (top of the first step), you can also use it for cooling and fast recovery after a window reopens — most fan-coil/hydronic units, heat pumps and air conditioners already run in both directions, so the same device can boost-heat a cold room back to temperature after airing it out.

**Enable Boost on window reopen** — shown once a Secondary climate device is configured. When on, if the room temperature drifts by more than the configured threshold while a window is open, closing the window arms a boost: colder than the threshold arms a **heat** boost, warmer arms a **cool** boost (checked against what the secondary device actually reports supporting). Either way it targets your normal target temperature, temporarily taking the secondary device over from its normal duty. Once the boost ends, the device is restored to exactly the mode, temperature, and fan setting it had the moment the boost took over.

**Boost temperature-drift threshold (°C)** — default `5.0°C`, range `0.1–15.0°C`.

**Use a different threshold for cooling** — off by default (one threshold covers both directions). Turn this on to set a separate, lower or higher **Boost cooling threshold (°C)** for the warm-day case, independent of the heat-boost threshold.

**Boost fan speed** — only shown if the Secondary climate device reports fan modes. Optionally forces the device's fan speed (e.g. to maximum) while boosting, restored automatically once the boost ends. Leave as "Don't change fan mode" to skip this.

**Boost anticipation (minutes)** — default `5`, range `0–30`. How many minutes of residual heating/cooling the device keeps delivering into the room after being told to stop; used to stop the boost *before* the room actually reaches target, avoiding overshoot in either direction. When the room's own learned solar gain is available, it's factored in too — a heat boost on a sunny day can stop a little earlier, a cool boost needs a little more margin.

**Boost safety timeout (minutes)** — default `60`, range `5–240`. Forces the boost off after this long regardless of temperature, as a guard against a stuck sensor or a device that isn't actually acting.

**Boost cooldown (minutes)** — default `15`, range `0–120`. Minimum time after a boost ends before another one can arm, so a room that keeps drifting past the threshold (e.g. repeated window in/out) doesn't short-cycle the shared device.

A **Boost Status** diagnostic sensor is created per instance. Its states are:

- `disabled` — Boost is switched off, or no Secondary climate device is configured. This is not an idle Boost cycle.
- `off` — Boost is enabled and correctly configured, but no cycle is armed.
- `pending` — a cycle is armed but the Secondary device is currently unavailable; it is cancelled after the safety pending timeout.
- `heating` / `cooling` — the Boost is actively commanding the reported HVAC direction.

The attributes include `reason`, `enabled`, `configured`, `cooler_entity_id`, and the current armed/active ages. This makes a failed test distinguishable from an idle feature instead of hiding both behind `off`.

**Manually testing it** — the `better_thermostat.test_boost` service arms a boost cycle on demand (pick `heat` or `cool`) so you can confirm the wiring — target device, fan mode, threshold reachability — without needing to actually open and close a window. It does not bypass required configuration: with Boost disabled, without a Secondary device, or when that device does not report the requested HVAC mode, no command is sent and the diagnostic sensor records the reason.

**Safety and ownership** — Boost only takes ownership after a valid window-close decision and only while the BT mode, contact state, and `call_for_heat` gate allow it. It snapshots the Secondary device's HVAC mode, setpoint, and fan mode, then restores them. A maximum runtime and cooldown prevent a stuck sensor or repeated window events from running the device indefinitely. The implementation is capability-based and heat-source-neutral: users may select any compatible `climate.*` entity that supports the requested mode; no specific room, relay, heat pump, or installation entity is assumed.
