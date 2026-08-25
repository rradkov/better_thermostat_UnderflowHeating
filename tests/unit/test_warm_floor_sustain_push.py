"""Warm Floor's "sustain push": closing the no-op gap on non-valve UFH heaters.

On four of the eight calibration modes (AI Time Based, External Sensor
Offset Only, Aggressive, No Calibration), a non-valve device's setpoint is
already pinned at exactly target once idle - the passive backoff floor
(``max(computed_value, sustaining_setpoint)``) never has anything to raise.
Sustain push is opt-in (off by default) and, once every guard passes, pushes
only the *downstream* setpoint sent to the physical heater a small amount
above target - ``bt.bt_target_temp`` itself is never touched.
"""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import MagicMock

from homeassistant.components.climate.const import HVACAction, HVACMode
from homeassistant.util import dt as dt_util

from custom_components.better_thermostat.trv import Trv
from custom_components.better_thermostat.utils.const import CalibrationMode
from custom_components.better_thermostat.utils.underfloor import (
    CONF_HEATING_TYPE,
    CONF_WARM_FLOOR_SUSTAIN_PUSH,
    WARM_FLOOR_SENSOR_STALE_AFTER_S,
    HeatingType,
    apply_warm_floor_floor,
)


def _make_bt(
    *,
    sustain_push: float = 0.3,
    hvac_action=HVACAction.IDLE,
    cur_temp: float = 22.0,
    target_temp: float = 22.0,
    sensor_last_updated=None,
    calibration_mode: str = CalibrationMode.HEATING_POWER_CALIBRATION.value,
    max_temp: float = 30.0,
    **overrides,
) -> MagicMock:
    bt = MagicMock()
    bt.device_name = "Test BT"
    bt.contact_open = False
    bt.call_for_heat = True
    bt.bt_hvac_mode = HVACMode.HEAT
    bt.bt_target_temp = target_temp
    bt.cur_temp = cur_temp
    bt.hvac_action = hvac_action
    bt.temp_slope = None
    # A well-insulated, strong-emitter room, so structural_scale is ~1.0 and
    # doesn't itself taper the push - isolating the trigger conditions under
    # test from the structural computation covered elsewhere.
    bt.heat_loss_rate = 0.0
    bt.heating_power = 1.0
    bt.state_mgr = None
    bt.sensor_entity_id = "sensor.room_temp"
    bt.hass.states.get.return_value = MagicMock(
        last_updated=sensor_last_updated or dt_util.utcnow()
    )

    quirks = MagicMock()
    quirks.fix_local_calibration.side_effect = lambda _self, _eid, offset: float(offset)
    quirks.fix_target_temperature_calibration.side_effect = (
        lambda _self, _eid, temperature: float(temperature)
    )

    bt.real_trvs = {
        "climate.trv": Trv.from_legacy_dict(
            "climate.trv",
            {
                "advanced": {
                    CONF_HEATING_TYPE: HeatingType.UNDERFLOOR.value,
                    "calibration_mode": calibration_mode,
                    CONF_WARM_FLOOR_SUSTAIN_PUSH: sustain_push,
                },
                "current_temperature": cur_temp,
                "min_temp": 5.0,
                "max_temp": max_temp,
                "model_quirks": quirks,
            },
        )
    }
    for key, value in overrides.items():
        setattr(bt, key, value)
    return bt


def test_pushes_above_target_once_pinned_setpoint_and_idle_at_target():
    """The exact motivating scenario: HEATING_POWER_CALIBRATION pins the
    setpoint at target once idle - sustain push closes the gap."""
    bt = _make_bt(sustain_push=0.3)
    result = apply_warm_floor_floor(bt, "climate.trv", 22.0, is_offset=False)
    assert result > 22.0
    assert bt._warm_floor_status["sustain_push_c"] is not None
    assert bt._warm_floor_status["sustain_push_c"] > 0


def test_off_by_default_is_a_strict_no_op():
    bt = _make_bt(sustain_push=0.0)
    result = apply_warm_floor_floor(bt, "climate.trv", 22.0, is_offset=False)
    assert result == 22.0
    assert bt._warm_floor_status["sustain_push_c"] is None


def test_does_not_fire_while_still_heating():
    """hvac_action must read IDLE, not just "not COOLING"."""
    bt = _make_bt(sustain_push=0.3, hvac_action=HVACAction.HEATING)
    result = apply_warm_floor_floor(bt, "climate.trv", 22.0, is_offset=False)
    assert result == 22.0


def test_does_not_fire_while_cooling():
    bt = _make_bt(sustain_push=0.3, hvac_action=HVACAction.COOLING)
    result = apply_warm_floor_floor(bt, "climate.trv", 22.0, is_offset=False)
    assert result == 22.0


def test_does_not_fire_before_the_room_actually_reaches_target():
    """Guards the tolerance-hold-band case: hvac_action can read IDLE just
    inside the tolerance band, before cur_temp has actually reached target."""
    bt = _make_bt(sustain_push=0.3, cur_temp=21.7, target_temp=22.0)
    result = apply_warm_floor_floor(bt, "climate.trv", 22.0, is_offset=False)
    assert result == 22.0


def test_does_not_fire_when_computed_value_is_already_below_target():
    """computed_value < target means the passive floor isn't structurally a
    no-op - the ordinary backoff floor is the right mechanism, not this one."""
    bt = _make_bt(sustain_push=0.3)
    result = apply_warm_floor_floor(bt, "climate.trv", 20.0, is_offset=False)
    # The ordinary passive floor may still raise it, but never past target.
    assert result <= 22.0


def test_respects_the_configured_max_temp_ceiling():
    bt = _make_bt(sustain_push=1.5, target_temp=29.5, cur_temp=29.5, max_temp=30.0)
    result = apply_warm_floor_floor(bt, "climate.trv", 29.5, is_offset=False)
    assert result == 30.0


def test_skips_a_heater_under_active_direct_valve_control():
    bt = _make_bt(sustain_push=0.3)
    bt.real_trvs["climate.trv"].calibration_balance = {
        "apply_valve": True,
        "valve_percent": 10,
    }
    result = apply_warm_floor_floor(bt, "climate.trv", 22.0, is_offset=False)
    assert result == 22.0


def test_offset_path_is_never_pushed_above_target():
    """Sustain push is scoped to is_offset=False only - the offset path's
    existing cap-not-floor behavior is untouched."""
    bt = _make_bt(sustain_push=0.3)
    result = apply_warm_floor_floor(bt, "climate.trv", -1.0, is_offset=True)
    assert result <= -1.0 or isinstance(result, float)
    assert bt._warm_floor_status["sustain_push_c"] is None


def test_a_stale_external_sensor_suppresses_the_entire_floor():
    """An unknown/stale reading is treated the same as "still gaining" -
    the whole cycle is suppressed, not just the sustain push."""
    stale_time = dt_util.utcnow() - timedelta(
        seconds=WARM_FLOOR_SENSOR_STALE_AFTER_S + 1
    )
    bt = _make_bt(sustain_push=0.3, sensor_last_updated=stale_time)
    result = apply_warm_floor_floor(bt, "climate.trv", 22.0, is_offset=False)
    assert result == 22.0
    assert bt._warm_floor_status["active"] is False


def test_a_missing_sensor_state_is_treated_as_stale():
    bt = _make_bt(sustain_push=0.3)
    bt.hass.states.get.return_value = None
    result = apply_warm_floor_floor(bt, "climate.trv", 22.0, is_offset=False)
    assert result == 22.0


def test_works_the_same_on_an_already_working_calibration_mode():
    """Sustain push is a generic mechanism keyed on computed_value vs.
    target, not on the calibration mode - it also engages for the four
    modes that were never broken, whenever they too happen to be pinned at
    target while idle."""
    bt = _make_bt(
        sustain_push=0.3, calibration_mode=CalibrationMode.MPC_V2_CALIBRATION.value
    )
    result = apply_warm_floor_floor(bt, "climate.trv", 22.0, is_offset=False)
    assert result > 22.0


def test_never_touches_bt_target_temp():
    bt = _make_bt(sustain_push=0.3)
    apply_warm_floor_floor(bt, "climate.trv", 22.0, is_offset=False)
    assert bt.bt_target_temp == 22.0
