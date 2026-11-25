---
title: Warm Floor
description: Maintenance heating for underfloor circuits in the Better Thermostat fork.
---

## Configuration and behavior

Enable **Underfloor Heating** in the first setup step. On each heater's advanced
screen choose its heating type and a Warm Floor level. Only heaters marked
underfloor receive maintenance; other emitters retain ordinary upstream control.
There is no additional actuator-type selection.

The form labels are **Enable Underfloor Heating**, **Warm Floor max backoff (°C)**,
**Warm Floor sustain push (°C)**, **Heat-source flow/water temperature sensor (optional)**
and **Heat-source flow/water temperature, static fallback (°C)**. The numeric
backoff and push values are used with Custom; the other levels choose them for you.

Levels **1 Eco**, **2 Balanced**, **3 Keep it Hot**, **Auto** and **Custom** remain
available. Custom exposes the stored maximum backoff and optional sustain push.
Backoff limits how far the maintenance setpoint may sit below the room target;
it is not a permitted drop of the actual room temperature. Sustain push adjusts
the underlying heater's request, never Better Thermostat's displayed room target.

Below target, normal room heating takes control. At or above target, Generic
Thermostat circuits alternate maintenance requests with pauses. Above-target
temperature and warming trends extend the pauses rather than automatically
eliminating maintenance. ON and OFF intervals each have a four-minute minimum;
OFF mode, open contacts, device release and safety limits take priority.

Durations use the chosen baseline and available heat-loss, heating-power,
outdoor-temperature and room-trend estimates. MPC v1's learned gain, loss,
insulation and solar contribution are used when present. MPC v2 contributes its
disturbance and room time-constant estimates. Its virtual radiator temperature
is not a measured floor temperature. Optional flow/water temperature and a
static fallback support the Warm Floor demand estimate; they do not set water
temperature or change upstream MPC v2's plant model.

Each circuit has its own phase start. New sensor reports recalculate the current
deadline using elapsed time, without restarting the timer. Valid sparse readings
remain usable between reports; time passing is not a fabricated temperature
measurement or a new confirmation of the slope. Explicitly unavailable or invalid
readings follow upstream fallback/hold behavior. HOLD starts no maintenance pulse.

For Generic Thermostat, an ON request raises the downstream setpoint above its
own temperature and the room temperature, respecting device steps and limits.
An OFF request lowers it below the room target to release the pulse. The
underlying thermostat still owns its relay and hysteresis. Its own cycle settings
can delay a response. Other supported devices use sustaining setpoint, calibration
offset or valve-floor adjustments through the normal safety and write pipeline.

The **Warm Floor Status** diagnostic sensor retains its existing unique id. Its
`circuits` attribute separates planned phase, calculated durations, requested
setpoint, write result and device-reported heating for every circuit. `sent`
means the service call returned, not that a relay moved. `reported_heating: null`
means feedback is unknown. Heating feedback does not prove water flow, floor
temperature or energy savings. The absolute maintenance ceiling remains the
lower of room target + 5°C and the device's maximum temperature.

After restart, phase timing is recovered from available heating feedback with a
full minimum interval. Unknown feedback starts a pause. A temporary Warm Floor
setpoint is not adopted as a new room target; saved room targets take priority,
and a fresh entry without a saved target uses the ordinary default instead of
an underfloor heater's temporary setpoint.

## Shared BT signals and adaptive maintenance

Warm Floor consumes Better Thermostat's existing short temperature slope,
Temperature EMA 1h, heating-power tracker and heat-loss tracker. There is no
second one-hour filter, room-temperature history or thermal learner per circuit.
The one-hour EMA now belongs to the thermostat, so disabling its diagnostic
sensor does not stop control updates. The sensor keeps its existing unique id.
Its `temperature_slope_per_hour` attribute exposes the slow rate in °C/hour.

The short trend remains BT's short-filter slope in K/min. The slow trend is
the derivative of BT's one-hour EMA: `(short EMA - one-hour EMA) / 60` in K/min.
It becomes available after 30 minutes of filter warm-up. This is a filtered
rate, not the raw difference between two sensor reports, a temperature, or a
promise about next hour's temperature. Display it as °C/hour by multiplying
by 60; preserve null when unavailable. `slow_trend_kelvin_per_min` in Warm Floor
Status is an alias of this shared BT value, with `trend_source` identifying it.
The former `trend_observation_span_seconds` key remains null for compatibility;
the shared filter does not claim a separate raw-report observation window.

Eco, Balanced, Keep it Hot and Auto all recalculate ON/OFF from BT's estimated
net warming/cooling rates, current temperature margin and shared trends.
The chosen intensity sets a small comfort reserve (0.05–0.2°C). ON estimates
the time to replenish that reserve and heat lost during the minimum pause,
using BT's warming rate. OFF uses the available margin and cooling rate.
Rising temperature reduces the next heating interval and extends the pause.
A weaker emitter or a slower measured room response can require a pulse much
longer than 12 minutes: there is no fixed ON maximum or 75% duty ceiling.
Missing gain estimates use minimum probing pulses, rather than invented gain.
Custom uses the same duration calculation with its configured backoff/push.

BT's existing heating and loss trackers also see device-reported heating during
maintenance. An intended ON or a successful write alone is insufficient.
Any emitter reporting heating prevents the room from being learned as idle;
otherwise every controlled emitter must confirm idle/off. Unknown feedback
suspends learning for that interval. Existing learning pause, open-contact and
cooling exclusions remain in force. This reuses the normal learner, including
its handling of heat that continues to arrive after heating stops.

Auto begins with Balanced. Fresh accepted sensor reports and the shared slow
trend can strengthen maintenance near target. Eco, Balanced and Keep it Hot
adapt too, while retaining their selected level. Auto can choose a level.
Cooling faster than 0.03°C/hour near target increases maintenance strength;
continued cooling can add 0.05 at most once per 15 minutes. A repeated report
or a control tick is never new evidence for this adaptation.

New reports showing a stable range within 0.05°C and a stable shared slow trend
start a 30-minute confirmation interval. Confirmed stability reduces strength
by 0.05 at most once per 15 minutes, stopping at the selected preset baseline
(or Eco's baseline for Auto). A report gap longer than the confirmation window
cannot prove stability. Renewed cooling restores strong maintenance. Changing
the target or level discards policy adaptation; BT's shared room filters and
learned physical response remain owned by BT.

All original safety controls retain priority. ON/OFF minimums remain four
minutes; device hysteresis, device-specific minimum cycles, limits, write pacing,
OFF, open contacts, device release and HOLD still apply. Below target, ordinary
BT room heating takes over. Auto retains its 60-minute maximum planned pause;
the other presets retain temperature-driven pauses without that extra cap.
No pause deadline overrides a safety block or forces a device to heat.

## Predictive timing

All four adaptive presets may shorten an OFF deadline using BT's shared slow
trend, while a positive short trend prevents early starts. The prediction never
extends the ordinary pause or resets its start. Four minutes OFF remain mandatory.
Custom uses the physical duration calculation without predictive early starts.

The planning lead remains 10 minutes when gain is unavailable. With valid gain,
it is four minutes plus the estimated time to deliver 0.1°C, bounded to 4–30
minutes. This is a planning allowance, not a learned actuator or floor delay.

The 30/60-minute temperatures remain conditional trend projections. They do
not simulate the thermal effect of future heating pulses. The shared BT filter
already advances between reports; prediction does not extrapolate report age a
second time. Estimates do not replace actual room measurements.

Per-circuit `forecast` separates estimated temperatures, minutes to target,
planning lead and `advanced_start` from the actual room reading.
`decision_reason: anticipating` identifies a shortened predictive pause.
