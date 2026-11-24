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

## Auto recovery and observed slow trends

Auto begins with Balanced maintenance while gathering sensor reports. Unlike
manual Eco, Balanced, Keep it Hot and Custom settings, Auto can adapt its
maintenance strength. Room readings and their original Home Assistant report
timestamps are tracked independently per circuit, with bounded two-hour history.
Control ticks do not create reports or confirm stabilization. History retains
one report per minute, replacing that minute's value when a new changed reading
arrives; frequent updates cannot erase older minutes. Unchanged values count
only when the source sensor has actually reported again in a later minute.
No report cadence or particular hardware is assumed.

A trend needs observations spanning at least 30 minutes; the anchor closest to
one hour is preferred, and gaps over two hours cannot establish a new trend.
Cooling faster than 0.03°C/hour with an observed change exceeding 0.05°C enters
Keep it Hot recovery when the room is between the target and target + 0.2°C,
inclusive. Recovery lengthens ON and shortens OFF. Below target, normal BT
heating retains ownership; above the band or while warming, recovery is released.

Recovery is held until new reports show a stable range of at most 0.05°C.
After another 30 minutes of confirmed stability, Auto reduces its intensity by
0.05 per step, no more often than every 15 minutes and only with a new report.
Renewed cooling restores strong maintenance. All original actuator minimums,
upstream blocks and the absolute maintenance ceiling still apply. Fixed levels
retain their existing behavior. A changed room target or unusable room sensor
discards the corresponding adaptation; restart begins collecting fresh evidence.

Auto's calculated OFF interval is capped at 60 minutes. Physical response can
still be delayed by device availability, its own cycle settings and upstream
write pacing. This cap never forces heating through an upstream block.

The diagnostic fields `decision_level` and `decision_reason` describe the actual
policy used by the controller, rather than a dashboard classification of a
percentage. `slow_trend_kelvin_per_min` and `trend_observation_span_seconds`
publish observed long-term evidence; an unavailable trend remains null. The
short-term slope retains its full precision in control snapshots. Dashboards
can display both trends separately and must not convert missing evidence to zero.
