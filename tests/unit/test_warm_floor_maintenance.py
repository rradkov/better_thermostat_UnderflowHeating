"""Adaptive Warm Floor pulses for Generic Thermostat / on-off UFH heaters."""

from __future__ import annotations

from unittest.mock import MagicMock

from homeassistant.components.climate.const import HVACAction, HVACMode
from homeassistant.util import dt as dt_util

from custom_components.better_thermostat.trv import Trv
from custom_components.better_thermostat.utils.const import CalibrationMode
from custom_components.better_thermostat.utils import underfloor as uf
from custom_components.better_thermostat.utils.underfloor import (
    CONF_HEATING_TYPE,
    CONF_WARM_FLOOR_LEVEL,
    HeatingType,
    apply_warm_floor_floor,
)


def _make_bt(*, level: str, current: float = 22.0, slope: float | None = 0.0):
    bt = MagicMock()
    bt.device_name = "Test BT"
    bt.contact_open = False
    bt.call_for_heat = True
    bt.bt_hvac_mode = HVACMode.HEAT
    bt.bt_target_temp = 22.0
    bt.cur_temp = current
    bt.hvac_action = HVACAction.IDLE
    bt.temp_slope = slope
    bt.heat_loss_rate = 0.0022
    bt.heating_power = 0.0054
    bt.solar_intensity = 0.0
    bt.external_temp_ema = current
    bt.external_temp_ema_1h = current
    bt.flow_temp_sensor = None
    bt.flow_temp_static_c = None
    bt.state_mgr = None
    bt.sensor_entity_id = "sensor.room_temp"
    bt.hass.states.get.return_value = MagicMock(last_updated=dt_util.utcnow())
    bt.real_trvs = {
        "climate.trv": Trv.from_legacy_dict(
            "climate.trv",
            {
                "advanced": {
                    CONF_HEATING_TYPE: HeatingType.UNDERFLOOR.value,
                    CONF_WARM_FLOOR_LEVEL: level,
                    "calibration_mode": CalibrationMode.HEATING_POWER_CALIBRATION.value,
                },
                "current_temperature": current,
                "min_temp": 5.0,
                "max_temp": 30.0,
            },
        )
    }
    return bt


def test_three_levels_have_different_pulse_lengths(monkeypatch):
    now = [1_000_000.0]
    monkeypatch.setattr(uf, "time", lambda: now[0])

    ends = {}
    for level, expected_minutes in (
        (uf.WARM_FLOOR_LEVEL_ECO, 4.0),
        (uf.WARM_FLOOR_LEVEL_BALANCED, 6.0),
        (uf.WARM_FLOOR_LEVEL_KEEP_HOT, 8.0),
    ):
        bt = _make_bt(level=level)
        result = apply_warm_floor_floor(bt, "climate.trv", 22.0, is_offset=False)
        ends[level] = bt.real_trvs["climate.trv"].extra[
            "warm_floor_maintenance_ends_at"
        ]
        assert result > 22.0
        assert ends[level] - now[0] == expected_minutes * 60.0
        assert bt._warm_floor_status["maintenance_active"] is True

    assert ends[uf.WARM_FLOOR_LEVEL_ECO] < ends[uf.WARM_FLOOR_LEVEL_BALANCED]
    assert ends[uf.WARM_FLOOR_LEVEL_BALANCED] < ends[uf.WARM_FLOOR_LEVEL_KEEP_HOT]


def test_stable_sun_warmed_room_still_gets_a_pulse(monkeypatch):
    monkeypatch.setattr(uf, "time", lambda: 1_000_000.0)
    bt = _make_bt(level=uf.WARM_FLOOR_LEVEL_BALANCED, current=24.8, slope=0.0)
    bt.solar_intensity = 1.0

    result = apply_warm_floor_floor(bt, "climate.trv", 22.0, is_offset=False)

    # There is no static 22 C ceiling: the pulse follows the actual room
    # temperature and is capped only by the device's declared max temperature.
    assert result > 24.8
    assert result <= 30.0


def test_positive_slope_suppresses_pulse_without_a_room_temperature_ceiling(
    monkeypatch,
):
    monkeypatch.setattr(uf, "time", lambda: 1_000_000.0)
    bt = _make_bt(level=uf.WARM_FLOOR_LEVEL_KEEP_HOT, current=24.8, slope=0.03)

    result = apply_warm_floor_floor(bt, "climate.trv", 22.0, is_offset=False)

    assert result == 22.0
    assert bt._warm_floor_status["maintenance_reason"] == "room_still_rising"


def test_heat_loss_and_slope_change_the_pause_dynamically():
    profile = uf.WARM_FLOOR_MAINTENANCE_PROFILES[uf.WARM_FLOOR_LEVEL_BALANCED]
    low_loss = _make_bt(level=uf.WARM_FLOOR_LEVEL_BALANCED, slope=0.0)
    high_loss = _make_bt(level=uf.WARM_FLOOR_LEVEL_BALANCED, slope=-0.01)
    low_loss.heat_loss_rate = 0.001
    high_loss.heat_loss_rate = 0.009

    assert uf._maintenance_pause_minutes(low_loss, profile) > uf._maintenance_pause_minutes(
        high_loss, profile
    )


def test_short_and_long_ema_detect_solar_warming_when_slope_is_flat():
    profile = uf.WARM_FLOOR_MAINTENANCE_PROFILES[uf.WARM_FLOOR_LEVEL_BALANCED]
    stable = _make_bt(level=uf.WARM_FLOOR_LEVEL_BALANCED)
    warming = _make_bt(level=uf.WARM_FLOOR_LEVEL_BALANCED)
    warming.external_temp_ema = 23.0
    warming.external_temp_ema_1h = 22.0

    assert uf._maintenance_pause_minutes(warming, profile) > uf._maintenance_pause_minutes(
        stable, profile
    )


def test_flow_temperature_uses_the_configured_sensor_and_changes_pulse_time(
    monkeypatch,
):
    monkeypatch.setattr(uf, "time", lambda: 1_000_000.0)
    bt = _make_bt(level=uf.WARM_FLOOR_LEVEL_BALANCED)
    bt.flow_temp_sensor = "sensor.flow_temperature"
    bt.flow_temp_static_c = 40.0
    bt.hass.states.get.side_effect = lambda entity_id: MagicMock(
        state="28.0" if entity_id == "sensor.flow_temperature" else "22.0",
        attributes={"unit_of_measurement": "°C"},
        last_updated=dt_util.utcnow(),
    )

    result = apply_warm_floor_floor(bt, "climate.trv", 22.0, is_offset=False)
    trv = bt.real_trvs["climate.trv"]

    assert result > 22.0
    assert trv.extra["warm_floor_maintenance_ends_at"] - 1_000_000.0 > 6.0 * 60.0
    assert bt._warm_floor_status["maintenance_flow_temp"] == 28.0
    assert bt._warm_floor_status["maintenance_flow_lift"] == 6.0


def test_flow_sensor_falls_back_to_static_value():
    bt = _make_bt(level=uf.WARM_FLOOR_LEVEL_BALANCED)
    bt.flow_temp_sensor = "sensor.missing_flow"
    bt.flow_temp_static_c = 40.0
    bt.hass.states.get.return_value = None

    _flow_temp, lift, factor = uf._maintenance_flow_factor(bt)

    assert _flow_temp == 40.0
    assert lift == 18.0
    assert factor == 1.5


def test_pulse_enters_cooldown_then_restarts(monkeypatch):
    now = [1_000_000.0]
    monkeypatch.setattr(uf, "time", lambda: now[0])
    bt = _make_bt(level=uf.WARM_FLOOR_LEVEL_ECO)
    trv = bt.real_trvs["climate.trv"]

    first = apply_warm_floor_floor(bt, "climate.trv", 22.0, is_offset=False)
    now[0] = trv.extra["warm_floor_maintenance_ends_at"] + 1
    cooldown = apply_warm_floor_floor(bt, "climate.trv", 22.0, is_offset=False)
    assert first > 22.0
    assert cooldown == 22.0
    assert bt._warm_floor_status["maintenance_reason"] == "cooldown"

    now[0] += bt._warm_floor_status["maintenance_pause_min"] * 60 + 1
    restarted = apply_warm_floor_floor(bt, "climate.trv", 22.0, is_offset=False)
    assert restarted > 22.0
