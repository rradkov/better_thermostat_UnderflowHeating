"""Tests for the Warm Floor and Boost diagnostic status sensors.

Both read a small status dict apply_warm_floor_floor()/update_boost_trend()
publish onto the climate entity each cycle, rather than deriving anything
themselves - kept intentionally dumb so the sensor can't drift from what the
control logic actually decided.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from homeassistant.components.climate.const import HVACMode

from custom_components.better_thermostat.sensor import (
    BetterThermostatBoostStatusSensor,
    BetterThermostatWarmFloorStatusSensor,
)

DOMAIN = "better_thermostat"


def _make_bt_climate(**overrides):
    bt = MagicMock()
    bt.unique_id = "test_bt_123"
    bt.device_info = {"identifiers": {(DOMAIN, "test_bt_123")}}
    for k, v in overrides.items():
        setattr(bt, k, v)
    return bt


# ---------------------------------------------------------------------------
# Warm Floor status sensor
# ---------------------------------------------------------------------------


def test_no_status_published_reads_disabled():
    """Before any calibration cycle has run (or no heater is underfloor)."""
    bt = _make_bt_climate()
    del bt._warm_floor_status  # MagicMock would otherwise auto-vivify a Mock
    sensor = BetterThermostatWarmFloorStatusSensor(bt)
    sensor._update_state()
    assert sensor._attr_native_value == "disabled"


def test_active_status_reads_active_with_attributes():
    bt = _make_bt_climate(
        _warm_floor_status={
            "active": True,
            "entity_id": "climate.ufh_living_room",
            "sustaining_setpoint_c": 21.2,
            "backoff_c": 0.8,
        }
    )
    sensor = BetterThermostatWarmFloorStatusSensor(bt)
    sensor._update_state()
    assert sensor._attr_native_value == "active"
    assert sensor._attr_extra_state_attributes["entity_id"] == "climate.ufh_living_room"
    assert sensor._attr_extra_state_attributes["backoff_c"] == 0.8


def test_idle_status_reads_idle():
    bt = _make_bt_climate(
        _warm_floor_status={
            "active": False,
            "entity_id": "climate.ufh_living_room",
            "sustaining_setpoint_c": None,
            "backoff_c": None,
        }
    )
    sensor = BetterThermostatWarmFloorStatusSensor(bt)
    sensor._update_state()
    assert sensor._attr_native_value == "idle"


# ---------------------------------------------------------------------------
# Boost status sensor
# ---------------------------------------------------------------------------


def test_no_status_published_reads_off():
    bt = _make_bt_climate()
    del bt._boost_status
    sensor = BetterThermostatBoostStatusSensor(bt)
    sensor._update_state()
    assert sensor._attr_native_value == "off"


def test_inactive_status_reads_off():
    bt = _make_bt_climate(
        _boost_status={
            "active": False,
            "direction": None,
            "elapsed_s": None,
            "cooler_entity_id": "climate.fancoil",
        }
    )
    sensor = BetterThermostatBoostStatusSensor(bt)
    sensor._update_state()
    assert sensor._attr_native_value == "off"


def test_active_heat_status_reads_heating():
    bt = _make_bt_climate(
        _boost_status={
            "active": True,
            "direction": HVACMode.HEAT,
            "elapsed_s": 42.3,
            "cooler_entity_id": "climate.fancoil",
        }
    )
    sensor = BetterThermostatBoostStatusSensor(bt)
    sensor._update_state()
    assert sensor._attr_native_value == "heating"
    assert sensor._attr_extra_state_attributes["elapsed_s"] == 42.3


def test_active_cool_status_reads_cooling():
    bt = _make_bt_climate(
        _boost_status={
            "active": True,
            "direction": HVACMode.COOL,
            "elapsed_s": 10.0,
            "cooler_entity_id": "climate.fancoil",
        }
    )
    sensor = BetterThermostatBoostStatusSensor(bt)
    sensor._update_state()
    assert sensor._attr_native_value == "cooling"
