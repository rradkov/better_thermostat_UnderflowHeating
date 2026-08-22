"""Config flow: the Underfloor Heating toggle and per-heater heating_type field.

Covers the "select either Fan-coil and/or underfloor heating" requirement's
underfloor half: a top-level opt-in toggle that unlocks a per-heater
``heating_type``/``warm_floor_max_backoff`` pair, normalized round-trip
through both the create and options flows.
"""

from __future__ import annotations

from custom_components.better_thermostat import config_flow as config_flow_module
from custom_components.better_thermostat.utils.underfloor import (
    CONF_HEATING_TYPE,
    CONF_UNDERFLOOR_HEATING_ENABLED,
    CONF_WARM_FLOOR_MAX_BACKOFF,
    HeatingType,
)


def _field_keys(fields) -> set[str]:
    """Extract plain string keys from a voluptuous schema OrderedDict."""
    return {getattr(key, "schema", key) for key in fields}


def test_user_fields_always_offer_the_underfloor_toggle():
    fields = config_flow_module._build_user_fields(mode="create", current={})
    assert CONF_UNDERFLOOR_HEATING_ENABLED in _field_keys(fields)


def test_normalize_user_submission_defaults_toggle_to_false():
    normalized = config_flow_module._normalize_user_submission({}, mode="create")
    assert normalized[CONF_UNDERFLOOR_HEATING_ENABLED] is False


def test_normalize_user_submission_round_trips_true():
    normalized = config_flow_module._normalize_user_submission(
        {CONF_UNDERFLOOR_HEATING_ENABLED: True}, mode="create"
    )
    assert normalized[CONF_UNDERFLOOR_HEATING_ENABLED] is True


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
    assert CONF_WARM_FLOOR_MAX_BACKOFF not in keys


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
    assert CONF_WARM_FLOOR_MAX_BACKOFF in keys


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


def test_normalize_advanced_submission_applies_heating_type_when_enabled():
    normalized = config_flow_module._normalize_advanced_submission(
        {CONF_HEATING_TYPE: HeatingType.UNDERFLOOR.value},
        default_calibration="target_temp_based",
        homematic=False,
        has_auto=False,
        underfloor_enabled=True,
    )
    assert normalized[CONF_HEATING_TYPE] == HeatingType.UNDERFLOOR.value


def test_normalize_advanced_submission_defaults_to_radiator():
    normalized = config_flow_module._normalize_advanced_submission(
        {},
        default_calibration="target_temp_based",
        homematic=False,
        has_auto=False,
        underfloor_enabled=True,
    )
    assert normalized[CONF_HEATING_TYPE] == HeatingType.RADIATOR.value


def test_normalize_advanced_submission_clamps_backoff_to_bounds():
    from custom_components.better_thermostat.utils.underfloor import (
        WARM_FLOOR_MAX_BACKOFF_CAP,
        WARM_FLOOR_MIN_BACKOFF,
    )

    too_high = config_flow_module._normalize_advanced_submission(
        {CONF_WARM_FLOOR_MAX_BACKOFF: 999.0},
        default_calibration="target_temp_based",
        homematic=False,
        has_auto=False,
        underfloor_enabled=True,
    )
    assert too_high[CONF_WARM_FLOOR_MAX_BACKOFF] == WARM_FLOOR_MAX_BACKOFF_CAP

    too_low = config_flow_module._normalize_advanced_submission(
        {CONF_WARM_FLOOR_MAX_BACKOFF: -5.0},
        default_calibration="target_temp_based",
        homematic=False,
        has_auto=False,
        underfloor_enabled=True,
    )
    assert too_low[CONF_WARM_FLOOR_MAX_BACKOFF] == WARM_FLOOR_MIN_BACKOFF
