"""Boost's solar-aware anticipatory termination.

Solar gain persists past the lag window in a way the boost device's own
residual heat doesn't, so it's folded into the projection on top of
temp_slope: a heat boost on a sunny day can stop a little earlier (the sun
finishes the job); a cool boost on a sunny day needs a little more margin
(the sun keeps fighting it). A no-op whenever nothing is learned yet - this
is exactly the telemetry gap flagged in the earlier audit (Boost previously
used none of Heat Loss/Heating Power/MPC Gain/MPC Ka/MPC Loss/Sun Intensity).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from homeassistant.components.climate.const import HVACAction, HVACMode
from homeassistant.core import State
import pytest

from custom_components.better_thermostat.trv import Trv
from custom_components.better_thermostat.utils.boost_heater import (
    BoostHeaterTracker,
    control_boost,
)
from custom_components.better_thermostat.utils.calibration.mpc import MpcState
from custom_components.better_thermostat.utils.const import CalibrationMode

ENTITY_ID = "climate.fancoil"
TRV_ID = "climate.trv"


class _MpcStateStub:
    def __init__(self, state: MpcState) -> None:
        self._state = state

    def get_mpc(self, _key: str) -> MpcState:
        return self._state


def _make_bt(
    *,
    cur_temp: float,
    bt_target_temp: float = 22.0,
    tolerance: float = 0.5,
    boost_direction: HVACMode,
    temp_slope: float | None,
    solar_gain_est: float | None,
    solar_intensity: float,
    last_action: HVACAction = HVACAction.HEATING,
) -> MagicMock:
    bt = MagicMock()
    bt.hass.services.async_call = AsyncMock()
    bt.device_name = "Test BT"
    bt.cooler_entity_id = ENTITY_ID
    bt.weather_entity = "weather.home"
    bt.boost_fan_mode = None
    bt.boost_lag_minutes = 5.0
    bt.boost_max_runtime_minutes = 60.0
    bt.cur_temp = cur_temp
    bt.cur_temp_filtered = cur_temp
    bt.temp_slope = temp_slope
    bt.bt_target_temp = bt_target_temp
    bt.tolerance = tolerance
    bt.bt_hvac_mode = HVACMode.HEAT
    bt.contact_open = False
    bt.call_for_heat = True
    bt._boost_heater_tracker = BoostHeaterTracker()
    bt._boost_heater_tracker.boost_direction = boost_direction
    bt._boost_heater_tracker.boost_started_ts = 0.0
    bt._boost_heater_tracker.last_action = last_action

    if solar_gain_est is not None:
        mpc_state = MpcState(
            loss_est=0.02, gain_est=0.05, solar_gain_est=solar_gain_est
        )
        bt.state_mgr = _MpcStateStub(mpc_state)
        bt.real_trvs = {
            TRV_ID: Trv.from_legacy_dict(
                TRV_ID,
                {
                    "advanced": {
                        "calibration_mode": CalibrationMode.MPC_CALIBRATION.value
                    },
                    "current_temperature": cur_temp,
                    "min_temp": 5.0,
                    "max_temp": 30.0,
                },
            )
        }
    else:
        bt.real_trvs = {}

    def _states_get(entity_id):
        if entity_id == ENTITY_ID:
            return State(
                ENTITY_ID, str(boost_direction), {"temperature": bt_target_temp}
            )
        if entity_id == bt.weather_entity:
            return MagicMock(
                attributes={"cloud_coverage": (1.0 - solar_intensity) * 100.0}
            )
        return None

    bt.hass.states.get.side_effect = _states_get
    return bt


@pytest.mark.asyncio
async def test_heat_boost_stops_earlier_thanks_to_solar_gain_on_top_of_a_modest_slope():
    """A modest slope alone doesn't project past target; add solar and it does."""
    common = {
        "cur_temp": 21.7,
        "bt_target_temp": 22.0,
        "tolerance": 0.5,
        "boost_direction": HVACMode.HEAT,
        "temp_slope": 0.02,  # alone: projected = 21.7 + 0.02*5 = 21.8, short of 22.0
    }
    bt_no_sun = _make_bt(**common, solar_gain_est=None, solar_intensity=0.0)
    # solar_adjustment = 0.08 * 1.0 * 5 = 0.4 -> projected = 21.8 + 0.4 = 22.2 >= target
    bt_with_sun = _make_bt(**common, solar_gain_est=0.08, solar_intensity=1.0)

    await control_boost(bt_no_sun)
    await control_boost(bt_with_sun)

    assert bt_no_sun._boost_heater_tracker.boost_active is True
    assert bt_with_sun._boost_heater_tracker.boost_active is False


@pytest.mark.asyncio
async def test_cool_boost_needs_more_margin_against_solar_gain():
    """A slope that would otherwise anticipate a stop is withheld while the
    sun keeps fighting the cool boost."""
    common = {
        "cur_temp": 22.3,
        "bt_target_temp": 22.0,
        "tolerance": 0.5,
        "boost_direction": HVACMode.COOL,
        "temp_slope": -0.1,  # alone: projected = 22.3 - 0.1*5 = 21.8 <= 22.0 -> would stop
        "last_action": HVACAction.COOLING,
    }
    bt_no_sun = _make_bt(**common, solar_gain_est=None, solar_intensity=0.0)
    bt_with_sun = _make_bt(**common, solar_gain_est=0.08, solar_intensity=1.0)
    # solar_adjustment = 0.08 * 5 = 0.4 -> projected = 21.8 + 0.4 = 22.2 > target -> withheld

    await control_boost(bt_no_sun)
    await control_boost(bt_with_sun)

    assert bt_no_sun._boost_heater_tracker.boost_active is False
    assert bt_with_sun._boost_heater_tracker.boost_active is True


@pytest.mark.asyncio
async def test_unlearned_solar_gain_is_a_strict_no_op():
    common = {
        "cur_temp": 21.7,
        "bt_target_temp": 22.0,
        "tolerance": 0.5,
        "boost_direction": HVACMode.HEAT,
        "temp_slope": 0.02,
    }
    bt_no_trvs = _make_bt(**common, solar_gain_est=None, solar_intensity=0.0)
    bt_unlearned = _make_bt(**common, solar_gain_est=None, solar_intensity=1.0)

    await control_boost(bt_no_trvs)
    await control_boost(bt_unlearned)

    assert bt_no_trvs._boost_heater_tracker.boost_active == (
        bt_unlearned._boost_heater_tracker.boost_active
    )
