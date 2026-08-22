"""Window open/close -> boost temperature capture and arming, through the real
debounce queue.

Drives events/window.py's window_queue() (which wraps events/contact.py's
contact_queue()) end to end, the same way tests/unit/test_window_events.py
does for the base window-open/close behavior - confirming
record_window_transition() is actually reached at the debounce commit
point, scoped to window events only, and correctly arms heat/cool direction
based on the drift and the Cooler entity's reported hvac_modes.
"""

from __future__ import annotations

import asyncio
from unittest.mock import Mock

from homeassistant.components.climate.const import HVACMode
import pytest

from custom_components.better_thermostat.events.door import door_queue
from custom_components.better_thermostat.events.window import window_queue
from custom_components.better_thermostat.utils.boost_heater import BoostHeaterTracker

COOLER_ID = "climate.fancoil"


def _make_bt(
    *,
    cur_temp,
    window_open=False,
    door_open=False,
    boost_enabled=True,
    cooler_entity_id=COOLER_ID,
    hvac_modes=("off", "heat", "cool"),
):
    bt = Mock()
    bt.device_name = "Test BT"
    bt.window_id = "binary_sensor.window"
    bt.door_id = "binary_sensor.door"
    bt.window_open = window_open
    bt.door_open = door_open
    bt.window_delay = 0
    bt.window_delay_after = 0
    bt.door_delay = 0
    bt.door_delay_after = 0
    bt.in_maintenance = False
    bt.cur_temp = cur_temp
    bt.boost_enabled = boost_enabled
    bt.cooler_entity_id = cooler_entity_id
    bt.boost_threshold_k = 5.0
    bt._boost_heater_tracker = BoostHeaterTracker()
    bt.async_write_ha_state = Mock()
    bt.window_queue_task = asyncio.Queue()
    bt.door_queue_task = asyncio.Queue()
    bt.control_queue_task = asyncio.Queue()

    cooler_state = Mock()
    cooler_state.attributes = {"hvac_modes": list(hvac_modes)}
    bt._cooler_state_stub = cooler_state

    contact_state = Mock()
    contact_state.state = "off"  # closed, by default

    def _states_get(entity_id):
        if entity_id == bt.cooler_entity_id:
            return bt._cooler_state_stub
        return contact_state

    bt.hass.states.get.side_effect = _states_get
    return bt


async def _run_window_queue(bt, is_open: bool, sensor_state: str):
    def _states_get(entity_id):
        if entity_id == bt.cooler_entity_id:
            return bt._cooler_state_stub
        return Mock(state=sensor_state)

    bt.hass.states.get.side_effect = _states_get
    task = asyncio.create_task(window_queue(bt))
    await bt.window_queue_task.put(is_open)
    await asyncio.wait_for(bt.window_queue_task.join(), timeout=1)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


async def _run_door_queue(bt, is_open: bool, sensor_state: str):
    bt.hass.states.get.side_effect = lambda entity_id: Mock(state=sensor_state)
    task = asyncio.create_task(door_queue(bt))
    await bt.door_queue_task.put(is_open)
    await asyncio.wait_for(bt.door_queue_task.join(), timeout=1)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


@pytest.mark.asyncio
async def test_window_open_commit_records_the_temperature():
    bt = _make_bt(cur_temp=23.0)
    await _run_window_queue(bt, True, "open")
    assert bt._boost_heater_tracker.open_temp == 23.0


@pytest.mark.asyncio
async def test_window_close_after_a_big_drop_arms_a_heat_boost():
    bt = _make_bt(cur_temp=23.0, window_open=True)
    await _run_window_queue(bt, True, "open")

    bt.cur_temp = 16.5  # dropped 6.5°C, above the 5.0 threshold
    await _run_window_queue(bt, False, "closed")

    assert bt._boost_heater_tracker.boost_direction == HVACMode.HEAT
    assert bt._boost_heater_tracker.open_temp is None


@pytest.mark.asyncio
async def test_window_close_after_a_big_rise_arms_a_cool_boost():
    bt = _make_bt(cur_temp=23.0, window_open=True)
    await _run_window_queue(bt, True, "open")

    bt.cur_temp = 30.0  # rose 7.0°C, above the 5.0 threshold
    await _run_window_queue(bt, False, "closed")

    assert bt._boost_heater_tracker.boost_direction == HVACMode.COOL


@pytest.mark.asyncio
async def test_window_close_after_a_small_drift_does_not_arm():
    bt = _make_bt(cur_temp=23.0, window_open=True)
    await _run_window_queue(bt, True, "open")

    bt.cur_temp = 21.0  # dropped only 2.0°C
    await _run_window_queue(bt, False, "closed")

    assert bt._boost_heater_tracker.boost_active is False


@pytest.mark.asyncio
async def test_unsupported_direction_is_not_armed():
    """A heat-only Cooler entity never gets a cool boost, even past threshold."""
    bt = _make_bt(cur_temp=23.0, window_open=True, hvac_modes=("off", "heat"))
    await _run_window_queue(bt, True, "open")

    bt.cur_temp = 30.0  # would otherwise arm a cool boost
    await _run_window_queue(bt, False, "closed")

    assert bt._boost_heater_tracker.boost_active is False


@pytest.mark.asyncio
async def test_boost_disabled_is_a_no_op():
    bt = _make_bt(cur_temp=23.0, boost_enabled=False)
    await _run_window_queue(bt, True, "open")
    assert bt._boost_heater_tracker.open_temp is None


@pytest.mark.asyncio
async def test_no_cooler_configured_is_a_no_op():
    bt = _make_bt(cur_temp=23.0, cooler_entity_id=None)
    await _run_window_queue(bt, True, "open")
    assert bt._boost_heater_tracker.open_temp is None


@pytest.mark.asyncio
async def test_door_events_never_touch_the_boost_tracker():
    """Boost arming is scoped to window events only - door transitions are untouched."""
    bt = _make_bt(cur_temp=23.0, door_open=True)
    await _run_door_queue(bt, True, "open")
    assert bt._boost_heater_tracker.open_temp is None

    bt.cur_temp = 10.0  # a huge apparent "drop", but via the door, not the window
    await _run_door_queue(bt, False, "closed")
    assert bt._boost_heater_tracker.boost_active is False


@pytest.mark.asyncio
async def test_reopening_mid_boost_cancels_it_and_a_fresh_close_reevaluates():
    """A window reopen while boosting cancels the boost (via control_boost()'s
    own contact_open guard, exercised in test_control_boost_heater.py); this test
    only confirms the tracker's own re-arm-from-scratch behavior on a fresh cycle."""
    bt = _make_bt(cur_temp=23.0)
    await _run_window_queue(bt, True, "open")
    bt.cur_temp = 16.0
    await _run_window_queue(bt, False, "closed")
    assert bt._boost_heater_tracker.boost_direction == HVACMode.HEAT

    # Simulate control_boost() cancelling on the reopen.
    bt._boost_heater_tracker.clear()
    assert bt._boost_heater_tracker.boost_active is False

    # A fresh open/close cycle re-evaluates independently - reset to a clean
    # baseline first, since cur_temp is still 16.0 from the previous cycle.
    bt.cur_temp = 23.0
    await _run_window_queue(bt, True, "open")
    bt.cur_temp = 22.5  # small drift this time
    await _run_window_queue(bt, False, "closed")
    assert bt._boost_heater_tracker.boost_active is False
