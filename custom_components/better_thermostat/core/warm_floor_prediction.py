"""Bounded trend projections that can advance a maintenance deadline."""

from dataclasses import dataclass, replace
import math

from .warm_floor import (
    MIN_PHASE_SECONDS,
    WarmFloorPhase,
    WarmFloorProfile,
    WarmFloorState,
    WarmFloorThermalInput,
)
from .warm_floor_policy import TREND_THRESHOLD


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
    profile: WarmFloorProfile,
    phase: WarmFloorState,
    now_monotonic: float,
) -> WarmFloorPrediction:
    """Shorten OFF against its original start, respecting observed warming.

    Use BT's shared one-hour EMA derivative, respecting its short-term warming
    signal. The filter already advances in BT; report age is not added again.
    """
    unavailable = WarmFloorPrediction(profile)
    slope = inputs.slow_slope_kelvin_per_min
    if slope is None or not math.isfinite(slope) or abs(slope) > 0.05:
        return unavailable
    predicted_room = inputs.room_temperature
    result = WarmFloorPrediction(
        profile, predicted_room + slope * 30.0, predicted_room + slope * 60.0
    )
    if (
        slope >= -TREND_THRESHOLD
        or (inputs.slope_kelvin_per_min or 0.0) > TREND_THRESHOLD
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
