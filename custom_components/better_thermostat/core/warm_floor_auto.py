"""Observed slow trends and gradual Auto maintenance, independent of HA."""

from __future__ import annotations

from dataclasses import dataclass, replace
import math

from .warm_floor import (
    WarmFloorProfile,
    WarmFloorThermalInput,
    compute_warm_floor_profile,
)

MAX_OFF_SECONDS = 3600.0
MIN_TREND_SECONDS = 1800.0
TREND_WINDOW_SECONDS = 3600.0
MAX_TREND_SECONDS = 7200.0
MIN_REPORT_SPACING_SECONDS = 60.0
STABILITY_SECONDS = 1800.0
REDUCTION_SECONDS = 900.0
TREND_THRESHOLD = 0.0005
TEMPERATURE_NOISE = 0.05
RECOVERY_INTENSITY = 0.8
INTENSITY_STEP = 0.05


@dataclass(frozen=True)
class FloorReport:
    """One actual room-sensor report, carrying its original timestamp."""

    timestamp: float
    temperature: float


@dataclass(frozen=True)
class WarmFloorAutoState:
    """Evidence and learned maintenance strength for one circuit."""

    target: float | None = None
    reports: tuple[FloorReport, ...] = ()
    intensity: float | None = None
    stable_since: float | None = None
    last_reduction: float | None = None
    rollback_intensity: float | None = None
    reason: str = "observing"


@dataclass(frozen=True)
class WarmFloorAutoDecision:
    """The actual selected policy, its evidence and commanded duty profile."""

    state: WarmFloorAutoState
    profile: WarmFloorProfile
    policy: str
    reason: str
    slow_slope: float | None
    observation_span_seconds: float | None


def _observe(
    reports: tuple[FloorReport, ...], report: FloorReport | None
) -> tuple[tuple[FloorReport, ...], bool]:
    if report is None or not all(
        math.isfinite(value) for value in (report.timestamp, report.temperature)
    ):
        return reports, False
    if reports:
        latest = reports[-1]
        if report.timestamp <= latest.timestamp:
            return reports, False
        if math.floor(report.timestamp / MIN_REPORT_SPACING_SECONDS) == math.floor(
            latest.timestamp / MIN_REPORT_SPACING_SECONDS
        ):
            if report.temperature == latest.temperature:
                return reports, False
            # Keep the latest real value within each minute. Continuous fast
            # reports must neither lose a changed value nor erase older minutes.
            return (*reports[:-1], report), True
    cutoff = report.timestamp - MAX_TREND_SECONDS
    # Keep one anchor before the window, but never use a gap >2h as a trend.
    previous = [item for item in reports if item.timestamp < cutoff]
    recent = [item for item in reports if item.timestamp >= cutoff]
    return tuple((previous[-1:] + recent + [report])[-241:]), True


def _trend(reports: tuple[FloorReport, ...]) -> tuple[float | None, float | None, bool]:
    if not reports:
        return None, None, False
    latest = reports[-1]
    anchors = [
        item
        for item in reports[:-1]
        if MIN_TREND_SECONDS <= latest.timestamp - item.timestamp <= MAX_TREND_SECONDS
    ]
    if not anchors:
        return None, None, False
    anchor = min(
        anchors,
        key=lambda item: abs(latest.timestamp - item.timestamp - TREND_WINDOW_SECONDS),
    )
    span = latest.timestamp - anchor.timestamp
    window = [
        item.temperature for item in reports if item.timestamp >= anchor.timestamp
    ]
    slope = (latest.temperature - anchor.temperature) / (span / 60.0)
    stable = max(window) - min(window) <= TEMPERATURE_NOISE + 1e-9
    return slope, span, stable


def advance_warm_floor_auto(
    inputs: WarmFloorThermalInput,
    state: WarmFloorAutoState,
    report: FloorReport | None,
    *,
    eligible: bool = True,
) -> WarmFloorAutoDecision:
    """React to slow cooling near target; taper only on fresh stable evidence.

    Repeated control ticks cannot create observations or confirm stability.
    A new target discards the previous target's adaptation. Outside the near-
    target band, warming/overshoot takes precedence over recovery strength.
    """
    if state.target != inputs.target_temperature:
        state = WarmFloorAutoState(target=inputs.target_temperature)
    eligible = eligible and inputs.room_temperature >= inputs.target_temperature
    reports, fresh = _observe(state.reports, report)
    state = replace(state, reports=reports)
    slope, span, stable = _trend(reports)
    policy = "level_2"
    if not eligible:
        reason = "normal_heating"
    elif inputs.room_temperature > inputs.target_temperature + 0.2 + 1e-9:
        policy = "level_1"
        state = replace(state, intensity=None, stable_since=None, reason="above_band")
        reason = state.reason
    elif (inputs.slope_kelvin_per_min or 0.0) > 0.005 or (
        fresh and slope is not None and slope > TREND_THRESHOLD and not stable
    ):
        policy = "level_1"
        state = replace(state, intensity=None, stable_since=None, reason="warming")
        reason = state.reason
    else:
        if state.intensity is None:
            state = replace(state, reason="observing")
        if fresh and slope is not None and slope < -TREND_THRESHOLD and not stable:
            intensity = max(
                RECOVERY_INTENSITY,
                compute_warm_floor_profile(replace(inputs, level="level_3")).intensity,
                state.intensity or 0.0,
                state.rollback_intensity or 0.0,
            )
            state = replace(
                state,
                intensity=intensity,
                stable_since=None,
                last_reduction=reports[-1].timestamp,
                reason="cooling_returned"
                if state.reason in ("balancing", "maintaining")
                else "recovering",
            )
        elif fresh and state.intensity is not None:
            timestamp = reports[-1].timestamp
            if stable:
                if state.stable_since is None:
                    state = replace(state, stable_since=timestamp, reason="stabilizing")
                elif timestamp - state.stable_since >= STABILITY_SECONDS and (
                    state.last_reduction is None
                    or timestamp - state.last_reduction >= REDUCTION_SECONDS
                ):
                    state = replace(
                        state,
                        rollback_intensity=state.intensity,
                        intensity=max(0.2, state.intensity - INTENSITY_STEP),
                        last_reduction=timestamp,
                        reason="maintaining"
                        if state.intensity <= 0.2 + 1e-9
                        else "balancing",
                    )
            else:
                state = replace(state, stable_since=None)
        reason = state.reason
        if state.intensity is not None:
            policy = (
                "level_3"
                if state.intensity >= 0.7 - 1e-9
                else "level_2"
                if state.intensity >= 0.45 - 1e-9
                else "level_1"
            )
    profile = compute_warm_floor_profile(replace(inputs, level=policy))
    if eligible and state.intensity is not None:
        strength = state.intensity
        profile = replace(
            profile,
            intensity=strength,
            on_seconds=240.0 + 480.0 * (strength - 0.15) / 0.85,
            off_seconds=240.0
            + 900.0 * (1.0 - strength) ** 2
            + 900.0
            * max(0.0, inputs.room_temperature - inputs.target_temperature - 0.1),
        )
    profile = replace(profile, off_seconds=min(MAX_OFF_SECONDS, profile.off_seconds))
    return WarmFloorAutoDecision(state, profile, policy, reason, slope, span)
