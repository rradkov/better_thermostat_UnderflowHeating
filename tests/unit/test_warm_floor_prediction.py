"""Forecasts may advance a deadline, but never turn estimates into evidence."""

import pytest

from custom_components.better_thermostat.core.warm_floor import (
    WarmFloorObservation,
    WarmFloorPhase,
    WarmFloorProfile,
    WarmFloorState,
    WarmFloorThermalInput,
    advance_warm_floor,
)
from custom_components.better_thermostat.core.warm_floor_prediction import (
    predict_warm_floor,
)


def predict(*, room=23.1, slow=-0.2 / 60, phase=None, elapsed=600, **inputs):
    return predict_warm_floor(
        WarmFloorThermalInput(
            room, 23.0, level="auto", slow_slope_kelvin_per_min=slow, **inputs
        ),
        WarmFloorProfile(600, 3600, 0.5, 0.7),
        WarmFloorState(WarmFloorPhase.OFF, 0) if phase is None else phase,
        elapsed,
    )


def test_predicted_crossing_shortens_an_existing_pause_without_restarting_it():
    result = predict(heating_power=0.01)
    assert result.temperature_after_30_minutes == pytest.approx(23.0)
    assert result.temperature_after_60_minutes == pytest.approx(22.9)
    assert result.minutes_to_target == pytest.approx(30)
    assert result.response_lead_minutes == 14
    assert result.advanced_start
    assert result.profile.off_seconds == pytest.approx(1560)
    later = predict(heating_power=0.01, elapsed=900)
    assert later.profile.off_seconds <= 3600
    assert later.profile.off_seconds - 900 == pytest.approx(
        result.profile.off_seconds - 600
    )


def test_an_imminent_drop_can_start_at_four_minutes_but_not_before():
    result = predict(room=23.0, elapsed=120)
    assert result.profile.off_seconds == 240
    phase = WarmFloorState(WarmFloorPhase.OFF, 0)
    before = advance_warm_floor(WarmFloorObservation(239, 23, 23, 600, 240), phase)
    due = advance_warm_floor(WarmFloorObservation(240, 23, 23, 600, 240), phase)
    assert not before.maintenance_on
    assert due.maintenance_on
    assert due.state.started_monotonic == 240


@pytest.mark.parametrize("power", [None, 0.0, -1.0, float("nan"), float("inf")])
def test_missing_or_unusable_gain_uses_an_explicit_planning_allowance(power):
    assert predict(heating_power=power).response_lead_minutes == 10


def test_a_weak_emitter_needs_more_lead_than_a_strong_one():
    weak = predict(heating_power=0.001)
    strong = predict(heating_power=0.1)
    assert weak.response_lead_minutes == 30
    assert weak.profile.off_seconds < strong.profile.off_seconds


@pytest.mark.parametrize("slow", [None, float("nan"), float("inf"), -0.06])
def test_missing_or_implausible_bt_trends_cannot_advance_heating(slow):
    result = predict(slow=slow)
    assert result.temperature_after_30_minutes is None
    assert not result.advanced_start
    assert result.profile.off_seconds == 3600


def test_recent_bt_stability_takes_precedence_over_an_older_drop():
    result = predict(slow=0)
    assert result.temperature_after_60_minutes == 23.1
    assert not result.advanced_start


@pytest.mark.parametrize("inputs", [{"slope_kelvin_per_min": 0.001}, {"room": 22.9}])
def test_residual_warming_and_normal_heating_prevent_an_early_start(inputs):
    result = predict(**inputs)
    assert not result.advanced_start


@pytest.mark.parametrize(
    "phase", [WarmFloorState(WarmFloorPhase.ON, 0), WarmFloorState()]
)
def test_a_forecast_cannot_shorten_an_on_phase_or_invent_a_start_time(phase):
    result = predict(phase=phase)
    assert result.minutes_to_target is not None
    assert not result.advanced_start


def test_control_ticks_do_not_age_bt_filtered_temperatures_a_second_time():
    first = predict(elapsed=0)
    later = predict(elapsed=3600)
    assert first.temperature_after_60_minutes == later.temperature_after_60_minutes


def test_forecast_never_delays_an_already_earlier_pause():
    result = predict(room=24.0)
    assert result.profile.off_seconds == 3600
    assert not result.advanced_start


@pytest.mark.parametrize("blocked,hold", [(True, False), (False, True)])
def test_predictive_durations_do_not_bypass_upstream_blocks(blocked, hold):
    result = predict(room=23)
    decision = advance_warm_floor(
        WarmFloorObservation(
            1000,
            23,
            23,
            result.profile.on_seconds,
            result.profile.off_seconds,
            blocked=blocked,
            hold=hold,
        ),
        WarmFloorState(WarmFloorPhase.OFF, 0),
    )
    assert not decision.maintenance_on
