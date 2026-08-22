---
title: Warm Floor & Boost
description: Deep explanation of the underfloor-heating sustaining floor and the window-reopen Boost feature.
---

Two related features aimed at rooms with high thermal inertia (underfloor heating) or a fast-reacting secondary device (a fan-coil, heat pump, or similar shared with cooling) — built entirely on telemetry Better Thermostat already computes, not a new thermal model.

## Warm Floor: why idling to zero is the wrong default for UFH

A radiator TRV backing off to 0% between heat calls is nearly free to recover from — it's a small metal object with little thermal mass. An underfloor slab is the opposite: once it's let go fully cold, bringing it back up costs a long, energy-heavy recovery, and the room feels the dip the whole time.

Warm Floor keeps a low, continuous background heat level in underfloor-flagged heaters instead of letting them idle to zero — a *sustaining floor* on the setpoint (or offset/valve %, depending on calibration type), never a ceiling. It never pushes the room above target and never fights the normal heating computation; it only raises what would otherwise be a fully-idle cycle.

## How much backoff is actually safe

The configured "max backoff" is a ceiling, not a fixed value. Each cycle, BT scales it down based on:

- **Heat loss and heating power** (or the equivalent learned MPC Loss/Gain, for MPC-calibrated heaters) — a leaky room or a weak emitter loses ground fast and recovers slowly, so it gets a *tighter* floor, not a looser one. This is the opposite of the naive assumption; a well-insulated, strong-emitter room can safely tolerate the full configured backoff because it won't drift far or fast.
- **MPC Insulation (Ka)** — a delta-normalized envelope coefficient (loss per degree of indoor/outdoor difference), independent of today's specific weather. Loss/heat-loss readings can look artificially low on a mild day even for a genuinely leaky room; Ka cross-checks against that and can only tighten the floor further, never loosen it.
- **Solar gain** (MPC-calibrated heaters that have learned a solar factor) — the south-facing-room scenario: if the room's current comfort is propped up by sunlight rather than delivered heat, backing the floor off the usual amount means the slab coasts on zero delivered heat for as long as the sun holds, then free-falls once it fades. Warm Floor keeps the floor close to target while solar gain is masking the load, so there's no gap to catch up on afterward — never by forcing heat while the sun is genuinely covering it.
- **The room's real-time trend** — the structural/solar estimate is a longer-run average; the live rate of change overrides it in both directions. Still actively gaining → the raise is suppressed entirely this cycle (no compounding an overshoot). Falling faster than the structural model expects → the floor clamps to its tightest setting immediately, regardless of what the longer-run numbers say.

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
