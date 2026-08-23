"""Tests for control_boost() in utils/boost_heater.py.

Covers the hard guards (off/contact_open/no-call-for-heat -> force off and
cancel), climate-domain dispatch for both heat and cool directions, and the
anticipatory/tolerance-floor/safety-cap termination in each direction.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, Mock

from homeassistant.components.climate.const import HVACMode
from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.core import State
import pytest

from custom_components.better_thermostat.utils.boost_heater import (
    BoostHeaterTracker,
    control_boost,
)

ENTITY_ID = "climate.fancoil"


def _make_bt(
    *,
    cur_temp=18.0,
    bt_target_temp=22.0,
    tolerance=0.5,
    bt_hvac_mode=HVACMode.HEAT,
    contact_open=False,
    call_for_heat=True,
    boost_direction=None,
    temp_slope=None,
    boost_fan_mode=None,
    boost_lag_minutes=5.0,
    boost_max_runtime_minutes=60.0,
):
    bt = Mock()
    bt.hass = Mock()
    bt.hass.services = Mock()
    bt.hass.services.async_call = AsyncMock()
    bt.device_name = "Test BT"
    bt.cooler_entity_id = ENTITY_ID
    bt.boost_fan_mode = boost_fan_mode
    bt.boost_lag_minutes = boost_lag_minutes
    bt.boost_max_runtime_minutes = boost_max_runtime_minutes
    bt.cur_temp = cur_temp
    bt.cur_temp_filtered = cur_temp
    bt.temp_slope = temp_slope
    bt.bt_target_temp = bt_target_temp
    bt.tolerance = tolerance
    bt.bt_hvac_mode = bt_hvac_mode
    bt.contact_open = contact_open
    bt.call_for_heat = call_for_heat
    bt._boost_heater_tracker = BoostHeaterTracker()
    bt._boost_heater_tracker.boost_direction = boost_direction
    return bt


def _state(hvac_mode=HVACMode.OFF, temperature=None, fan_modes=None, fan_mode=None):
    attributes = {"temperature": temperature}
    if fan_modes is not None:
        attributes["fan_modes"] = fan_modes
    if fan_mode is not None:
        attributes["fan_mode"] = fan_mode
    return State(ENTITY_ID, str(hvac_mode), attributes)


def _calls_for(bt, service):
    return [
        call
        for call in bt.hass.services.async_call.call_args_list
        if call.args[0] == "climate" and call.args[1] == service
    ]


@pytest.mark.asyncio
async def test_unavailable_state_is_a_no_op():
    bt = _make_bt(boost_direction=HVACMode.HEAT)
    bt.hass.states.get.return_value = State(ENTITY_ID, STATE_UNAVAILABLE)
    await control_boost(bt)
    bt.hass.services.async_call.assert_not_called()


@pytest.mark.asyncio
async def test_bt_hvac_off_forces_off_and_cancels_boost():
    bt = _make_bt(bt_hvac_mode=HVACMode.OFF, boost_direction=HVACMode.HEAT)
    bt.hass.states.get.return_value = _state(hvac_mode=HVACMode.HEAT)
    await control_boost(bt)

    mode_calls = _calls_for(bt, "set_hvac_mode")
    assert mode_calls
    assert mode_calls[-1].args[2]["hvac_mode"] == HVACMode.OFF
    assert bt._boost_heater_tracker.boost_active is False


@pytest.mark.asyncio
async def test_contact_open_forces_off_and_cancels_boost():
    bt = _make_bt(contact_open=True, boost_direction=HVACMode.HEAT)
    bt.hass.states.get.return_value = _state(hvac_mode=HVACMode.HEAT)
    await control_boost(bt)

    mode_calls = _calls_for(bt, "set_hvac_mode")
    assert mode_calls[-1].args[2]["hvac_mode"] == HVACMode.OFF
    assert bt._boost_heater_tracker.boost_active is False


@pytest.mark.asyncio
async def test_call_for_heat_false_forces_off_and_cancels_boost():
    bt = _make_bt(call_for_heat=False, boost_direction=HVACMode.HEAT)
    bt.hass.states.get.return_value = _state(hvac_mode=HVACMode.HEAT)
    await control_boost(bt)

    mode_calls = _calls_for(bt, "set_hvac_mode")
    assert mode_calls[-1].args[2]["hvac_mode"] == HVACMode.OFF
    assert bt._boost_heater_tracker.boost_active is False


@pytest.mark.asyncio
async def test_already_off_does_not_resend_off():
    bt = _make_bt(bt_hvac_mode=HVACMode.OFF, boost_direction=HVACMode.HEAT)
    bt.hass.states.get.return_value = _state(hvac_mode=HVACMode.OFF)
    await control_boost(bt)
    assert not _calls_for(bt, "set_hvac_mode")


@pytest.mark.asyncio
async def test_heat_boost_drives_heat_mode_and_target_temperature():
    bt = _make_bt(cur_temp=18.0, bt_target_temp=22.0, boost_direction=HVACMode.HEAT)
    bt.hass.states.get.return_value = _state(hvac_mode=HVACMode.OFF, temperature=18.0)
    await control_boost(bt)

    mode_calls = _calls_for(bt, "set_hvac_mode")
    assert mode_calls
    assert mode_calls[-1].args[2]["hvac_mode"] == HVACMode.HEAT

    temp_calls = _calls_for(bt, "set_temperature")
    assert temp_calls
    assert temp_calls[-1].args[2]["temperature"] == 22.0


@pytest.mark.asyncio
async def test_cool_boost_drives_cool_mode_and_target_temperature():
    bt = _make_bt(cur_temp=26.0, bt_target_temp=22.0, boost_direction=HVACMode.COOL)
    bt.hass.states.get.return_value = _state(hvac_mode=HVACMode.OFF, temperature=26.0)
    await control_boost(bt)

    mode_calls = _calls_for(bt, "set_hvac_mode")
    assert mode_calls
    assert mode_calls[-1].args[2]["hvac_mode"] == HVACMode.COOL

    temp_calls = _calls_for(bt, "set_temperature")
    assert temp_calls
    assert temp_calls[-1].args[2]["temperature"] == 22.0


@pytest.mark.asyncio
async def test_heat_boost_does_not_stop_while_meaningfully_short_of_target():
    bt = _make_bt(
        cur_temp=18.0, bt_target_temp=22.0, tolerance=0.5, boost_direction=HVACMode.HEAT
    )
    bt.hass.states.get.return_value = _state(hvac_mode=HVACMode.HEAT, temperature=22.0)
    await control_boost(bt)
    assert bt._boost_heater_tracker.boost_active is True


@pytest.mark.asyncio
async def test_cool_boost_does_not_stop_while_meaningfully_above_target():
    bt = _make_bt(
        cur_temp=26.0, bt_target_temp=22.0, tolerance=0.5, boost_direction=HVACMode.COOL
    )
    bt.hass.states.get.return_value = _state(hvac_mode=HVACMode.COOL, temperature=22.0)
    await control_boost(bt)
    assert bt._boost_heater_tracker.boost_active is True


@pytest.mark.asyncio
async def test_heat_boost_stops_once_target_reached_without_slope_data():
    bt = _make_bt(
        cur_temp=22.0, bt_target_temp=22.0, tolerance=0.5, boost_direction=HVACMode.HEAT
    )
    bt.hass.states.get.return_value = _state(hvac_mode=HVACMode.HEAT, temperature=22.0)
    await control_boost(bt)
    assert bt._boost_heater_tracker.boost_active is False
    mode_calls = _calls_for(bt, "set_hvac_mode")
    assert mode_calls[-1].args[2]["hvac_mode"] == HVACMode.OFF


@pytest.mark.asyncio
async def test_cool_boost_stops_once_target_reached_without_slope_data():
    bt = _make_bt(
        cur_temp=22.0, bt_target_temp=22.0, tolerance=0.5, boost_direction=HVACMode.COOL
    )
    bt.hass.states.get.return_value = _state(hvac_mode=HVACMode.COOL, temperature=22.0)
    await control_boost(bt)
    assert bt._boost_heater_tracker.boost_active is False
    mode_calls = _calls_for(bt, "set_hvac_mode")
    assert mode_calls[-1].args[2]["hvac_mode"] == HVACMode.OFF


@pytest.mark.asyncio
async def test_heat_boost_stops_early_when_projection_reaches_target():
    """Inside the tolerance band with a rising slope -> anticipatory early stop."""
    bt = _make_bt(
        cur_temp=21.7,
        bt_target_temp=22.0,
        tolerance=0.5,
        temp_slope=0.1,  # °C/min, rising
        boost_lag_minutes=5.0,  # projected = 21.7 + 0.1*5 = 22.2 >= target
        boost_direction=HVACMode.HEAT,
    )
    bt.hass.states.get.return_value = _state(hvac_mode=HVACMode.HEAT, temperature=22.0)
    await control_boost(bt)
    assert bt._boost_heater_tracker.boost_active is False


@pytest.mark.asyncio
async def test_cool_boost_stops_early_when_projection_reaches_target():
    """Inside the tolerance band with a falling slope -> anticipatory early stop."""
    bt = _make_bt(
        cur_temp=22.3,
        bt_target_temp=22.0,
        tolerance=0.5,
        temp_slope=-0.1,  # °C/min, falling
        boost_lag_minutes=5.0,  # projected = 22.3 - 0.1*5 = 21.8 <= target
        boost_direction=HVACMode.COOL,
    )
    bt.hass.states.get.return_value = _state(hvac_mode=HVACMode.COOL, temperature=22.0)
    await control_boost(bt)
    assert bt._boost_heater_tracker.boost_active is False


@pytest.mark.asyncio
async def test_heat_boost_does_not_stop_early_while_still_outside_tolerance_band():
    bt = _make_bt(
        cur_temp=19.0,
        bt_target_temp=22.0,
        tolerance=0.5,
        temp_slope=1.0,  # would project well past target
        boost_lag_minutes=5.0,
        boost_direction=HVACMode.HEAT,
    )
    bt.hass.states.get.return_value = _state(hvac_mode=HVACMode.HEAT, temperature=22.0)
    await control_boost(bt)
    assert bt._boost_heater_tracker.boost_active is True


@pytest.mark.asyncio
async def test_max_runtime_safety_cap_force_stops_regardless_of_temperature():
    bt = _make_bt(cur_temp=15.0, bt_target_temp=22.0, boost_direction=HVACMode.HEAT)
    bt.hass.states.get.return_value = _state(hvac_mode=HVACMode.HEAT, temperature=22.0)
    bt._boost_heater_tracker.boost_started_ts = 0.0
    bt.boost_max_runtime_minutes = 0.0001  # effectively already exceeded

    await control_boost(bt)
    assert bt._boost_heater_tracker.boost_active is False


@pytest.mark.asyncio
async def test_fan_mode_captured_on_boost_start_and_restored_on_stop():
    bt = _make_bt(
        cur_temp=22.0,
        bt_target_temp=22.0,
        boost_fan_mode="high",
        boost_direction=HVACMode.HEAT,
    )
    bt.hass.states.get.return_value = _state(
        hvac_mode=HVACMode.HEAT,
        temperature=22.0,
        fan_modes=["auto", "low", "high"],
        fan_mode="auto",
    )
    await control_boost(bt)

    fan_calls = [
        call
        for call in bt.hass.services.async_call.call_args_list
        if call.args[0] == "climate" and call.args[1] == "set_fan_mode"
    ]
    assert fan_calls[0].args[2]["fan_mode"] == "high"
    assert fan_calls[-1].args[2]["fan_mode"] == "auto"


@pytest.mark.asyncio
async def test_unsupported_fan_mode_is_silently_skipped():
    bt = _make_bt(cur_temp=18.0, boost_fan_mode="turbo", boost_direction=HVACMode.HEAT)
    bt.hass.states.get.return_value = _state(
        hvac_mode=HVACMode.OFF, temperature=18.0, fan_modes=["auto", "low"]
    )
    await control_boost(bt)
    fan_calls = [
        call
        for call in bt.hass.services.async_call.call_args_list
        if call.args[0] == "climate" and call.args[1] == "set_fan_mode"
    ]
    assert not fan_calls


@pytest.mark.asyncio
async def test_no_boost_fan_mode_configured_never_calls_set_fan_mode():
    bt = _make_bt(cur_temp=18.0, boost_fan_mode=None, boost_direction=HVACMode.HEAT)
    bt.hass.states.get.return_value = _state(
        hvac_mode=HVACMode.OFF, temperature=18.0, fan_modes=["auto", "low"]
    )
    await control_boost(bt)
    fan_calls = [
        call
        for call in bt.hass.services.async_call.call_args_list
        if call.args[0] == "climate" and call.args[1] == "set_fan_mode"
    ]
    assert not fan_calls


class TestBoostStatusReflectsTheSameCycleItStops:
    """Regression: found via live-HA testing, not the mocked unit tests.

    ``_record_boost_status()`` used to run only from ``update_boost_trend()``
    (once per cycle, *before* control_boost() decides to stop) - so the
    diagnostic sensor stayed on "heating"/"cooling" for however long it took
    some *other* trigger (a TRV state change, the 5-minute tick) to run
    another cycle and correct it, which in a live instance with no other
    activity could be minutes, not "the next cycle". control_boost() now
    also records status at every point it changes the tracker, so the
    snapshot is correct within the same cycle the stop happens.
    """

    @pytest.mark.asyncio
    async def test_status_reflects_off_the_same_cycle_a_guard_force_stops_it(self):
        bt = _make_bt(bt_hvac_mode=HVACMode.OFF, boost_direction=HVACMode.HEAT)
        bt.hass.states.get.return_value = _state(hvac_mode=HVACMode.HEAT)
        await control_boost(bt)
        assert bt._boost_status["active"] is False
        assert bt._boost_status["direction"] is None

    @pytest.mark.asyncio
    async def test_status_reflects_off_the_same_cycle_target_is_reached(self):
        bt = _make_bt(
            cur_temp=22.0,
            bt_target_temp=22.0,
            tolerance=0.5,
            boost_direction=HVACMode.HEAT,
        )
        bt.hass.states.get.return_value = _state(
            hvac_mode=HVACMode.HEAT, temperature=22.0
        )
        await control_boost(bt)
        assert bt._boost_status["active"] is False
        assert bt._boost_status["direction"] is None

    @pytest.mark.asyncio
    async def test_status_reflects_heating_while_still_active(self):
        bt = _make_bt(cur_temp=18.0, bt_target_temp=22.0, boost_direction=HVACMode.HEAT)
        bt.hass.states.get.return_value = _state(
            hvac_mode=HVACMode.HEAT, temperature=22.0
        )
        await control_boost(bt)
        assert bt._boost_status["active"] is True
        assert bt._boost_status["direction"] == HVACMode.HEAT
