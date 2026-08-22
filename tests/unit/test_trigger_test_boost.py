"""Tests for trigger_test_boost() - the better_thermostat.test_boost service backend.

Lets a user manually arm a boost cycle to sanity-check wiring (target
device, fan mode, threshold reachability) without needing to actually open
and close a window. From the moment it arms, behavior is identical to a
normally-armed boost - this only covers the manual arming path itself.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.components.climate.const import HVACMode
import pytest

from custom_components.better_thermostat.utils.boost_heater import (
    BoostHeaterTracker,
    trigger_test_boost,
)

ENTITY_ID = "climate.fancoil"


def _make_bt(
    *,
    boost_enabled=True,
    cooler_entity_id=ENTITY_ID,
    hvac_modes=("off", "heat", "cool"),
):
    bt = MagicMock()
    bt.device_name = "Test BT"
    bt.boost_enabled = boost_enabled
    bt.cooler_entity_id = cooler_entity_id
    bt._boost_heater_tracker = BoostHeaterTracker()
    bt.hass.states.get.return_value = MagicMock(
        attributes={"hvac_modes": list(hvac_modes)}
    )
    return bt


@pytest.mark.asyncio
async def test_disabled_instance_is_a_no_op():
    bt = _make_bt(boost_enabled=False)
    with patch(
        "custom_components.better_thermostat.utils.boost_heater.control_boost",
        new=AsyncMock(),
    ) as mock_control_boost:
        await trigger_test_boost(bt, HVACMode.HEAT)
        mock_control_boost.assert_not_called()
    assert bt._boost_heater_tracker.boost_active is False


@pytest.mark.asyncio
async def test_no_cooler_configured_is_a_no_op():
    bt = _make_bt(cooler_entity_id=None)
    with patch(
        "custom_components.better_thermostat.utils.boost_heater.control_boost",
        new=AsyncMock(),
    ) as mock_control_boost:
        await trigger_test_boost(bt, HVACMode.HEAT)
        mock_control_boost.assert_not_called()
    assert bt._boost_heater_tracker.boost_active is False


@pytest.mark.asyncio
async def test_unsupported_direction_is_not_armed():
    bt = _make_bt(hvac_modes=("off", "heat"))  # no cool support
    with patch(
        "custom_components.better_thermostat.utils.boost_heater.control_boost",
        new=AsyncMock(),
    ) as mock_control_boost:
        await trigger_test_boost(bt, HVACMode.COOL)
        mock_control_boost.assert_not_called()
    assert bt._boost_heater_tracker.boost_active is False


@pytest.mark.asyncio
async def test_supported_direction_arms_the_tracker_and_runs_a_boost_cycle():
    bt = _make_bt()
    with patch(
        "custom_components.better_thermostat.utils.boost_heater.control_boost",
        new=AsyncMock(),
    ) as mock_control_boost:
        await trigger_test_boost(bt, HVACMode.HEAT)
        mock_control_boost.assert_called_once_with(bt)
    assert bt._boost_heater_tracker.boost_direction == HVACMode.HEAT
    assert (
        bt._boost_heater_tracker.boost_started_ts is None
    )  # left for control_boost() to stamp


@pytest.mark.asyncio
async def test_cool_direction_arms_symmetrically():
    bt = _make_bt()
    with patch(
        "custom_components.better_thermostat.utils.boost_heater.control_boost",
        new=AsyncMock(),
    ):
        await trigger_test_boost(bt, HVACMode.COOL)
    assert bt._boost_heater_tracker.boost_direction == HVACMode.COOL
