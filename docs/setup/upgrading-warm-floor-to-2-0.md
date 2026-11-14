---
title: Upgrading the Warm Floor fork to 2.0
description: Preserved settings, retired secondary Boost, and validation workflow.
---

## Upgrade and migration

This fork uses upstream tag `2.0.0` and requires Home Assistant **2026.11.0 or
newer**. Back up Home Assistant before upgrading. The integration keeps the
`better_thermostat` domain and installation location.

Room settings are read from entry options over entry data, then stored together
in options. Existing Warm Floor heating types, levels and Custom values are
retained. The user-facing target remains separate from maintenance requests.
Optional flow/water inputs are retained for Warm Floor; upstream's existing
calibration algorithms continue to use their own inputs and semantics.

The fork's automatic secondary-device Boost Heat/Cool implementation, its
`test_boost` service, setup options and status sensor are removed. Retired Boost
settings are discarded, and the old status entity is removed by registry cleanup.
Automations calling the retired service must be removed or disabled. Upstream's
Boost temperature preset, other presets, normal cooler support and device-release
controls remain available. Do not remove automations using those features merely
because their name includes Boost.

Development is validated on Linux using the locked dependencies and published
to `dev` before any promotion to `main`. Testing includes the upstream suite,
maintenance timing, mixed devices, sparse sensors, settings migration and
restart behavior. Passing simulated tests is not a guarantee of physical flow
or correct operation of every possible device. Verify the reported commands and
device feedback after installing on a test Home Assistant instance.

Dashboard templates from 1.9 must read the status sensor's `circuits` attribute
instead of `warm_floor_heaters`. Each circuit exposes `phase`,
`remaining_seconds`, `on_seconds`, `off_seconds`, `requested_setpoint`,
`write_result` and `reported_heating`. Room temperature, target and trend are
in `thermal_inputs`; missing values stay unknown. Render a row for each circuit
so a grouped room does not hide the second heater. Planned ON and a successful
write are separate from device-reported heating.

## Validation of the 2.0 port

Validated on 14 November 2026 under WSL Ubuntu 26.04.1, Python 3.14.10 and
the locked Home Assistant 2026.11.1 dependencies:

```sh
uv sync --locked
uv run --locked pytest tests -n 4 -q --tb=short \
  --cov=custom_components/better_thermostat --cov-report=json:coverage.json
uv run --locked python scripts/coverage_floors.py check
uv run --locked pyrefly check
uv run --locked ruff check
uv run --locked ruff format --check
uv run --locked yamllint --strict .
```

The full suite passed **18,524 tests**, with two expected upstream skips: the
retired naming-backlog budget and optional replay without a supplied sequence.
All 128 modules met their coverage floors; existing floors were not lowered.
The naming, exception-budget and restated-contract gates also passed.

A separate Home Assistant 2026.11.1 process in WSL was started twice with a
real Generic Thermostat helper and an input-boolean test output. Normal heating
handed over to a Warm Floor ON phase, the output switched on, and the 23.5°C
room target survived restart independently of the temporary heater setpoint.
The restarted controller recovered an ON phase with a full four-minute minimum.
This checks software behavior with a simulated output, not a physical actuator.

The port starts at upstream `2.0.0` (`aed2036b`), retaining the previous fork
`dev` (`3967b2a9`) in its merge history. Calibration algorithms are unchanged.
Publication targets `dev`; production promotion and releases remain separate.
