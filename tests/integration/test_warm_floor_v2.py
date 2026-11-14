"""Warm Floor commands and migration through a running Home Assistant entry."""

from dataclasses import replace
from unittest.mock import patch

from homeassistant.components.climate import HVACMode
from homeassistant.core import State
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant.common import MockConfigEntry, mock_restore_cache

from custom_components.better_thermostat.core.clock import FakeClock
from custom_components.better_thermostat.core.warm_floor import (
    WarmFloorPhase,
    WarmFloorState,
)
from custom_components.better_thermostat.diagnostics import (
    async_get_config_entry_diagnostics,
)
from custom_components.better_thermostat.utils.scheduler import request_control_cycle

from .conftest import (
    BT_ENTITY,
    DOMAIN,
    WRITE_BUDGET,
    build_devices,
    click_through_the_options,
    make_entry,
    set_room_sensor,
    setup_entry,
    wait_for_startup,
)
from .device_profiles import GENERIC_HEAT_TRV, SPARE_HEAT_TRV, GroupScenario

FLOOR_PAIR = GroupScenario("floor_pair", (GENERIC_HEAT_TRV, SPARE_HEAT_TRV))


async def start_floor(hass, *, saved=True, boost=False, custom=False):
    set_room_sensor(hass, 23.4)
    await build_devices(hass, *FLOOR_PAIR.profiles)
    template = make_entry(FLOOR_PAIR)
    data = dict(template.data)
    data["underfloor_heating_enabled"] = True
    if custom:
        data["flow_temp_static_c"] = 38.0
        data["flow_temp_sensor"] = "sensor.flow"
        hass.states.async_set("sensor.flow", "36.0", {"unit_of_measurement": "°C"})
    data["thermostat"] = [
        {
            **bundle,
            "advanced": {
                **bundle["advanced"],
                "heating_type": "underfloor",
                "warm_floor_level": "custom" if custom else "level_2",
                "warm_floor_max_backoff": 0.8,
                "warm_floor_sustain_push": 0.4,
            },
        }
        for bundle in template.data["thermostat"]
    ]
    if boost:
        data["boost_heater_enabled"] = True
        data["boost_threshold"] = 5.0
        data["presets"] = ["boost"]
    entry = MockConfigEntry(domain=DOMAIN, version=18, data=data, title="BT Test")
    if saved:
        mock_restore_cache(hass, (State(BT_ENTITY, "heat", {"temperature": 23.0}),))
    if boost:
        entry.add_to_hass(hass)
        er.async_get(hass).async_get_or_create(
            "sensor", DOMAIN, f"{entry.entry_id}_boost_status", config_entry=entry
        )
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    else:
        await setup_entry(hass, entry)
    host = await wait_for_startup(hass, entry)
    host.clock = FakeClock(host.clock.monotonic(), host.clock.now())
    return entry, host


async def cycle(host):
    with patch(WRITE_BUDGET, 0.0):
        request_control_cycle(host)
        await host.control_queue_task.join()
        await host.hass.async_block_till_done()


async def test_two_circuits_receive_pulses_then_release_without_changing_room_target(
    hass,
):
    entry, host = await start_floor(hass)
    for trv in host.real_trvs.values():
        trv.warm_floor_state = WarmFloorState(WarmFloorPhase.ON, host.clock.monotonic())
    await cycle(host)
    assert host.heat_target_temperature == 23.0
    for entity_id, trv in host.real_trvs.items():
        assert hass.states.get(entity_id).attributes["temperature"] > 23.4
        assert trv.warm_floor_status["phase"] == "on"
        assert trv.warm_floor_status["write_result"] in ("sent", "held")
    assert isinstance(host.clock, FakeClock)
    host.clock.advance(1000.0)
    await host._warm_floor_tick(host.clock.now())
    await host.control_queue_task.join()
    for entity_id in host.real_trvs:
        assert hass.states.get(entity_id).attributes["temperature"] < 23.0
    assert host.heat_target_temperature == 23.0
    assert entry.options["underfloor_heating_enabled"] is True
    diagnostics = await async_get_config_entry_diagnostics(hass, entry)
    circuits = diagnostics["warm_floor"]
    assert isinstance(circuits, dict)
    assert set(circuits) == set(host.real_trvs)


async def test_one_unavailable_circuit_does_not_prevent_the_other_pulse(hass):
    _, host = await start_floor(hass)
    first, second = list(host.real_trvs)
    hass.states.async_set(first, "unavailable")
    host.real_trvs[second].warm_floor_state = WarmFloorState(
        WarmFloorPhase.ON, host.clock.monotonic()
    )
    await cycle(host)
    assert hass.states.get(second).attributes["temperature"] > 23.4
    assert hass.states.get(first).state == "unavailable"


async def test_off_and_release_keep_priority_over_floor_maintenance(hass):
    _, host = await start_floor(hass)
    await host.async_set_hvac_mode(HVACMode.OFF)
    await cycle(host)
    assert all(
        hass.states.get(entity_id).state == "off" for entity_id in host.real_trvs
    )
    host.devices_released = True
    before = {entity_id: hass.states.get(entity_id) for entity_id in host.real_trvs}
    await cycle(host)
    assert before == {
        entity_id: hass.states.get(entity_id) for entity_id in host.real_trvs
    }


async def test_secondary_boost_is_retired_but_normal_preset_remains(hass):
    entry, host = await start_floor(hass, boost=True)
    assert "boost_heater_enabled" not in entry.options
    assert "boost_threshold" not in entry.options
    assert not hass.services.has_service(DOMAIN, "test_boost")
    assert "boost" in host.preset_modes
    registry = er.async_get(hass)
    assert (
        registry.async_get_entity_id("sensor", DOMAIN, f"{host.unique_id}_boost_status")
        is None
    )
    assert (
        registry.async_get_entity_id(
            "sensor", DOMAIN, f"{host.unique_id}_warm_floor_status"
        )
        is not None
    )


async def test_fresh_room_does_not_adopt_a_temporary_floor_setpoint(hass):
    # A heater setpoint above the room may have been a maintenance pulse.
    set_room_sensor(hass, 23.4)
    profile = replace(GENERIC_HEAT_TRV, target_temperature=28.0)
    await build_devices(hass, profile)
    template = make_entry(profile)
    data = dict(template.data)
    data["underfloor_heating_enabled"] = True
    data["thermostat"] = [
        {**bundle, "advanced": {**bundle["advanced"], "heating_type": "underfloor"}}
        for bundle in template.data["thermostat"]
    ]
    entry = MockConfigEntry(domain=DOMAIN, version=18, data=data, title="BT Test")
    await setup_entry(hass, entry)
    host = await wait_for_startup(hass, entry)
    assert host.heat_target_temperature != 28.0


async def test_custom_settings_survive_the_options_form_and_flow_updates(hass):
    entry, host = await start_floor(hass, custom=True)
    hass.states.async_set("sensor.flow", "37.0", {"unit_of_measurement": "°C"})
    await hass.async_block_till_done()
    await host.control_queue_task.join()
    await click_through_the_options(hass, entry)
    assert entry.options["flow_temp_static_c"] == 38.0
    assert entry.options["flow_temp_sensor"] == "sensor.flow"
    for bundle in entry.options["thermostat"]:
        assert bundle["advanced"]["warm_floor_level"] == "custom"
        assert bundle["advanced"]["warm_floor_max_backoff"] == 0.8
        assert bundle["advanced"]["warm_floor_sustain_push"] == 0.4
