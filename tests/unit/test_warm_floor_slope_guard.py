"""Warm Floor's real-time temp_slope guard - the direct overshoot/undershoot check.

``temp_slope`` (already live on the BetterThermostat entity, EMA-derived)
overrides the slower structural/solar estimate: still-rising rooms never get
a floor-raise (would compound overshoot), and actively-falling rooms always
get the tightest floor available, regardless of what the structural numbers
alone would suggest.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from homeassistant.components.climate.const import HVACMode
from homeassistant.util import dt as dt_util

from custom_components.better_thermostat.trv import Trv
from custom_components.better_thermostat.utils.const import (
    MAX_HEAT_LOSS,
    MAX_HEATING_POWER,
    CalibrationMode,
)
from custom_components.better_thermostat.utils.underfloor import (
    CONF_HEATING_TYPE,
    OVERSHOOT_SLOPE_GUARD,
    UNDERSHOOT_SLOPE_GUARD,
    HeatingType,
    apply_warm_floor_floor,
)


def _make_bt(*, temp_slope, **overrides) -> MagicMock:
    bt = MagicMock()
    bt.device_name = "Test BT"
    bt.contact_open = False
    bt.call_for_heat = True
    bt.bt_hvac_mode = HVACMode.HEAT
    bt.bt_target_temp = 22.0
    bt.temp_slope = temp_slope
    # A very well-insulated, strong-emitter room, so the *structural* scale
    # alone would allow the full configured backoff - isolating the slope
    # guard's own effect from the structural computation.
    bt.heat_loss_rate = MAX_HEAT_LOSS * 0.01
    bt.heating_power = MAX_HEATING_POWER
    bt.state_mgr = None
    bt.sensor_entity_id = "sensor.room_temp"
    bt.hass.states.get.return_value = MagicMock(last_updated=dt_util.utcnow())
    bt.real_trvs = {
        "climate.trv": Trv.from_legacy_dict(
            "climate.trv",
            {
                "advanced": {
                    CONF_HEATING_TYPE: HeatingType.UNDERFLOOR.value,
                    "calibration_mode": CalibrationMode.HEATING_POWER_CALIBRATION.value,
                },
                "current_temperature": 20.0,
                "min_temp": 5.0,
                "max_temp": 30.0,
            },
        )
    }
    for key, value in overrides.items():
        setattr(bt, key, value)
    return bt


def test_still_gaining_suppresses_the_raise_entirely():
    """Room actively rising past the guard threshold -> no floor-raise this cycle."""
    bt = _make_bt(temp_slope=OVERSHOOT_SLOPE_GUARD + 0.01)
    assert apply_warm_floor_floor(bt, "climate.trv", 10.0, is_offset=False) == 10.0


def test_actively_falling_forces_the_tightest_floor():
    """Room actively falling past the guard -> tightest floor, ignoring the
    generous structural estimate this room would otherwise get."""
    falling = _make_bt(temp_slope=-(UNDERSHOOT_SLOPE_GUARD + 0.01))
    steady = _make_bt(temp_slope=0.0)

    falling_result = apply_warm_floor_floor(
        falling, "climate.trv", 10.0, is_offset=False
    )
    steady_result = apply_warm_floor_floor(steady, "climate.trv", 10.0, is_offset=False)

    # Falling forces the minimum backoff ratio -> setpoint closer to target
    # than the steady-state (generous, well-insulated-room) computation.
    assert falling_result > steady_result


def test_slope_within_guards_uses_the_structural_computation():
    """A slope inside both guard bands changes nothing about the computation."""
    inside_guard = _make_bt(temp_slope=0.0)
    no_slope_data = _make_bt(temp_slope=None)
    assert apply_warm_floor_floor(
        inside_guard, "climate.trv", 10.0, is_offset=False
    ) == apply_warm_floor_floor(no_slope_data, "climate.trv", 10.0, is_offset=False)


def test_none_slope_does_not_crash_and_uses_structural_computation():
    bt = _make_bt(temp_slope=None)
    result = apply_warm_floor_floor(bt, "climate.trv", 10.0, is_offset=False)
    assert result > 10.0
