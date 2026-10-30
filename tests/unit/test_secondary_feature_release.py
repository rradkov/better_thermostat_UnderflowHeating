"""Production isolation of the experimental secondary-device Boost."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

from homeassistant.components.climate.const import HVACMode
import pytest
import yaml

from custom_components.better_thermostat import climate as climate_module, config_flow
from custom_components.better_thermostat.climate import BetterThermostat
from custom_components.better_thermostat.events.contact import WINDOW
from custom_components.better_thermostat.utils import features
from custom_components.better_thermostat.utils.boost_heater import (
    CONF_BOOST_HEATER_ENABLED,
    BoostHeaterTracker,
    control_boost,
    record_window_transition,
    trigger_test_boost,
    update_boost_trend,
)
from custom_components.better_thermostat.utils.const import CONF_COOLER
from custom_components.better_thermostat.utils.controlling import control_queue

RELEASE_BOOST_AVAILABLE = features.SECONDARY_BOOST_AVAILABLE


@pytest.fixture(autouse=True)
def production_features(monkeypatch):
    monkeypatch.setattr(features, "SECONDARY_BOOST_AVAILABLE", False)


@pytest.mark.parametrize("mode", ["create", "options"])
def test_previously_enabled_boost_is_hidden_and_cannot_be_reenabled(mode):
    saved = {CONF_COOLER: "climate.secondary", CONF_BOOST_HEATER_ENABLED: True}
    fields = config_flow._build_user_fields(mode=mode, current=saved, hass=Mock())
    assert not any(
        str(getattr(key, "schema", key)).startswith("boost_") for key in fields
    )
    normalized = config_flow._normalize_user_submission(saved, mode=mode, base=saved)
    assert normalized[CONF_BOOST_HEATER_ENABLED] is False


@pytest.mark.parametrize("available", [False, True])
async def test_service_registration_obeys_release_policy(monkeypatch, available):
    monkeypatch.setattr(features, "SECONDARY_BOOST_AVAILABLE", available)
    entry = Mock(data={}, options={})
    platform = Mock()
    with (
        patch.object(
            climate_module.entity_platform,
            "async_get_current_platform",
            return_value=platform,
        ),
        patch.object(
            climate_module,
            "BetterThermostat",
            side_effect=RuntimeError("stop after registration"),
        ),
        pytest.raises(RuntimeError, match="stop after registration"),
    ):
        await climate_module.async_setup_entry(Mock(), entry, Mock())
    registered = {
        call.args[0] for call in platform.async_register_entity_service.call_args_list
    }
    assert ("test_boost" in registered) == available
    assert "reset_heating_power" in registered


async def test_all_boost_entry_points_are_inert_even_with_saved_enabled_state():
    bt = Mock()
    bt.boost_enabled = True
    bt.cooler_entity_id = "climate.secondary"
    bt._boost_heater_tracker = BoostHeaterTracker()
    bt._boost_heater_tracker.boost_direction = HVACMode.HEAT
    bt.hass.services.async_call = AsyncMock()
    before = repr(bt._boost_heater_tracker)

    record_window_transition(bt, WINDOW, True)
    record_window_transition(bt, WINDOW, False)
    update_boost_trend(bt)
    await control_boost(bt)
    await trigger_test_boost(bt, HVACMode.HEAT)
    await BetterThermostat.test_boost_service(bt)

    assert repr(bt._boost_heater_tracker) == before
    bt.hass.services.async_call.assert_not_awaited()
    bt.hass.states.get.assert_not_called()


async def test_armed_boost_cannot_preempt_normal_cooler_in_production():
    bt = Mock()
    bt.device_name = "Release test"
    bt.in_maintenance = False
    bt.ignore_states = False
    bt.startup_running = False
    bt.calculate_heating_power = AsyncMock()
    bt.calculate_heat_loss = AsyncMock()
    bt.real_trvs = {}
    bt.cooler_entity_id = "climate.secondary"
    bt.boost_enabled = True
    bt._boost_heater_tracker = BoostHeaterTracker()
    bt._boost_heater_tracker.boost_direction = HVACMode.HEAT
    bt.control_queue_task = asyncio.Queue()
    await bt.control_queue_task.put(bt)

    with (
        patch(
            "custom_components.better_thermostat.utils.controlling.control_cooler",
            new_callable=AsyncMock,
        ) as cooler,
        patch(
            "custom_components.better_thermostat.utils.controlling.control_boost",
            new_callable=AsyncMock,
        ) as boost,
        patch(
            "custom_components.better_thermostat.utils.controlling.update_boost_trend"
        ) as trend,
    ):
        task = asyncio.create_task(control_queue(bt))
        try:
            await asyncio.wait_for(bt.control_queue_task.join(), timeout=2)
        finally:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
        cooler.assert_awaited_once_with(bt)
        boost.assert_not_awaited()
        trend.assert_not_called()


def test_production_service_catalog_does_not_offer_test_boost():
    root = Path(__file__).resolve().parents[2]
    services = yaml.safe_load(
        (root / "custom_components/better_thermostat/services.yaml").read_text(
            encoding="utf-8"
        )
    )
    assert ("test_boost" in services) == RELEASE_BOOST_AVAILABLE
