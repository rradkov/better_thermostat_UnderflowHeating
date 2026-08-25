"""Config flow: the Underfloor Heating toggle and per-heater UFH fields.

Covers the "select either Fan-coil and/or underfloor heating" requirement's
underfloor half: a top-level opt-in toggle that unlocks a per-heater
``heating_type``/Warm Floor Level pair (plus the raw °C fields once "Custom"
is selected), normalized round-trip through both the create and options
flows, and the instance-level calibration-mode soft-filter for UFH heaters.
"""

from __future__ import annotations

from custom_components.better_thermostat import config_flow as config_flow_module
from custom_components.better_thermostat.utils.const import (
    CONF_CALIBRATION_MODE,
    CONF_SHOW_ALL_CALIBRATION_MODES,
    CalibrationMode,
)
from custom_components.better_thermostat.utils.underfloor import (
    CONF_HEATING_TYPE,
    CONF_UNDERFLOOR_HEATING_ENABLED,
    CONF_WARM_FLOOR_LEVEL,
    CONF_WARM_FLOOR_MAX_BACKOFF,
    CONF_WARM_FLOOR_SUSTAIN_PUSH,
    WARM_FLOOR_LEVEL_PRESETS,
    HeatingType,
)


def _field_keys(fields) -> set[str]:
    """Extract plain string keys from a voluptuous schema OrderedDict."""
    return {getattr(key, "schema", key) for key in fields}


def _field_options(fields, key: str) -> list[str]:
    """Return the option tokens a SelectSelector field for ``key`` offers."""
    for schema_key, field_type in fields.items():
        if getattr(schema_key, "schema", schema_key) != key:
            continue
        return list(field_type.config["options"])
    raise AssertionError(f"{key} not found in fields")


def test_user_fields_always_offer_the_underfloor_toggle():
    fields = config_flow_module._build_user_fields(mode="create", current={})
    assert CONF_UNDERFLOOR_HEATING_ENABLED in _field_keys(fields)


def test_user_fields_hide_show_all_calibration_modes_when_underfloor_off():
    fields = config_flow_module._build_user_fields(mode="create", current={})
    assert CONF_SHOW_ALL_CALIBRATION_MODES not in _field_keys(fields)


def test_user_fields_reveal_show_all_calibration_modes_when_underfloor_on():
    fields = config_flow_module._build_user_fields(
        mode="create", current={CONF_UNDERFLOOR_HEATING_ENABLED: True}
    )
    assert CONF_SHOW_ALL_CALIBRATION_MODES in _field_keys(fields)


def test_normalize_user_submission_defaults_toggle_to_false():
    normalized = config_flow_module._normalize_user_submission({}, mode="create")
    assert normalized[CONF_UNDERFLOOR_HEATING_ENABLED] is False
    assert normalized[CONF_SHOW_ALL_CALIBRATION_MODES] is False


def test_normalize_user_submission_round_trips_true():
    normalized = config_flow_module._normalize_user_submission(
        {CONF_UNDERFLOOR_HEATING_ENABLED: True}, mode="create"
    )
    assert normalized[CONF_UNDERFLOOR_HEATING_ENABLED] is True


def test_normalize_user_submission_drops_show_all_when_underfloor_off():
    """A stale show-all flag from a submission made while underfloor was on
    must not silently persist once it's off again."""
    normalized = config_flow_module._normalize_user_submission(
        {CONF_UNDERFLOOR_HEATING_ENABLED: False, CONF_SHOW_ALL_CALIBRATION_MODES: True},
        mode="create",
    )
    assert normalized[CONF_SHOW_ALL_CALIBRATION_MODES] is False


def test_normalize_user_submission_keeps_show_all_when_underfloor_on():
    normalized = config_flow_module._normalize_user_submission(
        {CONF_UNDERFLOOR_HEATING_ENABLED: True, CONF_SHOW_ALL_CALIBRATION_MODES: True},
        mode="create",
    )
    assert normalized[CONF_SHOW_ALL_CALIBRATION_MODES] is True


def test_advanced_fields_hidden_when_underfloor_disabled():
    fields = config_flow_module._build_advanced_fields(
        sources=({}, None),
        default_calibration="target_temp_based",
        homematic=False,
        has_auto=False,
        underfloor_enabled=False,
    )
    keys = _field_keys(fields)
    assert CONF_HEATING_TYPE not in keys
    assert CONF_WARM_FLOOR_LEVEL not in keys
    assert CONF_WARM_FLOOR_MAX_BACKOFF not in keys
    assert CONF_WARM_FLOOR_SUSTAIN_PUSH not in keys


def test_advanced_fields_shown_when_underfloor_enabled():
    fields = config_flow_module._build_advanced_fields(
        sources=({}, None),
        default_calibration="target_temp_based",
        homematic=False,
        has_auto=False,
        underfloor_enabled=True,
    )
    keys = _field_keys(fields)
    assert CONF_HEATING_TYPE in keys
    assert CONF_WARM_FLOOR_LEVEL in keys


def test_advanced_fields_heating_type_offers_all_four_device_types():
    fields = config_flow_module._build_advanced_fields(
        sources=({}, None),
        default_calibration="target_temp_based",
        homematic=False,
        has_auto=False,
        underfloor_enabled=True,
    )
    assert set(_field_options(fields, CONF_HEATING_TYPE)) == {
        HeatingType.UNDERFLOOR,
        HeatingType.RADIATOR,
        HeatingType.CONVECTOR,
        HeatingType.AC_HVAC,
    }


def test_advanced_fields_raw_backoff_and_push_hidden_by_default_level():
    """A fresh heater (no stored warm_floor_level) starts on the "Balanced"
    preset - the raw °C fields stay hidden until "Custom" is chosen."""
    fields = config_flow_module._build_advanced_fields(
        sources=({}, None),
        default_calibration="target_temp_based",
        homematic=False,
        has_auto=False,
        underfloor_enabled=True,
    )
    keys = _field_keys(fields)
    assert CONF_WARM_FLOOR_MAX_BACKOFF not in keys
    assert CONF_WARM_FLOOR_SUSTAIN_PUSH not in keys


def test_advanced_fields_raw_backoff_and_push_shown_once_level_is_custom():
    fields = config_flow_module._build_advanced_fields(
        sources=({CONF_WARM_FLOOR_LEVEL: "custom"}, None),
        default_calibration="target_temp_based",
        homematic=False,
        has_auto=False,
        underfloor_enabled=True,
    )
    keys = _field_keys(fields)
    assert CONF_WARM_FLOOR_MAX_BACKOFF in keys
    assert CONF_WARM_FLOOR_SUSTAIN_PUSH in keys


def test_normalize_advanced_submission_ignores_heating_type_when_disabled():
    """A heating_type in the submission is dropped if the instance-level
    toggle isn't on - it should never silently take effect."""
    normalized = config_flow_module._normalize_advanced_submission(
        {CONF_HEATING_TYPE: HeatingType.UNDERFLOOR.value},
        default_calibration="target_temp_based",
        homematic=False,
        has_auto=False,
        underfloor_enabled=False,
    )
    assert CONF_HEATING_TYPE not in normalized
    assert CONF_WARM_FLOOR_LEVEL not in normalized
    assert CONF_WARM_FLOOR_SUSTAIN_PUSH not in normalized


def test_normalize_advanced_submission_applies_heating_type_when_enabled():
    normalized = config_flow_module._normalize_advanced_submission(
        {CONF_HEATING_TYPE: HeatingType.RADIATOR.value},
        default_calibration="target_temp_based",
        homematic=False,
        has_auto=False,
        underfloor_enabled=True,
    )
    assert normalized[CONF_HEATING_TYPE] == HeatingType.RADIATOR.value


def test_normalize_advanced_submission_defaults_to_underfloor():
    """Once the field is shown at all, an unset heating_type defaults to
    "Под" / Underfloor, not Radiator - see DEFAULT_HEATING_TYPE_WHEN_SHOWN."""
    normalized = config_flow_module._normalize_advanced_submission(
        {},
        default_calibration="target_temp_based",
        homematic=False,
        has_auto=False,
        underfloor_enabled=True,
    )
    assert normalized[CONF_HEATING_TYPE] == HeatingType.UNDERFLOOR.value


def test_normalize_advanced_submission_default_level_resolves_to_balanced_preset():
    normalized = config_flow_module._normalize_advanced_submission(
        {},
        default_calibration="target_temp_based",
        homematic=False,
        has_auto=False,
        underfloor_enabled=True,
    )
    assert normalized[CONF_WARM_FLOOR_LEVEL] == "level_2"
    backoff, push = WARM_FLOOR_LEVEL_PRESETS["level_2"]
    assert normalized[CONF_WARM_FLOOR_MAX_BACKOFF] == backoff
    assert normalized[CONF_WARM_FLOOR_SUSTAIN_PUSH] == push


def test_normalize_advanced_submission_level_overrides_submitted_raw_fields():
    """A preset level drives both raw fields directly - anything submitted
    for them (e.g. leftover from a prior Custom selection) is replaced, not
    merged."""
    normalized = config_flow_module._normalize_advanced_submission(
        {
            CONF_WARM_FLOOR_LEVEL: "level_3",
            CONF_WARM_FLOOR_MAX_BACKOFF: 4.9,
            CONF_WARM_FLOOR_SUSTAIN_PUSH: 1.4,
        },
        default_calibration="target_temp_based",
        homematic=False,
        has_auto=False,
        underfloor_enabled=True,
    )
    backoff, push = WARM_FLOOR_LEVEL_PRESETS["level_3"]
    assert normalized[CONF_WARM_FLOOR_MAX_BACKOFF] == backoff
    assert normalized[CONF_WARM_FLOOR_SUSTAIN_PUSH] == push


def test_normalize_advanced_submission_custom_level_keeps_submitted_raw_fields():
    normalized = config_flow_module._normalize_advanced_submission(
        {
            CONF_WARM_FLOOR_LEVEL: "custom",
            CONF_WARM_FLOOR_MAX_BACKOFF: 2.2,
            CONF_WARM_FLOOR_SUSTAIN_PUSH: 0.7,
        },
        default_calibration="target_temp_based",
        homematic=False,
        has_auto=False,
        underfloor_enabled=True,
    )
    assert normalized[CONF_WARM_FLOOR_LEVEL] == "custom"
    assert normalized[CONF_WARM_FLOOR_MAX_BACKOFF] == 2.2
    assert normalized[CONF_WARM_FLOOR_SUSTAIN_PUSH] == 0.7


def test_normalize_advanced_submission_invalid_level_falls_back_to_default():
    normalized = config_flow_module._normalize_advanced_submission(
        {CONF_WARM_FLOOR_LEVEL: "not-a-real-level"},
        default_calibration="target_temp_based",
        homematic=False,
        has_auto=False,
        underfloor_enabled=True,
    )
    assert normalized[CONF_WARM_FLOOR_LEVEL] == "level_2"


def test_normalize_advanced_submission_clamps_custom_backoff_and_push_to_bounds():
    from custom_components.better_thermostat.utils.underfloor import (
        WARM_FLOOR_MAX_BACKOFF_CAP,
        WARM_FLOOR_MIN_BACKOFF,
        WARM_FLOOR_SUSTAIN_PUSH_CAP,
        WARM_FLOOR_SUSTAIN_PUSH_MIN,
    )

    too_high = config_flow_module._normalize_advanced_submission(
        {
            CONF_WARM_FLOOR_LEVEL: "custom",
            CONF_WARM_FLOOR_MAX_BACKOFF: 999.0,
            CONF_WARM_FLOOR_SUSTAIN_PUSH: 999.0,
        },
        default_calibration="target_temp_based",
        homematic=False,
        has_auto=False,
        underfloor_enabled=True,
    )
    assert too_high[CONF_WARM_FLOOR_MAX_BACKOFF] == WARM_FLOOR_MAX_BACKOFF_CAP
    assert too_high[CONF_WARM_FLOOR_SUSTAIN_PUSH] == WARM_FLOOR_SUSTAIN_PUSH_CAP

    too_low = config_flow_module._normalize_advanced_submission(
        {
            CONF_WARM_FLOOR_LEVEL: "custom",
            CONF_WARM_FLOOR_MAX_BACKOFF: -5.0,
            CONF_WARM_FLOOR_SUSTAIN_PUSH: -5.0,
        },
        default_calibration="target_temp_based",
        homematic=False,
        has_auto=False,
        underfloor_enabled=True,
    )
    assert too_low[CONF_WARM_FLOOR_MAX_BACKOFF] == WARM_FLOOR_MIN_BACKOFF
    assert too_low[CONF_WARM_FLOOR_SUSTAIN_PUSH] == WARM_FLOOR_SUSTAIN_PUSH_MIN


def test_calibration_mode_offers_only_recommended_modes_for_underfloor_heater():
    fields = config_flow_module._build_advanced_fields(
        sources=({CONF_HEATING_TYPE: HeatingType.UNDERFLOOR.value}, None),
        default_calibration="target_temp_based",
        homematic=False,
        has_auto=False,
        underfloor_enabled=True,
        show_all_calibration_modes=False,
    )
    assert set(_field_options(fields, CONF_CALIBRATION_MODE)) == {
        CalibrationMode.MPC_CALIBRATION,
        CalibrationMode.MPC_V2_CALIBRATION,
        CalibrationMode.TPI_CALIBRATION,
        CalibrationMode.PID_CALIBRATION,
    }


def test_calibration_mode_show_all_reveals_every_mode_for_underfloor_heater():
    fields = config_flow_module._build_advanced_fields(
        sources=({CONF_HEATING_TYPE: HeatingType.UNDERFLOOR.value}, None),
        default_calibration="target_temp_based",
        homematic=False,
        has_auto=False,
        underfloor_enabled=True,
        show_all_calibration_modes=True,
    )
    assert len(_field_options(fields, CONF_CALIBRATION_MODE)) == 8


def test_calibration_mode_offers_every_mode_for_a_non_underfloor_heater():
    fields = config_flow_module._build_advanced_fields(
        sources=({CONF_HEATING_TYPE: HeatingType.RADIATOR.value}, None),
        default_calibration="target_temp_based",
        homematic=False,
        has_auto=False,
        underfloor_enabled=True,
        show_all_calibration_modes=False,
    )
    assert len(_field_options(fields, CONF_CALIBRATION_MODE)) == 8


def test_calibration_mode_offers_every_mode_when_underfloor_instance_toggle_off():
    fields = config_flow_module._build_advanced_fields(
        sources=({}, None),
        default_calibration="target_temp_based",
        homematic=False,
        has_auto=False,
        underfloor_enabled=False,
        show_all_calibration_modes=False,
    )
    assert len(_field_options(fields, CONF_CALIBRATION_MODE)) == 8
