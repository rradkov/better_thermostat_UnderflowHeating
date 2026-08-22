"""Tests for BetterThermostat.test_boost_service - the entity service backend
for better_thermostat.test_boost.

Confirms the "heat"/"cool" string field is correctly mapped to HVACMode and
delegated to trigger_test_boost(), and that a failure inside it doesn't
raise out of the service call (matching every other entity service in this
file's family - reset_pid_learnings_service, run_valve_maintenance_service).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.components.climate.const import HVACMode
import pytest

from custom_components.better_thermostat.climate import BetterThermostat


@pytest.fixture
def bt():
    mock = MagicMock()
    mock.device_name = "Test BT"
    mock.async_write_ha_state = MagicMock()
    return mock


@pytest.mark.asyncio
async def test_default_direction_is_heat(bt):
    with patch(
        "custom_components.better_thermostat.climate.trigger_test_boost",
        new=AsyncMock(),
    ) as mock_trigger:
        await BetterThermostat.test_boost_service(bt)
        mock_trigger.assert_called_once_with(bt, HVACMode.HEAT)
    bt.async_write_ha_state.assert_called_once()


@pytest.mark.asyncio
async def test_cool_direction_maps_to_hvac_mode_cool(bt):
    with patch(
        "custom_components.better_thermostat.climate.trigger_test_boost",
        new=AsyncMock(),
    ) as mock_trigger:
        await BetterThermostat.test_boost_service(bt, direction="cool")
        mock_trigger.assert_called_once_with(bt, HVACMode.COOL)


@pytest.mark.asyncio
async def test_heat_direction_maps_to_hvac_mode_heat(bt):
    with patch(
        "custom_components.better_thermostat.climate.trigger_test_boost",
        new=AsyncMock(),
    ) as mock_trigger:
        await BetterThermostat.test_boost_service(bt, direction="heat")
        mock_trigger.assert_called_once_with(bt, HVACMode.HEAT)


@pytest.mark.asyncio
async def test_an_exception_is_swallowed_not_raised(bt):
    with patch(
        "custom_components.better_thermostat.climate.trigger_test_boost",
        new=AsyncMock(side_effect=RuntimeError("boom")),
    ):
        await BetterThermostat.test_boost_service(bt)  # must not raise
