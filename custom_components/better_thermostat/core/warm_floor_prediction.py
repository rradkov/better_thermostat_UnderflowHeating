"""Bounded trend projections that can advance an Auto maintenance deadline."""

from dataclasses import dataclass, replace
import math

from .warm_floor import (
    MIN_PHASE_SECONDS,
    WarmFloorPhase,
    WarmFloorProfile,
    WarmFloorState,
    WarmFloorThermalInput,
)
from .warm_floor_auto import (
    MAX_TREND_SECONDS,
    MIN_TREND_SECONDS,
    TEMPERATURE_NOISE,
    TREND_THRESHOLD,
    FloorReport,
)


@dataclass(frozen=True)
class WarmFloorPrediction:
    """Estimated temperatures are diagnostics, never replacement measurements."""

    profile: WarmFloorProfile
    temperature_after_30_minutes: float | None = None
    temperature_after_60_minutes: float | None = None
    minutes_to_target: float | None = None
    response_lead_minutes: float | None = None
    advanced_start: bool = False


def predict_warm_floor(
    inputs: WarmFloorThermalInput,
    reports: tuple[FloorReport, ...],
    profile: WarmFloorProfile,
    phase: WarmFloorState,
    now_monotonic: float,
    now_timestamp: float,
) -> WarmFloorPrediction:
    """Shorten OFF against its original start, respecting observed warming.

    The less pessimistic of the half-hour and hourly observed rates is used.
    Report age advances the projection, not the measured room temperature;
    extrapolation of a held reading is bounded to one additional hour.
    """
    unavailable = WarmFloorPrediction(profile)
    if len(reports) < 2:
        return unavailable
    latest = reports[-1]
    anchors = [
        report
        for report in reports[:-1]
        if MIN_TREND_SECONDS <= latest.timestamp - report.timestamp <= MAX_TREND_SECONDS
    ]
    if not anchors:
        return unavailable
    slopes = []
    for horizon in (1800.0, 3600.0):
        anchor = min(
            anchors,
            key=lambda report: abs(latest.timestamp - report.timestamp - horizon),
        )
        slopes.append(
            (latest.temperature - anchor.temperature)
            / ((latest.timestamp - anchor.timestamp) / 60.0)
        )
    slope = max(slopes)
    if not math.isfinite(slope) or abs(slope) > 0.05:
        return unavailable
    age = min(60.0, max(0.0, (now_timestamp - latest.timestamp) / 60.0))
    predicted_room = inputs.room_temperature + slope * age
    result = WarmFloorPrediction(
        profile, predicted_room + slope * 30.0, predicted_room + slope * 60.0
    )
    if (
        slope >= -TREND_THRESHOLD
        or (inputs.slope_kelvin_per_min or 0.0) > TREND_THRESHOLD
        or max(report.temperature for report in reports)
        - min(report.temperature for report in reports)
        <= TEMPERATURE_NOISE + 1e-9
        or inputs.room_temperature < inputs.target_temperature
    ):
        return result
    power = inputs.heating_power
    # This is a bounded planning allowance, not a learned slab delay. The
    # existing plant estimates still determine the underlying duty profile.
    lead = (
        min(30.0, max(4.0, 4.0 + 0.1 / power))
        if power is not None and math.isfinite(power) and power > 0.0
        else 10.0
    )
    minutes = max(0.0, (predicted_room - inputs.target_temperature) / -slope)
    result = replace(result, minutes_to_target=minutes, response_lead_minutes=lead)
    if phase.phase is not WarmFloorPhase.OFF or phase.started_monotonic is None:
        return result
    elapsed = max(0.0, now_monotonic - phase.started_monotonic)
    duration = max(MIN_PHASE_SECONDS, elapsed + max(0.0, minutes - lead) * 60.0)
    duration = min(profile.off_seconds, duration)
    return replace(
        result,
        profile=replace(profile, off_seconds=duration),
        advanced_start=duration < profile.off_seconds,
    )
