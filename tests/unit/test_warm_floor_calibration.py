"""Warm Floor: the sustaining-floor computation in utils/underfloor.py.

Covers the core guard rails (only underfloor-flagged heaters, never while a
window/door is open, never while HVAC is off, never above target) and the
corrected heat-loss/heating-power direction: a leaky room or a weak emitter
must get a *tighter* floor (closer to target), not a looser one.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from homeassistant.components.climate.const import HVACMode

from custom_components.better_thermostat.calibration import (
    calculate_calibration_setpoint,
)
from custom_components.better_thermostat.trv import Trv
from custom_components.better_thermostat.utils.const import (
    MAX_HEAT_LOSS,
    MAX_HEATING_POWER,
    CalibrationMode,
)
from custom_components.better_thermostat.utils.underfloor import (
    CONF_HEATING_TYPE,
    HeatingType,
    apply_warm_floor_floor,
)


def _make_bt(
    *, heating_type: str = HeatingType.UNDERFLOOR.value, **overrides
) -> MagicMock:
    bt = MagicMock()
    bt.device_name = "Test BT"
    bt.contact_open = False
    bt.call_for_heat = True
    bt.bt_hvac_mode = HVACMode.HEAT
    bt.bt_target_temp = 22.0
    bt.temp_slope = None
    bt.heat_loss_rate = 0.01
    bt.heating_power = 0.05
    bt.state_mgr = None

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
                    CONF_HEATING_TYPE: heating_type,
                    "calibration_mode": CalibrationMode.HEATING_POWER_CALIBRATION.value,
                },
                "current_temperature": 20.0,
                "min_temp": 5.0,
                "max_temp": 30.0,
                "model_quirks": quirks,
            },
        )
    }
    for key, value in overrides.items():
        setattr(bt, key, value)
    return bt


def test_radiator_heater_is_a_strict_no_op():
    """A radiator-flagged heater is never touched by Warm Floor."""
    bt = _make_bt(heating_type=HeatingType.RADIATOR.value)
    assert apply_warm_floor_floor(bt, "climate.trv", 10.0, is_offset=False) == 10.0


def test_unknown_entity_is_a_no_op():
    bt = _make_bt()
    assert apply_warm_floor_floor(bt, "climate.missing", 10.0, is_offset=False) == 10.0


def test_none_computed_value_passes_through():
    bt = _make_bt()
    assert apply_warm_floor_floor(bt, "climate.trv", None, is_offset=False) is None


def test_never_raises_while_contact_open():
    bt = _make_bt(contact_open=True)
    assert apply_warm_floor_floor(bt, "climate.trv", 10.0, is_offset=False) == 10.0


def test_never_raises_when_hvac_off():
    bt = _make_bt(bt_hvac_mode=HVACMode.OFF)
    assert apply_warm_floor_floor(bt, "climate.trv", 10.0, is_offset=False) == 10.0


def test_never_raises_when_call_for_heat_false():
    bt = _make_bt(call_for_heat=False)
    assert apply_warm_floor_floor(bt, "climate.trv", 10.0, is_offset=False) == 10.0


def test_raises_a_backed_off_setpoint_toward_target():
    bt = _make_bt()
    result = apply_warm_floor_floor(bt, "climate.trv", 10.0, is_offset=False)
    assert 10.0 < result < bt.bt_target_temp


def test_never_pushes_an_at_or_below_target_input_past_target():
    """The sustaining floor itself is always <= target, so it can only ever
    raise a below-target input up to (never past) target - it never invents
    an above-target result on its own."""
    bt = _make_bt()
    result = apply_warm_floor_floor(bt, "climate.trv", 21.9, is_offset=False)
    assert 21.9 <= result <= bt.bt_target_temp


def test_does_not_pull_an_already_above_target_value_down():
    """Warm Floor is a floor (max), not a ceiling - it never fights whatever
    produced an above-target value upstream (e.g. aggressive calibration's
    deliberate overshoot)."""
    bt = _make_bt()
    result = apply_warm_floor_floor(bt, "climate.trv", 25.0, is_offset=False)
    assert result == 25.0


def test_leaky_weak_emitter_room_gets_a_tighter_floor():
    """The corrected direction: high loss / low power -> smaller backoff (higher sustaining setpoint)."""
    leaky = _make_bt(
        heat_loss_rate=MAX_HEAT_LOSS, heating_power=MAX_HEATING_POWER * 0.1
    )
    insulated = _make_bt(
        heat_loss_rate=MAX_HEAT_LOSS * 0.05, heating_power=MAX_HEATING_POWER
    )
    leaky_result = apply_warm_floor_floor(leaky, "climate.trv", 10.0, is_offset=False)
    insulated_result = apply_warm_floor_floor(
        insulated, "climate.trv", 10.0, is_offset=False
    )
    assert leaky_result > insulated_result


def test_offset_path_caps_instead_of_flooring():
    """Offset semantics are inverted: Warm Floor must cap, not floor, the offset."""
    bt = _make_bt()
    unchanged = apply_warm_floor_floor(bt, "climate.trv", -1.0, is_offset=True)
    assert unchanged == -1.0

    capped = apply_warm_floor_floor(bt, "climate.trv", 5.0, is_offset=True)
    assert capped < 5.0


def test_wired_into_calculate_calibration_setpoint():
    """The floor actually reaches calculate_calibration_setpoint()'s return value."""
    bt = _make_bt()
    bt.cur_temp = 22.0
    bt.hvac_action = "idle"
    bt.name = "better_thermostat"
    result = calculate_calibration_setpoint(bt, "climate.trv")
    assert result is not None
    # Without Warm Floor this idle-cycle setpoint would be 20.0 (the TRV's
    # own internal temperature); the sustaining floor raises it toward
    # target instead of letting the setpoint (and the floor) fully idle.
    assert result > 21.0
    assert result <= bt.bt_target_temp
