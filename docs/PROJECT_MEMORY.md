# Better Thermostat — Project Memory

This file is the project decision log and working memory for future changes.
Before making a design decision, debugging change, configuration change, or
code change for this project, read this file first. After every change, add a
dated entry describing what changed, why it changed, and any important
follow-up.

### 2026-10-13 — Warm Floor must sustain above the room target

- **Required behavior:** Warm Floor is intended to keep the underfloor emitter
  warm by temporarily commanding the downstream heater above the Better
  Thermostat room target. Reaching the target alone is not sufficient; the
  Generic Thermostat pulse must have a higher actuator setpoint to turn the
  relay on.
- **Control rule:** Solar gain and a positive temperature slope may reduce or
  pause the sustaining request to prevent overshoot, but they must not change
  the fundamental requirement that an allowed Warm Floor ON pulse is above the
  displayed room target. The room target itself must never be changed.
- **Scope:** This is a generic capability requirement for all supported
  underfloor actuators, not a Tino-specific rule and not related to
  `climate.tatas`.

### 2026-10-13 — Make above-target Generic Thermostat pulses mandatory

- **Incident:** The implementation treated the above-target request as an
  optional `sustain_push`. With the default value of `0.0`, a Generic
  Thermostat could remain at the Better Thermostat target and therefore fail
  to energize its relay during a permitted Warm Floor ON window. If normal
  calibration had already produced a value above target, the diagnostic could
  also report `already_at_floor` instead of the current ON phase.
- **Change:** Generic Thermostat Warm Floor duty cycling now always raises the
  downstream setpoint by at least one valid device step above the Better
  Thermostat target during an allowed ON window. If the computed downstream
  value is already above target and within the safe ceiling, it is still
  reported as `duty_cycle_on` and `active`. The optional sustain amount only
  extends the pulse; it is no longer required to make the relay command
  possible.
- **Safety:** The room target is never changed. Existing HVAC, window/door,
  outdoor, stale-sensor, rising-slope, safe-ceiling, and actuator-type guards
  remain in force. Direct-valve and offset semantics are unchanged.
- **Verification:** The focused Warm Floor test file passes 26 tests; source
  compilation and `git diff --check` pass. The only test warning is the known
  `asyncio_mode` warning caused by disabling the Windows-incompatible HA pytest
  plugin autoload.

### 2026-10-13 — Warm Floor is slab maintenance above room target

- **Clarification:** Warm Floor is not ordinary room-temperature demand
  control. When the room is already above the user target, it should still
  maintain the underfloor slab so the floor does not become cold and require a
  long, inefficient recovery later.
- **Solar case:** If sunlight is raising the room temperature, Warm Floor may
  use shorter or less frequent maintenance pulses, but must continue keeping
  the slab warm whenever the safety envelope permits. A rising slope is not by
  itself a reason to disable all floor maintenance; it is an input for
  reducing the pulse and preventing room overshoot.
- **No-sun case:** When the room is above target without solar support, Warm
  Floor should likewise use economical maintenance pulses rather than wait
  for the room to fall below target. The goal is to prevent slab cooldown and
  avoid a later high-energy recovery.
- **Design consequence:** The controller needs to separate room-demand heat
  from slab-maintenance heat. The room target remains unchanged, Generic
  Thermostat ON commands remain above that target, and positive slope/solar
  telemetry should modulate duty intensity rather than act as a blanket OFF
  gate, subject to explicit safety ceilings.
- **Scope:** This is generic underfloor capability behavior for all users and
  does not introduce Tino/Tata entity handling; `climate.tatas` remains out
  of scope.

### 2026-10-13 — Modulate Warm Floor during positive temperature slope

- **Changed:** A valid positive `temp_slope` no longer disables Warm Floor.
  The slope now scales down the duty demand progressively, with a minimum
  maintenance ratio, while the normal safe temperature ceiling still blocks
  a pulse when the room is materially too warm.
- **Why:** Sunlight and residual heat can raise the room above target while
  the slab is losing stored heat. Turning Warm Floor fully off in that state
  makes the system wait for the room to cool before reheating the floor,
  causing a slow and inefficient recovery after the sunlight disappears.
- **Safety:** Unknown/stale sensor data still fails closed, and all existing
  window/door, outdoor, HVAC, actuator, target-band, and maximum-temperature
  guards remain active. This is capability-based behavior and does not touch
  `climate.tatas`.
- **Verification:** Added rising-room and stronger-slope regression tests;
  the focused Warm Floor suite must pass before deployment.

### 2026-10-13 — Exact Warm Floor activation boundary and three levels

- **Required boundary:** Warm Floor maintenance is eligible when the room
  temperature is equal to or above the Better Thermostat target
  (`current_temperature >= target_temperature`). It must not wait until the
  room is strictly above target, and it must not activate below target as a
  substitute for normal room-demand heating.
- **Required levels:** The public Warm Floor behavior keeps three levels:
  `Eco`, `Balanced`, and `Keep it Hot`. They control the baseline maintenance
  period, ON duration, and floor-hold band; live diagnostics and learned
  demand/slope data may adapt the resulting cycle without removing the three
  user-selectable intensity choices.
- **Generic behavior:** During an allowed ON window, the downstream Generic
  Thermostat setpoint remains above the user target so the relay can energize;
  the Better Thermostat target itself is never modified.

### 2026-10-13 — Separate normal room demand from Warm Floor demand

- **Changed:** Removed the ordinary `target - current` error from the Warm
  Floor `demand_factor`. Below-target error is handled by the normal Better
  Thermostat heating controller and must not make Warm Floor maintenance more
  aggressive.
- **Why:** Warm Floor is eligible only at or above the room target. Its demand
  factor must describe slab-maintenance conditions such as cooling slope,
  learned heat loss/heating power, and MPC feedback—not normal room heating
  demand.
- **Verification:** Added a regression test proving that a below-target room
  does not increase Warm Floor demand when no cooling or thermal-load signal
  is present. The three user levels remain the baseline profiles.

### 2026-10-13 — Feed per-heater MPC feedback into Generic Warm Floor pulses

- **Change:** Generic Thermostat Warm Floor demand now also considers trained
  per-heater MPC `loss_est` and `gain_est` when the heater uses an MPC
  calibration mode. Existing live target error, temperature slope,
  instance-level heat loss, and heating power inputs remain in the demand
  calculation.
- **Why:** The pulse controller should use the most specific valid thermal
  diagnostics available, while safely ignoring missing or invalid learned
  values. It must not feed derived status fields back into the controller.
- **Scope:** Generic, capability-based behavior for all supported MPC-backed
  heaters; no installation-specific entities and no changes to
  `climate.tatas`.

### 2026-10-13 — Make Warm Floor status describe the current actuator phase

- **Incident:** Tino reported `Warm Floor = active` while its Generic
  Thermostat and physical relay were in the OFF duty window. The status was
  technically showing that Warm Floor had affected the last calculation, not
  that an ON command was currently being applied.
- **Change:** The diagnostic sensor now reports `active` only for a current
  sustaining command, including the current Generic Thermostat duty phase.
  OFF-window, release, and temperature-rising decisions report `idle`; a new
  `enabled` attribute makes the configured/enabled state explicit.
- **Scope:** Generic sensor semantics for single- and multi-heater instances;
  no entity-specific logic and no changes to `climate.tatas`.

### 2026-10-13 — Keep durable active presets authoritative during restore

- **Incident:** After the Better Thermostat entities became available again,
  their active presets were all reported as `none`, although the per-preset
  heating and cooling temperatures were still present.
- **Root cause:** `_restore_state()` restored the active preset from the
  durable StateManager, then a legacy restore block later read the stale HA
  snapshot and overwrote it with `none`.
- **Change:** The legacy fallback now runs only when StateManager does not have
  a persisted active preset. A regression test covers a durable `comfort`
  preset with a stale HA `none` state.
- **Scope:** Generic lifecycle behavior for all Better Thermostat entities;
  no room/device-specific logic and no changes to `climate.tatas`.
- **Verification:** Focused pytest execution is blocked on Windows because the
  installed Home Assistant pytest plugin imports POSIX-only `fcntl`; compile,
  diff, and available non-HA checks must still be run before deployment.

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

### 2026-10-13 — Prepared current-main deployment branch

- **Action:** Rebased the fix conceptually by cherry-picking it onto the
  current `origin/main`, which had advanced substantially beyond the original
  local checkout. The resulting branch is
  `codex/warm-floor-fix-publish`.
- **Verification:** The current-main branch compiles and 102 focused tests
  pass, including the Warm Floor, HVAC-action, temperature-event, and periodic
  tick tests.
- **Deployment status:** The branch was pushed to GitHub. The configured GitHub
  API integration lacks permission to create the pull request, so direct main
  replacement was intentionally not attempted. HA must not be updated from
  this branch until it is merged into the repository's current main.

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

### 2026-10-13 — Created separate public DEV repository

- **Changed:** Created `rradkov/better_thermostat_UnderflowHeating-dev` as the
  development repository and copied the current production `main` into it.
- **Why:** Changes need a controlled place for implementation, tests, and Home
  Assistant validation before they are proposed to production `main`.
- **Workflow:** Feature branches and review happen in DEV first; production
  changes move through a pull request from DEV `main` to production `main`.
  Releases are created only from production `main` and must include human-readable
  change and verification information.
- **Scope:** The repository remains generic for all supported users. It does not
  encode Tino/Tata entity names or installation-specific behavior; `climate.tatas`
  remains explicitly out of Warm Floor scope.

### 2026-10-13 — DEV baseline for Warm Floor recovery fix

- **Confirmed:** The DEV `main` contains the merged Warm Floor recovery and
  diagnostics fix at commit `267d64c9`, included in merge commit `30626696`.
- **Purpose:** This is the code now being installed from the public DEV
  repository for Home Assistant validation before any future production release.
- **Verification boundary:** Installing the DEV repository overwrites the shared
  `better_thermostat` integration path in the test Home Assistant. A restart is
  required before the code is active; the test result must be checked against
  downstream Generic Thermostat and physical relay histories.

### 2026-10-13 — Separate user targets from Warm Floor actuator pulses

- **Changed:** Persist the user's heating and cooling targets in the unified
  StateManager and prefer them during startup restoration. When the previous
  entity state is unavailable, startup no longer derives a room target from
  child Generic Thermostat setpoints.
- **Why:** Warm Floor can temporarily raise a downstream Generic Thermostat
  target during its duty cycle. A reload can expose that temporary value while
  the Better Thermostat entity is unavailable, causing the value to be restored
  as the user's room target.
- **Expected behavior:** User target/preset intent survives restart or config
  entry reload; Warm Floor may continue to change only downstream actuator
  setpoints and must never overwrite the room target.
- **Verification:** Added StateManager round-trip and target-recording tests;
  Python compilation passed. Full pytest remains environment-dependent on
  Windows because the Home Assistant test plugin imports Unix-only `fcntl`.

### 2026-10-13 — DEV deployment of user-target restoration fix

- **Deployment:** HACS updated the public DEV repository from `4c00024` to
  `715ed3f`; the installed DEV version now matches the available version and
  has no pending update. Home Assistant was restarted so the custom component
  loaded the new code.
- **Verification:** All seven Better Thermostat config entries reported
  `loaded`, and the system log contained no Better Thermostat error. The Tino
  Generic Thermostat remained available at approximately 24.8°C while the
  outer Tino Better Thermostat entity stayed `unavailable`; its relay and the
  floor pump were ON. This makes the live restore result inconclusive until
  the separate unavailable outer-entity condition is understood.
- **Scope:** No temperatures, relays, pump states, production repository, or
  release metadata were changed manually. Tata's fan-coil remains excluded.

### 2026-10-13 — Persist user target before reload and shutdown

- **Changed:** Better Thermostat now records the current user heating/cooling
  targets immediately before a config-entry unload and during Home Assistant's
  final-write shutdown stage. This closes the gap where the entity became
  `unavailable` before the new `user_targets` store had been updated.
- **Why:** When no persisted target was available, startup restored the generic
  default and bounded it to the configured minimum (16°C in the live Tino
  case). The child Generic Thermostat's Warm Floor pulse must not be used as a
  substitute, but a valid user target must also never be lost.
- **Expected behavior:** After one normal running cycle, reloads and restarts
  restore the last user target even if the old HA entity state is unavailable;
  temporary Warm Floor actuator setpoints remain excluded.
- **Verification:** Source compilation, diff validation, and 46 focused state
  and restore tests passed. Lifecycle/integration tests are added for storing
  targets at unload and final write; native Windows HA pytest execution still
  requires the supported Linux/HA environment because of the `fcntl` plugin
  dependency.

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

## Deployment verification

### 2026-10-13 — DEV deployment of `0bac469`

- HACS refreshed and installed DEV commit `0bac469` (`Persist user targets
  before thermostat unload`); no release was created.
- Home Assistant restarted successfully; all 7 Better Thermostat config entries
  reported `loaded` and HACS reported the same installed and available commit.
- The first post-restart observation showed Tino unavailable and the old
  fallback target of 16°C. Logs confirmed this was expected for this specific
  transition: the previous version had unloaded before the new persistence code
  existed, so it had no saved user target and restored the configured minimum.
- Tino was set back to the user-requested 22°C, then its config entry was
  reloaded. After reload, the outer thermostat remained `heat` at 22.0°C,
  proving the new unload persistence path works.
- The child Generic Thermostat temporarily reported 26.0°C and both the Tino
  relay and floor-heating pump were on; this is a Warm Floor actuator pulse and
  must not overwrite the outer user target.
- No `climate.tatas` entity was changed or used in this verification.

### 2026-10-13 — Release stale Generic Thermostat setpoint when Warm Floor is idle

- **Observed defect:** Tino's outer Better Thermostat reported `idle` at
  22.0°C with room temperature 22.1°C and Warm Floor status `idle`, while the
  Generic Thermostat child still held a 26.0°C setpoint and its relay remained
  on for more than 30 minutes.
- **Root cause:** For an ON/OFF Generic Thermostat, the downstream setpoint is
  itself the relay command. The Warm Floor OFF-window path returned the
  previously computed value unchanged, so an old value above room temperature
  could continue heating even though the Warm Floor status was idle.
- **Change:** In `utils/underfloor.py`, when the controller is idle, the room
  is at/above the user target, the heater is a Generic Thermostat, and no
  explicit sustain push is configured, clamp the downstream setpoint back to
  the user target. Valve and offset-based heaters are not affected. The
  diagnostic reason is recorded as `idle_release`.
- **Verification:** Added a regression test for a stale 26.0°C Generic
  Thermostat setpoint; 35 focused synchronous tests passed, source compilation
  and `git diff --check` passed. Native Windows HA pytest execution remains
  unavailable because the HA plugin imports POSIX-only `fcntl`.

## Review checkpoint

The repository review on 2026-10-13 covered the root project metadata and
README, the complete integration file inventory, configuration and deep
explanation documentation, the calibration/underfloor/boost architecture, the
adapter and entity surfaces, and the unit/integration/benchmark test layout.
The most important project-wide constraint is now explicit: Tino/Tata is a
diagnostic example only; future decisions must be capability-based and valid
for all supported Home Assistant installations.

### 2026-10-13 — Preserve targets and presets across startup/unload

- **Incident:** After a restart/reload, all Better Thermostat wrappers could
  fall to 16°C and lose the active `home` preset. This was not related to
  `climate.tatas`.
- **Root cause:** The entity was removed while `startup_running` was still
  true. Its live fields still contained the initial 5°C default, so the
  removal callback overwrote the durable snapshot before `restore_state()`
  ran. The old HA state could also be unavailable, so the active preset and
  its custom maps were lost.
- **Change:** Startup/unload callbacks no longer capture live targets while
  startup restoration is incomplete. The unified state store now persists the
  active preset plus heating/cooling preset maps, and restoration treats that
  store as authoritative when HA's previous entity state is unavailable.
  User target and preset changes schedule a debounced durable save.
- **Scope:** The logic is capability- and lifecycle-based; it contains no
  room/device-specific handling and does not touch `climate.tatas`.
- **Verification:** `compileall`, `git diff --check`, and the synchronous
  state-manager roundtrip/access tests pass. Full HA async tests could not run
  on Windows because the installed HA pytest plugin requires POSIX `fcntl`.
  The restored live HA targets/presets were set back before code deployment:
  Tino 22, Master 22, Bathroom FL2 24, Office/Guest 22, Kitchen/Dining 23,
  Living/Bathroom 23; all `home`.

### 2026-10-13 — Deployment note for commit `8e0eced`

- HACS installed and reports DEV commit `8e0eced` as both installed and
  available; all seven Better Thermostat config entries are `loaded`.
- The restart itself completed, but HA remains in a prolonged startup wait
  because an unrelated `tuya_local` entry cannot connect. Consequently the
  Better Thermostat entities are currently published as `unavailable`, so
  their post-restart wrapper-state verification must be repeated after HA
  completes startup.
- The six available Generic Thermostat child setpoints were restored to the
  last known room targets (22/22/24/22/23/23). Tino's child is also currently
  unavailable and was not forced. `climate.tatas` was not touched.

### 2026-10-13 — Fix startup crash caused by missing StateManager accessors

- **Incident:** After deploying `8e0eced`, all Better Thermostat entities were
  `unavailable` although their config entries were reported `loaded`.
- **Root cause:** `climate.py` read `state_mgr.preset_heat_temperatures` and
  `state_mgr.preset_cool_temperatures`, but only the fields on `RuntimeState`
  had been added; the corresponding `StateManager` properties were missing.
  Startup therefore raised `AttributeError` for every Better Thermostat.
- **Change:** Added read-only `StateManager` accessors and regression coverage.
