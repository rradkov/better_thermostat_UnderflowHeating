# Better Thermostat — Project Memory

### 2026-10-15 — Continuous target balance with economical floor maintenance

- **Requirement:** Warm Floor must keep the room around the Better Thermostat
  target (preferably target to target + 0.1°C) with `temp_slope` near zero,
  while still keeping the slab warm at the lowest useful energy cost.
- **Change:** Added a continuous target-balance factor after the Auto/level
  baseline and live demand blend. The factor reduces actual ON intensity as
  the room moves above the target band, while cooling, preheat risk, heat-loss/
  power, outdoor/flow and applicable MPC diagnostics can restore maintenance
  intensity when the room is moving back toward target.
- **Safety:** The factor never removes the minimum valid floor pulse, never
  changes the user's target or preset, preserves the `current >= target`
  Warm Floor boundary, and keeps the four-minute actuator travel minimum.
  Eco/Balanced/Keep it Hot remain diagnostic labels; the duty cycle is now the
  primary continuous control output.
- **Verification:** Focused Warm Floor suite passed 58 tests; compilation,
  Ruff, and `git diff --check` passed. No release was created.


### 2026-10-15 — Auto prioritizes a large above-target margin

- **Observed:** Kitchen was materially above target (`25.1°C` vs `23.0°C`),
  but Auto remained `Keep it Hot` because a mild negative EMA separation was
  treated as sufficient cooling evidence.
- **Problem:** A short-term cooling hint could select the strongest maintenance
  baseline even while the room still needed to drift back toward target.
- **Change:** When the measured margin is materially above the near-target
  band, Auto now selects `Eco` while the room returns toward target. Only a
  clearly strong cooling/load condition selects `Balanced` in that band.
- **Safety:** Eco still keeps the minimum valid Warm Floor maintenance pulse;
  this changes the maintenance baseline, not the `current >= target` guard,
  normal heating ownership below target, or the four-minute actuator minimum.
  Near the target, the existing EMA/slope recovery logic can return the policy
  to `Balanced` or `Keep it Hot` as the diagnostics justify it.
- **Verification:** Added regression coverage for mild cooling while materially
  above target and for strong cooling in the same band. The focused Warm Floor
  suite passed 56 tests; compilation and `git diff --check` passed. The broader
  legacy maintenance test file still contains eight pre-existing references to
  removed APIs and is not part of this change.


### 2026-10-15 — Separate plant load from target-aligned Warm Floor demand

- **Observed:** Kitchen had a `demand_factor` near `0.9` because
  `heat_loss / heating_power` was approximately `0.00665 / 0.007`, even while
  the room stayed around `24.3°C` against a `23°C` target.
- **Problem:** Plant-load demand was allowed to lengthen maintenance pulses
  above target. That signal describes heat loss/capacity, not a request to add
  heat while the room is already moving toward target.
- **Change:** Preserve the raw plant-load demand as a diagnostic, then cap the
  effective demand above target using only measured cooling direction (slope or
  EMA separation) and relative preheat risk. If the room is rising or has no
  cooling evidence, plant-load demand no longer increases the Warm Floor
  pulse. At exact target, the raw load signal remains available so the
  controller can maintain `temp_slope ≈ 0`.
- **Goal:** Keep the floor warm while allowing an above-target room to return
  toward its target, without introducing a fixed Celsius ceiling or changing
  the below-target ownership of normal Better Thermostat heating.

### 2026-10-15 — Release stale Generic Thermostat command at exact target

- **Observed:** After Warm Floor became eligible at the exact target, Tino could
  show Better Thermostat idle and Warm Floor outside its duty window while the
  downstream Generic Thermostat still held a 30°C setpoint and kept its relay
  on.
- **Root cause:** Stale-command release required `current_temperature > target`,
  so equality skipped the release path.
- **Change:** Release a stale Generic Thermostat Warm Floor command when
  `current_temperature >= target_temperature`, during an OFF/rising duty phase,
  while BT is idle and no sustain push is active. The command is moved one
  valid device step below target; the user's target and preset are unchanged.
- **Verification:** Add exact-target regression coverage and verify the
  downstream setpoint, HVAC action, and relay after the DEV deployment.

### 2026-10-14 — Allow Warm Floor maintenance at exact target

- **Requirement correction:** Warm Floor may operate when
  `current_temperature >= target_temperature`. The below-target region remains
  owned by normal Better Thermostat heating.
- **Exact-target behavior:** When the room is exactly at target and idle, Auto
  starts from Keep it Hot while the live slope, EMA/EMA 1h, heat loss,
  heating power, outdoor temperature, flow temperature, and MPC signals
  continuously determine the actual ON/pause duty cycle. The goal is a stable
  room with `temp_slope` near zero and a warm slab, not a fixed temperature
  overshoot.
- **Auto explanation:** A room can be above target and still select Keep it
  Hot when the diagnostics show cooling/load risk. The level is a baseline;
  the dynamic duty profile remains responsible for reducing heat as the room
  warms or the margin grows.
- **Implementation:** The equality boundary is now eligible for Warm Floor,
  Generic Thermostat pulses can turn on at exact target, and Auto treats an
  exact-target non-warming room as Keep it Hot until warming is confirmed.


### 2026-10-14 — Warm Floor control objective: target-centered room with zero slope

- **Requirement clarified:** The primary Warm Floor objective is not to keep the
  room substantially above the Better Thermostat target. It should keep the
  floor warm while bringing the room toward a narrow band around the target.
- **Control target:** While the room is above target and Warm Floor is allowed,
  the controller should reduce positive temperature excess and aim for a
  `temp_slope` close to `0`, rather than continuously adding heat because the
  room is already more than approximately 1°C above target.
- **Boundary:** The existing strict rule remains authoritative: Warm Floor may
  issue no command when `current_temperature <= target_temperature`; normal
  heating owns that region. The maintenance controller must therefore operate
  only in the above-target band and must not fight normal heating.
- **Proposed behavior:** A large above-target margin with a positive slope
  should progressively reduce or suspend maintenance intensity, while a room
  near target with a flat or cooling trend should receive the minimum valid
  actuator pulse needed to keep the floor warm. The four-minute actuator travel
  minimum remains mandatory.
- **Auto interaction:** Auto/Eco/Balanced/Keep it Hot should select the
  maintenance intensity, but the temperature margin and slope must remain the
  stronger safety feedback. A mode label must not justify heating a room that
  is already materially above target.
- **Status:** Design requirement recorded; runtime implementation and HA
  configuration are unchanged in this step.

### 2026-10-14 — Normal heating owns the at/below-target region

- **Explicit boundary:** If `current_temperature <= target_temperature`,
  normal Better Thermostat heating owns the zone. Warm Floor must not issue a
  maintenance command there, regardless of whether Auto would otherwise prefer
  Balanced or Keep it Hot.
- **Meaning of Auto levels:** Eco, Balanced, and Keep it Hot are only relevant
  while the room is strictly above target and Warm Floor is permitted to run.
  Keep it Hot means stronger above-target maintenance to prevent the room from
  reaching the target from above; it never means that Warm Floor replaces the
  normal heating demand below target.
- **Status:** Clarification recorded; no runtime implementation or HA change
  was made in this step.

### 2026-10-14 — Predictive Warm Floor maintenance near target

- **Goal:** Warm Floor should keep the room very close to the configured target
  while it is still strictly above target, reducing unnecessary crossings into
  the normal-heating region.
- **Decision inputs:** The decision must combine the complete available
  diagnostic picture: current temperature margin, short EMA, one-hour EMA,
  temperature slope, heat-loss rate, heating power, normalized response,
  outdoor temperature, configured flow/water temperature, and learned MPC
  signals when available. Current room temperature and measured trend remain
  authoritative; the other signals qualify or bias the decision and must not
  override the target boundary.
- **Predictive behavior:** When the room is above target but the filtered trend
  predicts that it will reach target soon, Warm Floor should increase
  maintenance early enough to avoid normal heating starting. When the room is
  materially above target and still warming, it should reduce or defer new
  pulses. Near target, a flat trend uses minimum valid maintenance; a confirmed
  cooling trend increases maintenance before the target is crossed.
- **Hard boundary:** At or below target, Warm Floor sends no command. Normal
  heating owns that state. Forecasting may only affect the preceding
  above-target maintenance decision.
- **Timing:** No fixed maximum pause is reintroduced. Pulse and pause remain
  dynamic, with the actuator's four-minute ON/OFF minimum always enforced.
- **Robustness:** Missing or stale diagnostics must fall back conservatively;
  persistence and hysteresis are required so one noisy sample cannot trigger a
  mode or pulse change.
- **Status:** Design requirement recorded; runtime implementation and HA
  configuration are unchanged in this step.

### 2026-10-14 — Near-target thermal-balance decision

- **Requirement refined:** When the room is very close to target, Auto must use
  all available diagnostics to decide whether the floor needs `Balanced` or
  `Keep it Hot` maintenance and what interval is appropriate. The objective is
  for the room to neither continue warming nor begin cooling materially.
- **Balanced equilibrium:** Prefer Balanced when the temperature margin is
  small, the short EMA and one-hour EMA are close, the slope is persistently
  near zero, and the learned/telemetry signals indicate that heat loss and
  delivered heating are approximately in balance. Keep the minimum valid
  actuator pulse, but allow the dynamic pause to grow.
- **Keep-it-Hot recovery:** Prefer Keep it Hot when the room is close to target
  but the slope or EMA separation shows cooling, heat loss is high, available
  heating power is weak, outdoor conditions are colder, flow temperature is
  insufficient for the current response, or MPC predicts an imminent target
  crossing. Increase maintenance before normal heating must start, while the
  room remains strictly above target.
- **Eco exception:** If the room is near target but persistently warming, Auto
  may select Eco and lengthen the interval; a flat trend alone is not evidence
  for Eco when the room is close to needing heat.
- **Status:** Design requirement recorded; runtime implementation and HA
  configuration are unchanged in this step.

### 2026-10-14 — Continuous interval adaptation toward thermal balance

- **Requirement refined:** Warm Floor must not treat Eco, Balanced, and Keep it
  Hot as fixed schedules. They are regions of a continuously calculated
  maintenance controller. The controller should gradually recalculate pulse
  length, pause length, and effective policy using the diagnostic feedback until
  the room reaches thermal balance.
- **Two intervals must be distinguished:** The diagnostic/control reevaluation
  interval may become shorter when the room is near target or changing quickly.
  The heating-pulse interval must follow the thermal direction: when the room is
  above target and still warming, pause longer and reduce maintenance; when it
  is above target but cooling toward target, shorten the pause gradually so the
  normal heater does not need to start.
- **Hysteresis objective:** Use filtered temperature margin, EMA separation,
  slope, heat-loss/heating-power balance, outdoor/flow inputs, and MPC response
  to narrow the actual room oscillation around target. Do not reduce hysteresis
  by repeatedly forcing heat while the room is warming above target.
- **Mode mapping:** The displayed effective level may move gradually through
  Eco, Balanced, and Keep it Hot, or jump when evidence is strong, but the
  underlying intensity and intervals remain continuous and diagnostic-driven.
- **Hard constraints:** Warm Floor still sends no command at or below target,
  and every ON/OFF transition must respect the four-minute actuator travel
  minimum.
- **Status:** Design requirement recorded; runtime implementation and HA
  configuration are unchanged in this step.

### 2026-10-14 — Warm Floor may anticipate normal heating before target crossing

- **Clarification:** Warm Floor may continue preventive maintenance until just
  before normal heating needs to start, provided the room is still strictly
  above target. This is the predictive transition zone, not an exception to the
  above-target safety boundary.
- **Prediction source:** Prefer a validated Better Thermostat/MPC forecast of
  imminent heating demand when one is exposed. Otherwise derive the forecast
  from the same live diagnostics: filtered target margin, EMA/EMA-1h, slope,
  heat loss, heating power, outdoor/flow inputs, and learned MPC response.
  `call_for_heat` alone is not sufficient because it describes current demand
  state and may not be a future-demand forecast.
- **Control:** If the forecast shows that the room will reach target soon,
  Warm Floor gradually shortens its dynamic pause or raises its effective
  maintenance level while still above target. If the forecast weakens or the
  room continues warming, it lengthens the pause again.
- **Boundary:** Once `current_temperature <= target_temperature`, Warm Floor
  must stop immediately and normal Better Thermostat heating owns the zone.
- **Status:** Design requirement recorded; runtime implementation and HA
  configuration are unchanged in this step.

### 2026-10-14 — DEV deployment of trend-confirmed Auto policy

- **Deployment:** HACS installed DEV commit `6a4b4e5` and Home Assistant was
  restarted successfully. HACS reports `installed_version=available_version`
  `6a4b4e5`; all seven Better Thermostat entries are `loaded`.
- **Live verification:** Tino, Master, Office/Guest, Living/Bathroom, and the
  configured Warm Floor heaters started with fresh status ages and
  `auto_policy: level_3` (`Keep it Hot`), as intended by the conservative
  bootstrap. Living/Bathroom showed the expected independent multi-heater
  phase behavior. Bathroom FL2 remained under normal below-target heating and
  the fan-coil remained HVAC off.
- **Safety:** No target, preset, relay, or manual configuration was changed.
  `climate.tatas` was not queried or changed. Kitchen/Dining remained
  `unavailable`/disabled as a separate existing runtime state; it was not
  manually altered. No release was created.

### 2026-10-14 — Implement conservative, trend-confirmed Auto policy

- **Change:** Auto now bootstraps at `Keep it Hot` when it first becomes active,
  then changes at most one level at a time after minimum dwell and persistence
  windows. Confirmed warming moves `Keep it Hot` → `Balanced` → `Eco`; confirmed
  cooling or high thermal demand moves back toward `Keep it Hot`.
- **Signals:** The policy candidate uses short-vs-one-hour EMA separation and
  temperature slope as primary direction evidence, while the existing demand
  factor incorporates heat loss, heating power, outdoor temperature, flow
  temperature, and applicable MPC data. Strong warming can request Eco, but it
  still takes two confirmed transitions from the initial Keep it Hot state.
- **Safety:** EMA/slope can select maintenance intensity only. The strict
  `current_temperature > target_temperature` Warm Floor guard, HVAC/contact
  gates, actuator travel protection, and absolute ceiling remain unchanged.
  No heat source or installation entity is hardcoded.
- **Verification:** Added bootstrap, warming-transition, and cooling-recovery
  tests. The focused Warm Floor/config suite passed 70 tests; compilation and
  `git diff --check` passed. HA deployment is the next step; no release was
  created in this change.

### 2026-10-14 — Candidate signals for a more precise Auto policy

- **Primary policy signals:** current temperature margin above target,
  short-vs-one-hour EMA separation, slope and its persistence. These describe
  what the room is actually doing and should drive Keep it Hot → Balanced →
  Eco transitions and the reverse path.
- **Thermal confirmation:** `heat_loss_rate / heating_power`, normalized
  heating power, and learned MPC loss/gain/thermal-response values can confirm
  whether the room is cooling despite heating or warming without active heat.
  They should be bounded and should not override the room-temperature guard.
- **Feed-forward hints:** configured outdoor sensor/weather temperature and
  configured flow-temperature sensor/static fallback can bias the decision for
  cold weather or low water capacity. They are not sufficient by themselves to
  select a policy and remain heat-source-neutral.
- **Optional solar context:** weather condition, sun elevation/time-to-sunset,
  or a configured solar/irradiance signal could strengthen the warming-side
  transition, but only as optional bounded evidence. The controller must still
  trust measured room slope/EMA over a weather label or forecast.
- **Safety/state gates:** `hvac_action`, `call_for_heat`, window/door state,
  unavailable data, actuator phase/recovery, and absolute temperature ceiling
  should block or constrain Warm Floor, not independently choose Eco or Keep it
  Hot. Humidity is useful for diagnostics but is not a reliable floor-demand
  signal.
- **Decision model:** combine the signals with confidence/quality checks,
  persistence and hysteresis. If evidence conflicts, prefer the safer stronger
  maintenance level while the room is above target; at or below target normal
  heating remains authoritative and Warm Floor sends no command.
- **Reference observation:** Tino currently has outdoor 12.6°C, flow 29.3°C,
  positive slope and short EMA above EMA 1h; this is mixed but warming evidence,
  so it should not be promoted to Keep it Hot solely because EMA 1h is below
  target.
- **Status:** Analysis/design only; no runtime or HA configuration change was
  made in this step.

### 2026-10-14 — Use EMA separation as an Auto transition trigger

- **Signal:** Auto should explicitly use the separation between the short
  temperature EMA and the one-hour EMA as a transition signal, not merely as a
  hidden intensity correction.
- **Interpretation:** `short EMA > EMA 1h` means the room has recently been
  warmer than its slower baseline and is a warming/residual-solar candidate;
  after persistence and a positive slope, it can move from `Keep it Hot` to
  `Balanced`, then `Eco`. `short EMA < EMA 1h` means recent cooling relative to
  the baseline and should keep or restore `Keep it Hot`.
- **Guardrails:** EMA separation alone must not change policy on a single
  noisy sample. Require a persistence window, a minimum separation, current
  temperature above target, and hysteresis between up/down transitions.
- **Tino interpretation:** The observed short EMA around 22.02°C versus EMA
  1h around 21.75°C is a warming signal, not a reason to select Keep it Hot.
- **Status:** Design refinement only; no runtime behavior or HA configuration
  was changed in this step.

### 2026-10-14 — Auto bootstrap should start conservatively near target

- **Design refinement:** When the room is only slightly above the Better
  Thermostat target and Auto has no confirmed recent warming trend, start the
  Warm Floor baseline at `Keep it Hot`. This protects the slab during the
  uncertain transition around the target instead of assuming that a low
  short-term demand is already safe.
- **Adaptive downgrade:** After a minimum dwell/observation period, move down
  to `Balanced` when the short EMA remains above the one-hour EMA and the
  temperature slope is consistently positive. Move further to `Eco` only when
  warming is sustained or strong solar/residual warming is confirmed.
- **Recovery:** Stay at `Keep it Hot` when the room is flat/cooling, the
  one-hour EMA is falling relative to target, heat loss is high, or the
  heating response is weak. Use hysteresis and minimum dwell times so the
  policy does not chatter between levels.
- **Boundary:** This policy selects maintenance intensity only. Warm Floor
  still sends no command at or below target; the strict
  `current_temperature > target_temperature` guard remains authoritative.
- **Status:** Design recorded for discussion and later implementation. No HA
  configuration, code behavior, target, preset, or relay was changed.

### 2026-10-14 — Set all reference BT Warm Floor configurations to Auto

- **Change:** Updated the Warm Floor level to `auto` for all seven configured
  Better Thermostat entries: Tino, Master, Office/Guest Thermostat,
  Living/Bathroom, Kitchen/Dining, Bathroom FL2, and Office/Guest Fan-Coil.
- **Method:** Used each integration's validated options flow and submitted
  only `warm_floor_level=auto`; no target, preset, sensor, heater list, or
  relay command was sent.
- **Verification:** Every Warm Floor status entity now reports
  `warm_floor_level: auto`. The live Auto policy resolves dynamically (for
  example `level_2` or `level_3`) from the current diagnostics. User targets
  and presets remained unchanged.
- **Scope:** `climate.tatas` was not queried or changed. No release was
  created; this is a Home Assistant configuration change only.

### 2026-10-14 — DEV deployment of outdoor/water/Auto Warm Floor changes

- **Deployment:** HACS installed DEV commit `ffb8f80` from
  `rradkov/better_thermostat_UnderflowHeating-dev`, then Home Assistant was
  restarted so the integration code was loaded.
- **Verification:** HACS reports `installed_version=available_version=ffb8f80`.
  All seven Better Thermostat config entries are `loaded` after the restart.
  Warm Floor status entities are publishing fresh diagnostics, including
  outdoor source/value and flow-temperature source/value where configured.
- **Live observations:** Master, Tino, Living/Bathroom, and Office/Guest
  generic heaters are above target and currently in valid OFF/recovery phases;
  Bathroom FL2 is below target and normal heating owns the actuator; the
  Office/Guest fan-coil is HVAC off; Kitchen/Dining is disabled in its current
  configuration. No state was manually forced during verification.
- **Scope:** No target, preset, relay, or configuration was changed manually.
  `climate.tatas` was not queried or changed. No release was created.

### 2026-10-14 — Implement outdoor/water inputs and Auto Warm Floor baseline

- **Outdoor input:** Warm Floor now resolves the current value from the
  existing BT `outdoor_sensor`, falling back to the configured
  `weather_entity` state temperature. It is a bounded feed-forward heat-loss
  hint and is never used as the normal HVAC cutoff decision.
- **Water input:** Warm Floor now resolves the existing BT
  `flow_temp_sensor`, then `flow_temp_static_c` fallback. The value is a
  bounded capacity hint; room response remains authoritative, and missing
  water data keeps the previous telemetry-only fallback.
- **Auto:** Added `Auto` as a fourth Warm Floor selection. It smoothly moves
  between Eco, Balanced, and Keep it Hot baselines according to the live
  demand factor, which includes room telemetry plus configured outdoor/water
  hints. The strict `current_temperature > target_temperature` guard remains
  unchanged.
- **Diagnostics:** Warm Floor status now records outdoor/water values and
  sources, plus the resolved Auto policy, so the decision is explainable.
- **Verification:** Focused Warm Floor/config tests passed 68 tests with
  plugin autoload disabled in this Windows environment. The older
  `test_warm_floor_maintenance.py` file still targets a superseded API from
  the pre-dynamic implementation and remains a separate test maintenance
  item. No commit, deployment, or release was made.

### 2026-10-14 — Document Warm Floor additions in the project README

- **Change:** Updated the public `README.md` to explain the fork's Warm Floor
  behavior and the newly implemented outdoor-temperature, water-temperature,
  and Auto features in user-facing language.
- **Included:** The README now documents the strict above-target boundary,
  dynamic three-level duty cycle, actuator travel protection, optional input
  fallbacks, Auto policy selection, diagnostics, multi-heater behavior, and
  heat-source neutrality.
- **Reason:** People evaluating or installing the project must understand what
  was added without reading implementation details or relying on this private
  project memory.
- **Status:** Documentation only in this step; no commit, deployment, or
  release was made.

### 2026-10-14 — Water-temperature input may be sensor, manual value, or absent

- **Requirement clarified:** A user may not have a water-temperature sensor, but
  may still know or manually enter a representative water/buffer temperature.
- **Input priority:** Use the configured live water-temperature sensor when it
  is valid and available; otherwise use the configured manual/static value;
  otherwise disable the water correction and keep the current telemetry-only
  Warm Floor fallback.
- **Semantics:** A manual value is a fallback estimate, not a live measurement.
  Diagnostics and documentation must label it accordingly, and the controller
  must not infer real-time water changes from it.
- **Control role:** The value only modifies Warm Floor intensity together with
  room temperature, slope, EMA, heat loss, heating power, and learned data. It
  never bypasses the strict `current_temperature > target_temperature` guard.
- **Generic scope:** The implementation must remain heat-source-neutral and
  must not hardcode `sensor.h_coil_2_temperature`. For the reference
  installation, that entity may be selected as the live input, while other
  users may select a different sensor or a manual value.
- **Configuration reuse:** The sensor entity and manual/static temperature
  already exist in Better Thermostat configuration as `flow_temp_sensor` and
  `flow_temp_static_c`. Warm Floor must reuse these existing fields instead of
  introducing duplicate configuration options.
- **Outdoor-temperature reuse:** Better Thermostat already provides the
  configured `outdoor_sensor` and `weather_entity` sources, plus resolved
  current/EMA outdoor-temperature state. Warm Floor must consume that existing
  BT-resolved signal rather than adding a second outdoor-temperature setting.
  The Warm Floor calculation should use the current/smoothed value for thermal
  demand, not the separate multi-day outdoor threshold decision used for HVAC
  cutoff behavior.
- **Status:** Design requirement recorded; no code, commit, deployment, or
  release was made in this turn.

### 2026-10-14 — Future water-temperature signal for Warm Floor

- **Design requirement:** Warm Floor should account for the actual water/flow
  temperature when that capability is available, because the same room and
  floor can receive substantially different heat during day and night.
- **Source-neutral implementation:** Water temperature must be an optional
  capacity modifier, not a heat-source assumption or a universal threshold.
  The logic must work with heat pumps, boilers, district heating, electric
  boilers, solar buffers, and other systems. If no water sensor exists, the
  current telemetry-only behavior remains the fallback.
- **Signal quality:** Flow temperature alone is useful but incomplete. A future
  implementation should prefer floor temperature when available, then flow +
  return temperature/Delta-T, then flow temperature alone, while continuing to
  validate the result against room slope, EMA, heat loss, heating power, and
  learned MPC data. High water temperature must not imply that the floor is
  warm if there is no flow or the actuator is closed.
- **Control role:** The signal should modify Auto/level intensity and
  available heating-power estimation, never bypass the strict
  `current_temperature > target_temperature` Warm Floor guard.
- **Status:** Recorded as a future design requirement; no water-temperature
  control change has been implemented yet.

### 2026-10-14 — Auto mode concept and heat-source neutrality

- **Future Auto mode:** Auto should keep the floor warm continuously enough to
  reduce cold-slab recovery and avoid large catch-up requests. It should infer
  the active policy from live conditions: strong solar/residual warming selects
  an Eco-like baseline; fading sun and stable conditions move through
  Balanced; confirmed cooling, high heat loss, or weak heating response moves
  toward Keep it Hot. Transitions need smoothing and hysteresis so the mode
  does not chatter. The strict rule remains: Warm Floor may send commands only
  while `current_temperature > target_temperature`.
- **Global heat-source neutrality:** The entire project, not only Auto or Warm
  Floor, must support underfloor systems heated by heat pumps, boilers,
  district heating, electric boilers, solar buffers, and other sources. No
  controller behavior, level, duty calculation, or safety decision may assume
  a particular heat source or require a heat-pump-specific flow temperature.
- **MPC v2 distinction:** An optional flow/water sensor may still be used as a
  plant-model input when a user chooses MPC v2. Its fallback prior is a model
  parameter, not a project-wide assumption and must never leak into generic
  Warm Floor behavior or other calibration modes.
- **Current implementation finding:** The Generic Warm Floor duty calculation
  does not directly use water/flow temperature. It uses slope, short/one-hour
  EMA, heat loss, heating power, and learned MPC Loss/Gain/Ka/solar data when
  available. The configured flow/water sensor currently feeds the MPC v2 plant
  model only; any Warm Floor influence is indirect through learned/controller
  outputs. This distinction must be preserved unless a future generic,
  capability-based water-temperature feature is designed.
- **Status:** Concept recorded for later implementation; no Auto option or
  water-temperature change has been implemented in this turn.

### 2026-10-14 — Remove fixed maximum Warm Floor pause

- **Requirement:** Keep the three levels fully dynamic; do not impose a fixed
  maximum pause between maintenance pulses.
- **Change:** Removed `WARM_FLOOR_DUTY_MAX_PAUSE_S`. Pause duration now follows
  the dynamically calculated intensity with a continuous response curve. The
  only hard timing constraint left is the actuator travel minimum needed to
  open and close slow electrothermal relays. The minimum dynamic intensity
  remains so solar/residual warming cannot reduce maintenance to zero.
- **Verification:** The focused Warm Floor suite remains covered by the next
  test run; no commit, Home Assistant deployment, or release was made.

### 2026-10-14 — Make the three Warm Floor levels telemetry-driven

- **Requirement:** Eco, Balanced, and Keep it Hot must not be fixed clock
  schedules. They are user-selected baseline intensities; live diagnostics
  determine the actual Generic Thermostat ON and pause durations.
- **Old behavior:** The levels used fixed profiles of approximately 4+43,
  6+28, and 8+13 minutes. Heat-loss telemetry could only make those profiles
  somewhat more assertive, so the observed cycle still looked time-based.
- **Change:** Replaced the fixed profile table with baseline intensities. Each
  control tick now combines the selected level with cooling demand from slope,
  short/one-hour EMA separation, heat loss/heating power, and learned MPC data.
  Positive slope, warmer EMA, and a larger margin above target reduce the
  resulting intensity. ON and pause are then recalculated within the physical
  actuator bounds; the four-minute travel minimum remains as a mechanical
  safety constraint, not a level schedule.
- **Safety:** The strict `current_temperature > target_temperature` guard is
  unchanged and still runs before every Warm Floor command. `climate.tatas`
  remains unrelated. A dynamic minimum intensity prevents
  solar/residual-warming telemetry from reducing maintenance to zero; there is
  no fixed maximum pause. Only the four-minute ON/OFF travel minimum protects
  slow electrothermal travel. No fixed `WARM_FLOOR_DUTY_PROFILES` table
  remains.
- **Verification:** 77 focused Warm Floor tests passed, all 81 Python files
  passed AST parsing, JSON validation passed, and `git diff --check` passed.
  No commit, Home Assistant deployment, or release was made.

### 2026-10-14 — Full audit for stale Warm Floor boundary logic

- **Audit scope:** Reviewed all Warm Floor execution routes, helper calls,
  calibration entry points, Generic Thermostat handling, direct-valve handling,
  offset handling, diagnostics, tests, documentation, and English UI text.
- **Result:** No executable Warm Floor path remains that activates at or below
  target. All three entry paths call `apply_warm_floor_floor()`, whose strict
  `current_temperature > target_temperature` guard runs before backoff,
  sustain push, duty-cycle, offset, or valve-floor calculations.
- **Residuals corrected:** Updated one stale `at/above` code comment and one
  duplicated configuration description. Remaining `>= target` references are
  intentionally unrelated normal-heating, cooling, boost, MPC, or target-reached
  logic, or are historical notes explicitly marked as superseded.
- **Verification:** 74 focused Warm Floor tests passed; all 81 Python files
  passed AST parsing; JSON and `git diff --check` validation passed. No commit,
  Home Assistant deployment, or release was made.

### 2026-10-14 — Enforce strict above-target Warm Floor boundary

- **Requirement clarified:** Warm Floor is allowed only when the measured room
  temperature is strictly above the Better Thermostat target
  (`current_temperature > target_temperature`). At or below target, normal
  Better Thermostat heating owns the actuator; Warm Floor must not add a
  passive floor, sustain push, valve floor, or duty-cycle pulse.
- **Gap found:** Generic duty-cycle code rejected only `current < target`, and
  the shared Warm Floor path allowed passive setpoint/valve flooring for other
  actuator types at or below target. This contradicted the current project
  requirement even though earlier historical notes had described `>= target`.
- **Change:** Added one capability-independent guard before all Warm Floor
  calculations. It returns the normal computed command, records
  `below_target`, `at_target`, or `temperature_unavailable`, and interrupts a
  Generic Thermostat Warm Floor phase when required. The Generic duty helper
  also independently uses the strict `>` check as defense in depth.
- **Verification:** Warm Floor calibration and sustain/duty tests pass (49
  tests) with explicit below-target, equal-target, direct-valve, and latched
  Generic Thermostat coverage. This supersedes the earlier `>= target`
  activation note; the three user levels remain unchanged.
- **Scope:** Capability-based for all configured underfloor heaters. No
  installation entity names are used, `climate.tatas` remains unrelated, and
  no HA deployment or release has been made yet.

### 2026-10-14 — DEV deployment of actuator-phase latching fix

- **Deployment:** DEV commit `851d3f9` was installed through HACS and Home
  Assistant was restarted so the new Warm Floor code was loaded.
- **Verification:** HACS reports `installed_version=available_version=851d3f9`
  with no pending update. After restart, Warm Floor status records exposed the
  new `actuator_phase`, `actuator_guard`, `phase_recovered`, and minimum-travel
  diagnostics. Status ages continued refreshing after the restart.
- **Live result:** Kitchen/Dining entered `duty_cycle_on` with both child
  heaters in the ON actuator phase; physical relays R2 and R3 were ON and the
  independent R7 pump automation was ON. Office/Guest was in a valid OFF duty
  window with its actuator phase held OFF after recovery.
- **Scope:** No target, preset, manual relay, or configuration was changed by
  the deployment. `climate.tatas` was not queried or changed. No release was
  created.

### 2026-10-13 — Force one downstream setpoint write after recovery

- **Follow-up to:** The unavailable-to-available recovery cycle fix.
- **Observed gap:** The recovery cycle was queued, but the control layer could
  still skip the service call when the desired setpoint matched the cached
  downstream value. A Generic Thermostat can have restored its relay ON while
  retaining that same value, so a calculation alone does not reassert the safe
  idle/Warm Floor command.
- **Change:** Recovery now marks the affected controlled heater with a one-shot
  `recovery_pending` flag. The next eligible control cycle forces one normal
  setpoint write through the configured adapter, then consumes the flag. This
  remains capability-based and does not directly operate a relay or reference
  any installation entity.
- **Safety:** The normal HVAC, window/door, outdoor, stale-data, Warm Floor,
  and actuator guards still choose the setpoint. Recovery only ensures that
  the chosen command is actually sent once; it does not force heating.
- **Verification target:** After deployment, a recovered Generic Thermostat
  must no longer remain physically ON solely because its relay restored during
  an outage while Better Thermostat was idle at/above target.

### 2026-10-13 — Make Generic Thermostat idle release unambiguous

- **Observed after deployment:** Recovery correctly ran and Warm Floor reported
  `idle_release`, but the Generic Thermostat still reported `heating` at
  `22.0/22.0` and its relay remained ON. Re-sending an equal setpoint is not
  an OFF command for every Generic Thermostat configuration, especially with
  zero hot tolerance.
- **Change:** The generic Warm Floor idle-release path now sends one valid
  device step below the Better Thermostat target (bounded by the actuator's
  minimum), instead of sending the equal target. This is only the downstream
  actuator setpoint; the user's target and preset are not changed.
- **Scope and safety:** The rule is generic for ON/OFF Generic Thermostats,
  preserves the normal safety gates, and does not directly toggle relays or
  reference `climate.tatas`.
- **Status semantics:** The release setpoint is an OFF command, not an active
  Warm Floor pulse; the diagnostic therefore remains `idle` during this path.

### 2026-10-13 — Verified recovery release in HA

- **Deployment:** HACS installed DEV commit `2793ed3` and Home Assistant was
  restarted.
- **Live result:** Tino Better Thermostat returned available at `22.0/22.0`
  with `hvac_action: idle` and preset `home`. The downstream Generic
  Thermostat received `21.9°C` and reported `hvac_action: idle`; its relay
  `switch.tasmota_8` was OFF. Warm Floor reported `idle`, `active: false`,
  and `reason: idle_release`.
- **Conclusion:** The recovery path now survives the unavailable transition,
  performs a real downstream setpoint reassertion, and releases a stale
  restored relay without changing the user's Better Thermostat target or
  preset. This verification is specific evidence for a generic capability
  path; no `climate.tatas` entity was used or changed.

### 2026-10-13 — Register Warm Floor tick by heater capability

- **Incident:** HA history showed no Warm Floor relay transitions for an hour.
  Warm Floor status timestamps were stale even though the separate EMA timer
  was still running.
- **Root cause:** The one-minute control tick was gated by both the adapter
  integration label and `heating_type=underfloor`. Adapter labels are not a
  reliable capability contract during startup/reload, so the tick could be
  skipped even when Warm Floor was configured and active.
- **Change:** Periodic Warm Floor control is now enabled from the normalized
  per-heater `heating_type` capability alone. This keeps the behavior generic
  for all supported adapters and ensures duty-cycle decisions and diagnostic
  timestamps are recalculated every minute.
- **Verification target:** After deployment, each configured Warm Floor zone
  must refresh its `updated_at/status_age_s` and the corresponding downstream
  relay must receive ON pulses when its calculated duty window and safety
  ceiling allow them.

### 2026-10-13 — Verified capability-based Warm Floor tick in HA

- **Deployment:** HACS installed DEV commit `439ccee` and Home Assistant was
  restarted. No release was created.
- **Live verification:** Warm Floor `status_age_s` values now refresh roughly
  every minute (about 30–60 seconds during the check), whereas before the fix
  they were stale for approximately an hour. This confirms that the periodic
  control tick is registered and is recalculating the duty-cycle diagnostics.
- **Observed conditions:** No new Warm Floor relay ON pulse occurred in the
  short post-restart observation window. Tino was in an ON duty phase but its
  current decision was `idle_release`; Kitchen/Living were outside their
  allowed duty window, Master was blocked by `contact_open`, Office was HVAC
  off or outside its safety window, and Bathroom FL2 was normal room-demand
  heating rather than Warm Floor maintenance.
- **Follow-up:** Verify a complete duty-cycle transition in a longer live
  observation. A status refresh alone proves the missed-tick defect is fixed,
  but a relay pulse still requires the normal Warm Floor safety envelope to
  allow it. The unrelated `climate.tatas` entity was not queried or changed.

### 2026-10-13 — Modulate Warm Floor above target instead of hard blocking

- **Incident:** The Generic Thermostat duty-cycle path treated a narrow
  `target + adaptive band` as a hard room-temperature ceiling. Rooms heated by
  sunlight or residual heat were therefore reported as `outside_duty_window`
  and could not keep the slab warm, even though Warm Floor is explicitly
  intended to operate above the room target.
- **Change:** Removed that narrow hard-off gate. Above-target temperature now
  remains eligible for the normal Eco/Balanced/Keep-it-Hot duty profile, with
  `temp_slope`, short/one-hour EMA separation, thermal demand, and MPC inputs
  reducing the cycle intensity as appropriate.
- **Safety:** Added a broad absolute guard at `target + 5°C`, bounded by the
  configured actuator maximum. This is an emergency/safety boundary, not the
  normal Warm Floor operating band; ordinary above-target conditions continue
  to receive maintenance pulses when the duty phase is ON.
- **Verification:** Added regression coverage for maintenance at `target +
  0.6°C` and for stopping at the absolute safety boundary. Focused tests,
  compilation, and diff validation passed before deployment. The behavior is
  generic and does not reference or modify `climate.tatas`.

### 2026-10-13 — Verified above-target Warm Floor pulse in HA

- **Deployment:** HACS installed DEV commit `f741669` and Home Assistant was
  restarted successfully.
- **Live result:** Office/Guest room entered `duty_cycle_on` with room
  temperature `23.2°C` and user target `22.0°C`; its Warm Floor status became
  `active` and physical relay `switch.tasmota_5` became `on`. This confirms
  the new above-target path produces a real Generic Thermostat pulse.
- **Other zones:** Tino remained in the intentional `idle_release` path,
  Kitchen/Living were in their duty OFF phases, and Bathroom FL2 was handling
  ordinary below-target room demand. These are not failures of the new
  above-target eligibility rule.

### 2026-10-13 — Started multi-day Warm Floor and Boost monitoring

- **Monitoring:** Created an active Codex heartbeat that checks all Warm Floor
  and Boost status entities every six hours, compares multi-heater zones and
  physical relays/pump, and reports only meaningful failures. It explicitly
  excludes `climate.tatas` and performs no target, preset, relay, or
  configuration changes.
- **Baseline issue:** The initial post-deployment check found that only the
  Kitchen/Dining entry was refreshing its Warm Floor status; the other six
  Better Thermostat entries had stale diagnostic timestamps even though their
  status sensors were still reporting.
- **Recovery:** Reloaded the six affected Better Thermostat config entries
  individually without changing user targets or presets. All Warm Floor
  statuses then refreshed again within roughly 18–30 seconds (Kitchen/Dining
  within about two minutes), and all Boost statuses were `off` at the baseline
  check.
- **Follow-up:** The multi-day observation must verify real ON/OFF pulses,
  paired relay behavior, Boost transitions, recovery after any restart, and
  that status timestamps remain fresh without manual reloads.

This file is the project decision log and working memory for future changes.
Before making a design decision, debugging change, configuration change, or
code change for this project, read this file first. After every change, add a
dated entry describing what changed, why it changed, and any important
follow-up.

### 2026-10-13 — Reassert safe Generic Thermostat state after recovery

- **Incident:** After a power interruption, a Generic Thermostat could return
  from `unavailable` with the same reported mode and setpoint as before the
  outage while its relay had restored ON. Better Thermostat saw no ordinary
  input change and therefore did not run a control cycle to release that
  stale heating command when the room was idle and at/above target.
- **Change:** A TRV/climate report that transitions from `unavailable` (or a
  non-operating `unknown`) to an available state now always queues one full
  Better Thermostat control cycle, even when mode, setpoint, and temperature
  values are unchanged.
- **Safety:** The normal controller and Warm Floor guards still decide the
  resulting command. This does not force heat; it reasserts normal heating or
  the Generic Thermostat idle/Warm Floor duty-cycle release as appropriate.
  The rule is generic for all recovered controlled heaters and does not touch
  `climate.tatas`.
- **Verification:** Added a regression test for recovery without an input
  change. The focused event test and source compilation must pass before
  deployment.

### 2026-10-13 — Use short and one-hour EMA in Warm Floor demand

- **Change:** The central Better Thermostat climate state now maintains both
  the responsive short temperature EMA and a persistent one-hour EMA. The
  one-hour EMA sensor reads this central value instead of maintaining a second
  live calculation; its local calculation remains only as a compatibility
  fallback for lightweight test doubles.
- **Control rule:** Warm Floor compares the short EMA with the one-hour EMA.
  If the short EMA is below the long-term baseline, the room is cooling and
  maintenance demand is increased. If it is above the baseline, solar or
  residual heat is likely warming the room and existing maintenance demand is
  reduced, while the selected Eco/Balanced/Keep it Hot baseline and safety
  limits remain in force.
- **Why:** `temp_slope` can be flat or noisy when the source sensor updates
  infrequently. The EMA separation adds a generic sustained-trend signal using
  telemetry already maintained by Better Thermostat, without special-casing
  Tino or touching `climate.tatas`.
- **Verification:** Added regression coverage for cooling and warming EMA
  separation. Full sensor test execution still requires the repository's
  async pytest plugin environment; the synchronous tests and focused Warm
  Floor tests must pass locally.

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

### 2026-10-13 — Historical activation boundary and three levels (superseded)

- **Historical boundary:** This earlier note allowed Warm Floor when the room
  temperature was equal to or above target. That boundary was superseded on
  2026-10-14 by the stricter project rule: Warm Floor is allowed only strictly
  above target (`current_temperature > target_temperature`).
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
- **Why:** Warm Floor is eligible only strictly above the room target. Its demand
  factor must describe slab-maintenance conditions such as cooling slope,
  learned heat loss/heating power, and MPC feedback—not normal room heating
  demand.
- **Verification:** Added a regression test proving that a below-target room
  does not increase Warm Floor demand when no cooling or thermal-load signal
  is present. The three user levels remain the baseline profiles.

### 2026-10-13 — DEV deployment of Warm Floor maintenance-demand fix

- **Commit:** `237d9e0` was pushed to DEV `main` with the corrected Warm Floor
  boundary, three-level profiles, slope modulation, and separation from normal
  below-target room demand.
- **HA deployment:** HACS refreshed and installed DEV commit `237d9e0`; the
  installed and available versions match. Home Assistant was restarted so the
  integration loaded the new code.
- **Live verification:** Tino Better Thermostat returned as `heat` at 22.0°C
  current and 22.0°C target with preset `home`. Its Warm Floor diagnostic is
  enabled at `level_2` (Balanced); the downstream relay and floor pump were
  both ON at the observation point. The diagnostic correctly exposed the
  per-heater record and duty timing.
- **Logs:** No new Better Thermostat errors were found. Existing warnings about
  missing local calibration entities remain unrelated to this Warm Floor
  change. `climate.tatas` was not queried or changed.

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

### 2026-10-13 — Make Generic Warm Floor phase edges actionable

- **Observed defect:** Kitchen/Dining could show a deterministic Warm Floor ON
  phase without producing a new relay command, and Tino could remain stuck on
  the previous downstream setpoint after an OFF/ON boundary or restart. The
  status sensor could also recompute a new wall-clock phase while retaining the
  previous control reason, producing combinations such as `duty_phase=on` with
  `reason=outside_duty_window`.
- **Root cause:** The control path skipped a setpoint write whenever the
  calculated value matched the cached/device-reported value, even when the
  Generic Thermostat had just crossed a Warm Floor duty boundary. The
  diagnostic sensor independently recalculated phase instead of exposing the
  phase and reason from one control evaluation.
- **Change:** Each Generic Thermostat now records its last evaluated Warm Floor
  phase and arms one forced setpoint write only on an ON/OFF transition. The
  controlling layer consumes and clears that flag after the command is sent;
  each heater has its own flag, so multi-heater zones are handled independently.
  The status sensor now preserves phase/reason from the same evaluation and
  exposes `status_age_s` for refresh visibility. The existing rule remains:
  ON pulses are eligible strictly above target, while `idle_release` is only an
  OFF-window action.
- **Verification:** 32 focused Warm Floor tests passed, `compileall` passed,
  and `git diff --check` passed. Deployment and live HA verification are the
  next required steps. This is generic capability-based logic; `climate.tatas`
  is excluded and no room-specific behavior was added.

### 2026-10-13 — Keep Warm Floor diagnostic phase and reason atomic

- **Follow-up finding during HA verification:** At a duty boundary, the live
  Tino status could still combine `reason=idle_release` with
  `duty_phase=on`. The first fix armed the correct command edge, but the
  status writer independently recalculated the wall-clock phase after the
  reason had already been selected.
- **Change:** `_record_status()` now derives `duty_phase` from the same reason
  used by the control decision (`duty_cycle_on` => ON; OFF/release/rising
  reasons => OFF). Generic Warm Floor `active` is now false for an OFF-window
  or release decision even if the structural floor value differs from the
  ordinary computed value.
- **Verification:** Focused Warm Floor tests remain 32/32 passing after adding
  deterministic phase assertions. A follow-up DEV commit and HA deployment
  are required before treating the live fix as verified.

### 2026-10-13 — DEV deployment and multi-heater live verification

- **Deployment:** DEV commit `9d2e125` was pushed and installed through HACS.
  The production HACS entry `rradkov/better_thermostat_UnderflowHeating` was
  removed from the HA installation because it used the same
  `custom_components/better_thermostat` domain and could overwrite or race the
  DEV files. DEV is now the single installed Better Thermostat source for this
  test instance. The seven Better Thermostat config entries are loaded.
- **Live result:** Tino at 22.0/22.0 is in a valid OFF decision with
  `reason=idle_release` and `duty_phase=off`; its child Generic Thermostat is
  holding 21.9°C and the relay is off. Kitchen/Dining later entered a valid ON
  decision for both heaters: both `climate.dinning_room_thermostat` and
  `climate.kitchen_thermostat` reported `reason=duty_cycle_on` and
  `duty_phase=on`, while R2 and R3 and the shared floor pump R7 were physically
  on. This verifies the multi-heater command path.
- **Status result:** After the production/DEV collision was removed, the live
  status correctly kept `idle_release` paired with `duty_phase=off` and
  `duty_cycle_on` paired with `duty_phase=on`; no mixed diagnostic pair was
  observed. User target/preset persistence remained intact (`Tino 22`,
  `home`). `climate.tatas` was not touched.
- **Operational note:** HACS may still display historical production metadata,
  but only the DEV repository is installed. No release was created.

### 2026-10-13 — Clarify pump attribution in live verification

- **Correction:** R7 (`switch.dingtian_relay47253_r7`) is controlled by the
  home's floor-heating/pump automation, not directly by Better Thermostat.
  The earlier live report correctly observed R2/R3 and R7 on together, but its
  wording incorrectly implied that BT had switched R7.
- **Attribution rule:** For Warm Floor verification, R2/R3 (or the relevant
  zone actuators) demonstrate the BT/Generic Thermostat commands. R7 only
  demonstrates that the independent pump automation followed the active zone
  demand. Do not use R7 alone as proof of BT behavior.

### 2026-10-13 — Refresh Warm Floor status when a heater is unavailable

- **Observed gap:** The periodic control tick could correctly run while an
  individual heater was unavailable, but `control_trv()` skipped that heater
  before reaching the Warm Floor calculation. Its diagnostic sensor therefore
  kept showing the previous ON/OFF decision and an old `updated_at` timestamp.
- **Change:** Unavailable underfloor heaters now publish a fresh diagnostic
  snapshot with reason `heater_unavailable` and `active: false` before control
  returns. This is a status-only update; it does not send a setpoint, toggle a
  relay, or alter the user's target or preset.
- **Scope:** The behavior is capability-based for every configured underfloor
  heater, including multi-heater zones. Radiator/fan-coil heaters are not
  marked as Warm Floor, and `climate.tatas` remains out of scope.
- **Verification:** Warm Floor status tests pass (13/13), source compilation
  and `git diff --check` pass. Deployment to the DEV HA instance is still
  required before live verification.

### 2026-10-13 — DEV deployment verification for commit `5666d4f`

- **Deployment:** DEV commit `5666d4f` was pushed and installed through HACS.
  All seven Better Thermostat config entries were reloaded successfully;
  production was not installed or touched.
- **Live result:** Every Warm Floor status entity refreshed after reload with
  `status_age_s` approximately 0–3 seconds. This confirms the installed
  integration is actively publishing fresh diagnostic snapshots rather than
  retaining the earlier stale values.
- **Safety check:** No room target, preset, or relay was changed by the
  deployment verification. `climate.tatas` was not queried or changed.
- **Follow-up:** Kitchen/Dining currently exposes only one heater in its
  multi-heater Warm Floor attribute despite both child climates being present;
  this is a separate multi-heater status issue to investigate.

### 2026-10-13 — Register Warm Floor before optional-sensor startup waits

- **Incident:** Master Bedroom produced a valid Warm Floor ON pulse from
  15:40:59 to 15:46:56, then its relay stayed OFF for more than an hour. The
  last diagnostic decision remained `outside_duty_window` with a stale
  timestamp, even though the room was above target and the contact was closed.
- **Root cause:** The one-minute Warm Floor tick was registered only after
  `await_optional_sensors()`. A slow/unavailable optional or outdoor sensor
  could hold startup/reload before that registration, freezing the duty-cycle
  evaluator and missing later ON windows.
- **Change:** Warm Floor capability now registers its one-minute tick before
  the optional-sensor wait, and the later startup path skips duplicate
  registration. This does not bypass safety checks or directly control relays;
  it only ensures that normal control cycles continue to be scheduled.
- **Verification:** Warm Floor status tests pass (13/13), source compilation
  and `git diff --check` pass. The async registration suite could not run in
  the Windows environment because the installed Home Assistant pytest plugin
  imports POSIX-only `fcntl` and disabling it leaves no async test plugin.
- **Follow-up:** Deploy to DEV and verify Master status ages remain below the
  one-minute tick interval after reload, then observe a complete ON/OFF cycle.

### 2026-10-13 — DEV verification of early Warm Floor tick registration

- **Deployment:** DEV commit `e4a658d` was installed through HACS and all seven
  Better Thermostat entries were reloaded successfully.
- **Live result:** Master status initially reported `outside_duty_window` with
  `status_age_s=3`; after a further 45-second observation it continued to
  refresh with a new timestamp and `status_age_s=30`. This confirms the tick
  continues after reload instead of stopping after the initial control cycle.
- **Current decision:** Master relay `switch.tasmota_7` remained OFF because
  the current valid duty phase was OFF. The room was above its target, the
  contact was closed, and no safety block was reported.
- **Scope:** No target, preset, or relay was changed manually. No release was
  created, and `climate.tatas` was not queried or changed.

### 2026-10-13 — Add a Warm Floor liveness watchdog

- **Incident:** Despite the early timer registration in `e4a658d`, all seven
  Warm Floor statuses later stopped refreshing at the same timestamp for about
  53 minutes. HACS showed DEV `e4a658d` installed and all config entries
  `loaded`, so this was a runtime scheduling/control-liveness failure rather
  than a missing installation.
- **Change:** Each configured underfloor Better Thermostat now starts a
  lightweight watchdog. Every minute it checks the newest Warm Floor decision
  age and the control queue task. If the status is stale it queues one normal
  control cycle; if the queue task has stopped it recreates that task. The
  watchdog never selects a setpoint, changes a target/preset, or toggles a
  relay directly.
- **Safety:** Normal HVAC, contact, outdoor, stale-data, Warm Floor ceiling,
  duty-cycle, and actuator guards remain inside the existing control path.
  The watchdog only restores scheduling and is capability-based for all
  underfloor heaters; `climate.tatas` remains out of scope.
- **Verification:** Warm Floor status tests pass (13/13), source compilation
  and `git diff --check` pass. Deployment and live stale-recovery verification
  are pending.

### 2026-10-13 — DEV verification of Warm Floor liveness watchdog

- **Deployment:** DEV commit `ea4fed6` was installed through HACS; HACS
  reported `ea4fed6` as both installed and available. All seven Better
  Thermostat entries were reloaded successfully.
- **Recovery result:** Immediately after reload, Master entered
  `duty_cycle_on` with `status_age_s=0` and its physical relay
  `switch.tasmota_7` was ON. After a further 45-second observation the status
  remained fresh at `status_age_s=30` and the relay remained ON.
- **Multi-heater result:** Kitchen/Dining showed both child heaters active in
  the same ON phase, with R2 and R3 ON; R7 was also ON as the independent pump
  automation response. This confirms the watchdog did not break grouped-zone
  synchronization.
- **Safety:** Boost statuses remained off during the check. No target, preset,
  relay, or configuration was manually changed, and `climate.tatas` was not
  queried or changed. No release was created.

### 2026-10-14 — Fix Warm Floor control liveness after Home Assistant restart

- **Observed:** After the `ea4fed6` deployment and a full Home Assistant
  restart, all Better Thermostat entities continued publishing current state,
  but Warm Floor decision timestamps stayed at the restart point for roughly
  two hours. The Generic Thermostat children and their relays did not receive
  new Warm Floor commands. This occurred across all inspected zones, so it was
  not a single relay or room fault.
- **Root cause:** The Warm Floor watchdog was started from
  `async_added_to_hass()`, before startup had populated `real_trvs`. Its
  capability check therefore saw an empty heater map and skipped the watchdog
  for every Warm Floor instance. The startup path also ran its checks with
  `event=None`, which intentionally avoided queuing control work and left no
  guaranteed first control cycle after restart.
- **Changed:** Watchdog startup is now deferred until `_initialize_trvs()` has
  created the configured heater models. The watchdog iteration is isolated so
  a diagnostic/scheduling exception cannot kill the watchdog itself. Once
  startup releases `startup_running`, one normal control cycle is queued
  explicitly. These changes restore scheduling only; the existing HVAC,
  sensor, contact, outdoor, Warm Floor safety ceiling, duty-cycle, and
  actuator guards still decide the actual command.
- **Scope:** The fix is capability-based for all configured underfloor
  heaters, including multi-heater zones. It does not name or access Tino,
  Tata, any Tasmota relay, or `climate.tatas`; the latter remains unrelated.
- **Live evidence before fix:** BT wrapper states were current, while the
  Warm Floor records had `status_age_s` around 6840 seconds. Generic child
  states had stopped changing after the restart, and the corresponding relay
  states remained at their startup values. HACS confirmed DEV `ea4fed6` was
  installed.
- **Verification target:** After deployment, every Warm Floor record must
  refresh within the watchdog interval, the Generic Thermostat children must
  receive new ON/OFF decisions, and real zone relays must produce valid
  pulses. Multi-heater relays must remain synchronized. No target, preset,
  manual relay state, or release metadata may be changed as part of this fix.

### 2026-10-14 — Fix control queue permanently paused after a successful cycle

- **Observed:** After the `f58d90e` restart-liveness fix, Warm Floor records
  refreshed only during startup and then stopped together. The control queue
  task remained present, but no later duty-cycle decisions or relay commands
  were produced. HA logs contained no Warm Floor exception, which ruled out a
  relay-specific failure and pointed to an internal queue state problem.
- **Root cause:** `control_queue()` sets `ignore_states = True` when it takes a
  request. The flag was cleared only inside the failed-TRV retry branch. A
  successful control cycle therefore left the queue in its own pause condition
  forever; every future Warm Floor tick and state-change request was skipped.
- **Change:** The per-cycle `finally` now always acknowledges the queue item and
  clears `ignore_states` when maintenance is not active. This applies to every
  configured thermostat and does not select a setpoint, change a target/preset,
  or operate a relay directly.
- **Verification:** Added a regression test proving that two consecutive
  successful queue items are both processed. The regression subset passed and
  the focused Warm Floor suites passed 57 tests. The broader control-queue
  file still contains an unrelated pre-existing dual-role cooling expectation
  failure; it is not part of this Warm Floor fix.
- **Deployment target:** Commit and push to the public DEV repository, install
  through HACS, restart/reload Home Assistant, then verify fresh status ages,
  Generic Thermostat setpoint changes, real zone relay pulses, and synchronized
  multi-heater zones. No release is requested.

### 2026-10-14 — Verified successful-cycle queue fix in DEV HA

- **Deployment:** HACS installed DEV commit `a82828f` and Home Assistant was
  restarted. HACS reports `installed_version=available_version=a82828f` with
  no pending update. No release was created.
- **Control liveness:** After more than one minute of runtime, all seven Warm
  Floor status entities had refreshed again with `status_age_s` in the normal
  tick range (approximately 1–34 seconds). This is the expected behavior that
  was missing before the queue cleanup fix.
- **Real pulse:** Kitchen/Dining changed to `active`, `reason=duty_cycle_on`,
  `duty_phase=on` at 01:05:45 local time. Its two physical Generic Thermostat
  relays R2 and R3 both changed to `on` at 01:05:45–01:05:48, confirming the
  multi-heater zone was controlled together by the normal path.
- **Other zones:** Master, Living/Bathroom, and Office/Guest were in valid
  duty-cycle OFF windows; Tino and Bathroom FL2 were handling normal room
  demand below target, so their Warm Floor maintenance record correctly showed
  `already_at_floor` rather than a maintenance pulse. These are expected
  decisions, not blockers.
- **Scope:** No target, preset, configuration, or manual relay action was
  changed. `climate.tatas` was not queried or touched. R7 remains an external
  pump automation and was not used as proof of BT control.
- **Follow-up:** Continue passive observation for long-period ON/OFF edges and
  restart recovery across all zones; the liveness defect itself is verified.

### 2026-10-14 — Multi-heater Warm Floor zones may alternate safely

- **Design clarification:** Multiple underfloor heaters in one Better
  Thermostat zone do not have to be ON at exactly the same instant. Independent
  duty-cycle decisions are acceptable when the heaters alternate and the zone
  still receives regular Warm Floor maintenance.
- **Acceptance rule:** Treat alternating relays as healthy behavior when there
  is no excessive gap with both heaters OFF, each active decision produces its
  corresponding real relay response, and the shared floor/pump demand remains
  adequate. Do not force simultaneous ON commands merely to make the status
  look synchronized.
- **Scope:** This is a generic multi-heater rule. It does not reference room
  names, relay IDs, or `climate.tatas`, and it does not change the current
  control implementation by itself.

### 2026-10-14 — Respect actuator travel time in Warm Floor pulses

- **Hardware constraint:** The underfloor thermal actuators used in the
  reference installation need approximately four minutes of continuous ON
  time to open and approximately four minutes of continuous OFF time to
  close. This is an actuator capability, not a room-specific preference.
- **Control implication:** A calculated Warm Floor ON pulse shorter than the
  configured opening time may not open the valve meaningfully. Likewise, an
  OFF interval shorter than the configured closing time may leave the valve
  partially open. Duty-cycle calculations must therefore account for
  configurable actuator opening/closing times before issuing pulses.
- **Multi-heater implication:** Alternating heaters remains acceptable only
  when each selected pulse is long enough for its actuator and the resulting
  sequence still provides the required floor maintenance. Short pulses must
  not be treated as successful heating merely because a relay changed state.
- **Generic scope:** The implementation must expose or derive these timings
  from actuator capability/configuration and must not hardcode Tino, room, or
  relay identifiers. `climate.tatas` remains unrelated and out of scope.

### 2026-10-14 — Latch Generic Warm Floor phases for actuator travel

- **Change:** Generic Thermostat Warm Floor now latches each requested ON or
  OFF phase for the default four-minute actuator travel interval. A new duty
  calculation may select the next phase only after the current opening or
  closing movement has completed. Dynamic slope/MPC calculations therefore
  affect the next cycle instead of interrupting the current actuator movement.
- **Safety overrides:** Window/contact, outdoor cutoff, HVAC off, normal room
  demand, below-target operation, and the absolute temperature ceiling may
  interrupt Warm Floor immediately. The following cycle still observes the
  required closing interval.
- **Recovery:** After a restart, the first decision uses the downstream
  Generic Thermostat's reported `hvac_action` as the best available phase
  hint and holds that phase for one travel interval before accepting a new
  Warm Floor transition. Devices without a trustworthy report start from the
  deterministic duty decision.
- **Diagnostics:** Warm Floor status now exposes the effective actuator phase,
  phase start, remaining minimum travel time, guard reason, and whether the
  phase came from recovery. This separates a requested duty decision from the
  actuator movement protection that is currently holding it.
- **Verification:** Added regression tests for ON latching, OFF latching, and
  restart recovery. The focused Warm Floor/status test set passed 48 tests,
  source compilation passed, and `git diff --check` passed. No configurable
  actuator timing fields were added; the default four-minute behavior remains
  internal and generic.
