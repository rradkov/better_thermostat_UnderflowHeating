"""End-to-end QA scenario tests for Boost's control_queue() wiring.

Unlike the narrower unit tests (test_boost_heater_tracker.py,
test_control_boost_heater.py), these exercise the *real* control_queue()
loop and the real control_boost()/update_boost_trend() functions together,
covering two things a purely function-level test can't:

1. Routing exclusivity - control_boost() and control_cooler() must never both
   run in the same cycle, and the loop must switch between them correctly as
   a boost arms and later clears.
2. The exact regression update_boost_trend() fixes - before it existed,
   BoostHeaterTracker.slow_ema was only ever fed from inside control_boost(),
   so a freshly-armed boost had no real history and its `trend_confirmed`
   guard (`tracker.slow_ema is None or ...`) defaulted to permissive. That
   let a single noisy fast-reading blip trigger an early stop the room's
   real (still-cold) slow-moving state did not support. Feeding the EMA
   every idle cycle - not just while boosting - closes that hole.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, Mock, patch

from homeassistant.components.climate.const import HVACAction, HVACMode
from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.core import State
import pytest

from custom_components.better_thermostat.utils.boost_heater import (
    BoostHeaterTracker,
    control_boost,
    update_boost_trend,
)
from custom_components.better_thermostat.utils.controlling import control_queue

ENTITY_ID = "climate.fancoil"


def _make_self(**overrides):
    """A minimal BetterThermostat mock wired for the boost/cooler routing branch."""
    bt = Mock()
    bt.device_name = "Test BT"
    bt.in_maintenance = False
    bt.ignore_states = False
    bt.startup_running = False
    bt.calculate_heating_power = AsyncMock()
    bt.calculate_heat_loss = AsyncMock()
    bt.real_trvs = {}
    bt.cooler_entity_id = ENTITY_ID
    bt.hass = Mock()
    bt.hass.services = Mock()
    bt.hass.services.async_call = AsyncMock()
    bt.boost_enabled = True
    bt.boost_fan_mode = None
    bt.boost_lag_minutes = 5.0
    bt.boost_max_runtime_minutes = 60.0
    bt.bt_hvac_mode = HVACMode.HEAT
    bt.contact_open = False
    bt.call_for_heat = True
    bt.bt_target_temp = 22.0
    bt.tolerance = 0.5
    bt.cur_temp = 19.0
    bt.cur_temp_filtered = 19.0
    bt.temp_slope = None
    bt._boost_heater_tracker = BoostHeaterTracker()
    for key, value in overrides.items():
        setattr(bt, key, value)
    return bt


def _cooler_state(hvac_mode=HVACMode.OFF, temperature=22.0):
    return State(ENTITY_ID, str(hvac_mode), {"temperature": temperature})


async def _run_one_cycle(bt):
    """Push one control cycle through the real control_queue() loop and let it settle."""
    queue = asyncio.Queue()
    bt.control_queue_task = queue
    await queue.put(bt)
    task = asyncio.create_task(control_queue(bt))
    await asyncio.sleep(0.05)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


class TestBoostVsCoolerRoutingExclusivity:
    """control_boost() and control_cooler() must never both fire in one cycle."""

    @pytest.mark.asyncio
    async def test_idle_cycle_routes_to_cooler_not_boost(self):
        bt = _make_self()
        bt.hass.states.get.return_value = _cooler_state()
        with (
            patch(
                "custom_components.better_thermostat.utils.controlling.control_cooler",
                new=AsyncMock(),
            ) as mock_cooler,
            patch(
                "custom_components.better_thermostat.utils.controlling.control_boost",
                new=AsyncMock(),
            ) as mock_boost,
        ):
            await _run_one_cycle(bt)
            mock_cooler.assert_called_once_with(bt)
            mock_boost.assert_not_called()

    @pytest.mark.asyncio
    async def test_armed_boost_routes_to_boost_not_cooler(self):
        bt = _make_self()
        bt._boost_heater_tracker.boost_direction = HVACMode.HEAT
        bt.hass.states.get.return_value = _cooler_state()
        with (
            patch(
                "custom_components.better_thermostat.utils.controlling.control_cooler",
                new=AsyncMock(),
            ) as mock_cooler,
            patch(
                "custom_components.better_thermostat.utils.controlling.control_boost",
                new=AsyncMock(),
            ) as mock_boost,
        ):
            await _run_one_cycle(bt)
            mock_boost.assert_called_once_with(bt)
            mock_cooler.assert_not_called()

    @pytest.mark.asyncio
    async def test_control_reverts_to_cooler_the_cycle_after_boost_clears(self):
        """A boost that completes mid-cycle must not leave the loop stuck on boost."""
        bt = _make_self()
        bt.hass.states.get.return_value = _cooler_state(hvac_mode=HVACMode.OFF)

        async def _clearing_boost(self):
            self._boost_heater_tracker.boost_direction = None

        with (
            patch(
                "custom_components.better_thermostat.utils.controlling.control_cooler",
                new=AsyncMock(),
            ) as mock_cooler,
            patch(
                "custom_components.better_thermostat.utils.controlling.control_boost",
                new=AsyncMock(side_effect=_clearing_boost),
            ) as mock_boost,
        ):
            bt._boost_heater_tracker.boost_direction = HVACMode.HEAT
            await _run_one_cycle(bt)
            mock_boost.assert_called_once()
            mock_cooler.assert_not_called()

            # Next cycle: boost_direction is now None, routing must have moved on.
            await _run_one_cycle(bt)
            mock_cooler.assert_called_once_with(bt)


class TestBoostTrendHasStandingMemoryBeforeBoostStarts:
    """The regression update_boost_trend() fixes.

    Without it, `tracker.slow_ema` stayed None through every idle cycle and
    only started accumulating once a boost began - so the very first
    anticipatory-stop check of a fresh boost always had `trend_confirmed`
    default to True (the `tracker.slow_ema is None or ...` fallback),
    regardless of the room's real recent history. That let a single noisy
    fast-reading blip stop the boost early.
    """

    @pytest.mark.asyncio
    async def test_idle_cycles_seed_slow_ema_via_control_queue_before_any_boost(self):
        """update_boost_trend() runs every control_queue() cycle, boost or not."""
        bt = _make_self(cur_temp=19.0, cur_temp_filtered=19.0)
        bt.hass.states.get.return_value = _cooler_state()
        assert bt._boost_heater_tracker.slow_ema is None

        with patch(
            "custom_components.better_thermostat.utils.controlling.control_cooler",
            new=AsyncMock(),
        ):
            await _run_one_cycle(bt)

        # Standing memory exists even though a boost never ran this cycle.
        assert bt._boost_heater_tracker.slow_ema == pytest.approx(19.0)

    @pytest.mark.asyncio
    async def test_real_cold_history_withholds_an_early_stop_on_a_hot_blip(self):
        """A single warm reading must not stop the boost while true history is cold."""
        bt = _make_self(cur_temp=19.0, cur_temp_filtered=19.0)

        # Several idle cycles at a genuinely cold reading - the room's real
        # slow-moving state, seeded exactly as it would be in production via
        # update_boost_trend() every cycle.
        for _ in range(3):
            update_boost_trend(bt)
        assert bt._boost_heater_tracker.slow_ema == pytest.approx(19.0)

        # Window opens and closes with a big drop -> boost arms, and has been
        # actively heating for a while (as it would be in a real run) - the
        # anticipatory-stop path is only reachable once still_needs_heat's own
        # hysteresis is already latched onto HEATING.
        bt._boost_heater_tracker.boost_direction = HVACMode.HEAT
        bt._boost_heater_tracker.last_action = HVACAction.HEATING

        # A single noisy fast reading spikes near target with a rising slope -
        # exactly the shape that would trip the anticipatory early-stop check
        # if trend_confirmed defaulted to True.
        bt.cur_temp = 21.8
        bt.cur_temp_filtered = 21.8
        bt.temp_slope = 0.1  # projected = 21.8 + 0.1*5 = 22.3 >= target
        bt.hass.states.get.return_value = _cooler_state(hvac_mode=HVACMode.HEAT)

        await control_boost(bt)

        # The blip alone must not have stopped it: real recent history (19.0)
        # is nowhere near target - tolerance (21.5).
        assert bt._boost_heater_tracker.boost_active is True

    @pytest.mark.asyncio
    async def test_same_blip_does_stop_the_boost_once_the_slow_trend_truly_agrees(self):
        """Sanity check: the guard isn't just permanently stuck closed either."""
        bt = _make_self(cur_temp=19.0, cur_temp_filtered=19.0)
        bt._boost_heater_tracker.boost_direction = HVACMode.HEAT
        bt._boost_heater_tracker.last_action = HVACAction.HEATING

        # Force the slow EMA to have genuinely converged near target already
        # (equivalent to a long, real boost run having been underway).
        bt._boost_heater_tracker.slow_ema = 21.9
        bt._boost_heater_tracker._slow_ema_ts = 0.0

        bt.cur_temp = 21.8
        bt.cur_temp_filtered = 21.8
        bt.temp_slope = 0.1
        bt.hass.states.get.return_value = _cooler_state(hvac_mode=HVACMode.HEAT)

        await control_boost(bt)

        assert bt._boost_heater_tracker.boost_active is False

    @pytest.mark.asyncio
    async def test_update_boost_trend_is_a_no_op_when_boost_disabled(self):
        bt = _make_self(boost_enabled=False, cur_temp=19.0, cur_temp_filtered=19.0)
        update_boost_trend(bt)
        assert bt._boost_heater_tracker.slow_ema is None

    @pytest.mark.asyncio
    async def test_unavailable_cooler_state_is_a_no_op_through_control_queue(self):
        """A cooler/boost device gone unavailable must not crash the control loop."""
        bt = _make_self()
        bt._boost_heater_tracker.boost_direction = HVACMode.HEAT
        bt.hass.states.get.return_value = State(ENTITY_ID, STATE_UNAVAILABLE)

        await _run_one_cycle(bt)

        bt.hass.services.async_call.assert_not_called()
        # Still armed - an unavailable device doesn't silently cancel the boost.
        assert bt._boost_heater_tracker.boost_active is True
