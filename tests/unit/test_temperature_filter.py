import math

import pytest

from custom_components.better_thermostat.climate import BetterThermostat
from custom_components.better_thermostat.core.containers import BtConfig, BtRuntime
from custom_components.better_thermostat.events import temperature as temperature_events


def _thermostat() -> BetterThermostat:
    """Entity shell holding the EMA state, without running __init__."""
    bt = object.__new__(BetterThermostat)
    bt.config = BtConfig(
        sensor_entity_id="sensor.room_temperature", device_name="dummy"
    )
    bt.runtime = BtRuntime(heat_target_temperature=21.0)
    bt.room_temperature_ema_tau_seconds = 900.0
    bt._room_temperature_ema_monotonic = None
    return bt


def test_room_temperature_ema_initializes(monkeypatch):
    """Test that external temperature EMA initializes correctly on first reading."""
    bt = _thermostat()

    monkeypatch.setattr(temperature_events, "monotonic", lambda: 100.0)
    ema = temperature_events._update_room_temperature_ema(bt, 20.0)

    assert ema == 20.0
    assert bt.room_temperature_ema == 20.0
    assert bt.room_temperature_filtered == 20.0


def test_room_temperature_ema_time_based(monkeypatch):
    """Test that external temperature EMA applies time-based smoothing."""
    bt = _thermostat()

    # First sample
    monkeypatch.setattr(temperature_events, "monotonic", lambda: 100.0)
    temperature_events._update_room_temperature_ema(bt, 20.0)

    # Second sample after 900s with tau=900s -> alpha = 1-exp(-1)
    monkeypatch.setattr(temperature_events, "monotonic", lambda: 1000.0)
    ema = temperature_events._update_room_temperature_ema(bt, 21.0)

    alpha = 1.0 - math.exp(-1.0)
    expected = 20.0 + alpha * (21.0 - 20.0)

    assert ema == pytest.approx(expected, rel=1e-6, abs=1e-6)
    assert bt.room_temperature_ema == pytest.approx(expected, rel=1e-6, abs=1e-6)
    assert bt.room_temperature_filtered == round(expected, 2)


def test_room_temperature_ema_zero_dt_no_change(monkeypatch):
    """Test that EMA does not change when time delta is zero."""
    bt = _thermostat()

    monkeypatch.setattr(temperature_events, "monotonic", lambda: 100.0)
    temperature_events._update_room_temperature_ema(bt, 20.0)

    # Same timestamp => alpha=0
    monkeypatch.setattr(temperature_events, "monotonic", lambda: 100.0)
    ema = temperature_events._update_room_temperature_ema(bt, 30.0)

    assert ema == 20.0
    assert bt.room_temperature_filtered == 20.0


@pytest.mark.parametrize("interval", [60, 300, 900])
def test_one_hour_filter_is_shared_and_warms_up_independently_of_sensors(
    monkeypatch, interval
):
    bt = _thermostat()
    monkeypatch.setattr(temperature_events, "monotonic", lambda: 0.0)
    temperature_events._update_room_temperature_ema(bt, 24.0)
    assert bt.room_temperature_ema_1h == 24.0
    assert bt.temperature_slope_1h is None
    for timestamp in range(interval, 3601, interval):
        monkeypatch.setattr(temperature_events, "monotonic", lambda: float(timestamp))
        temperature_events._update_room_temperature_ema(bt, 23.0)
        if timestamp < 1800:
            assert bt.temperature_slope_1h is None
    assert bt.room_temperature_ema_1h is not None
    assert bt.temperature_slope_1h is not None and bt.temperature_slope_1h < 0
    assert bt.room_temperature_ema is not None
    assert bt.temperature_slope_1h == pytest.approx(
        (bt.room_temperature_ema - bt.room_temperature_ema_1h) / 60.0
    )


def test_shared_filter_rejects_backward_time_and_restarts_warmup(monkeypatch):
    bt = _thermostat()
    for timestamp in (100.0, 3700.0):
        monkeypatch.setattr(temperature_events, "monotonic", lambda: timestamp)
        temperature_events._update_room_temperature_ema(bt, 23.0)
    assert bt.temperature_slope_1h == 0.0
    monkeypatch.setattr(temperature_events, "monotonic", lambda: 50.0)
    temperature_events._update_room_temperature_ema(bt, 22.0)
    assert bt.temperature_slope_1h is None


def test_shared_slow_ema_uses_unrounded_bt_input_and_retains_precision(monkeypatch):
    bt = _thermostat()
    monkeypatch.setattr(temperature_events, "monotonic", lambda: 0.0)
    temperature_events._update_room_temperature_ema(bt, 23.0)
    monkeypatch.setattr(temperature_events, "monotonic", lambda: 3600.0)
    temperature_events._update_room_temperature_ema(bt, 22.9)
    assert bt.room_temperature_ema_1h is not None
    assert bt.room_temperature_ema is not None
    expected = 23.0 + (1 - math.exp(-1)) * (bt.room_temperature_ema - 23.0)
    assert bt.room_temperature_ema_1h == pytest.approx(expected)
    before = bt.room_temperature_ema_1h
    temperature_events._update_room_temperature_ema(bt, 20.0)
    assert bt.room_temperature_ema_1h == before
