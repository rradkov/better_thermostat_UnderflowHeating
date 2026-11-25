"""BT's slow filter resumes across process and host restarts without fake history."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from custom_components.better_thermostat.climate import BetterThermostat
from custom_components.better_thermostat.core.clock import FakeClock
from custom_components.better_thermostat.events import temperature as temperature_events
from custom_components.better_thermostat.utils.state_manager import (
    FilterState,
    StateManager,
    _deserialize,
    _serialize,
)
from tests.factories import make_bt

NOW = datetime(2026, 11, 25, 12, tzinfo=UTC)


def thermostat(hass, *, uptime=10000.0):
    bt = make_bt()
    bt.heating_power = 0.01
    bt.heat_loss_rate = 0.005
    bt.temperature_slope = None
    bt.state_mgr = StateManager(hass, "slow-filter")
    bt.state_loaded = True
    bt.clock = FakeClock(monotonic_value=uptime, now_value=NOW)
    bt.room_temperature_ema_tau_seconds = 300.0
    bt.room_temperature_ema = None
    bt._room_temperature_ema_monotonic = None
    return bt


def reading(bt, monkeypatch, temperature, seconds=0):
    bt.clock.advance(seconds)
    monkeypatch.setattr(temperature_events, "monotonic", bt.clock.monotonic)
    temperature_events._update_room_temperature_ema(bt, temperature)


def roundtrip(hass, bt, *, gap, uptime):
    BetterThermostat._record_runtime_to_state(bt)
    stored = _deserialize(_serialize(bt.state_mgr.state))
    restored = thermostat(hass, uptime=uptime)
    restored.clock = FakeClock(
        monotonic_value=uptime, now_value=bt.clock.utcnow() + timedelta(seconds=gap)
    )
    restored.state_mgr.record_filters(stored.filters)
    BetterThermostat._hydrate_thermal_from_state(restored)
    return restored


@pytest.mark.parametrize("uptime", [60.0, 50000.0])
@pytest.mark.parametrize("gap", [0.0, 30.0, 300.0, 1800.0])
def test_mature_filter_has_a_slope_on_the_first_live_reading(
    hass, monkeypatch, uptime, gap
):
    bt = thermostat(hass)
    reading(bt, monkeypatch, 24)
    reading(bt, monkeypatch, 23, 3600)
    old_hour = bt.room_temperature_ema_1h
    assert bt.temperature_slope_1h is not None
    restored = roundtrip(hass, bt, gap=gap, uptime=uptime)
    assert restored.room_temperature_ema_1h == old_hour
    assert restored.temperature_slope_1h is None
    reading(restored, monkeypatch, 23)
    assert restored.temperature_slope_1h is not None
    assert restored.room_temperature_ema_1h is not None
    assert restored.temperature_slope_1h == pytest.approx(
        (restored.room_temperature_ema - restored.room_temperature_ema_1h) / 60
    )


def test_shutdown_does_not_count_time_after_the_last_filter_update(hass, monkeypatch):
    bt = thermostat(hass)
    reading(bt, monkeypatch, 24)
    reading(bt, monkeypatch, 23, 600)
    bt.clock.advance(100)
    BetterThermostat._record_runtime_to_state(bt)
    assert bt.state_mgr.filters.room_temperature_ema_1h_observed_seconds == 600
    assert (
        bt.state_mgr.filters.room_temperature_ema_recorded_at
        == bt.clock.utcnow().timestamp() - 100
    )


def test_restart_resumes_partial_warmup_without_counting_downtime(hass, monkeypatch):
    bt = thermostat(hass)
    reading(bt, monkeypatch, 24)
    reading(bt, monkeypatch, 23, 600)
    restored = roundtrip(hass, bt, gap=1500, uptime=60)
    reading(restored, monkeypatch, 23)
    assert restored.temperature_slope_1h is None
    reading(restored, monkeypatch, 23, 1199)
    assert restored.temperature_slope_1h is None
    reading(restored, monkeypatch, 23, 1)
    assert restored.temperature_slope_1h is not None


@pytest.mark.parametrize("gap", [1800.01, 3600, 86400, -1, -3600])
def test_long_outages_and_future_wall_stamps_restart_the_slow_filter(
    hass, monkeypatch, gap
):
    bt = thermostat(hass)
    reading(bt, monkeypatch, 24)
    reading(bt, monkeypatch, 23, 3600)
    restored = roundtrip(hass, bt, gap=gap, uptime=60)
    assert restored.room_temperature_ema_1h is None
    reading(restored, monkeypatch, 21)
    assert restored.room_temperature_ema_1h == restored.room_temperature_ema
    assert restored.temperature_slope_1h is None


@pytest.mark.parametrize(
    "field", ["room_temperature_ema_1h", "room_temperature_ema_1h_observed_seconds"]
)
@pytest.mark.parametrize("bad", [None, float("nan"), float("inf")])
def test_missing_or_corrupt_new_metadata_leaves_existing_filters_usable(
    hass, field, bad
):
    bt = thermostat(hass)
    filters = FilterState(
        room_temperature_ema=23,
        temperature_slope=0.01,
        room_temperature_ema_recorded_at=NOW.timestamp(),
        room_temperature_ema_1h=23.2,
        room_temperature_ema_1h_observed_seconds=1800,
    )
    bt.state_mgr.record_filters(replace(filters, **{field: bad}))
    BetterThermostat._hydrate_thermal_from_state(bt)
    assert bt.room_temperature_ema == 23
    assert bt.temperature_slope == 0.01
    assert bt.room_temperature_ema_1h is None


@pytest.mark.parametrize("observed", [-1.0, 1800.01])
def test_invalid_warmup_duration_does_not_manufacture_history(hass, observed):
    bt = thermostat(hass)
    bt.state_mgr.record_filters(
        FilterState(
            room_temperature_ema=23,
            room_temperature_ema_recorded_at=NOW.timestamp(),
            room_temperature_ema_1h=23.2,
            room_temperature_ema_1h_observed_seconds=observed,
        )
    )
    BetterThermostat._hydrate_thermal_from_state(bt)
    assert bt.room_temperature_ema_1h is None


def test_old_stores_keep_short_filter_and_start_slow_filter_normally(hass, monkeypatch):
    state = _deserialize(
        {
            "filters": {
                "external_temp_ema": 23.0,
                "temp_slope": 0.01,
                "room_temperature_ema_recorded_at": NOW.timestamp(),
            }
        }
    )
    bt = thermostat(hass)
    bt.state_mgr.record_filters(state.filters)
    BetterThermostat._hydrate_thermal_from_state(bt)
    assert bt.room_temperature_ema == 23
    reading(bt, monkeypatch, 23)
    assert bt.room_temperature_ema_1h == 23
    assert bt.temperature_slope_1h is None


@pytest.mark.parametrize("restarts", [2, 10])
def test_repeated_restarts_do_not_reset_or_complete_partial_warmup(
    hass, monkeypatch, restarts
):
    bt = thermostat(hass)
    reading(bt, monkeypatch, 24)
    reading(bt, monkeypatch, 23, 600)
    for _ in range(restarts):
        bt = roundtrip(hass, bt, gap=300, uptime=60)
        reading(bt, monkeypatch, 23)
        assert bt.temperature_slope_1h is None
        BetterThermostat._record_runtime_to_state(bt)
        assert bt.state_mgr.filters.room_temperature_ema_1h_observed_seconds == 600


def test_sensor_recovery_still_discards_history_from_the_outage(hass, monkeypatch):
    bt = thermostat(hass)
    reading(bt, monkeypatch, 24)
    reading(bt, monkeypatch, 23, 3600)
    restored = roundtrip(hass, bt, gap=120, uptime=60)
    # The existing sensor-return path clears the short EMA and its stamp.
    restored.room_temperature_ema = None
    restored._room_temperature_ema_monotonic = None
    reading(restored, monkeypatch, 21)
    assert restored.room_temperature_ema_1h == 21
    assert restored.temperature_slope_1h is None


def test_startup_wait_does_not_mature_partial_history(hass, monkeypatch):
    bt = thermostat(hass)
    reading(bt, monkeypatch, 24)
    reading(bt, monkeypatch, 23, 600)
    restored = roundtrip(hass, bt, gap=60, uptime=60)
    reading(restored, monkeypatch, 23, 1200)
    assert restored.temperature_slope_1h is None
    BetterThermostat._record_runtime_to_state(restored)
    assert restored.state_mgr.filters.room_temperature_ema_1h_observed_seconds == 600


def test_a_long_startup_expires_previously_recent_history(hass, monkeypatch):
    bt = thermostat(hass)
    reading(bt, monkeypatch, 24)
    reading(bt, monkeypatch, 23, 3600)
    restored = roundtrip(hass, bt, gap=120, uptime=60)
    reading(restored, monkeypatch, 21, 1800)
    assert restored.room_temperature_ema_1h == restored.room_temperature_ema
    assert restored.temperature_slope_1h is None


def test_restarts_before_a_live_sample_keep_the_original_duration_and_stamp(
    hass, monkeypatch
):
    bt = thermostat(hass)
    reading(bt, monkeypatch, 24)
    reading(bt, monkeypatch, 23, 600)
    recorded_at = bt.clock.utcnow().timestamp()
    for _ in range(5):
        bt = roundtrip(hass, bt, gap=120, uptime=60)
        BetterThermostat._record_runtime_to_state(bt)
        assert bt.state_mgr.filters.room_temperature_ema_1h_observed_seconds == 600
        assert bt.state_mgr.filters.room_temperature_ema_recorded_at == recorded_at


async def test_filter_metadata_survives_the_native_ha_store(hass, monkeypatch):
    bt = thermostat(hass)
    reading(bt, monkeypatch, 24)
    reading(bt, monkeypatch, 23, 3600)
    BetterThermostat._record_runtime_to_state(bt)
    await bt.state_mgr.flush()
    restored = thermostat(hass, uptime=60)
    restored.clock = FakeClock(monotonic_value=60, now_value=bt.clock.utcnow())
    restored.clock.advance(120)
    await restored.state_mgr.load()
    BetterThermostat._hydrate_thermal_from_state(restored)
    reading(restored, monkeypatch, 23)
    assert restored.temperature_slope_1h is not None
