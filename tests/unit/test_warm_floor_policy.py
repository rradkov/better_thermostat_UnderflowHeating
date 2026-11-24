"""Shared BT evidence must drive every maintenance preset without a timer cap."""

from dataclasses import replace
import math

import pytest

from custom_components.better_thermostat.core.warm_floor import (
    WarmFloorObservation,
    WarmFloorPhase,
    WarmFloorState,
    WarmFloorThermalInput,
    advance_warm_floor,
    compute_warm_floor_profile,
)
from custom_components.better_thermostat.core.warm_floor_policy import (
    FloorReport,
    WarmFloorPolicyState,
    advance_warm_floor_policy,
)

LEVELS = ["auto", "level_1", "level_2", "level_3"]


def inputs(room=23.0, **changes):
    return replace(
        WarmFloorThermalInput(
            room,
            23.0,
            level="auto",
            heat_loss=0.003,
            heating_power=0.006,
            slow_slope_kelvin_per_min=-0.002,
        ),
        **changes,
    )


def recovering(level="auto"):
    return advance_warm_floor_policy(
        inputs(level=level), WarmFloorPolicyState(), FloorReport(3600, 23.0)
    )


@pytest.mark.parametrize("level", LEVELS)
def test_cooling_uses_bt_trend_and_retains_the_selected_preset(level):
    decision = recovering(level)
    assert decision.reason == "recovering"
    assert decision.policy == ("level_3" if level == "auto" else level)
    assert decision.slow_slope == -0.002
    assert decision.profile.intensity >= 0.8
    assert decision.profile.on_seconds > 720


@pytest.mark.parametrize("level", LEVELS)
def test_repeated_ticks_do_not_confirm_stability_or_increase_heat(level):
    initial = decision = recovering(level)
    for _ in range(50):
        decision = advance_warm_floor_policy(
            inputs(level=level), decision.state, FloorReport(3600, 23.0)
        )
    assert decision.state == initial.state
    assert decision.profile == initial.profile


@pytest.mark.parametrize("level", LEVELS)
def test_continued_cooling_increases_heat_only_on_fresh_spaced_reports(level):
    initial = recovering(level)
    stronger = advance_warm_floor_policy(
        inputs(level=level), initial.state, FloorReport(4500, 23.0)
    )
    assert stronger.profile.intensity == pytest.approx(initial.profile.intensity + 0.05)
    assert stronger.profile.on_seconds > initial.profile.on_seconds
    assert stronger.profile.off_seconds <= initial.profile.off_seconds
    too_soon = advance_warm_floor_policy(
        inputs(level=level), stronger.state, FloorReport(4560, 23.0)
    )
    assert too_soon.profile.intensity == stronger.profile.intensity


@pytest.mark.parametrize("level", LEVELS)
def test_stability_requires_new_reports_and_cumulative_temperature_stability(level):
    decision = recovering(level)
    thermal = inputs(level=level, slow_slope_kelvin_per_min=0)
    initial = decision.profile.intensity
    for timestamp in (4500, 5400, 6300):
        decision = advance_warm_floor_policy(
            thermal, decision.state, FloorReport(timestamp, 23)
        )
    assert decision.reason == "balancing"
    assert decision.profile.intensity == pytest.approx(initial - 0.05)
    restored = advance_warm_floor_policy(
        inputs(level=level), decision.state, FloorReport(7200, 23)
    )
    assert restored.reason == "cooling_returned"
    assert restored.profile.intensity >= initial


def test_sensor_silence_cannot_prove_stability():
    decision = recovering()
    result = advance_warm_floor_policy(
        inputs(slow_slope_kelvin_per_min=0), decision.state, FloorReport(10000, 23)
    )
    assert result.state.stable_since is None
    assert result.profile.intensity == decision.profile.intensity


@pytest.mark.parametrize(
    "changes,reason",
    [
        ({"room_temperature": 23.3}, "above_band"),
        ({"slope_kelvin_per_min": 0.001}, "warming"),
        ({"slow_slope_kelvin_per_min": 0.001}, "warming"),
    ],
)
def test_residual_heat_and_room_margin_release_recovery(changes, reason):
    result = advance_warm_floor_policy(
        replace(inputs(), **changes), recovering().state, None
    )
    assert result.reason == reason
    assert result.state.intensity is None


@pytest.mark.parametrize("slow", [None, float("nan"), float("inf")])
def test_missing_bt_slow_trend_does_not_create_another_temperature_history(slow):
    result = advance_warm_floor_policy(
        inputs(slow_slope_kelvin_per_min=slow),
        WarmFloorPolicyState(),
        FloorReport(3600, 23),
    )
    assert result.state.intensity is None
    assert result.slow_slope is None
    assert not hasattr(result.state, "reports")


@pytest.mark.parametrize(
    "report",
    [
        None,
        FloorReport(3600, 23),
        FloorReport(0, 23),
        FloorReport(float("nan"), 23),
        FloorReport(4500, float("inf")),
    ],
)
def test_missing_repeated_invalid_and_old_reports_are_not_new_evidence(report):
    decision = recovering()
    result = advance_warm_floor_policy(inputs(), decision.state, report)
    assert result.state == decision.state


@pytest.mark.parametrize(
    "changes",
    [{"target_temperature": 24, "room_temperature": 24}, {"level": "level_1"}],
)
def test_target_and_preset_changes_discard_previous_adaptation(changes):
    result = advance_warm_floor_policy(
        replace(inputs(slow_slope_kelvin_per_min=None), **changes),
        recovering().state,
        None,
    )
    assert result.state.intensity is None
    assert result.state.last_report is None


@pytest.mark.parametrize("level", LEVELS)
def test_normal_heating_and_upstream_blocks_remain_authoritative(level):
    decision = advance_warm_floor_policy(
        inputs(22.9, level=level), recovering(level).state, None
    )
    assert decision.reason == "normal_heating"
    for blocked, hold in [(True, False), (False, True)]:
        result = advance_warm_floor(
            WarmFloorObservation(
                1000,
                23,
                23,
                decision.profile.on_seconds,
                decision.profile.off_seconds,
                blocked=blocked,
                hold=hold,
            ),
            WarmFloorState(WarmFloorPhase.ON, 0),
        )
        assert not result.maintenance_on


@pytest.mark.parametrize("level", LEVELS + ["custom"])
@pytest.mark.parametrize(
    "gain,loss",
    [
        (0.001, 0.002),
        (0.003, 0.01),
        (0.01, 0.002),
        (0.08, 0.02),
        (None, None),
        (0, 0),
        (float("nan"), float("inf")),
    ],
)
def test_different_installations_produce_finite_durations_with_device_minimums(
    level, gain, loss
):
    profile = compute_warm_floor_profile(
        inputs(level=level, heating_power=gain, heat_loss=loss)
    )
    assert math.isfinite(profile.on_seconds) and profile.on_seconds >= 240
    assert math.isfinite(profile.off_seconds) and profile.off_seconds >= 240


@pytest.mark.parametrize("level", LEVELS)
def test_slow_response_requires_longer_heating_and_high_losses_shorten_the_pause(level):
    base = inputs(level=level)
    slow = compute_warm_floor_profile(replace(base, heating_power=0.001))
    fast = compute_warm_floor_profile(replace(base, heating_power=0.02))
    assert slow.on_seconds > fast.on_seconds
    assert slow.on_seconds > 720
    low_loss = compute_warm_floor_profile(replace(base, heat_loss=0.001))
    high_loss = compute_warm_floor_profile(replace(base, heat_loss=0.02))
    assert high_loss.off_seconds <= low_loss.off_seconds
    assert high_loss.on_seconds >= low_loss.on_seconds


def test_long_pulse_survives_twelve_minutes_and_still_finishes():
    profile = compute_warm_floor_profile(inputs(heating_power=0.001))
    state = WarmFloorState(WarmFloorPhase.ON, 0)
    assert advance_warm_floor(
        WarmFloorObservation(721, 23, 23, profile.on_seconds, profile.off_seconds),
        state,
    ).maintenance_on
    assert not advance_warm_floor(
        WarmFloorObservation(
            profile.on_seconds, 23, 23, profile.on_seconds, profile.off_seconds
        ),
        state,
    ).maintenance_on


def test_recovery_discards_the_old_stable_temperature_after_a_new_drop():
    state = replace(
        recovering().state,
        stable_temperature=23.1,
        stable_since=1000,
        reason="balancing",
        last_report=FloorReport(3600, 23.1),
    )
    cooling = advance_warm_floor_policy(inputs(), state, FloorReport(4500, 23.0))
    assert cooling.state.stable_temperature is None
    stable = advance_warm_floor_policy(
        inputs(slow_slope_kelvin_per_min=0), cooling.state, FloorReport(5400, 23.0)
    )
    assert stable.reason == "stabilizing"
    assert stable.state.stable_temperature == 23.0


def test_return_from_warming_reports_observing_until_new_cooling_evidence():
    warming = advance_warm_floor_policy(
        inputs(slope_kelvin_per_min=0.003), recovering().state, None
    )
    resumed = advance_warm_floor_policy(inputs(), warming.state, None)
    assert resumed.reason == "observing"


@pytest.mark.parametrize("level", LEVELS)
def test_only_auto_retains_its_existing_maximum_pause(level):
    result = advance_warm_floor_policy(
        inputs(24, level=level, heat_loss=0.0001, slow_slope_kelvin_per_min=0),
        WarmFloorPolicyState(),
        None,
    )
    if level == "auto":
        assert result.profile.off_seconds == 3600
    else:
        assert result.profile.off_seconds > 3600
