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
