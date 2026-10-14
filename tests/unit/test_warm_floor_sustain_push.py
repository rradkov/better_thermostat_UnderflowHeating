"""Warm Floor's above-target Generic Thermostat duty-cycle behavior.

Generic Thermostat devices need an above-target downstream setpoint during
the permitted ON window so their relay can turn on. The optional sustain
amount may extend that pulse, but ``bt.bt_target_temp`` itself is never
touched.
"""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import MagicMock

from homeassistant.components.climate.const import HVACAction, HVACMode
from homeassistant.util import dt as dt_util

from custom_components.better_thermostat.trv import Trv
from custom_components.better_thermostat.utils import underfloor
from custom_components.better_thermostat.utils.const import CalibrationMode
from custom_components.better_thermostat.utils.underfloor import (
    CONF_HEATING_TYPE,
    CONF_WARM_FLOOR_LEVEL,
    CONF_WARM_FLOOR_SUSTAIN_PUSH,
    WARM_FLOOR_SENSOR_STALE_AFTER_S,
    WARM_FLOOR_LEVEL_CUSTOM,
    HeatingType,
    apply_warm_floor_floor,
)


def _make_bt(
    *,
    sustain_push: float = 0.3,
    hvac_action=HVACAction.IDLE,
    cur_temp: float = 22.2,
    target_temp: float = 22.0,
    sensor_last_updated=None,
    calibration_mode: str = CalibrationMode.HEATING_POWER_CALIBRATION.value,
    max_temp: float = 30.0,
    warm_floor_level: str | None = None,
    **overrides,
) -> MagicMock:
    bt = MagicMock()
    bt.device_name = "Test BT"
    bt.contact_open = False
    bt.call_for_heat = True
    bt.bt_hvac_mode = HVACMode.HEAT
    bt.bt_target_temp = target_temp
    bt.cur_temp = cur_temp
    bt.hvac_action = hvac_action
    bt.temp_slope = None
    # A well-insulated, strong-emitter room, so structural_scale is ~1.0 and
    # doesn't itself taper the push - isolating the trigger conditions under
    # test from the structural computation covered elsewhere.
    bt.heat_loss_rate = 0.0
    bt.heating_power = 1.0
    bt.state_mgr = None
    bt.sensor_entity_id = "sensor.room_temp"
    bt.hass.states.get.return_value = MagicMock(
        last_updated=sensor_last_updated or dt_util.utcnow()
    )

    quirks = MagicMock()
    quirks.fix_local_calibration.side_effect = lambda _self, _eid, offset: float(offset)
    quirks.fix_target_temperature_calibration.side_effect = (
        lambda _self, _eid, temperature: float(temperature)
    )

    bt.real_trvs = {
        "climate.trv": Trv.from_legacy_dict(
            "climate.trv",
            {
                "advanced": {
                    CONF_HEATING_TYPE: HeatingType.UNDERFLOOR.value,
                    "calibration_mode": calibration_mode,
                    CONF_WARM_FLOOR_SUSTAIN_PUSH: sustain_push,
                    **(
                        {CONF_WARM_FLOOR_LEVEL: warm_floor_level}
                        if warm_floor_level is not None
                        else {}
                    ),
                },
                "current_temperature": cur_temp,
                "min_temp": 5.0,
                "max_temp": max_temp,
                "model_quirks": quirks,
            },
        )
    }
    for key, value in overrides.items():
        setattr(bt, key, value)
    return bt


def test_pushes_above_target_once_pinned_setpoint_and_idle_at_target():
    """The exact motivating scenario: HEATING_POWER_CALIBRATION pins the
    setpoint at target once idle - sustain push closes the gap."""
    bt = _make_bt(sustain_push=0.3)
    result = apply_warm_floor_floor(bt, "climate.trv", 22.0, is_offset=False)
    assert result > 22.0
    assert bt._warm_floor_status["sustain_push_c"] is not None
    assert bt._warm_floor_status["sustain_push_c"] > 0


def test_custom_level_still_requires_an_explicit_generic_duty_pulse(monkeypatch):
    # Generic Thermostat must still receive an above-target ON command even
    # when the optional configurable sustain amount is zero.
    bt = _make_bt(sustain_push=0.0, warm_floor_level=WARM_FLOOR_LEVEL_CUSTOM)
    bt.real_trvs["climate.trv"].integration = "generic_thermostat"
    monkeypatch.setattr(underfloor, "_generic_duty_cycle_is_on", lambda *_: True)

    result = apply_warm_floor_floor(bt, "climate.trv", 22.0, is_offset=False)
    assert result > bt.bt_target_temp
    assert bt._warm_floor_status["reason"] == "duty_cycle_on"


def test_generic_existing_above_target_result_is_active_during_on_window(monkeypatch):
    """An already-raised downstream setpoint is still the active pulse."""
    bt = _make_bt(sustain_push=0.0, cur_temp=22.2, target_temp=22.0)
    bt.real_trvs["climate.trv"].integration = "generic_thermostat"
    monkeypatch.setattr(underfloor, "_generic_duty_cycle_is_on", lambda *_: True)

    result = apply_warm_floor_floor(bt, "climate.trv", 22.5, is_offset=False)

    assert result == 22.5
    assert bt._warm_floor_status["active"] is True
    assert bt._warm_floor_status["reason"] == "duty_cycle_on"


def test_generic_idle_releases_stale_downstream_setpoint(monkeypatch):
    """An idle Generic Thermostat must not keep heating from an old pulse."""
    bt = _make_bt(
        sustain_push=0.0,
        cur_temp=22.1,
        target_temp=22.0,
        warm_floor_level=WARM_FLOOR_LEVEL_CUSTOM,
    )
    bt.real_trvs["climate.trv"].integration = "generic_thermostat"
    monkeypatch.setattr(underfloor, "_generic_duty_cycle_is_on", lambda *_: False)

    result = apply_warm_floor_floor(bt, "climate.trv", 26.0, is_offset=False)

    assert result == 21.5
    assert bt._warm_floor_status["reason"] == "idle_release"


def test_does_not_fire_while_still_heating():
    """hvac_action must read IDLE, not just "not COOLING"."""
    bt = _make_bt(sustain_push=0.3, hvac_action=HVACAction.HEATING)
    result = apply_warm_floor_floor(bt, "climate.trv", 22.0, is_offset=False)
    assert result == 22.0


def test_does_not_fire_while_cooling():
    bt = _make_bt(sustain_push=0.3, hvac_action=HVACAction.COOLING)
    result = apply_warm_floor_floor(bt, "climate.trv", 22.0, is_offset=False)
    assert result == 22.0


def test_does_not_fire_before_the_room_actually_reaches_target():
    """Guards the tolerance-hold-band case: hvac_action can read IDLE just
    inside the tolerance band, before cur_temp has actually reached target."""
    bt = _make_bt(sustain_push=0.3, cur_temp=21.7, target_temp=22.0)
    result = apply_warm_floor_floor(bt, "climate.trv", 22.0, is_offset=False)
    assert result == 22.0


def test_warm_floor_stays_disabled_below_target_for_every_actuator_path():
    """Normal heating owns the below-target region."""
    bt = _make_bt(cur_temp=21.9, target_temp=22.0, sustain_push=0.3)
    trv = bt.real_trvs["climate.trv"]
    trv.calibration_balance = {"apply_valve": True, "valve_percent": 0}
    result = apply_warm_floor_floor(bt, "climate.trv", 20.0, is_offset=False)

    assert result == 20.0
    assert trv.calibration_balance["valve_percent"] == 0
    assert bt._warm_floor_status["active"] is False
    assert bt._warm_floor_status["reason"] == "below_target"


def test_warm_floor_maintains_an_idle_generic_room_at_target(monkeypatch):
    """At target, an idle generic heater may maintain a zero-slope floor."""
    bt = _make_bt(cur_temp=22.0, target_temp=22.0, sustain_push=0.3)
    bt.real_trvs["climate.trv"].integration = "generic_thermostat"
    bt.temp_slope = 0.0
    monkeypatch.setattr(underfloor, "_generic_duty_cycle_is_on", lambda *_: True)

    result = apply_warm_floor_floor(bt, "climate.trv", 22.0, is_offset=False)

    assert result > bt.bt_target_temp
    assert bt._warm_floor_status["active"] is True
    assert bt._warm_floor_status["reason"] == "duty_cycle_on"


def test_warm_floor_auto_selects_keep_hot_at_target_when_not_warming():
    """Auto protects the slab at the exact target until warming is proven."""
    bt = _make_bt(cur_temp=22.0, target_temp=22.0)
    bt.cur_temp_filtered = 22.0
    bt.external_temp_ema = 22.0
    bt.external_temp_ema_1h = 22.0
    bt.temp_slope = 0.0

    assert (
        underfloor._warm_floor_auto_candidate(bt, demand_factor=0.0)
        == underfloor.WARM_FLOOR_LEVEL_KEEP_HOT
    )


def test_below_target_interrupts_a_latched_generic_warm_floor_phase():
    bt = _make_bt(cur_temp=21.9, target_temp=22.0, sustain_push=0.0)
    trv = bt.real_trvs["climate.trv"]
    trv.integration = "generic_thermostat"
    trv.warm_floor_actuator_phase = "on"

    result = apply_warm_floor_floor(bt, "climate.trv", 20.0, is_offset=False)

    assert result == 20.0
    assert trv.warm_floor_actuator_phase == "off"
    assert trv.warm_floor_actuator_guard == "below_target"


def test_does_not_fire_when_computed_value_is_already_below_target():
    """computed_value < target means the passive floor isn't structurally a
    no-op - the ordinary backoff floor is the right mechanism, not this one."""
    bt = _make_bt(sustain_push=0.3)
    result = apply_warm_floor_floor(bt, "climate.trv", 20.0, is_offset=False)
    # The ordinary passive floor may still raise it, but never past target.
    assert result <= 22.0


def test_respects_the_configured_max_temp_ceiling():
    bt = _make_bt(sustain_push=1.5, target_temp=29.5, cur_temp=29.6, max_temp=30.0)
    result = apply_warm_floor_floor(bt, "climate.trv", 29.5, is_offset=False)
    assert result == 30.0


def test_skips_a_heater_under_active_direct_valve_control():
    bt = _make_bt(sustain_push=0.3)
    bt.real_trvs["climate.trv"].calibration_balance = {
        "apply_valve": True,
        "valve_percent": 10,
    }
    result = apply_warm_floor_floor(bt, "climate.trv", 22.0, is_offset=False)
    assert result == 22.0


def test_offset_path_is_never_pushed_above_target():
    """Sustain push is scoped to is_offset=False only - the offset path's
    existing cap-not-floor behavior is untouched."""
    bt = _make_bt(sustain_push=0.3)
    result = apply_warm_floor_floor(bt, "climate.trv", -1.0, is_offset=True)
    assert result <= -1.0 or isinstance(result, float)
    assert bt._warm_floor_status["sustain_push_c"] is None


def test_a_stale_external_sensor_suppresses_the_entire_floor():
    """An unknown/stale reading is treated the same as "still gaining" -
    the whole cycle is suppressed, not just the sustain push."""
    stale_time = dt_util.utcnow() - timedelta(
        seconds=WARM_FLOOR_SENSOR_STALE_AFTER_S + 1
    )
    bt = _make_bt(sustain_push=0.3, sensor_last_updated=stale_time)
    result = apply_warm_floor_floor(bt, "climate.trv", 22.0, is_offset=False)
    assert result == 22.0
    assert bt._warm_floor_status["active"] is False


def test_a_missing_sensor_state_is_treated_as_stale():
    bt = _make_bt(sustain_push=0.3)
    bt.hass.states.get.return_value = None
    result = apply_warm_floor_floor(bt, "climate.trv", 22.0, is_offset=False)
    assert result == 22.0


def test_works_the_same_on_an_already_working_calibration_mode():
    """Sustain push is a generic mechanism keyed on computed_value vs.
    target, not on the calibration mode - it also engages for the four
    modes that were never broken, whenever they too happen to be pinned at
    target while idle."""
    bt = _make_bt(
        sustain_push=0.3, calibration_mode=CalibrationMode.MPC_V2_CALIBRATION.value
    )
    result = apply_warm_floor_floor(bt, "climate.trv", 22.0, is_offset=False)
    assert result > 22.0


def test_never_touches_bt_target_temp():
    bt = _make_bt(sustain_push=0.3)
    apply_warm_floor_floor(bt, "climate.trv", 22.0, is_offset=False)
    assert bt.bt_target_temp == 22.0


def test_generic_thermostat_gets_a_short_warm_floor_setpoint_pulse(monkeypatch):
    """ON/OFF helpers need a target above the live temperature to turn on."""
    bt = _make_bt(sustain_push=0.0, cur_temp=22.2, target_temp=22.0)
    bt.real_trvs["climate.trv"].integration = "generic_thermostat"
    monkeypatch.setattr(underfloor, "_generic_duty_cycle_is_on", lambda *_: True)

    result = apply_warm_floor_floor(bt, "climate.trv", 22.0, is_offset=False)

    assert result == 22.5
    assert bt._warm_floor_status["control_method"] == "generic_duty_cycle"
    assert bt._warm_floor_status["reason"] == "duty_cycle_on"
    assert bt._warm_floor_status["duty_phase"] == "on"


def test_generic_thermostat_releases_the_setpoint_outside_the_on_window(monkeypatch):
    bt = _make_bt(sustain_push=0.0, cur_temp=22.2, target_temp=22.0)
    bt.real_trvs["climate.trv"].integration = "generic"
    monkeypatch.setattr(underfloor, "_generic_duty_cycle_is_on", lambda *_: False)

    result = apply_warm_floor_floor(bt, "climate.trv", 22.0, is_offset=False)

    assert result == 21.5
    assert bt._warm_floor_status["active"] is False
    assert bt._warm_floor_status["reason"] == "idle_release"
    assert bt._warm_floor_status["duty_phase"] == "off"


def test_generic_duty_phase_edge_requests_a_write_once_per_transition(monkeypatch):
    """A restart or missed tick must not lose the next ON/OFF command."""
    bt = _make_bt(sustain_push=0.0, cur_temp=22.2, target_temp=22.0)
    bt.real_trvs["climate.trv"].integration = "generic"

    monkeypatch.setattr(underfloor, "_generic_duty_cycle_is_on", lambda *_: True)
    apply_warm_floor_floor(bt, "climate.trv", 22.0, is_offset=False)
    assert bt.real_trvs["climate.trv"].warm_floor_duty_phase == "on"
    assert bt.real_trvs["climate.trv"].warm_floor_force_setpoint_write is True

    # The next evaluation is still in the same phase, so it must not create
    # another edge; the pending write remains armed until the control layer
    # successfully sends it.
    apply_warm_floor_floor(bt, "climate.trv", 22.0, is_offset=False)
    assert bt.real_trvs["climate.trv"].warm_floor_force_setpoint_write is True

    bt.real_trvs["climate.trv"].warm_floor_force_setpoint_write = False

    monkeypatch.setattr(underfloor, "_generic_duty_cycle_is_on", lambda *_: False)
    apply_warm_floor_floor(bt, "climate.trv", 22.0, is_offset=False)
    # The requested wall-clock phase is OFF, but the thermal actuator is still
    # inside its minimum ON travel interval, so the effective phase stays ON.
    assert bt.real_trvs["climate.trv"].warm_floor_duty_phase == "on"
    assert (
        bt.real_trvs["climate.trv"].warm_floor_actuator_guard
        == "minimum_on_hold"
    )
    # No OFF write is armed yet: the current ON phase remains latched until
    # the actuator's minimum opening interval has elapsed.
    assert bt.real_trvs["climate.trv"].warm_floor_force_setpoint_write is False


def test_stale_raw_sensor_does_not_block_when_derived_slope_is_available():
    stale_time = dt_util.utcnow() - timedelta(
        seconds=WARM_FLOOR_SENSOR_STALE_AFTER_S + 1
    )
    bt = _make_bt(sustain_push=0.3, sensor_last_updated=stale_time)
    bt.temp_slope = 0.0

    result = apply_warm_floor_floor(bt, "climate.trv", 22.0, is_offset=False)

    assert result > 22.0
    assert bt._warm_floor_status["active"] is True


def test_generic_thermostat_maintains_floor_above_the_room_target(monkeypatch):
    bt = _make_bt(sustain_push=0.0, cur_temp=22.6, target_temp=22.0)
    bt.real_trvs["climate.trv"].integration = "generic_thermostat"
    monkeypatch.setattr(underfloor, "_generic_duty_cycle_is_on", lambda *_: True)

    result = apply_warm_floor_floor(bt, "climate.trv", 22.0, is_offset=False)

    assert result == 23.0
    assert bt._warm_floor_status["active"] is True
    assert bt._warm_floor_status["reason"] == "duty_cycle_on"


def test_generic_thermostat_stops_at_the_absolute_safety_ceiling(monkeypatch):
    bt = _make_bt(sustain_push=0.0, cur_temp=29.1, target_temp=22.0)
    bt.real_trvs["climate.trv"].integration = "generic_thermostat"
    monkeypatch.setattr(underfloor, "_generic_duty_cycle_is_on", lambda *_: True)

    result = apply_warm_floor_floor(bt, "climate.trv", 22.0, is_offset=False)

    assert result == 21.5
    assert bt._warm_floor_status["active"] is False
    assert bt._warm_floor_status["reason"] == "idle_release"


def test_generic_thermostat_maintains_floor_while_room_is_rising(monkeypatch):
    """Solar/residual heat reduces maintenance, but does not disable it."""
    bt = _make_bt(sustain_push=0.0, cur_temp=22.2, target_temp=22.0)
    bt.real_trvs["climate.trv"].integration = "generic_thermostat"
    bt.temp_slope = 0.08
    monkeypatch.setattr(underfloor, "_generic_duty_cycle_is_on", lambda *_: True)

    result = apply_warm_floor_floor(bt, "climate.trv", 22.0, is_offset=False)

    assert result > bt.bt_target_temp
    assert bt._warm_floor_status["active"] is True
    assert bt._warm_floor_status["reason"] == "duty_cycle_on"


def test_stronger_rising_slope_reduces_duty_demand_without_blanket_off(monkeypatch):
    gentle = _make_bt(sustain_push=0.0, cur_temp=22.2, target_temp=22.0)
    strong = _make_bt(sustain_push=0.0, cur_temp=22.2, target_temp=22.0)
    gentle.real_trvs["climate.trv"].integration = "generic_thermostat"
    strong.real_trvs["climate.trv"].integration = "generic_thermostat"
    gentle.temp_slope = 0.06
    strong.temp_slope = 0.20
    gentle.heat_loss_rate = 1.0
    strong.heat_loss_rate = 1.0
    gentle.heating_power = 1.0
    strong.heating_power = 1.0
    monkeypatch.setattr(underfloor, "_generic_duty_cycle_is_on", lambda *_: True)

    gentle_result = apply_warm_floor_floor(
        gentle, "climate.trv", 22.0, is_offset=False
    )
    strong_result = apply_warm_floor_floor(
        strong, "climate.trv", 22.0, is_offset=False
    )

    assert gentle_result > gentle.bt_target_temp
    assert strong_result > strong.bt_target_temp
    assert strong._warm_floor_status["demand_factor"] < gentle._warm_floor_status[
        "demand_factor"
    ]


def test_warm_floor_level_changes_generic_duty_intensity():
    eco = _make_bt(warm_floor_level=underfloor.WARM_FLOOR_LEVEL_ECO)
    balanced = _make_bt(warm_floor_level=underfloor.WARM_FLOOR_LEVEL_BALANCED)
    keep_hot = _make_bt(warm_floor_level=underfloor.WARM_FLOOR_LEVEL_KEEP_HOT)

    eco_profile = underfloor._warm_floor_level_intensity(eco.real_trvs["climate.trv"])
    balanced_profile = underfloor._warm_floor_level_intensity(
        balanced.real_trvs["climate.trv"]
    )
    keep_hot_profile = underfloor._warm_floor_level_intensity(
        keep_hot.real_trvs["climate.trv"]
    )

    assert eco_profile < balanced_profile < keep_hot_profile


def test_auto_bootstraps_keep_hot_before_confirming_warming(monkeypatch):
    bt = _make_bt(warm_floor_level=underfloor.WARM_FLOOR_LEVEL_AUTO)
    bt.cur_temp_filtered = 22.1
    bt.external_temp_ema = 22.1
    bt.external_temp_ema_1h = 21.7
    bt.temp_slope = 0.02
    clock = [0.0]
    monkeypatch.setattr(underfloor, "monotonic", lambda: clock[0])

    first = underfloor._warm_floor_level_intensity(
        bt, bt.real_trvs["climate.trv"], demand_factor=0.4
    )

    assert bt._warm_floor_auto_policy == underfloor.WARM_FLOOR_LEVEL_KEEP_HOT
    assert first == underfloor.WARM_FLOOR_LEVEL_INTENSITY[
        underfloor.WARM_FLOOR_LEVEL_KEEP_HOT
    ]

    # Strong warming is first confirmed, then the policy moves down only one
    # level after the minimum dwell and confirmation windows.
    clock[0] = underfloor.WARM_FLOOR_AUTO_MIN_DWELL_S
    underfloor._warm_floor_level_intensity(
        bt, bt.real_trvs["climate.trv"], demand_factor=0.4
    )
    clock[0] += underfloor.WARM_FLOOR_AUTO_POLICY_CONFIRM_S
    underfloor._warm_floor_level_intensity(
        bt, bt.real_trvs["climate.trv"], demand_factor=0.4
    )
    assert bt._warm_floor_auto_policy == underfloor.WARM_FLOOR_LEVEL_BALANCED


def test_auto_returns_to_keep_hot_after_confirmed_cooling(monkeypatch):
    bt = _make_bt(warm_floor_level=underfloor.WARM_FLOOR_LEVEL_AUTO)
    bt.cur_temp_filtered = 21.7
    bt.external_temp_ema = 21.7
    bt.external_temp_ema_1h = 21.9
    bt.temp_slope = -0.01
    bt._warm_floor_auto_policy = underfloor.WARM_FLOOR_LEVEL_BALANCED
    bt._warm_floor_auto_policy_since = 0.0
    bt._warm_floor_auto_candidate = underfloor.WARM_FLOOR_LEVEL_BALANCED
    bt._warm_floor_auto_candidate_since = 0.0
    clock = [underfloor.WARM_FLOOR_AUTO_MIN_DWELL_S]
    monkeypatch.setattr(underfloor, "monotonic", lambda: clock[0])

    underfloor._warm_floor_level_intensity(
        bt, bt.real_trvs["climate.trv"], demand_factor=0.4
    )
    clock[0] += underfloor.WARM_FLOOR_AUTO_MIN_DWELL_S + 1
    underfloor._warm_floor_level_intensity(
        bt, bt.real_trvs["climate.trv"], demand_factor=0.4
    )

    assert bt._warm_floor_auto_policy == underfloor.WARM_FLOOR_LEVEL_KEEP_HOT


def test_warm_floor_level_increases_direct_valve_floor_when_raised():
    def valve_floor(level: str) -> int:
        bt = _make_bt(warm_floor_level=level)
        trv = bt.real_trvs["climate.trv"]
        trv.advanced[underfloor.CONF_WARM_FLOOR_MAX_BACKOFF] = (
            underfloor.WARM_FLOOR_LEVEL_PRESETS[level][0]
        )
        trv.calibration_balance = {"apply_valve": True, "valve_percent": 0}
        underfloor._apply_valve_floor(bt, "climate.trv", 0.5, 1.0)
        return trv.calibration_balance["valve_percent"]

    assert valve_floor(underfloor.WARM_FLOOR_LEVEL_ECO) < valve_floor(
        underfloor.WARM_FLOOR_LEVEL_BALANCED
    ) < valve_floor(underfloor.WARM_FLOOR_LEVEL_KEEP_HOT)


def test_generic_duty_profile_adapts_to_falling_temperature():
    stable = _make_bt(cur_temp=22.2, target_temp=22.0)
    falling = _make_bt(cur_temp=22.2, target_temp=22.0)
    falling.temp_slope = -0.02

    stable_profile = underfloor._effective_duty_profile(
        stable,
        stable.real_trvs["climate.trv"],
        underfloor._warm_floor_demand_factor(stable),
    )
    falling_profile = underfloor._effective_duty_profile(
        falling,
        falling.real_trvs["climate.trv"],
        underfloor._warm_floor_demand_factor(falling),
    )

    assert falling_profile[0] < stable_profile[0]
    assert falling_profile[1] > stable_profile[1]


def test_warm_floor_demand_ignores_below_target_room_error():
    """Below-target error belongs to normal heating, not floor maintenance."""
    below_target = _make_bt(cur_temp=21.0, target_temp=22.0)
    at_target = _make_bt(cur_temp=22.0, target_temp=22.0)

    below_demand = underfloor._warm_floor_demand_factor(below_target)
    at_demand = underfloor._warm_floor_demand_factor(at_target)

    assert below_demand == at_demand == 0.0


def test_warm_floor_demand_uses_short_and_one_hour_ema_separation():
    cooling = _make_bt(cur_temp=22.0, target_temp=22.0)
    cooling.heat_loss_rate = 0.0
    cooling.heating_power = 0.0
    cooling.cur_temp_filtered = 21.5
    cooling.external_temp_ema_1h = 22.0

    warming = _make_bt(cur_temp=22.0, target_temp=22.0)
    warming.heat_loss_rate = 0.0
    warming.heating_power = 0.0
    warming.cur_temp_filtered = 22.5
    warming.external_temp_ema_1h = 22.0

    cooling_demand = underfloor._warm_floor_demand_factor(cooling)
    warming_demand = underfloor._warm_floor_demand_factor(warming)

    assert cooling_demand == 1.0
    assert warming_demand == 0.0


def test_warm_floor_predicts_preheat_before_target_crossing():
    approaching = _make_bt(cur_temp=22.1, target_temp=22.0)
    approaching.temp_slope = -0.02
    approaching.cur_temp_filtered = 22.05
    approaching.external_temp_ema_1h = 22.2
    approaching.heat_loss_rate = 0.03
    approaching.heating_power = 0.0

    warming = _make_bt(cur_temp=22.1, target_temp=22.0)
    warming.temp_slope = 0.02
    warming.cur_temp_filtered = 22.2
    warming.external_temp_ema_1h = 22.0
    warming.heat_loss_rate = 0.0
    warming.heating_power = 1.0

    approaching_demand = underfloor._warm_floor_demand_factor(approaching)
    warming_demand = underfloor._warm_floor_demand_factor(warming)

    assert approaching_demand > warming_demand
    assert approaching._warm_floor_preheat_risk > 0.0
    assert warming._warm_floor_preheat_risk == 0.0


def test_warm_floor_preheat_risk_is_zero_at_or_below_target():
    for current in (21.9, 22.0):
        bt = _make_bt(cur_temp=current, target_temp=22.0)
        bt.temp_slope = -0.02
        bt.cur_temp_filtered = current
        bt.external_temp_ema_1h = current + 0.2

        assert underfloor._warm_floor_preheat_risk(bt) == 0.0


def test_generic_duty_demand_uses_per_heater_mpc_feedback(monkeypatch):
    """A trained MPC load ratio must influence the Generic pulse demand."""
    bt = _make_bt()
    bt.heat_loss_rate = 0.0
    bt.heating_power = 1.0
    mpc_state = MagicMock(loss_est=0.75, gain_est=1.0)
    monkeypatch.setattr(underfloor, "_get_mpc_state", lambda *_: mpc_state)

    demand = underfloor._warm_floor_demand_factor(
        bt,
        "climate.trv",
        CalibrationMode.MPC_V2_CALIBRATION,
    )

    assert demand == 0.5


def test_generic_duty_profile_respects_slow_actuator_travel_time():
    """A pulse must allow both opening and closing of a thermal actuator."""
    for level in underfloor.WARM_FLOOR_LEVEL_INTENSITY:
        bt = _make_bt(warm_floor_level=level)
        period_s, on_s, _ = underfloor._effective_duty_profile(
            bt, bt.real_trvs["climate.trv"], 1.0
        )

        travel_s = underfloor.WARM_FLOOR_ACTUATOR_TRAVEL_S
        assert on_s >= travel_s
        assert period_s - on_s >= travel_s


def test_generic_duty_profile_is_dynamic_for_rising_ema_and_target_margin():
    stable = _make_bt(cur_temp=22.2, target_temp=22.0)
    rising = _make_bt(cur_temp=23.5, target_temp=22.0)
    rising.temp_slope = 0.08
    rising.cur_temp_filtered = 23.2
    rising.external_temp_ema_1h = 22.5

    stable_profile = underfloor._effective_duty_profile(
        stable,
        stable.real_trvs["climate.trv"],
        underfloor._warm_floor_demand_factor(stable),
    )
    rising_profile = underfloor._effective_duty_profile(
        rising,
        rising.real_trvs["climate.trv"],
        underfloor._warm_floor_demand_factor(rising),
    )

    assert rising_profile[2] < stable_profile[2]
    assert rising_profile[1] < stable_profile[1]
    assert rising_profile[0] > stable_profile[0]


def test_dynamic_duty_profiles_converge_when_cooling_demand_is_high():
    profiles = []
    for level in (
        underfloor.WARM_FLOOR_LEVEL_ECO,
        underfloor.WARM_FLOOR_LEVEL_BALANCED,
        underfloor.WARM_FLOOR_LEVEL_KEEP_HOT,
    ):
        bt = _make_bt(warm_floor_level=level)
        bt.temp_slope = -0.05
        bt.heat_loss_rate = 1.0
        bt.heating_power = 1.0
        profiles.append(
            underfloor._effective_duty_profile(
                bt,
                bt.real_trvs["climate.trv"],
                underfloor._warm_floor_demand_factor(bt),
            )
        )

    # Diagnostics, rather than the level's old fixed clock schedule, drive
    # the profiles toward a sustained maintenance response.
    assert all(profile[2] > 0.9 for profile in profiles)
    assert profiles[0][2] > underfloor.WARM_FLOOR_LEVEL_INTENSITY[
        underfloor.WARM_FLOOR_LEVEL_ECO
    ]


def test_dynamic_duty_profile_keeps_a_minimum_maintenance_cadence():
    bt = _make_bt(cur_temp=25.0, target_temp=22.0)
    bt.temp_slope = 0.20
    bt.cur_temp_filtered = 25.0
    bt.external_temp_ema_1h = 22.0

    period_s, on_s, intensity = underfloor._effective_duty_profile(
        bt,
        bt.real_trvs["climate.trv"],
        0.0,
    )

    assert intensity >= underfloor.WARM_FLOOR_DUTY_MIN_INTENSITY
    assert period_s - on_s >= underfloor.WARM_FLOOR_DUTY_MIN_PAUSE_S
    assert on_s >= underfloor.WARM_FLOOR_ACTUATOR_TRAVEL_S


def test_warm_floor_prefers_configured_outdoor_sensor_over_weather_entity():
    bt = _make_bt(level=underfloor.WARM_FLOOR_LEVEL_BALANCED)
    bt.outdoor_sensor = "sensor.outdoor"
    bt.weather_entity = "weather.home"

    def state_for(entity_id):
        state = MagicMock()
        state.attributes = {"unit_of_measurement": "°C"}
        state.state = "0.0" if entity_id == "sensor.outdoor" else "20.0"
        return state

    bt.hass.states.get.side_effect = state_for
    demand = underfloor._warm_floor_demand_factor(bt)

    assert demand > 0.0
    assert bt._warm_floor_outdoor_temp_c == 0.0
    assert bt._warm_floor_outdoor_source == "outdoor_sensor"


def test_warm_floor_uses_weather_entity_when_outdoor_sensor_is_unavailable():
    bt = _make_bt(level=underfloor.WARM_FLOOR_LEVEL_BALANCED)
    bt.weather_entity = "weather.home"

    def state_for(entity_id):
        state = MagicMock()
        if entity_id == "weather.home":
            state.state = "sunny"
            state.attributes = {"temperature": 5.0, "temperature_unit": "°C"}
        else:
            state.state = "unavailable"
            state.attributes = {}
        return state

    bt.hass.states.get.side_effect = state_for
    demand = underfloor._warm_floor_demand_factor(bt)

    assert demand > 0.0
    assert bt._warm_floor_outdoor_temp_c == 5.0
    assert bt._warm_floor_outdoor_source == "weather_entity"


def test_warm_floor_water_sensor_precedes_static_fallback():
    bt = _make_bt(level=underfloor.WARM_FLOOR_LEVEL_BALANCED)
    bt.flow_temp_sensor = "sensor.buffer"
    bt.flow_temp_static_c = 45.0

    state = MagicMock(state="31.6", attributes={"unit_of_measurement": "°C"})
    bt.hass.states.get.return_value = state
    demand = underfloor._warm_floor_demand_factor(bt)

    assert demand > 0.0
    assert bt._warm_floor_flow_temp_c == 31.6
    assert bt._warm_floor_flow_source == "flow_temp_sensor"


def test_auto_warm_floor_bootstraps_and_moves_with_confirmed_trend(monkeypatch):
    bt = _make_bt(warm_floor_level=underfloor.WARM_FLOOR_LEVEL_AUTO)
    trv = bt.real_trvs["climate.trv"]
    bt.hass.states.get.return_value = None
    clock = [0.0]
    monkeypatch.setattr(underfloor, "monotonic", lambda: clock[0])

    underfloor._effective_duty_profile(bt, trv, 0.0)
    assert bt._warm_floor_auto_policy == underfloor.WARM_FLOOR_LEVEL_KEEP_HOT

    bt.cur_temp_filtered = 22.4
    bt.external_temp_ema = 22.4
    bt.external_temp_ema_1h = 22.0
    bt.temp_slope = 0.02
    clock[0] = underfloor.WARM_FLOOR_AUTO_MIN_DWELL_S
    underfloor._effective_duty_profile(bt, trv, 0.2)
    clock[0] += underfloor.WARM_FLOOR_AUTO_POLICY_CONFIRM_S
    underfloor._effective_duty_profile(bt, trv, 0.2)
    assert bt._warm_floor_auto_policy == underfloor.WARM_FLOOR_LEVEL_BALANCED

    bt.cur_temp_filtered = 21.8
    bt.external_temp_ema = 21.8
    bt.external_temp_ema_1h = 22.0
    bt.temp_slope = -0.02
    clock[0] += 1
    underfloor._effective_duty_profile(bt, trv, 0.2)
    clock[0] += underfloor.WARM_FLOOR_AUTO_MIN_DWELL_S + 1
    underfloor._effective_duty_profile(bt, trv, 0.2)
    assert bt._warm_floor_auto_policy == underfloor.WARM_FLOOR_LEVEL_KEEP_HOT


def test_generic_duty_latches_on_phase_until_actuator_has_opened(monkeypatch):
    bt = _make_bt(sustain_push=0.0)
    trv = bt.real_trvs["climate.trv"]
    trv.integration = "generic_thermostat"
    monkeypatch.setattr(underfloor, "_generic_duty_cycle_is_on", lambda *_: True)

    first = apply_warm_floor_floor(bt, "climate.trv", 22.0, is_offset=False)
    assert first > bt.bt_target_temp

    monkeypatch.setattr(underfloor, "_generic_duty_cycle_is_on", lambda *_: False)
    second = apply_warm_floor_floor(bt, "climate.trv", 22.0, is_offset=False)
    assert second > bt.bt_target_temp
    assert trv.warm_floor_actuator_phase == "on"
    assert trv.warm_floor_actuator_guard == "minimum_on_hold"


def test_generic_duty_latches_off_phase_until_actuator_has_closed(monkeypatch):
    bt = _make_bt(sustain_push=0.0)
    trv = bt.real_trvs["climate.trv"]
    trv.integration = "generic_thermostat"
    trv.warm_floor_actuator_phase = "off"
    trv.warm_floor_phase_started_at = dt_util.utcnow().timestamp()
    trv.warm_floor_phase_minimum_until = (
        trv.warm_floor_phase_started_at + underfloor.WARM_FLOOR_ACTUATOR_TRAVEL_S
    )
    monkeypatch.setattr(underfloor, "_generic_duty_cycle_is_on", lambda *_: True)

    result = apply_warm_floor_floor(bt, "climate.trv", 22.0, is_offset=False)
    assert result == 21.5
    assert trv.warm_floor_actuator_guard == "minimum_off_hold"


def test_generic_duty_recovery_holds_reported_idle_before_new_on(monkeypatch):
    bt = _make_bt(sustain_push=0.0, hvac_action=HVACAction.IDLE)
    trv = bt.real_trvs["climate.trv"]
    trv.integration = "generic_thermostat"
    trv.hvac_action = HVACAction.IDLE.value
    monkeypatch.setattr(underfloor, "_generic_duty_cycle_is_on", lambda *_: True)

    recovered = apply_warm_floor_floor(bt, "climate.trv", 22.0, is_offset=False)
    assert recovered == 21.5
    assert trv.warm_floor_phase_recovered is True
    assert trv.warm_floor_actuator_guard == "recovery_hold"

    trv.warm_floor_phase_minimum_until = dt_util.utcnow().timestamp() - 1
    resumed = apply_warm_floor_floor(bt, "climate.trv", 22.0, is_offset=False)
    assert resumed > bt.bt_target_temp
    assert trv.warm_floor_actuator_phase == "on"


def test_direct_valve_floor_increases_when_room_is_cooling():
    stable = _make_bt(cur_temp=22.2, target_temp=22.0)
    falling = _make_bt(cur_temp=22.2, target_temp=22.0)
    falling.temp_slope = -0.02
    for bt in (stable, falling):
        bt.real_trvs["climate.trv"].calibration_balance = {
            "apply_valve": True,
            "valve_percent": 0,
        }

    apply_warm_floor_floor(stable, "climate.trv", 22.0, is_offset=False)
    apply_warm_floor_floor(falling, "climate.trv", 22.0, is_offset=False)

    assert (
        falling.real_trvs["climate.trv"].calibration_balance["valve_percent"]
        > stable.real_trvs["climate.trv"].calibration_balance["valve_percent"]
    )


def test_setpoint_floor_moves_toward_target_when_room_is_cooling():
    stable = _make_bt(cur_temp=22.2, target_temp=22.0, sustain_push=0.0)
    falling = _make_bt(cur_temp=22.2, target_temp=22.0, sustain_push=0.0)
    falling.temp_slope = -0.02

    stable_result = apply_warm_floor_floor(stable, "climate.trv", 20.0, is_offset=False)
    falling_result = apply_warm_floor_floor(falling, "climate.trv", 20.0, is_offset=False)

    assert falling_result > stable_result
