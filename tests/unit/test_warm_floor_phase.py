"""Requirements for maintenance phase timing independent of device IO."""

from dataclasses import replace

from custom_components.better_thermostat.core.warm_floor import (
    WarmFloorObservation,
    WarmFloorPhase,
    WarmFloorState,
    advance_warm_floor,
)


def observation(now=0.0, **changes):
    """Build a valid at-target maintenance observation."""
    return replace(WarmFloorObservation(now, 23.0, 23.0, 300.0, 600.0), **changes)


def test_new_reading_changes_deadline_without_restarting_phase():
    """Room updates adjust the remaining pause, preserving its elapsed time."""
    state = WarmFloorState(WarmFloorPhase.OFF, 0.0)
    decision = advance_warm_floor(observation(400.0, off_seconds=900.0), state)
    assert decision.state.started_monotonic == 0.0
    assert decision.remaining_seconds == 500.0


def test_sparse_reports_still_allow_timed_transitions():
    """An unchanged valid observation does not freeze maintenance scheduling."""
    state = WarmFloorState(WarmFloorPhase.OFF, 0.0)
    decision = advance_warm_floor(observation(600.0), state)
    assert decision.maintenance_on
    assert decision.state.started_monotonic == 600.0
    decision = advance_warm_floor(observation(900.0), decision.state)
    assert not decision.maintenance_on


def test_warm_room_retains_maintenance():
    """Above-target rooms can request a maintenance pulse after a long pause."""
    decision = advance_warm_floor(
        observation(3600.0, room_temperature=24.0, off_seconds=3600.0),
        WarmFloorState(WarmFloorPhase.OFF, 0.0),
    )
    assert decision.maintenance_on


def test_duration_updates_respect_actuator_minimum():
    """A shortened profile still gives a slow actuator time to travel."""
    state = WarmFloorState(WarmFloorPhase.ON, 0.0)
    decision = advance_warm_floor(observation(239.0, on_seconds=1.0), state)
    assert decision.maintenance_on
    assert decision.remaining_seconds == 1.0
    assert not advance_warm_floor(
        observation(240.0, on_seconds=1.0), state
    ).maintenance_on


def test_block_interrupts_pulse_without_repeatedly_resetting_close():
    """Upstream OFF and contact blocks take priority over the ON minimum."""
    state = WarmFloorState(WarmFloorPhase.ON, 0.0)
    stopped = advance_warm_floor(observation(10.0, blocked=True), state)
    assert not stopped.maintenance_on
    repeated = advance_warm_floor(observation(20.0, blocked=True), stopped.state)
    assert repeated.state.started_monotonic == 10.0


def test_hold_does_not_start_an_impulse():
    """No new maintenance starts when upstream has no usable temperature."""
    state = WarmFloorState(WarmFloorPhase.OFF, 0.0)
    decision = advance_warm_floor(observation(900.0, hold=True), state)
    assert not decision.maintenance_on
    assert decision.state == state


def test_below_target_hands_control_to_normal_heating():
    """Maintenance cannot replace the room controller's heating demand."""
    decision = advance_warm_floor(
        observation(900.0, room_temperature=22.9),
        WarmFloorState(WarmFloorPhase.ON, 0.0),
    )
    assert decision.reason == "normal_heating"
    assert not decision.maintenance_on


def test_restart_uses_feedback_with_a_full_minimum_interval():
    """A reported heating phase is not immediately reversed after restart."""
    first = advance_warm_floor(
        observation(1000.0, reported_heating=True), WarmFloorState()
    )
    assert first.maintenance_on
    assert first.state.recovered
    assert advance_warm_floor(observation(1239.0), first.state).maintenance_on
    assert not advance_warm_floor(observation(1240.0), first.state).maintenance_on


def test_unknown_feedback_does_not_confirm_physical_off():
    """Missing feedback starts a pause without asserting actuator position."""
    decision = advance_warm_floor(observation(), WarmFloorState())
    assert not decision.maintenance_on
    assert not decision.state.recovered


def test_circuits_do_not_share_phase_timing():
    """A second circuit retains its own deadline during the same room cycle."""
    first = advance_warm_floor(
        observation(600.0), WarmFloorState(WarmFloorPhase.OFF, 0.0)
    )
    second = advance_warm_floor(
        observation(600.0), WarmFloorState(WarmFloorPhase.OFF, 500.0)
    )
    assert first.maintenance_on
    assert not second.maintenance_on
    assert second.remaining_seconds == 500.0
