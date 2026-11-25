"""Predictive BT demand owns maintenance; the relay port owns minimum cycles."""

from dataclasses import replace

import pytest

from custom_components.better_thermostat.core.desired import Suppression
from custom_components.better_thermostat.core.snapshot import HvacMode
from custom_components.better_thermostat.core.warm_floor import (
    WarmFloorObservation,
    WarmFloorPhase,
    WarmFloorProfile,
    WarmFloorState,
    advance_warm_floor,
    profile_for_model_demand,
)
from custom_components.better_thermostat.utils.calibration.mpc_v2 import (
    MpcV2Input,
    MpcV2Params,
    compute_mpc_v2,
)
from custom_components.better_thermostat.utils.calibration.mpc_v2_internals.plant import (
    PlantParams,
)
from custom_components.better_thermostat.utils.const import CalibrationMode
from custom_components.better_thermostat.utils.warm_floor_control import (
    apply_warm_floor,
)
from tests.factories import make_calibration_balance
from tests.unit.test_warm_floor_port import host_and_snapshot

LEVELS = ["auto", "level_1", "level_2", "level_3"]


def observation(now, demand, **changes):
    return replace(
        WarmFloorObservation(now, 23.3, 23, 1200, 1800, model_demand=demand), **changes
    )


@pytest.mark.parametrize("demand", [0.01, 0.2, 0.5, 0.9, 0.99])
def test_relay_duty_preserves_model_request_and_both_minimum_phases(demand):
    result = profile_for_model_demand(WarmFloorProfile(240, 3600, 0.5, 0.2), demand)
    assert result.on_seconds >= 240
    assert result.off_seconds >= 240 - 1e-9
    assert result.on_seconds / (
        result.on_seconds + result.off_seconds
    ) == pytest.approx(demand)
    assert result.intensity == demand


@pytest.mark.parametrize("demand", [0.0, 1.0])
def test_model_extremes_are_not_invented_minimum_loads(demand):
    result = profile_for_model_demand(WarmFloorProfile(1200, 1800, 0.5, 0.2), demand)
    assert result.intensity == demand


def test_new_predictive_demand_starts_above_target_after_minimum_pause():
    state = WarmFloorState(WarmFloorPhase.OFF, 0, model_demand=0)
    for now in (60, 120, 239):
        result = advance_warm_floor(observation(now, 0.3), state)
        state = result.state
        assert not result.maintenance_on
        assert state.started_monotonic == 0
    result = advance_warm_floor(observation(240, 0.3), state)
    assert result.maintenance_on
    assert result.state.started_monotonic == 240


def test_subsequent_pause_keeps_the_requested_duty_instead_of_restarting_early():
    state = WarmFloorState(WarmFloorPhase.ON, 0, model_demand=0.3)
    end = advance_warm_floor(observation(1200, 0.3), state)
    assert not end.maintenance_on
    before = advance_warm_floor(observation(1500, 0.3), end.state)
    assert not before.maintenance_on
    assert before.remaining_seconds == 1500


def test_model_coasting_ends_a_pulse_early_but_respects_minimum_on():
    state = WarmFloorState(WarmFloorPhase.ON, 0, model_demand=0.9)
    before = advance_warm_floor(observation(239, 0), state)
    assert before.maintenance_on
    stopped = advance_warm_floor(observation(240, 0), before.state)
    assert not stopped.maintenance_on
    later = advance_warm_floor(observation(100000, 0), stopped.state)
    assert not later.maintenance_on
    assert later.remaining_seconds is None


def test_full_model_demand_has_no_on_ceiling_or_forced_pause():
    state = WarmFloorState(WarmFloorPhase.OFF, 0, model_demand=0.2)
    started = advance_warm_floor(observation(240, 1), state)
    assert started.maintenance_on
    later = advance_warm_floor(observation(100000, 1), started.state)
    assert later.maintenance_on
    assert later.remaining_seconds is None


@pytest.mark.parametrize("demand", [-1, 2, float("nan"), float("inf")])
def test_invalid_model_demand_cannot_request_heat(demand):
    result = advance_warm_floor(observation(1000, demand), WarmFloorState())
    assert result.reason == "blocked"
    assert not result.maintenance_on


@pytest.mark.parametrize(
    "changes,reason",
    [
        ({"blocked": True}, "blocked"),
        ({"hold": True}, "hold"),
        ({"normal_heating": True}, "normal_heating"),
        ({"room_temperature": 22}, "normal_heating"),
    ],
)
def test_model_demand_never_bypasses_safety_or_normal_bt(changes, reason):
    result = advance_warm_floor(observation(1000, 1, **changes), WarmFloorState())
    assert result.reason == reason
    assert not result.maintenance_on


@pytest.mark.parametrize("demand", [0, 0.3, 1])
def test_startup_does_not_invent_elapsed_off_time(demand):
    initial = advance_warm_floor(observation(1000, demand), WarmFloorState())
    assert not initial.maintenance_on
    assert initial.state.started_monotonic == 1000
    later = advance_warm_floor(observation(1240, demand), initial.state)
    assert later.maintenance_on == (demand > 0)


@pytest.mark.parametrize("level", LEVELS)
@pytest.mark.parametrize("tau_emitter", [5.0, 90.0])
@pytest.mark.parametrize("hot", [False, True])
def test_actual_bt_model_drives_fast_and_slow_installations(
    hass, level, tau_emitter, hot
):
    host, trv, snapshot, desired = host_and_snapshot(hass)
    trv.advanced["warm_floor_level"] = level
    params = MpcV2Params(
        plant=PlantParams(tau_room_min=480, tau_rad_min=tau_emitter, T_water=50)
    )
    output, state = compute_mpc_v2(
        MpcV2Input("test", 23, 23.4, 48 if hot else 23.4, 5), params, now=1700000000
    )
    assert output is not None
    percent = output.valve_percent
    # Both cold emitters need heat before the room drops. The slow hot emitter
    # coasts on stored heat; the fast one loses it quickly and needs more input.
    assert (percent == 0) == (hot and tau_emitter == 90)
    assert state.controller is not None
    unchanged = state.controller.export_snapshot().to_mapping()
    trv.calibration_balance = make_calibration_balance(
        CalibrationMode.MPC_V2_CALIBRATION,
        {"tau_room_min": 480, "D_hat_K_per_min": 0},
        valve_percent=percent,
    )
    trv.warm_floor_state = WarmFloorState(WarmFloorPhase.OFF, 0, model_demand=0)
    command, _ = apply_warm_floor(host, trv.entity_id, snapshot, desired, 23, None)
    assert command is not None
    assert (command > 23.4) == (percent > 0)
    assert trv.warm_floor_status["model_demand_percent"] == percent
    assert trv.calibration_balance["valve_percent"] == percent
    assert state.controller.export_snapshot().to_mapping() == unchanged


@pytest.mark.parametrize("level", LEVELS)
@pytest.mark.parametrize("percent", [0, 35, 100])
def test_native_channels_keep_the_original_predictive_command(hass, level, percent):
    host, trv, snapshot, desired = host_and_snapshot(hass, generic=False)
    trv.advanced["warm_floor_level"] = level
    trv.calibration_balance = make_calibration_balance(
        CalibrationMode.MPC_V2_CALIBRATION,
        {"tau_room_min": 480, "D_hat_K_per_min": 0},
        valve_percent=percent,
        apply_valve=True,
    )
    assert apply_warm_floor(host, trv.entity_id, snapshot, desired, 22, -2) == (22, -2)
    assert trv.calibration_balance["valve_percent"] == percent
    assert trv.warm_floor_status["phase"] == "model"


@pytest.mark.parametrize("percent", [-1, 101, float("nan"), float("inf")])
def test_unusable_model_output_keeps_existing_fallback(hass, percent):
    host, trv, snapshot, desired = host_and_snapshot(hass)
    trv.calibration_balance = make_calibration_balance(
        CalibrationMode.MPC_V2_CALIBRATION,
        {"tau_room_min": 480, "D_hat_K_per_min": 0},
        valve_percent=percent,
    )
    apply_warm_floor(host, trv.entity_id, snapshot, desired, 23, None)
    assert trv.warm_floor_status["model_demand_percent"] is None


@pytest.mark.parametrize("block", ["off", "window", "door", "hold", "normal"])
def test_full_model_request_preserves_upstream_commands(hass, block):
    host, trv, snapshot, desired = host_and_snapshot(hass)
    trv.calibration_balance = make_calibration_balance(
        CalibrationMode.MPC_V2_CALIBRATION,
        {"tau_room_min": 480, "D_hat_K_per_min": 0},
        valve_percent=100,
    )
    if block == "off":
        desired = replace(desired, hvac_mode=HvacMode.OFF)
    elif block in ("window", "door"):
        desired = replace(desired, suppression=Suppression(block))
    else:
        snapshot = replace(snapshot, room_temperature=None if block == "hold" else 22)
    assert apply_warm_floor(host, trv.entity_id, snapshot, desired, 15, -2) == (15, -2)
    assert trv.warm_floor_status["phase"] is None
    assert trv.warm_floor_status["reason"] == (
        "hold"
        if block == "hold"
        else "normal_heating"
        if block == "normal"
        else "blocked"
    )
