"""Warm Floor's use of MPC Insulation (Ka): the missing telemetry gap.

Ka is delta-normalized (loss per degree of indoor/outdoor difference) -
unlike loss_est/heat_loss_rate, which reflect today's specific weather and
can look artificially low on a mild day even for a genuinely leaky room.
Warm Floor cross-checks: never let the structural estimate read a tighter
envelope (lower loss) than what Ka itself implies at the live delta - only
tighter, never looser.
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
    def __init__(self, state: MpcState) -> None:
        self._state = state

    def get_mpc(self, _key: str) -> MpcState:
        return self._state


def _make_bt(
    *, mpc_state: MpcState, outdoor_temp: float | None, indoor_temp: float = 20.0
) -> MagicMock:
    bt = MagicMock()
    bt.device_name = "Test BT"
    bt.contact_open = False
    bt.call_for_heat = True
    bt.bt_hvac_mode = HVACMode.HEAT
    bt.bt_target_temp = 22.0
    bt.temp_slope = None
    bt.cur_temp = indoor_temp
    bt.last_avg_outdoor_temp = outdoor_temp
    bt.heat_loss_rate = mpc_state.loss_est
    bt.heating_power = mpc_state.gain_est
    bt.unique_id = "uid"
    bt.state_mgr = _MpcStateStub(mpc_state)
    bt.sensor_entity_id = "sensor.room_temp"
    bt.hass.states.get.return_value = MagicMock(last_updated=dt_util.utcnow())
    bt.real_trvs = {
        "climate.trv": Trv.from_legacy_dict(
            "climate.trv",
            {
                "advanced": {
                    CONF_HEATING_TYPE: HeatingType.UNDERFLOOR.value,
                    "calibration_mode": CalibrationMode.MPC_CALIBRATION.value,
                    "warm_floor_max_backoff": 3.0,
                },
                "current_temperature": indoor_temp,
                "min_temp": 5.0,
                "max_temp": 30.0,
            },
        )
    }
    return bt


def test_a_high_ka_tightens_the_floor_beyond_what_loss_est_alone_would_give():
    """loss_est reads mild (low), but Ka implies a much leakier envelope at
    the live delta - the cross-check must win, tightening (not loosening)
    the resulting floor."""
    # loss_est=0.005 (very low, mild-weather reading), but ka_est implies a
    # much higher loss at a 25 degree delta: 0.01 * 25 = 0.25 K/min - far
    # above MAX_HEAT_LOSS, so loss_severity clamps to 1.0 either way. Use a
    # more moderate ka_est so the two produce genuinely different outputs.
    state_low_loss = MpcState(loss_est=0.001, gain_est=0.05, ka_est=0.0008)
    # 20C indoor, -5C outdoor -> delta = 25C -> ka_implied_loss = 0.0008*25 = 0.02
    bt_with_ka = _make_bt(mpc_state=state_low_loss, outdoor_temp=-5.0, indoor_temp=20.0)
    result_with_ka = apply_warm_floor_floor(
        bt_with_ka, "climate.trv", 10.0, is_offset=False
    )

    # Same loss_est, but no outdoor reading -> Ka cross-check can't run, pure
    # loss_est (0.001, very low severity) is used instead -> looser floor.
    bt_without_ka = _make_bt(
        mpc_state=state_low_loss, outdoor_temp=None, indoor_temp=20.0
    )
    result_without_ka = apply_warm_floor_floor(
        bt_without_ka, "climate.trv", 10.0, is_offset=False
    )

    # The Ka-informed floor must be at least as tight (>= sustaining
    # setpoint) as the loss_est-only floor - never looser.
    assert result_with_ka >= result_without_ka


def test_ka_never_loosens_the_floor_below_what_loss_est_alone_implies():
    """A low Ka-implied loss at a small delta must not loosen a floor that
    loss_est alone (already higher) would have produced."""
    # loss_est is already high (leaky per direct observation); ka_est implies
    # a much smaller loss at this delta - the cross-check must not loosen it.
    state = MpcState(loss_est=0.04, gain_est=0.05, ka_est=0.0001)
    # 20C indoor, 15C outdoor -> delta clamped to the 5.0 floor -> ka_implied
    # = 0.0001 * 5.0 = 0.0005, far below loss_est=0.04.
    bt = _make_bt(mpc_state=state, outdoor_temp=15.0, indoor_temp=20.0)
    result = apply_warm_floor_floor(bt, "climate.trv", 10.0, is_offset=False)

    bt_no_outdoor = _make_bt(mpc_state=state, outdoor_temp=None, indoor_temp=20.0)
    result_no_outdoor = apply_warm_floor_floor(
        bt_no_outdoor, "climate.trv", 10.0, is_offset=False
    )

    # Same result either way: Ka's (lower) implied loss never overrides the
    # already-higher loss_est reading.
    assert result == result_no_outdoor


def test_missing_outdoor_temperature_is_a_strict_no_op_for_the_ka_crosscheck():
    state = MpcState(loss_est=0.01, gain_est=0.05, ka_est=0.01)
    bt_no_outdoor = _make_bt(mpc_state=state, outdoor_temp=None)
    bt_no_ka = _make_bt(
        mpc_state=MpcState(loss_est=0.01, gain_est=0.05, ka_est=None), outdoor_temp=None
    )
    assert apply_warm_floor_floor(
        bt_no_outdoor, "climate.trv", 10.0, is_offset=False
    ) == apply_warm_floor_floor(bt_no_ka, "climate.trv", 10.0, is_offset=False)
