"""Auto must use observed cooling, then cautiously search for equilibrium."""

from dataclasses import replace

import pytest

from custom_components.better_thermostat.core.warm_floor import (
    WarmFloorThermalInput,
    compute_warm_floor_profile,
)
from custom_components.better_thermostat.core.warm_floor_auto import (
    FloorReport,
    WarmFloorAutoState,
    advance_warm_floor_auto,
)


def inputs(room=23.0, **changes):
    return replace(WarmFloorThermalInput(room, 23.0, level="auto"), **changes)


def recovering():
    first = advance_warm_floor_auto(
        inputs(23.1), WarmFloorAutoState(), FloorReport(0, 23.1)
    )
    return advance_warm_floor_auto(inputs(), first.state, FloorReport(3600, 23.0))


@pytest.mark.parametrize("end", [23.0, 23.1, 23.2])
def test_slow_hourly_cooling_at_or_near_target_selects_keep_it_hot(end):
    first = advance_warm_floor_auto(
        inputs(end + 0.1), WarmFloorAutoState(), FloorReport(0, end + 0.1)
    )
    decision = advance_warm_floor_auto(inputs(end), first.state, FloorReport(3600, end))
    assert decision.policy == "level_3"
    assert decision.reason == "recovering"
    assert decision.slow_slope == pytest.approx(-0.1 / 60)
    assert decision.profile.intensity >= 0.8
    baseline = compute_warm_floor_profile(replace(inputs(end), level="level_2"))
    assert decision.profile.on_seconds > baseline.on_seconds
    assert decision.profile.off_seconds < baseline.off_seconds


def test_old_report_and_control_ticks_cannot_confirm_stability_or_reduce_heat():
    decision = recovering()
    for _ in range(240):
        decision = advance_warm_floor_auto(
            inputs(), decision.state, FloorReport(3600, 23.0)
        )
    assert len(decision.state.reports) == 2
    assert decision.reason == "recovering"
    assert decision.state.stable_since is None
    assert decision.profile.intensity >= 0.8


def test_new_stable_measurements_hold_then_reduce_in_small_steps():
    decision = recovering()
    stable = advance_warm_floor_auto(inputs(), decision.state, FloorReport(7200, 23.0))
    assert stable.reason == "stabilizing"
    assert stable.profile.intensity == decision.profile.intensity
    waiting = advance_warm_floor_auto(inputs(), stable.state, FloorReport(7800, 23.0))
    assert waiting.reason == "stabilizing"
    reduced = advance_warm_floor_auto(inputs(), waiting.state, FloorReport(9000, 23.0))
    assert reduced.reason == "balancing"
    assert reduced.profile.intensity == pytest.approx(decision.profile.intensity - 0.05)
    too_soon = advance_warm_floor_auto(inputs(), reduced.state, FloorReport(9300, 23.0))
    assert too_soon.profile.intensity == reduced.profile.intensity
    later = advance_warm_floor_auto(inputs(), too_soon.state, FloorReport(9900, 23.0))
    assert later.profile.intensity == pytest.approx(reduced.profile.intensity - 0.05)


def test_cooling_after_a_reduction_restores_strong_maintenance():
    state = WarmFloorAutoState(
        23.0, (FloorReport(0, 23.1),), 0.65, 0, 0, 0.7, "balancing"
    )
    decision = advance_warm_floor_auto(inputs(), state, FloorReport(3600, 23.0))
    assert decision.reason == "cooling_returned"
    assert decision.policy == "level_3"
    assert decision.profile.intensity >= 0.8
    assert decision.state.stable_since is None


@pytest.mark.parametrize(
    "room,fast,reason", [(23.21, 0, "above_band"), (23.1, 0.006, "warming")]
)
def test_room_margin_and_warming_release_recovery(room, fast, reason):
    decision = advance_warm_floor_auto(
        inputs(room, slope_kelvin_per_min=fast), recovering().state, None
    )
    assert decision.policy == "level_1"
    assert decision.reason == reason
    assert decision.state.intensity is None
    resumed = advance_warm_floor_auto(inputs(), decision.state, None)
    assert resumed.reason == "observing"


def test_observed_slow_warming_does_not_wait_for_the_short_filter():
    state = WarmFloorAutoState(23.0, (FloorReport(0, 23.0),), 0.8)
    decision = advance_warm_floor_auto(inputs(23.1), state, FloorReport(3600, 23.1))
    assert decision.policy == "level_1"
    assert decision.reason == "warming"


@pytest.mark.parametrize("eligible,room", [(False, 23.0), (True, 22.9)])
def test_normal_heating_keeps_ownership_below_target(eligible, room):
    decision = advance_warm_floor_auto(
        inputs(room), recovering().state, None, eligible=eligible
    )
    assert decision.reason == "normal_heating"
    assert decision.profile == replace(
        compute_warm_floor_profile(inputs(room, level="level_2")),
        off_seconds=min(
            3600, compute_warm_floor_profile(inputs(room, level="level_2")).off_seconds
        ),
    )


def test_target_changes_discard_the_previous_adaptation():
    decision = advance_warm_floor_auto(
        inputs(24.0, target_temperature=24.0),
        recovering().state,
        FloorReport(4000, 24.0),
    )
    assert decision.state.intensity is None
    assert len(decision.state.reports) == 1
    assert decision.slow_slope is None


@pytest.mark.parametrize(
    "report",
    [
        None,
        FloorReport(float("nan"), 23.0),
        FloorReport(100, float("inf")),
        FloorReport(-1, 23.0),
        FloorReport(20, 23.1),
    ],
)
def test_missing_invalid_old_or_too_close_reports_are_not_new_evidence(report):
    state = WarmFloorAutoState(23.0, (FloorReport(0, 23.1),))
    decision = advance_warm_floor_auto(inputs(23.1), state, report)
    assert decision.state.reports == state.reports
    assert decision.slow_slope is None


def test_long_gaps_do_not_prove_a_current_cooling_trend():
    state = WarmFloorAutoState(23.0, (FloorReport(0, 23.2),))
    decision = advance_warm_floor_auto(inputs(), state, FloorReport(10800, 23.0))
    assert decision.slow_slope is None
    assert decision.reason == "observing"


def test_history_is_bounded_and_old_anchors_age_out():
    state = WarmFloorAutoState()
    for timestamp in range(0, 30000, 60):
        state = advance_warm_floor_auto(
            inputs(), state, FloorReport(timestamp, 23.0)
        ).state
    assert len(state.reports) <= 122
    assert state.reports[-1].timestamp - state.reports[0].timestamp <= 7260


def test_stability_must_restart_if_measured_temperature_varies():
    state = WarmFloorAutoState(23.0, (FloorReport(0, 23.2),), 0.8, 0, 0)
    result = advance_warm_floor_auto(inputs(23.1), state, FloorReport(1200, 23.1))
    assert result.state.stable_since is None


@pytest.mark.parametrize(
    "strength,policy",
    [(0.7, "level_3"), (0.5, "level_2"), (0.25, "level_1"), (0.2, "level_1")],
)
def test_balancing_selects_the_real_policy_and_stops_at_its_lower_bound(
    strength, policy
):
    state = WarmFloorAutoState(23.0, (FloorReport(0, 23.0),), strength, 0, 0)
    decision = advance_warm_floor_auto(inputs(), state, None)
    assert decision.policy == policy
    assert decision.profile.intensity == strength
    later = advance_warm_floor_auto(inputs(), state, FloorReport(3600, 23.0))
    assert later.profile.intensity >= 0.2
    if strength == 0.2:
        assert later.reason == "maintaining"


def test_auto_off_cap_retains_all_device_minimums():
    decision = advance_warm_floor_auto(
        inputs(27.9, slope_kelvin_per_min=0.05), WarmFloorAutoState(), None
    )
    assert decision.profile.off_seconds == 3600
    assert decision.profile.on_seconds >= 240


def test_a_changed_report_within_the_minute_updates_the_observed_trend():
    state = WarmFloorAutoState(23.0, (FloorReport(0, 23.1), FloorReport(3600, 23.05)))
    decision = advance_warm_floor_auto(inputs(), state, FloorReport(3620, 23.0))
    assert len(decision.state.reports) == 2
    assert decision.state.reports[-1].temperature == 23.0
    assert decision.reason == "recovering"


def test_frequent_changed_reports_keep_enough_history_for_slow_cooling():
    state = WarmFloorAutoState()
    for timestamp in range(3601):
        temperature = 23.1 - 0.1 * timestamp / 3600
        decision = advance_warm_floor_auto(
            inputs(temperature), state, FloorReport(timestamp, temperature)
        )
        state = decision.state
    assert len(state.reports) <= 61
    assert decision.slow_slope is not None and decision.slow_slope < 0
    assert decision.policy == "level_3"
