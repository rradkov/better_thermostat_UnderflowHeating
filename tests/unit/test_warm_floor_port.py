"""Warm Floor must preserve targets, channel semantics and sparse readings."""

from dataclasses import replace
from datetime import UTC, datetime

from homeassistant.components.climate.const import HVACAction
import pytest

from custom_components.better_thermostat.core.decide import running_kernel_state
from custom_components.better_thermostat.core.desired import Suppression, TrvDesired
from custom_components.better_thermostat.core.fsm.control_mode import (
    ControlMode,
    ControlModeState,
)
from custom_components.better_thermostat.core.snapshot import HvacMode, WorldSnapshot
from custom_components.better_thermostat.core.warm_floor import (
    WarmFloorPhase,
    WarmFloorState,
    WarmFloorThermalInput,
    compute_warm_floor_profile,
)
from custom_components.better_thermostat.utils.calibration.mpc import (
    MpcState,
    build_mpc_group_key,
    build_mpc_key,
)
from custom_components.better_thermostat.utils.const import CalibrationMode
from custom_components.better_thermostat.utils.entry_schema import parse_settings
from custom_components.better_thermostat.utils.state_manager import StateManager
from custom_components.better_thermostat.utils.underfloor import (
    build_warm_floor_fields,
    finite_number,
    migrate_fork_settings,
    normalize_warm_floor,
)
from custom_components.better_thermostat.utils.warm_floor_control import (
    apply_warm_floor,
    record_warm_floor_write,
)
from tests.factories import make_bt, make_calibration_balance, make_trv


def host_and_snapshot(hass, *, generic=True):
    trv = make_trv("climate.loop")
    trv.advanced.update(heating_type="underfloor", warm_floor_level="level_2")
    trv.current_temperature = 23.4
    trv.target_temp_step = 0.1
    host = make_bt(heat_target_temperature=23.0, room_temperature=23.4)
    host.real_trvs = {trv.entity_id: trv}
    host.hass = hass
    host.warm_floor_enabled = True
    host.warm_floor_water_sensor = None
    host.warm_floor_water_temperature = None
    host.kernel_state = running_kernel_state()
    host.hvac_action = HVACAction.IDLE
    host.heat_loss_rate = 0.01
    host.heating_power = 0.05
    host.all_trvs = [
        {
            "trv": trv.entity_id,
            "integration": "generic_thermostat" if generic else "mqtt",
        }
    ]
    hass.states.async_set(trv.entity_id, "heat", {"hvac_action": "idle"})
    snapshot = WorldSnapshot(
        datetime(2026, 11, 14, tzinfo=UTC), 1000.0, 23.0, room_temperature=23.4
    )
    return host, trv, snapshot, TrvDesired(trv.entity_id, hvac_mode=HvacMode.HEAT)


@pytest.mark.parametrize("step", [0.1, 0.5, 1.0])
def test_pulse_and_release_cover_different_grids_and_sensors(hass, step):
    host, trv, snapshot, desired = host_and_snapshot(hass)
    trv.target_temp_step = step
    trv.current_temperature = 23.7
    trv.warm_floor_state = WarmFloorState(WarmFloorPhase.ON, 950.0)
    temperature, _ = apply_warm_floor(
        host, trv.entity_id, snapshot, desired, 23.0, None
    )
    assert temperature is not None and temperature > 23.7
    assert temperature / step == pytest.approx(round(temperature / step))
    assert snapshot.heat_target_temperature == 23.0
    trv.warm_floor_state = WarmFloorState(WarmFloorPhase.OFF, 950.0)
    temperature, _ = apply_warm_floor(
        host, trv.entity_id, snapshot, desired, temperature, None
    )
    assert temperature is not None and temperature < 23.0
    assert trv.warm_floor_status["reported_heating"] is False
    assert trv.warm_floor_status["write_result"] == "pending"


def test_sparse_valid_reading_keeps_a_timed_cycle(hass):
    host, trv, snapshot, desired = host_and_snapshot(hass)
    snapshot = replace(snapshot, now_monotonic=3600.0)
    trv.warm_floor_state = WarmFloorState(WarmFloorPhase.OFF, 0.0)
    first, _ = apply_warm_floor(host, trv.entity_id, snapshot, desired, 23.0, None)
    assert first is not None and first > 23.4
    later = replace(snapshot, now_monotonic=4600.0)
    second, _ = apply_warm_floor(host, trv.entity_id, later, desired, first, None)
    assert second is not None and second < 23.0


@pytest.mark.parametrize(
    "mode,suppression",
    [
        (HvacMode.OFF, None),
        (HvacMode.HEAT, Suppression.WINDOW),
        (HvacMode.HEAT, Suppression.DOOR),
    ],
)
def test_upstream_blocks_are_not_overridden(hass, mode, suppression):
    host, trv, snapshot, desired = host_and_snapshot(hass)
    trv.warm_floor_state = WarmFloorState(WarmFloorPhase.ON, 950.0)
    assert apply_warm_floor(
        host,
        trv.entity_id,
        snapshot,
        replace(desired, hvac_mode=mode, suppression=suppression),
        7.0,
        None,
    ) == (7.0, None)
    assert trv.warm_floor_status["phase"] is None


def test_hold_never_begins_a_pulse(hass):
    host, trv, snapshot, desired = host_and_snapshot(hass)
    host.kernel_state = replace(
        host.kernel_state, control_mode=ControlModeState(mode=ControlMode.HOLD)
    )
    assert apply_warm_floor(host, trv.entity_id, snapshot, desired, 23.0, None) == (
        23.0,
        None,
    )
    assert trv.warm_floor_status["reason"] == "hold"


@pytest.mark.parametrize(
    "enabled,heating_type", [(False, "underfloor"), (True, "radiator")]
)
def test_other_rooms_retain_their_original_commands(hass, enabled, heating_type):
    host, trv, snapshot, desired = host_and_snapshot(hass)
    host.warm_floor_enabled = enabled
    trv.advanced["heating_type"] = heating_type
    assert apply_warm_floor(host, trv.entity_id, snapshot, desired, 15.0, 2.0) == (
        15.0,
        2.0,
    )


def test_below_target_belongs_to_normal_heating(hass):
    host, trv, snapshot, desired = host_and_snapshot(hass)
    assert apply_warm_floor(
        host,
        trv.entity_id,
        replace(snapshot, room_temperature=22.0),
        desired,
        29.0,
        None,
    ) == (29.0, None)


def test_offset_floor_keeps_the_original_channel_direction(hass):
    host, trv, snapshot, desired = host_and_snapshot(hass, generic=False)
    _, calibration = apply_warm_floor(host, trv.entity_id, snapshot, desired, 15.0, 7.0)
    assert calibration is not None and calibration < 0.0


@pytest.mark.parametrize("level", ["level_1", "level_2", "level_3", "auto", "custom"])
def test_levels_keep_maintenance_above_target(level):
    profile = compute_warm_floor_profile(WarmFloorThermalInput(24.0, 23.0, level=level))
    assert profile.on_seconds >= 240.0
    assert profile.off_seconds >= 240.0


def test_warming_extends_pause_and_cooling_increases_maintenance():
    base = WarmFloorThermalInput(24.0, 23.0)
    neutral = compute_warm_floor_profile(base)
    warming = compute_warm_floor_profile(replace(base, slope_kelvin_per_min=0.03))
    cooling = compute_warm_floor_profile(replace(base, slope_kelvin_per_min=-0.02))
    assert warming.off_seconds > neutral.off_seconds
    assert cooling.intensity >= neutral.intensity
    assert (
        compute_warm_floor_profile(replace(base, room_temperature=25.0)).off_seconds
        > neutral.off_seconds
    )


def test_insulation_tightens_backoff_and_solar_reduces_demand():
    base = WarmFloorThermalInput(
        23.0, 23.0, heat_loss=0.02, heating_power=0.05, outdoor_temperature=5.0
    )
    plain = compute_warm_floor_profile(base)
    assert (
        compute_warm_floor_profile(replace(base, insulation=0.01)).structural_scale
        <= plain.structural_scale
    )
    assert (
        compute_warm_floor_profile(replace(base, solar_gain=0.02)).intensity
        <= plain.intensity
    )
    assert (
        compute_warm_floor_profile(replace(base, flow_temperature=20.0)).intensity
        >= plain.intensity
    )


@pytest.mark.parametrize(
    "value", [None, {}, True, "invalid", float("nan"), float("inf")]
)
def test_invalid_settings_do_not_create_nonfinite_commands(value):
    assert finite_number(value, 1.5) == 1.5


def test_legacy_settings_migrate_without_secondary_boost():
    raw = {
        "name": "Mixed room",
        "temperature_sensor": "sensor.room",
        "underfloor_heating_enabled": True,
        "boost_heater_enabled": True,
        "boost_threshold": 5.0,
        "presets": ["boost"],
        "cooler": "climate.cooler",
        "thermostat": [
            {
                "trv": "climate.floor",
                "integration": "generic_thermostat",
                "advanced": {
                    "heating_type": "underfloor",
                    "warm_floor_level": "custom",
                    "warm_floor_max_backoff": 0.8,
                    "warm_floor_sustain_push": 0.4,
                },
            }
        ],
    }
    cleaned = migrate_fork_settings(raw)
    parsed = parse_settings(cleaned)
    assert "boost_heater_enabled" not in cleaned
    assert "boost_threshold" not in cleaned
    assert parsed["presets"] == ["boost"]
    assert parsed["cooler"] == "climate.cooler"
    advanced = parsed["thermostat"][0]["advanced"]
    assert advanced["warm_floor_max_backoff"] == 0.8
    assert advanced["warm_floor_sustain_push"] == 0.4
    assert "boost_heater_enabled" in raw


def test_fields_preserve_levels_without_an_actuator_type():
    fields = build_warm_floor_fields(lambda key, default: default)
    names = {str(key) for key in fields}
    assert names == {
        "heating_type",
        "warm_floor_level",
        "warm_floor_max_backoff",
        "warm_floor_sustain_push",
    }
    assert (
        normalize_warm_floor({"warm_floor_level": "bad"})["warm_floor_level"]
        == "level_2"
    )


@pytest.mark.parametrize("group", [False, True])
@pytest.mark.parametrize("learned", [False, True])
def test_mpc_uses_available_thermal_estimates(hass, group, learned):
    host, trv, snapshot, desired = host_and_snapshot(hass, generic=False)
    if group:
        host.real_trvs["climate.second"] = make_trv("climate.second")
    host.state_mgr = StateManager(hass, "warm-floor-unit")
    host._unique_id = "warm-floor-unit"
    key = build_mpc_group_key(host) if group else build_mpc_key(host, trv.entity_id)
    host.state_mgr.set_mpc(
        key,
        MpcState(
            loss_est=0.03 if learned else None,
            gain_est=0.08 if learned else None,
            ka_est=0.001 if learned else None,
            solar_gain_est=0.02 if learned else None,
        ),
    )
    trv.calibration_balance = make_calibration_balance(
        CalibrationMode.MPC_CALIBRATION, apply_valve=True
    )
    apply_warm_floor(
        host, trv.entity_id, replace(snapshot, solar_intensity=1.0), desired, 15.0, None
    )
    assert trv.calibration_balance["valve_percent"] > 0
    thermal = trv.warm_floor_status["thermal_inputs"]
    assert isinstance(thermal, dict)
    assert thermal["solar_gain"] == (0.02 if learned else None)


@pytest.mark.parametrize("time_constant", [480.0, 0.0, float("nan")])
def test_mpc_v2_contributes_disturbance_and_usable_time_constant(hass, time_constant):
    host, trv, snapshot, desired = host_and_snapshot(hass, generic=False)
    trv.calibration_balance = make_calibration_balance(
        CalibrationMode.MPC_V2_CALIBRATION,
        {"tau_room_min": time_constant, "D_hat_K_per_min": 0.01},
    )
    apply_warm_floor(host, trv.entity_id, snapshot, desired, 15.0, None)
    thermal = trv.warm_floor_status["thermal_inputs"]
    assert isinstance(thermal, dict)
    assert thermal["disturbance"] == 0.01
    assert thermal["insulation"] == (1 / 480.0 if time_constant == 480.0 else None)


@pytest.mark.parametrize(
    "state,expected", [("0", 0.0), ("35.5", 35.5), ("unavailable", 40.0), (None, 40.0)]
)
def test_optional_water_sensor_uses_static_only_when_needed(hass, state, expected):
    host, trv, snapshot, desired = host_and_snapshot(hass)
    host.warm_floor_water_sensor = "sensor.flow"
    host.warm_floor_water_temperature = 40.0
    if state is not None:
        hass.states.async_set("sensor.flow", state, {"unit_of_measurement": "°C"})
    apply_warm_floor(host, trv.entity_id, snapshot, desired, 23.0, None)
    thermal = trv.warm_floor_status["thermal_inputs"]
    assert isinstance(thermal, dict)
    assert thermal["flow_temperature"] == expected


@pytest.mark.parametrize("temperature", [None, float("nan")])
def test_unusable_room_temperature_does_not_start_maintenance(hass, temperature):
    host, trv, snapshot, desired = host_and_snapshot(hass)
    apply_warm_floor(
        host,
        trv.entity_id,
        replace(snapshot, room_temperature=temperature),
        desired,
        23.0,
        None,
    )
    assert trv.warm_floor_status["reason"] == "hold"


def test_sensor_fallback_uses_the_upstream_effective_temperature(hass):
    host, trv, snapshot, desired = host_and_snapshot(hass)
    host.kernel_state = replace(
        host.kernel_state,
        control_mode=ControlModeState(mode=ControlMode.SENSOR_FALLBACK),
    )
    host.room_temperature = 23.4
    apply_warm_floor(
        host,
        trv.entity_id,
        replace(snapshot, room_temperature=None),
        desired,
        23.0,
        None,
    )
    assert trv.warm_floor_status["reason"] != "hold"


def test_unknown_feedback_is_not_relabelled_as_physical_off(hass):
    host, trv, snapshot, desired = host_and_snapshot(hass)
    hass.states.async_remove(trv.entity_id)
    apply_warm_floor(host, trv.entity_id, snapshot, desired, 23.0, None)
    assert trv.warm_floor_status["reported_heating"] is None


def test_absolute_ceiling_releases_an_old_heating_request(hass):
    host, trv, snapshot, desired = host_and_snapshot(hass)
    result, _ = apply_warm_floor(
        host,
        trv.entity_id,
        replace(snapshot, room_temperature=29.0),
        desired,
        30.0,
        None,
    )
    assert result == 23.0
    assert trv.warm_floor_status["reason"] == "temperature_ceiling"


def test_device_limits_are_visible_when_a_pulse_cannot_be_reached(hass):
    host, trv, snapshot, desired = host_and_snapshot(hass)
    trv.warm_floor_state = WarmFloorState(WarmFloorPhase.ON, 950.0)
    trv.current_temperature = 30.0
    apply_warm_floor(host, trv.entity_id, snapshot, desired, 23.0, None)
    assert trv.warm_floor_status["reason"] == "device_limit"


def test_optional_push_applies_without_replacing_the_room_target(hass):
    host, trv, snapshot, desired = host_and_snapshot(hass, generic=False)
    trv.advanced.update(warm_floor_level="custom", warm_floor_sustain_push=0.4)
    temperature, _ = apply_warm_floor(
        host, trv.entity_id, snapshot, desired, 23.0, None
    )
    assert temperature is not None and temperature > 23.0
    trv.current_temperature = None
    assert apply_warm_floor(host, trv.entity_id, snapshot, desired, None, None) == (
        None,
        None,
    )


@pytest.mark.parametrize(
    "result", ["sent", "held", "failed", "pending", "not_requested"]
)
def test_write_outcome_is_separate_from_physical_feedback(result):
    trv = make_trv()
    record_warm_floor_write(trv, "setpoint", result, 24.0)
    assert trv.warm_floor_status == {}
    trv.warm_floor_status = {"reported_heating": None}
    record_warm_floor_write(trv, "setpoint", result, 24.0)
    assert trv.warm_floor_status["write_result"] == result
    assert trv.warm_floor_status["reported_heating"] is None


def test_failed_or_repeated_writes_do_not_restart_the_phase_timer():
    trv = make_trv()
    trv.warm_floor_status = {"phase": "on", "on_seconds": 300.0}
    trv.warm_floor_state = WarmFloorState(WarmFloorPhase.ON, 10.0)
    record_warm_floor_write(trv, "setpoint", "failed", 24.0, 100.0)
    assert trv.warm_floor_state.started_monotonic == 10.0
    record_warm_floor_write(trv, "setpoint", "sent", 24.0, 200.0)
    assert trv.warm_floor_state.started_monotonic == 200.0
    record_warm_floor_write(trv, "setpoint", "held", 24.0, 250.0)
    assert trv.warm_floor_state.started_monotonic == 200.0
    trv.warm_floor_status["phase"] = "sustaining"
    record_warm_floor_write(trv, "setpoint", "held", 24.0, 300.0)
    assert trv.warm_floor_state.started_monotonic == 200.0
    trv.warm_floor_status["phase"] = None
    record_warm_floor_write(trv, "setpoint", "sent", 23.0, 350.0)
    assert trv.warm_floor_commanded_phase is None
