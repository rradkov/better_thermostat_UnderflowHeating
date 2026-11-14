"""Clock-free phase timing for underfloor maintenance.

The caller supplies observations and a calculated duty profile. This module
owns phase timing only; adapters, safety limits and normal heating remain
outside it. A phase is planned intent, not proof that an actuator moved.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import math

MIN_PHASE_SECONDS = 240.0


@dataclass(frozen=True)
class WarmFloorThermalInput:
    """Available physical estimates; missing estimates remain absent."""

    room_temperature: float
    target_temperature: float
    slope_kelvin_per_min: float | None = None
    short_average: float | None = None
    long_average: float | None = None
    heat_loss: float | None = None
    heating_power: float | None = None
    insulation: float | None = None
    outdoor_temperature: float | None = None
    flow_temperature: float | None = None
    solar_gain: float | None = None
    disturbance: float | None = None
    level: str = "level_2"
    max_backoff: float = 1.5


@dataclass(frozen=True)
class WarmFloorProfile:
    """Calculated maintenance durations and bounded backoff allowance."""

    on_seconds: float
    off_seconds: float
    structural_scale: float
    intensity: float


def _clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(value, upper))


def compute_warm_floor_profile(inputs: WarmFloorThermalInput) -> WarmFloorProfile:
    """Compute source-neutral maintenance from the last valid observations.

    Missing plant estimates contribute no load signal. The selected level is
    still a usable baseline, rather than an invented learned measurement.
    Above target, room margin dominates feed-forward plant demand.
    """
    loss = inputs.heat_loss
    if inputs.insulation is not None and inputs.outdoor_temperature is not None:
        envelope_loss = max(0.0, inputs.insulation) * max(
            5.0, inputs.room_temperature - inputs.outdoor_temperature
        )
        loss = max(loss or 0.0, envelope_loss)
    severity = _clamp((loss or 0.0) / 0.05, 0.0, 1.0)
    power = (
        _clamp(inputs.heating_power / 0.2, 0.0, 1.0)
        if inputs.heating_power is not None
        else 1.0
    )
    structural = _clamp((1.0 - severity) * power, 0.1, 1.0)
    demand = severity * (1.0 - 0.5 * power)
    if inputs.flow_temperature is not None:
        demand = max(
            demand,
            0.6 * (1.0 - _clamp((inputs.flow_temperature - 20.0) / 30.0, 0.0, 1.0)),
        )
    if inputs.outdoor_temperature is not None:
        demand = max(
            demand, 0.5 * _clamp((20.0 - inputs.outdoor_temperature) / 25.0, 0.0, 1.0)
        )
    slope = inputs.slope_kelvin_per_min or 0.0
    average_difference = (
        inputs.short_average - inputs.long_average
        if inputs.short_average is not None and inputs.long_average is not None
        else 0.0
    )
    cooling = max(
        _clamp(-slope / 0.02, 0.0, 1.0), _clamp(-average_difference / 0.5, 0.0, 1.0)
    )
    warming = max(
        _clamp(slope / 0.05, 0.0, 1.0), _clamp(average_difference / 0.5, 0.0, 1.0)
    )
    demand = max(demand, cooling)
    extra_heat = max(0.0, inputs.solar_gain or 0.0, inputs.disturbance or 0.0)
    if loss is not None and loss > 0:
        demand *= 1.0 - _clamp(extra_heat / loss, 0.0, 1.0)
    baseline = {"level_1": 0.2, "level_2": 0.45, "level_3": 0.7}.get(inputs.level, 0.45)
    if inputs.level == "auto":
        baseline = 0.2 + 0.5 * _clamp(demand, 0.0, 1.0)
    elif inputs.level == "custom":
        baseline = _clamp(0.7 - inputs.max_backoff / 10.0, 0.2, 0.7)
    intensity = baseline + (1.0 - baseline) * demand
    excess = max(0.0, inputs.room_temperature - inputs.target_temperature)
    if excess > 0.1:
        proximity = 0.1 / excess
        intensity *= _clamp(proximity * (1.0 + cooling + 0.1 * demand), 0.15, 1.0)
    intensity = _clamp(intensity * (1.0 - 0.85 * warming), 0.15, 1.0)
    on_seconds = MIN_PHASE_SECONDS + 480.0 * (intensity - 0.15) / 0.85
    off_seconds = MIN_PHASE_SECONDS + 900.0 * (1.0 - intensity) ** 2
    off_seconds += 900.0 * max(0.0, excess - 0.1) * (1.0 + warming)
    return WarmFloorProfile(on_seconds, off_seconds, structural, intensity)


class WarmFloorPhase(StrEnum):
    """Planned actuator phase."""

    ON = "on"
    OFF = "off"


@dataclass(frozen=True)
class WarmFloorState:
    """Timing owned independently by one heating circuit."""

    phase: WarmFloorPhase = WarmFloorPhase.OFF
    started_monotonic: float | None = None
    recovered: bool = False


@dataclass(frozen=True)
class WarmFloorObservation:
    """Inputs captured for a single maintenance decision.

    ``reported_heating`` may be absent; absence cannot confirm physical OFF.
    ``blocked`` is the upstream decision's block, not a new local override.
    Report age deliberately has no timing gate: sparse valid observations
    remain usable until updated or explicitly marked unusable by the caller.
    """

    now_monotonic: float
    room_temperature: float
    target_temperature: float
    on_seconds: float
    off_seconds: float
    blocked: bool = False
    hold: bool = False
    normal_heating: bool = False
    reported_heating: bool | None = None


@dataclass(frozen=True)
class WarmFloorDecision:
    """Timing result; the shell still has to apply and observe the command."""

    state: WarmFloorState
    maintenance_on: bool
    remaining_seconds: float | None
    reason: str


def advance_warm_floor(
    observation: WarmFloorObservation, state: WarmFloorState
) -> WarmFloorDecision:
    """Advance a circuit using elapsed time rather than a global time window.

    Updated duty durations change the current deadline without resetting its
    start. Missed cycles trigger one transition now, not catch-up pulses.
    Upstream blocks interrupt immediately; normal heating owns below target.
    HOLD releases maintenance ownership without starting a new impulse.
    """
    values = (
        observation.now_monotonic,
        observation.room_temperature,
        observation.target_temperature,
        observation.on_seconds,
        observation.off_seconds,
    )
    valid = all(math.isfinite(value) for value in values)
    if not valid or observation.blocked:
        # Repeated blocked cycles must not move the closing deadline forward.
        stopped = state
        if state.phase is WarmFloorPhase.ON or state.started_monotonic is None:
            stopped = WarmFloorState(
                started_monotonic=(
                    observation.now_monotonic
                    if math.isfinite(observation.now_monotonic)
                    else None
                )
            )
        return WarmFloorDecision(stopped, False, None, "blocked")
    if observation.hold:
        return WarmFloorDecision(state, False, None, "hold")
    if (
        observation.normal_heating
        or observation.room_temperature < observation.target_temperature
    ):
        return WarmFloorDecision(WarmFloorState(), False, None, "normal_heating")

    now = observation.now_monotonic
    if state.started_monotonic is None or state.started_monotonic > now:
        # Restore only from explicit feedback. Unknown starts conservatively
        # with a full OFF phase; it does not claim the device is physically off.
        phase = (
            WarmFloorPhase.ON
            if observation.reported_heating is True
            else WarmFloorPhase.OFF
        )
        state = WarmFloorState(phase, now, observation.reported_heating is not None)
        remaining = (
            MIN_PHASE_SECONDS
            if state.recovered
            else max(MIN_PHASE_SECONDS, observation.off_seconds)
        )
        return WarmFloorDecision(
            state, phase is WarmFloorPhase.ON, remaining, "startup"
        )

    duration = (
        observation.on_seconds
        if state.phase is WarmFloorPhase.ON
        else observation.off_seconds
    )
    duration = max(MIN_PHASE_SECONDS, duration)
    # Recovered physical phases have unknown age and must receive one full
    # minimum interval, rather than a whole newly calculated maintenance phase.
    if state.recovered:
        duration = MIN_PHASE_SECONDS
    remaining = max(0.0, state.started_monotonic + duration - now)
    if remaining == 0.0:
        phase = (
            WarmFloorPhase.OFF
            if state.phase is WarmFloorPhase.ON
            else WarmFloorPhase.ON
        )
        state = WarmFloorState(phase, now)
        remaining = max(
            MIN_PHASE_SECONDS,
            observation.on_seconds
            if phase is WarmFloorPhase.ON
            else observation.off_seconds,
        )
    return WarmFloorDecision(
        state, state.phase is WarmFloorPhase.ON, remaining, "maintenance"
    )
