---
title: Warm Floor & Boost
description: Deep explanation of the underfloor-heating sustaining floor and the window-reopen Boost feature.
---

Two related features aimed at rooms with high thermal inertia (underfloor heating) or a fast-reacting secondary device (a fan-coil, heat pump, or similar shared with cooling) — built entirely on telemetry Better Thermostat already computes, not a new thermal model.

## Warm Floor: why idling to zero is the wrong default for UFH

A radiator TRV backing off to 0% between heat calls is nearly free to recover from — it's a small metal object with little thermal mass. An underfloor slab is the opposite: once it's let go fully cold, bringing it back up costs a long, energy-heavy recovery, and the room feels the dip the whole time.

Warm Floor keeps a low, continuous background heat level in underfloor-flagged heaters instead of letting them idle to zero — a *sustaining floor* on the setpoint (or offset/valve %, depending on calibration type), never a ceiling. It is eligible only while the measured room temperature is strictly above the Better Thermostat room target. At or below target, normal BT heating owns the actuator and Warm Floor returns the normal command unchanged. For Generic Thermostat heaters, the duty-cycle ON phase must send the downstream setpoint above the Better Thermostat room target; otherwise the Generic Thermostat cannot turn its relay on. This never changes the user-facing target.

For Generic Thermostat heaters, an idle cycle also has to release any previous
downstream pulse. The relay is controlled only by the Generic Thermostat's
setpoint, so a stale value above the room temperature is still a real heating
command even when Warm Floor is outside its ON window. When the room is
strictly above target and no sustain push is configured, Warm Floor clamps that stale
idle value back to the user target. This release is limited to Generic
Thermostat heaters and does not alter valve or calibration-offset semantics.

## How much backoff is actually safe

The configured "max backoff" is a ceiling, not a fixed value. Each cycle, BT scales it down based on:

- **Heat loss and heating power** (or the equivalent learned MPC Loss/Gain, for MPC-calibrated heaters) — a leaky room or a weak emitter loses ground fast and recovers slowly, so it gets a *tighter* floor, not a looser one. This is the opposite of the naive assumption; a well-insulated, strong-emitter room can safely tolerate the full configured backoff because it won't drift far or fast.
- **MPC Insulation (Ka)** — a delta-normalized envelope coefficient (loss per degree of indoor/outdoor difference), independent of today's specific weather. Loss/heat-loss readings can look artificially low on a mild day even for a genuinely leaky room; Ka cross-checks against that and can only tighten the floor further, never loosen it.
- **Solar gain** (MPC-calibrated heaters that have learned a solar factor) — the south-facing-room scenario: if the room's current comfort is propped up by sunlight rather than delivered heat, the maintenance duty is reduced but not automatically removed. Warm Floor keeps the floor close to target while solar gain is masking the load, so there's no gap to catch up on afterward — while the live temperature ceiling still prevents unnecessary overheating.
- **The room's real-time trend** — the structural/solar estimate is a longer-run average; the live rate of change modulates the maintenance duty in both directions. Still actively gaining → pulses become shorter/less frequent, but are not suppressed solely because the room is rising; the safe upper band still prevents overshoot. Falling faster than the structural model expects → the floor clamps to its tightest setting immediately, regardless of what the longer-run numbers say.
- **Short versus one-hour temperature EMA** — the short EMA follows recent room movement, while the one-hour EMA is a slower thermal baseline. When the short EMA is below the one-hour value, Warm Floor treats the room as undergoing sustained cooling even if the instantaneous slope is flat and increases maintenance demand. When it is above the baseline, solar or residual heat is likely helping and the existing maintenance demand is reduced, without removing the selected level or bypassing safety limits.
- **The external sensor's own freshness** — some real-world temperature sensors only report hourly, or on a ~0.5°C change, which can leave `temp_slope` reading artificially flat for long stretches even while the room is genuinely still moving. If the sensor's last real update is more than ~20 minutes old, BT can't trust that flatness as evidence the room has actually settled - it's treated the same as "still actively gaining," the same suppression as the live-trend guard above, not the more permissive "no trend data available" fallback that applies when a *fresh* reading simply has no slope to report yet.

For Generic Thermostat heaters, the three Warm Floor levels are baseline intensities, not fixed timers. Each control tick converts the selected baseline plus the live thermal demand into a new ON duration and pause. The calculation respects the actuator's minimum travel time, increases maintenance when cooling, heat loss, heating power, or MPC data show a real load, and reduces it when the slope/EMA shows warming or the room is further above target. The wall-clock window is only the mechanism needed to operate a slow ON/OFF actuator; its values are dynamic outputs, not level presets.

## Closing the no-op gap: sustain push

The backoff floor above only ever has an effect if the setpoint it's flooring has genuine room to be raised. On four of Better Thermostat's eight calibration modes — AI Time Based, External Sensor Offset Only, Aggressive, and No Calibration — a non-valve heater's setpoint is computed as a pinned function of target temperature: once the heater goes idle, that setpoint sits at *exactly* target, every cycle, regardless of how far the room has actually drifted. `max(computed_value, sustaining_setpoint)` where `computed_value` already equals target is a structural no-op — there's nothing to float above.

The other four modes — MPC Predictive, (AI) MPC v2, TPI Controller, and PID Controller — don't have this problem: each one already computes a setpoint that's a genuinely graduated function of the room's own state (duty cycle, learned gain/loss, PID error term), so the backoff floor has real room to work in.

The configurable sustain push is an optional extension of that ON command. Even when it is `0.0`, Generic Thermostat duty cycling still raises the downstream setpoint by at least one valid device step above target. The optional amount can extend the pulse, but never changes `bt_target_temp`; only the value written to the underlying `climate.*` entity changes. Solar/rising-temperature guards can suppress the pulse entirely when heating would risk overshoot.

Because the mechanism keys entirely off "is the computed setpoint already pinned at target while idle," not off which calibration mode produced it, it can in principle also engage on the four already-working modes on the rare cycle where they land exactly on target too - it simply has nothing to do most of the time there, since their setpoint is already below target whenever there's real backoff to give.

## Boost: symmetric recovery on window reopen

The original ask was narrower — a fan-coil that kicks in after a window closes if the room got too cold. In practice the same device usually does double duty (heating and cooling), and a hot day with cooling suppressed by an open window can overheat a room just as a cold day can under-heat it. So Boost is symmetric: a drop past the threshold arms a **heat** boost, a rise arms a **cool** boost, checked against what the shared device's `hvac_modes` actually report supporting.

Because the boost channel and the normal cooler control both drive the same entity, an active boost preempts the regular cooling pass for as long as it runs, and control reverts automatically once the boost ends.

### Stopping before overshoot, not after

A naive "stop once within tolerance of target" both overshoots (the device keeps delivering residual heat/cooling for a few minutes after being told to stop) and risks stopping too early on a noisy blip. Boost's termination is anticipatory instead:

- It projects the room's trajectory forward by a configurable **anticipation window** (the time residual heat/cooling keeps flowing after the device is told to stop) using the room's current rate of change, and stops the instant that projection is expected to reach target — not once it's already there.
- **Solar gain**, once learned, is folded into that projection too: solar is always a warming contribution, so it always pushes the projected temperature warmer. For a heat boost that means stopping a little earlier (the sun keeps finishing the job); for a cool boost it means needing a little more margin (the sun keeps fighting it).
- A slower, locally-tracked trend signal guards against stopping early on a single noisy reading — fed every control cycle, not just while a boost is active, so it has genuine standing memory of the room's actual recent state by the time a boost starts.
- A safety timeout force-stops the boost regardless of temperature, guarding against a stuck sensor or a device that isn't actually acting.
- A cooldown after each boost ends prevents a room that keeps drifting past the threshold (repeated window in/out) from short-cycling the shared device.

## What's deliberately out of scope

- No bespoke thermal model for either feature — everything above reuses telemetry (heat loss, heating power, MPC Loss/Gain/Ka, solar gain, temperature slope) BT already maintains for its calibration algorithms.
- No switch-domain relay dispatch — both features work through ordinary `climate.*` entities, including Home Assistant's own Generic Thermostat helper, which already covers the common on/off-relay underfloor-heating setup.
- Warm Floor never forces heat and Boost never bypasses the open-window/door gate — both sit strictly on top of the existing control decisions, never underneath them.
