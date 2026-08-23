"""Boost restores the Cooler entity to its exact previous state on stop.

Not a fresh control_cooler() decision and not always "off" - a literal
snapshot (mode, temperature, fan mode) taken the moment the boost first
started actuating, restored verbatim once it ends, regardless of what
control_cooler() would decide fresh at that point.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, Mock

from homeassistant.components.climate.const import HVACMode
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
    boost_direction=HVACMode.HEAT,
    temp_slope=None,
    boost_fan_mode=None,
):
    bt = Mock()
    bt.hass = Mock()
    bt.hass.services = Mock()
    bt.hass.services.async_call = AsyncMock()
    bt.device_name = "Test BT"
    bt.cooler_entity_id = ENTITY_ID
    bt.boost_fan_mode = boost_fan_mode
    bt.boost_lag_minutes = 5.0
    bt.boost_max_runtime_minutes = 60.0
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


def _state(hvac_mode, temperature=None, fan_modes=None, fan_mode=None):
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
async def test_stopping_restores_a_device_that_was_actively_cooling_before_boost():
    """Boost overrides a device mid-way through its own independent cooling
    run; once boost ends, that cooling run is restored, not just off."""
    bt = _make_bt(
        cur_temp=22.0, bt_target_temp=22.0, tolerance=0.5
    )  # stops immediately
    bt.hass.states.get.return_value = _state(HVACMode.COOL, temperature=25.0)

    await control_boost(bt)

    mode_calls = _calls_for(bt, "set_hvac_mode")
    temp_calls = _calls_for(bt, "set_temperature")
    # First command: boost taking over into heat mode.
    assert mode_calls[0].args[2]["hvac_mode"] == HVACMode.HEAT
    # Last command: restored back to the pre-boost cool mode.
    assert mode_calls[-1].args[2]["hvac_mode"] == HVACMode.COOL
    # Temperature restored to what it was before boost took over (25.0),
    # not left at whatever boost had set it to.
    assert temp_calls[-1].args[2]["temperature"] == 25.0


@pytest.mark.asyncio
async def test_stopping_restores_off_when_the_device_was_off_before_boost():
    bt = _make_bt(cur_temp=22.0, bt_target_temp=22.0, tolerance=0.5)
    bt.hass.states.get.return_value = _state(HVACMode.OFF)

    await control_boost(bt)

    mode_calls = _calls_for(bt, "set_hvac_mode")
    assert mode_calls[-1].args[2]["hvac_mode"] == HVACMode.OFF
    # Off restore never sends a temperature - matches _ensure_off()'s guard
    # against writing a setpoint to a device that's now off.
    temp_calls = _calls_for(bt, "set_temperature")
    assert not any(c.args[2].get("temperature") is not None for c in temp_calls[1:])


@pytest.mark.asyncio
async def test_the_off_guard_branch_also_restores_previous_state_not_just_off():
    """contact_open/bt_hvac_off/no-call-for-heat forcing a stop mid-boost
    restores the same snapshot a natural target-reached stop would, not a
    blind off - but only once a snapshot actually exists (a boost that
    never got past its first cycle has nothing captured yet; see the
    "arms and stops in the same cycle" test below for that case)."""
    bt = _make_bt(cur_temp=18.0, bt_target_temp=22.0, tolerance=0.5)  # still needs heat
    bt.hass.states.get.return_value = _state(HVACMode.COOL, temperature=25.0)
    await control_boost(bt)  # cycle 1: captures cool/25.0, commands heat, still active

    # By cycle 2 the device has actually confirmed boost's heat command -
    # a real system's next state fetch would reflect that, not the stale
    # pre-boost reading.
    bt.hass.states.get.return_value = _state(HVACMode.HEAT, temperature=22.0)
    bt.contact_open = True  # a window opens mid-boost
    await control_boost(bt)  # cycle 2: guard fires, restores the captured snapshot

    mode_calls = _calls_for(bt, "set_hvac_mode")
    assert mode_calls[-1].args[2]["hvac_mode"] == HVACMode.COOL
    temp_calls = _calls_for(bt, "set_temperature")
    assert temp_calls[-1].args[2]["temperature"] == 25.0


@pytest.mark.asyncio
async def test_arming_and_stopping_within_the_same_cycle_still_restores_correctly():
    """Regression: comparing the restore target against a stale state
    snapshot (fetched once at the top of control_boost()) instead of what
    this cycle actually just commanded meant a same-cycle arm-and-stop
    silently skipped the restore command, leaving the device stuck in
    boost's own heat/cool mode instead of its real previous state."""
    bt = _make_bt(
        cur_temp=22.0, bt_target_temp=22.0, tolerance=0.5
    )  # stops immediately
    bt.hass.states.get.return_value = _state(HVACMode.OFF)  # off before boost

    await control_boost(bt)

    mode_calls = _calls_for(bt, "set_hvac_mode")
    # Must have actually sent the restore-to-off command, not silently
    # concluded (from the stale "off" snapshot) that no command was needed.
    assert len(mode_calls) >= 2
    assert mode_calls[0].args[2]["hvac_mode"] == HVACMode.HEAT
    assert mode_calls[-1].args[2]["hvac_mode"] == HVACMode.OFF


@pytest.mark.asyncio
async def test_no_restore_command_sent_when_previous_mode_matches_boost_direction():
    """The device was already in the boost's own direction before boost
    started - restoring that exact mode back is correctly a no-op command,
    not a redundant resend."""
    bt = _make_bt(
        cur_temp=18.0, bt_target_temp=22.0, tolerance=0.5, boost_direction=HVACMode.HEAT
    )
    bt.hass.states.get.return_value = _state(HVACMode.HEAT, temperature=20.0)

    await control_boost(bt)  # still short of target, boost continues

    # No mode command needed at all - device was already heating.
    assert not _calls_for(bt, "set_hvac_mode")


@pytest.mark.asyncio
async def test_fan_mode_and_hvac_mode_are_both_restored_together():
    bt = _make_bt(
        cur_temp=22.0, bt_target_temp=22.0, tolerance=0.5, boost_fan_mode="on_high"
    )
    bt.hass.states.get.return_value = _state(
        HVACMode.COOL, temperature=25.0, fan_modes=["auto", "on_high"], fan_mode="auto"
    )

    await control_boost(bt)

    fan_calls = _calls_for(bt, "set_fan_mode")
    mode_calls = _calls_for(bt, "set_hvac_mode")
    assert fan_calls[0].args[2]["fan_mode"] == "on_high"  # boost's override
    assert fan_calls[-1].args[2]["fan_mode"] == "auto"  # restored
    assert mode_calls[-1].args[2]["hvac_mode"] == HVACMode.COOL  # restored


@pytest.mark.asyncio
async def test_snapshot_is_only_captured_once_not_overwritten_each_cycle():
    """Confirms the snapshot reflects the device's state *before* boost
    ever touched it, even across multiple control_boost() calls while the
    boost keeps running."""
    bt = _make_bt(cur_temp=18.0, bt_target_temp=22.0, tolerance=0.5)
    bt.hass.states.get.return_value = _state(HVACMode.COOL, temperature=25.0)

    await control_boost(bt)  # cycle 1: captures cool/25.0, commands heat
    assert bt._boost_heater_tracker.previous_hvac_mode == HVACMode.COOL
    assert bt._boost_heater_tracker.previous_temperature == 25.0

    # Cycle 2: device now reports heat (as boost commanded) - must not
    # overwrite the original snapshot with this.
    bt.hass.states.get.return_value = _state(HVACMode.HEAT, temperature=22.0)
    await control_boost(bt)
    assert bt._boost_heater_tracker.previous_hvac_mode == HVACMode.COOL
    assert bt._boost_heater_tracker.previous_temperature == 25.0
