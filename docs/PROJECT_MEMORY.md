# Better Thermostat — Project Memory

This file is the project decision log and working memory for future changes.
Before making a design decision, debugging change, configuration change, or
code change for this project, read this file first. After every change, add a
dated entry describing what changed, why it changed, and any important
follow-up.

## Project intent

This project extends Better Thermostat for a real Home Assistant installation
with radiator, fan-coil, and underfloor-heating use cases. The primary goal is
reliable, explainable heating control: the system should preserve comfort,
avoid unnecessary heating, and recover safely after Home Assistant or device
restarts. Although the current debugging case comes from one installation, the
implementation is for all Better Thermostat users and must not encode that
installation's entity names, topology, sensor quirks, or preferred temperatures.

## Repository and architecture map

- This is a Home Assistant custom integration. The supported baseline is
  Home Assistant `>=2026.7.2` and Python `>=3.14.2`.
- `custom_components/better_thermostat/__init__.py` owns config-entry setup,
  reload/unload, migration, and forwarding to the climate, sensor, number, and
  switch platforms.
- `climate.py` owns the Better Thermostat climate entity, lifecycle, listeners,
  periodic ticks, startup/recovery behavior, and the control queue boundary.
- `trv.py` is the typed per-heater state model. It separates persistent
  configuration, reported device state, write tracking, calibration results,
  and device-quirk scratch state.
- `config_flow.py` owns the user/options flows and normalization of heater,
  sensor, calibration, preset, underfloor, and boost settings. Config-entry
  data is the source of truth; do not edit HA `.storage` files directly.
- `events/` translates Home Assistant state changes for temperature, TRVs,
  windows, doors, and coolers into queued control work.
- `utils/controlling.py` is the actuator/control execution layer. Adapters in
  `adapters/` translate the common control request to Generic Thermostat,
  MQTT, deCONZ, Tado, Z-Wave JS, and fallback device APIs.
- `calibration.py` contains the calibration-mode calculations and the
  integration point where Warm Floor modifies the normal result. The main
  modes are Normal, Aggressive, AI Time Based, MPC Predictive, MPC v2, PID,
  TPI, and No Calibration, combined with target-temperature, offset, or direct
  valve control where the heater supports it.
- `utils/underfloor.py` implements the opt-in Warm Floor behavior for heaters
  explicitly marked `Underfloor`; `utils/boost_heater.py` implements symmetric
  heat/cool recovery after a window reopens.
- `sensor.py`, `number.py`, and `switch.py` expose diagnostics, learned
  values, preset controls, PID controls, child lock, and status entities.
- `diagnostics.py`, device automations, blueprints, translations, and the
  `docs/` site are public compatibility surfaces and need to stay aligned with
  behavior changes.

## General design principles for every user

- Prefer native Home Assistant behavior and validated config flows over raw
  YAML, templates, or internal storage edits.
- Keep user intent separate from actuator reality. A requested setpoint,
  computed controller output, command accepted by the device, and measured room
  response are different facts and should remain diagnosable separately.
- Every control feature must define safe behavior for startup, restart,
  unavailable/unknown states, stale sensors, rejected commands, delayed
  actuators, and recovery. Never assume that a command was applied merely
  because it was calculated.
- Do not silently force heat. Warm Floor may only raise an otherwise idle
  heater after the normal window/door/weather/HVAC gates have passed. Boost has
  its own anticipation, safety timeout, and cooldown guards.
- Scope behavior by capability and heater type. Generic ON/OFF heaters,
  offset-based TRVs, direct-valve TRVs, fan-coils, and cooling devices have
  different actuator semantics; never apply a valve assumption to a relay or
  a fan-coil assumption to a radiator.
- Protect actuator life and energy as well as comfort. The benchmark evaluates
  comfort, actuator longevity, energy, resilience, and adaptation. A change is
  not successful if it improves one metric by creating pathological cycling,
  excessive valve travel, wasted energy, or a restart failure.
- Preserve deterministic behavior where possible. The benchmark separates
  plant, sensor, actuator, and controller, uses explicit seeds, and compares
  controllers against an ideal oracle. Changes to controller logic should have
  a deterministic unit/regression test whenever practical.
- Generalize from the physical signal chain, not one user's entity names:
  sensor -> Better Thermostat state -> controller/calibration -> adapter ->
  heater/valve/relay -> room response. Live HA history is evidence for a bug,
  not a license to hard-code that installation into the integration.
- Favor small, explainable changes with diagnostic state and logs. When a
  behavior changes, document the expected status attributes, the recovery path,
  and how a user can verify it from HA history.
- Make Git history understandable to people who did not implement the change.
  Every commit must have a clear subject describing what changed and why it was
  needed; avoid vague messages such as "update" or "fixes" without context.
- Every release must include human-readable release information describing
  what was added, changed, fixed, or removed, plus any migration, compatibility,
  configuration, or upgrade notes that users need. Release notes are part of
  the public product documentation, not an optional afterthought.

## Verification expectations

- For source changes, run at least syntax compilation plus the narrowest
  relevant unit/integration tests. Run the benchmark/regression gate when a
  controller, calibration algorithm, thermal model, or actuator behavior is
  changed.
- Add or update tests for lifecycle and fault cases, not only the happy path:
  startup, restart, unavailable recovery, stale sensor, multi-heater setups,
  direct and indirect control, and the relevant heater capability.
- Keep README, configuration docs, deep explanations, translations, service
  references, and diagnostic sensors consistent with public behavior.
- Before deployment, verify the live entity history for intent, command,
  actuator state, and downstream pump/plant behavior. Record the result here.
- The repository currently contains a benchmark suite with deterministic
  simulation and a broad unit/integration suite. The local environment used
  during the 2026-10-13 investigation did not have `pytest` installed, so test
  execution remains an explicit follow-up rather than an assumed success.

## Rules and decisions

- Do not conflate the Tino underfloor-heating chain with Tata's fan-coil.
  `climate.tatas` is the fan-coil climate entity and is unrelated to Tino's
  Warm Floor relay.
- Tino's relevant control chain is:
  `climate.tino_thermostat` → Tino Generic Thermostat →
  `switch.tasmota_8` ("1 Tino Bedroom") →
  `switch.tasmota_4` ("5 Floor Heating Pump").
- Warm Floor `active` means the sustaining feature is enabled. It does not
  mean the relay must be continuously ON. Generic Thermostat underfloor
  control uses a duty cycle with explicit ON and OFF windows.
- A duty-cycle implementation must be event-driven or periodically
  re-evaluated. It must never remain stuck in an OFF window after a restart,
  device-unavailable interval, or other state recovery.
- Any recurring control loop must be scoped to the relevant heater type. Do
  not add Warm Floor behavior to radiators or fan-coils.
- The floor pump automation follows the zone relays. A pump switching OFF is
  expected when all relevant zone relays are OFF; investigate the zone relay
  control before changing pump logic.
- Home Assistant history and entity attributes are the source of truth when
  diagnosing live behavior. Check the actual relay, thermostat, Warm Floor
  diagnostic sensor, and pump history together.
- Do not make live Home Assistant configuration changes while diagnosing unless
  the user explicitly asks for the change. Code changes belong in the
  repository and must be verified before deployment.

## Relevant historical chats reviewed

On 2026-10-13, the other chats that clearly belonged to this repository or to
the Warm Floor investigation were reviewed. Their content is historical
context, not a substitute for the current source code, tests, Git history, or
live Home Assistant evidence.

- **Underfloor heating feature (fork):** confirmed the project-wide goal of
  preserving Warm Floor behavior across target changes, protecting actuators
  with a minimum four-minute ON/OFF behavior where configured by the design,
  and exposing richer Warm Floor diagnostics. It also recorded that standard
  Home Assistant `hvac_action` values must remain compatible; controller state,
  command/request state, heater feedback, and feedback availability should be
  represented separately rather than by inventing new `hvac_action` values.
- **Clone UnderflowHeating repository:** repeated the target-change and
  recovery expectations: lowering a target must not leave an old sustaining
  command active, while a later valid heating request must still be able to
  produce a complete, actuator-safe pulse. It also described expanded status
  attributes for explaining duty phase, timing, temperatures, and reasons.
- **Проверка на термостат Warmfloor:** contained a Warm Floor symptom report,
  but no final technical conclusion. It is retained as context only.

The repository history currently verifies related Warm Floor commits including
`3d234f36` (adaptive Warm Floor control), `7e8e09ed` (actuator timing and
diagnostics), and `90987c51` (starter duty profiles). A historical chat
mentioned a different commit hash and a historical test count; those details
must be re-verified from the current checkout before being used as evidence.

Historical release discussions requested release line `1.9.3.1.2`. The current
checkout is tagged `1.9.2.1.2` and also contains newer branch history, so the
release number must be reconciled from the actual metadata and intended release
plan rather than inferred from chat text.

Chats about unrelated Immergas/Legionella or domestic hot-water behavior were
excluded from this project's memory. They do not define Better Thermostat or
Warm Floor requirements.

## Cross-user behavior rules added from chat history

- Keep the standard Home Assistant climate contract stable. Do not overload
  `hvac_action` with project-specific duty-cycle labels.
- Make diagnostics explain four separate layers: controller decision,
  requested command, actuator/heater feedback, and whether feedback is
  available. If feedback is unavailable, report unknown/unavailable instead of
  guessing that the command was applied.
- Apply Warm Floor timing, target-change handling, and recovery logic to the
  capability/type model, not to Tino, Tata, Tasmota, or any other installation
  entity name.
- When changing an actuator timing rule, cover both comfort and actuator safety:
  no stale heating after a target reduction, no too-short pulse, and no missed
  valid ON window after restart or unavailable recovery.
- Rich diagnostics are part of the public behavior: they should remain useful
  for Generic Thermostat, TRV, HVAC, fan-coil, heat-pump, and other supported
  actuator types without assuming direct relay feedback.

## Change log

### 2026-10-13 — Warm Floor diagnostics and stale HVAC action cache

- **Changed:** Warm Floor now retains one diagnostic record per controlled
  heater and the status sensor exposes all records through
  `warm_floor_heaters` and `heater_count`. Duty-cycle timing is refreshed by
  sensor polling, so an unchanged climate state does not leave the displayed
  ON/OFF window frozen.
- **Changed:** The cached standard Home Assistant `hvac_action` is invalidated
  when temperature, target temperature, or HVAC mode input changes. The next
  control boundary recalculates the action instead of temporarily reporting a
  decision for the previous inputs.
- **Why:** Live history showed stale Warm Floor diagnostic attributes and a
  second-floor bathroom aggregate action of `heating` while its Generic
  Thermostat and relay were idle. Multi-heater history showed both relays
  operating together, but the single status dictionary hid one heater.
- **Compatibility:** The historical `_warm_floor_status` single-record view is
  retained for existing callers. No installation entity names, Tino/Tata
  special cases, or non-standard HVAC action values were added.
- **Verification:** Source compilation and the initial 66 focused tests passed
  after installing Home Assistant 2026.10.1 and the project test dependencies.
  The expanded run passed 103 tests; eight unrelated pre-existing Warm Floor
  maintenance tests target symbols absent from the current checkout, and the
  test-double gate issue was corrected before commit. Full HA
  test execution on native Windows still requires the repository's supported
  Linux/HA test environment because the HA pytest plugin imports POSIX-only
  `fcntl`.

### 2026-10-13 — Post-commit live HA verification

- **Action:** Reloaded all six Better Thermostat config entries through the HA
  MCP connection, explicitly excluding the unrelated Tata fan-coil entity.
- **Result:** The entries reloaded successfully and the temporarily unavailable
  grouped climates recovered to `heat/idle`. No new Better Thermostat error was
  found in the HA system log; the remaining warning concerns missing local
  calibration entities for grouped Generic Thermostats.
- **Live observation:** The current second-floor bathroom and Tino targets are
  above their current temperatures, so their live `heating` action is expected
  for the present targets. The first-floor relay pairs still change together.
- **Limitation:** The live Warm Floor sensors still expose the old attribute
  schema and do not show `heater_count` or `warm_floor_heaters`. Therefore this
  HA instance is not loading the committed Windows worktree directly; the new
  diagnostic schema still needs deployment to the HA custom-components path
  before it can be validated in a real overnight cycle.

### 2026-10-13 — Warm Floor recovery after restart

- **Observed:** Tino's relay was ON at 02:00, became unavailable during a
  Home Assistant/device recovery around 02:25, then returned OFF at 02:26 and
  stayed OFF. Warm Floor reported `active` but `outside_duty_window`.
- **Cause:** Generic Thermostat Warm Floor duty-cycle decisions were not
  guaranteed to be re-evaluated after recovery. If the first evaluation landed
  in the OFF portion of the duty cycle, the next ON window could be missed.
- **Change:** `climate.py` now detects Generic Thermostat heaters marked as
  underfloor and enables a one-minute periodic control tick for them. The tick
  re-queues the normal control cycle so the Warm Floor setpoint is recalculated
  and the next ON window is not lost.
- **Scope:** The timer applies only to Generic Thermostat + `Underfloor`
  heaters. Tata's fan-coil (`climate.tatas`) is not part of this change.
- **Verification:** Python bytecode compilation passed. The repository test
  suite could not be run because `pytest` is not installed in the current
  environment.

### 2026-10-13 — Historical chat context reconciled

- **Changed:** Added the relevant decisions from the other repository chats to
  this memory file, including the universal status model, actuator timing and
  target-change expectations, and the requirement for capability-based rather
  than installation-specific behavior.
- **Why:** Future changes must preserve the project's existing public contract
  and design intent, not only fix the current Tino incident.
- **Evidence:** Compared the chats with the current source layout and Git
  history. Confirmed current Warm Floor commits in the checkout; left release
  numbering and historical test-count claims explicitly open until verified.

### 2026-10-13 — Commit and release documentation rule

- **Changed:** Added a permanent rule requiring explanatory commit messages and
  user-readable release notes.
- **Why:** Contributors and users must be able to understand what changed, why
  it changed, and whether an upgrade or configuration action is needed.
- **Required practice:** Before committing, write a concise subject plus enough
  context in the commit body when the change is non-trivial. Before releasing,
  document additions, behavior changes, fixes, removals, compatibility impact,
  migration steps, and relevant verification results.

### 2026-10-13 — Live Tino and second-floor bathroom diagnostic

- **Observed:** Tino's Better Thermostat currently reports `hvac_action: idle`
  with approximately 22.1°C / 22.0°C, while its Warm Floor status reports
  `active` and `outside_duty_window`. Its physical relay and the floor pump are
  OFF.
- **Observed:** The second-floor bathroom Better Thermostat reports
  approximately 23.6°C / 23.0°C and `hvac_action: heating`, but the underlying
  Generic Thermostat is `idle` and the physical bathroom relay is OFF. The
  floor pump is also OFF. The underlying Generic Thermostat had previously
  exposed a 30.0°C target during the control history, which is an actuator
  command/setpoint detail and not the Better Thermostat user's target.
- **Important distinction:** `call_for_heat: true` means the outdoor/seasonal
  heating permission is open; it does not prove that a relay is ON. The live
  bathroom data demonstrates that the reported outer `hvac_action` can remain
  inconsistent with the downstream actuator after a target/control transition
  or recovery.
- **Current diagnosis:** This is a state-synchronization/reporting issue to
  investigate in the Better Thermostat control-cycle and HVAC-action update
  path, not evidence that the bathroom relay is currently heating. The Warm
  Floor diagnostic sensor for the bathroom is also stale (last update near the
  earlier recovery), so its attributes must not be treated as current feedback.
- **Scope:** This investigation excludes `climate.tatas`, which is the
  unrelated fan-coil entity.

### 2026-10-13 — All Warm Floor zones and multi-heater status diagnostic

- **Observed:** The live installation exposes Warm Floor status sensors for
  Tino, Master, Kitchen and Dining, Living room + Bathroom, Bathroom FL2, and
  Office/Guest room. Most status sensors still show the last decision from the
  earlier recovery window rather than a current update, so their attributes
  must be treated as stale until the status-refresh path is fixed.
- **Observed:** The first-floor multi-heater zones are configured as follows:
  Kitchen and Dining controls `climate.dinning_room_thermostat` and
  `climate.kitchen_thermostat` through relays R2/R3; Living room + Bathroom
  controls `climate.living_room_thermostat` and
  `climate.bathroom_fl_1_thermostat` through relays R1/R4.
- **Verified from history:** During the recorded Warm Floor ON window, both
  relays in each multi-heater pair changed together. R1/R4 were ON together
  from the recovery pulse, and R2/R3 were ON together in their later pulse.
  The corresponding floor pump followed the active relays. This does not
  support a current conclusion that Warm Floor only commands one of the two
  Generic Thermostats.
- **Confirmed implementation limitation:** `apply_warm_floor_floor()` runs
  per controlled heater, but `_record_status()` stores one
  `bt._warm_floor_status` dictionary on the whole Better Thermostat entity.
  With multiple heaters, later calls overwrite earlier status. The single
  Warm Floor sensor therefore exposes only one heater's last decision and can
  falsely suggest that the other heater was not processed.
- **Required direction:** Keep per-heater Warm Floor decisions and expose them
  in diagnostics without breaking the existing single-zone sensor contract.
  Multi-heater behavior must be verified using each downstream Generic
  Thermostat and physical relay, not only the aggregate status sensor.
- **Scope:** The unrelated `climate.tatas` fan-coil remains excluded.

## Open items

- After deploying the fix, verify one overnight cycle using the histories of
  `switch.tasmota_8`,
  `sensor.kids_room_tino_thermostat_warm_floor_status`, and
  `switch.tasmota_4`.
- Confirm that the one-minute tick produces the intended relay duty cycle and
  does not violate the Generic Thermostat minimum cycle or cooldown settings.
- If the behavior still fails, inspect the control queue and the Generic
  Thermostat setpoint history before changing the duty-cycle formula.
- Investigate why an outer Better Thermostat can retain `hvac_action: heating`
  after the Generic Thermostat reports `idle` and its physical relay is OFF;
  the fix must preserve standard Home Assistant `hvac_action` values and work
  for all supported actuator types.
- Refresh or make Warm Floor diagnostics event/periodically driven for every
  applicable heater type so stale status attributes cannot be mistaken for
  current actuator feedback.
- Preserve per-heater Warm Floor status for Better Thermostat instances that
  control more than one heater; do not let the last processed heater overwrite
  the diagnostic view of all other heaters.
- Reconcile the current version metadata before release: `pyproject.toml` and
  `README.md` report `1.9.2.1.1`, while the working-tree manifest reports
  `1.9.2.1.2`. Treat the manifest/release-directory edits as existing working
  tree changes until their owner confirms the intended release version.

## Review checkpoint

The repository review on 2026-10-13 covered the root project metadata and
README, the complete integration file inventory, configuration and deep
explanation documentation, the calibration/underfloor/boost architecture, the
adapter and entity surfaces, and the unit/integration/benchmark test layout.
The most important project-wide constraint is now explicit: Tino/Tata is a
diagnostic example only; future decisions must be capability-based and valid
for all supported Home Assistant installations.
