"""Maintenance policy using BT's shared filters and thermal learning."""

from __future__ import annotations

from dataclasses import dataclass, replace
import math

from .warm_floor import (
    WarmFloorProfile,
    WarmFloorThermalInput,
    compute_warm_floor_profile,
    profile_for_intensity,
)

MAX_OFF_SECONDS = 3600.0
STABILITY_SECONDS = 1800.0
REDUCTION_SECONDS = 900.0
TREND_THRESHOLD = 0.0005
TEMPERATURE_NOISE = 0.05
RECOVERY_INTENSITY = 0.8
INTENSITY_STEP = 0.05


@dataclass(frozen=True)
class FloorReport:
    """One accepted room report; ticks must not create fresh evidence."""

    timestamp: float
    temperature: float


@dataclass(frozen=True)
class WarmFloorAutoState:
    """Policy adaptation only: BT owns filters, trends and plant learning."""

    target: float | None = None
    intensity: float | None = None
    stable_since: float | None = None
    last_reduction: float | None = None
    rollback_intensity: float | None = None
    reason: str = "observing"
    last_report: FloorReport | None = None
    stable_temperature: float | None = None
    level: str | None = None


@dataclass(frozen=True)
class WarmFloorAutoDecision:
    """Selected policy and durations, with the shared BT trend."""

    state: WarmFloorAutoState
    profile: WarmFloorProfile
    policy: str
    reason: str
    slow_slope: float | None


def advance_warm_floor_auto(
    inputs: WarmFloorThermalInput,
    state: WarmFloorAutoState,
    report: FloorReport | None,
    *,
    eligible: bool = True,
) -> WarmFloorAutoDecision:
    """Adapt every preset on real reports without relearning room physics.

    Fixed presets retain their selected level. Auto may choose a level.
    Continued cooling strengthens either; confirmed stability tapers it.
    A held report, changed target or changed preset cannot confirm stability.
    """
    if state.target != inputs.target_temperature or state.level != inputs.level:
        state = WarmFloorAutoState(target=inputs.target_temperature, level=inputs.level)
    fresh = (
        report is not None
        and math.isfinite(report.timestamp)
        and math.isfinite(report.temperature)
        and (
            state.last_report is None or report.timestamp > state.last_report.timestamp
        )
    )
    previous = state.last_report
    if fresh:
        state = replace(state, last_report=report)
    eligible = eligible and inputs.room_temperature >= inputs.target_temperature
    slope = inputs.slow_slope_kelvin_per_min
    if slope is not None and not math.isfinite(slope):
        slope = None
    automatic = inputs.level == "auto"
    policy = "level_2" if automatic else inputs.level
    if not eligible:
        reason = "normal_heating"
    elif inputs.room_temperature > inputs.target_temperature + 0.2 + 1e-9:
        policy = "level_1" if automatic else policy
        state = replace(state, intensity=None, stable_since=None, reason="above_band")
        reason = state.reason
    elif (inputs.slope_kelvin_per_min or 0.0) > TREND_THRESHOLD or (
        slope is not None and slope > TREND_THRESHOLD
    ):
        policy = "level_1" if automatic else policy
        state = replace(state, intensity=None, stable_since=None, reason="warming")
        reason = state.reason
    else:
        if state.intensity is None:
            state = replace(state, reason="observing")
        if (
            fresh
            and report is not None
            and slope is not None
            and slope < -TREND_THRESHOLD
        ):
            intensity = max(
                RECOVERY_INTENSITY,
                compute_warm_floor_profile(replace(inputs, level=policy)).intensity,
                state.intensity or 0.0,
                state.rollback_intensity or 0.0,
            )
            continuing = state.reason in ("recovering", "cooling_returned")
            reinforce = (
                continuing
                and state.last_reduction is not None
                and report.timestamp - state.last_reduction >= REDUCTION_SECONDS
            )
            if reinforce:
                intensity = min(1.0, intensity + INTENSITY_STEP)
            state = replace(
                state,
                intensity=intensity,
                stable_since=None,
                stable_temperature=None,
                last_reduction=report.timestamp
                if not continuing or reinforce
                else state.last_reduction,
                reason="cooling_returned"
                if state.reason in ("balancing", "maintaining")
                else "recovering",
            )
        elif fresh and report is not None and state.intensity is not None:
            anchor = state.stable_temperature
            stable = (
                slope is not None
                and abs(slope) <= TREND_THRESHOLD
                and previous is not None
                and report.timestamp - previous.timestamp <= STABILITY_SECONDS
                and abs(
                    report.temperature
                    - (anchor if anchor is not None else previous.temperature)
                )
                <= TEMPERATURE_NOISE + 1e-9
            )
            if stable:
                if state.stable_since is None:
                    state = replace(
                        state,
                        stable_since=report.timestamp,
                        stable_temperature=report.temperature,
                        reason="stabilizing",
                    )
                elif report.timestamp - state.stable_since >= STABILITY_SECONDS and (
                    state.last_reduction is None
                    or report.timestamp - state.last_reduction >= REDUCTION_SECONDS
                ):
                    floor = {"level_1": 0.2, "level_2": 0.45, "level_3": 0.7}.get(
                        inputs.level, 0.2
                    )
                    state = replace(
                        state,
                        rollback_intensity=state.intensity,
                        intensity=max(floor, state.intensity - INTENSITY_STEP),
                        last_reduction=report.timestamp,
                        reason="maintaining"
                        if state.intensity <= floor + 1e-9
                        else "balancing",
                    )
            else:
                state = replace(state, stable_since=None, stable_temperature=None)
        reason = state.reason
        if automatic and state.intensity is not None:
            policy = (
                "level_3"
                if state.intensity >= 0.7 - 1e-9
                else "level_2"
                if state.intensity >= 0.45 - 1e-9
                else "level_1"
            )
    profile = compute_warm_floor_profile(replace(inputs, level=policy))
    if eligible and state.intensity is not None:
        profile = profile_for_intensity(
            inputs, state.intensity, profile.structural_scale
        )
    if automatic:
        profile = replace(
            profile, off_seconds=min(MAX_OFF_SECONDS, profile.off_seconds)
        )
    return WarmFloorAutoDecision(state, profile, policy, reason, slope)
