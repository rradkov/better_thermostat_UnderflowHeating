"""Tests for ``calibration.py``'s ``_get_current_flow_temp_c()``.

Resolves the live heat-source (flow/water) temperature MPC v2's plant model
uses instead of its hardcoded 65.0°C prior - sensor (if configured and
available) takes priority over the static fallback, and ``None`` means
neither is configured, leaving the caller's own default untouched.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from custom_components.better_thermostat.calibration import _get_current_flow_temp_c


def _make_bt(**overrides) -> SimpleNamespace:
    bt = SimpleNamespace(
        device_name="Test BT",
        flow_temp_sensor=None,
        flow_temp_static_c=None,
        hass=MagicMock(),
    )
    for key, value in overrides.items():
        setattr(bt, key, value)
    return bt


def _mock_state(state: str, *, unit: str | None = "°C") -> SimpleNamespace:
    return SimpleNamespace(state=state, attributes={"unit_of_measurement": unit})


def test_neither_sensor_nor_static_returns_none():
    bt = _make_bt()
    assert _get_current_flow_temp_c(bt) is None


def test_static_fallback_used_when_no_sensor_configured():
    bt = _make_bt(flow_temp_static_c=42.5)
    assert _get_current_flow_temp_c(bt) == 42.5


def test_sensor_reading_takes_priority_over_static_fallback():
    bt = _make_bt(flow_temp_sensor="sensor.flow_temp", flow_temp_static_c=42.5)
    bt.hass.states.get.return_value = _mock_state("38.2")
    assert _get_current_flow_temp_c(bt) == 38.2


def test_sensor_converts_fahrenheit_to_celsius():
    bt = _make_bt(flow_temp_sensor="sensor.flow_temp")
    bt.hass.states.get.return_value = _mock_state("104", unit="°F")
    result = _get_current_flow_temp_c(bt)
    assert result is not None
    assert abs(result - 40.0) < 0.01


def test_missing_sensor_state_falls_back_to_static():
    """The sensor entity has no state at all (never created/removed)."""
    bt = _make_bt(flow_temp_sensor="sensor.flow_temp", flow_temp_static_c=42.5)
    bt.hass.states.get.return_value = None
    assert _get_current_flow_temp_c(bt) == 42.5


def test_unconvertible_sensor_state_falls_back_to_static():
    """An 'unavailable'/'unknown' sensor reading falls through, same as a
    missing state - convert_to_float_celsius returns None for it."""
    bt = _make_bt(flow_temp_sensor="sensor.flow_temp", flow_temp_static_c=42.5)
    bt.hass.states.get.return_value = _mock_state("unavailable")
    assert _get_current_flow_temp_c(bt) == 42.5


def test_sensor_configured_but_unresolvable_and_no_static_returns_none():
    bt = _make_bt(flow_temp_sensor="sensor.flow_temp")
    bt.hass.states.get.return_value = None
    assert _get_current_flow_temp_c(bt) is None
