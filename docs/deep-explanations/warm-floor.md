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
