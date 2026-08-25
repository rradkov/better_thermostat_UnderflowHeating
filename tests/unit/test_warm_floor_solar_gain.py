"""Warm Floor's solar-gain adjustment: the south-facing-room regression case.

A room whose apparent comfort is currently propped up by solar gain (rather
than delivered heat) must keep its floor close to target - so it doesn't
free-fall once the sun's contribution fades - without ever forcing heat
while the sun is still genuinely covering the load.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from homeassistant.components.climate.const import HVACMode
from homeassistant.util import dt as dt_util

from custom_components.better_thermostat.trv import Trv
from custom_components.better_thermostat.utils.calibration.mpc import MpcState
from custom_components.better_thermostat.utils.const import CalibrationMode
from custom_components.better_thermostat.utils.underfloor import (
    CONF_HEATING_TYPE,
    HeatingType,
    apply_warm_floor_floor,
)


class _MpcStateStub:
    """Minimal stand-in for the state manager's MPC accessors."""

    def __init__(self, state: MpcState) -> None:
        self._state = state

    def get_mpc(self, _key: str) -> MpcState:
        return self._state


def _make_bt(*, mpc_state: MpcState, solar_intensity: float, **overrides) -> MagicMock:
    bt = MagicMock()
    bt.device_name = "Test BT"
    bt.contact_open = False
    bt.call_for_heat = True
    bt.bt_hvac_mode = HVACMode.HEAT
    bt.bt_target_temp = 22.0
    bt.temp_slope = None
    bt.heat_loss_rate = mpc_state.loss_est
    bt.heating_power = mpc_state.gain_est
    bt.unique_id = "uid"
    bt.weather_entity = "weather.home"
    bt.sensor_entity_id = "sensor.room_temp"
    bt.hass.states.get.return_value = MagicMock(
        attributes={"cloud_coverage": (1.0 - solar_intensity) * 100.0},
        last_updated=dt_util.utcnow(),
    )
    bt.state_mgr = _MpcStateStub(mpc_state)
    bt.real_trvs = {
        "climate.trv": Trv.from_legacy_dict(
            "climate.trv",
            {
                "advanced": {
                    CONF_HEATING_TYPE: HeatingType.UNDERFLOOR.value,
                    "calibration_mode": CalibrationMode.MPC_CALIBRATION.value,
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


def test_high_solar_gain_keeps_the_floor_close_to_target():
    """Strong learned solar gain + bright sun -> sustaining setpoint near target."""
    state = MpcState(loss_est=0.02, gain_est=0.05, solar_gain_est=0.02)
    bt = _make_bt(mpc_state=state, solar_intensity=1.0)
    result = apply_warm_floor_floor(bt, "climate.trv", 10.0, is_offset=False)
    assert result > 21.0  # backoff nearly fully suppressed


def test_no_sun_falls_back_to_the_plain_structural_backoff():
    """With solar_intensity at 0, the solar term is a no-op."""
    state = MpcState(loss_est=0.02, gain_est=0.05, solar_gain_est=0.02)
    sunny = _make_bt(mpc_state=state, solar_intensity=1.0)
    cloudy = _make_bt(mpc_state=state, solar_intensity=0.0)

    sunny_result = apply_warm_floor_floor(sunny, "climate.trv", 10.0, is_offset=False)
    cloudy_result = apply_warm_floor_floor(cloudy, "climate.trv", 10.0, is_offset=False)
    assert sunny_result > cloudy_result


def test_unlearned_solar_gain_is_a_no_op():
    """Before the model has learned anything, solar_gain_est is None -> no-op."""
    state = MpcState(loss_est=0.02, gain_est=0.05, solar_gain_est=None)
    with_sun = _make_bt(mpc_state=state, solar_intensity=1.0)
    without_sun = _make_bt(mpc_state=state, solar_intensity=0.0)

    assert apply_warm_floor_floor(
        with_sun, "climate.trv", 10.0, is_offset=False
    ) == apply_warm_floor_floor(without_sun, "climate.trv", 10.0, is_offset=False)


def test_non_mpc_calibration_mode_ignores_solar_gain():
    """The solar adjustment only applies to MPC-calibrated heaters."""
    state = MpcState(loss_est=0.02, gain_est=0.05, solar_gain_est=0.05)
    bt = _make_bt(mpc_state=state, solar_intensity=1.0)
    bt.real_trvs["climate.trv"].advanced["calibration_mode"] = (
        CalibrationMode.HEATING_POWER_CALIBRATION.value
    )
    bt.heat_loss_rate = 0.02
    bt.heating_power = 0.05
    # Should behave the same regardless of solar_intensity now.
    bt2 = _make_bt(mpc_state=state, solar_intensity=0.0)
    bt2.real_trvs["climate.trv"].advanced["calibration_mode"] = (
        CalibrationMode.HEATING_POWER_CALIBRATION.value
    )
    bt2.heat_loss_rate = 0.02
    bt2.heating_power = 0.05

    assert apply_warm_floor_floor(
        bt, "climate.trv", 10.0, is_offset=False
    ) == apply_warm_floor_floor(bt2, "climate.trv", 10.0, is_offset=False)


def test_sustaining_floor_never_exceeds_target_even_with_full_solar_gain():
    """Full solar masking pushes the sustaining floor up to target, never past it."""
    state = MpcState(loss_est=0.02, gain_est=0.05, solar_gain_est=0.05)
    bt = _make_bt(mpc_state=state, solar_intensity=1.0)
    result = apply_warm_floor_floor(bt, "climate.trv", 10.0, is_offset=False)
    assert result <= bt.bt_target_temp
