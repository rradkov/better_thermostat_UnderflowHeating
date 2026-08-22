"""Config flow: the Boost toggle, gated on an already-configured Cooler.

Mirrors tests/unit/test_config_flow_underfloor.py for the second half of
the "select either Fan-coil and/or underfloor heating" requirement - reworked
so Boost reuses the existing Cooler entity instead of a separate picker.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from custom_components.better_thermostat import config_flow as config_flow_module
from custom_components.better_thermostat.utils.boost_heater import (
    CONF_BOOST_ASYMMETRIC_THRESHOLD,
    CONF_BOOST_COOLDOWN_MINUTES,
    CONF_BOOST_FAN_MODE,
    CONF_BOOST_HEATER_ENABLED,
    CONF_BOOST_LAG_MINUTES,
    CONF_BOOST_MAX_RUNTIME,
    CONF_BOOST_THRESHOLD,
    CONF_BOOST_THRESHOLD_COOL,
    DEFAULT_BOOST_COOLDOWN_MINUTES,
    DEFAULT_BOOST_LAG_MINUTES,
    DEFAULT_BOOST_MAX_RUNTIME,
    DEFAULT_BOOST_THRESHOLD,
)
from custom_components.better_thermostat.utils.const import CONF_COOLER


def _field_keys(fields) -> set[str]:
    return {getattr(key, "schema", key) for key in fields}


def _fake_hass(fan_modes=None):
    hass = MagicMock()
    if fan_modes is not None:
        hass.states.get.return_value = MagicMock(attributes={"fan_modes": fan_modes})
    else:
        hass.states.get.return_value = None
    return hass


def test_boost_toggle_hidden_without_a_configured_cooler():
    fields = config_flow_module._build_user_fields(
        mode="create", current={}, hass=_fake_hass()
    )
    assert CONF_BOOST_HEATER_ENABLED not in _field_keys(fields)


def test_boost_toggle_shown_once_cooler_is_configured():
    fields = config_flow_module._build_user_fields(
        mode="create", current={CONF_COOLER: "climate.fancoil"}, hass=_fake_hass()
    )
    assert CONF_BOOST_HEATER_ENABLED in _field_keys(fields)


def test_boost_fields_hidden_when_toggle_off():
    fields = config_flow_module._build_user_fields(
        mode="create", current={CONF_COOLER: "climate.fancoil"}, hass=_fake_hass()
    )
    keys = _field_keys(fields)
    assert CONF_BOOST_THRESHOLD not in keys


def test_boost_fields_shown_when_toggle_on():
    fields = config_flow_module._build_user_fields(
        mode="create",
        current={CONF_COOLER: "climate.fancoil", CONF_BOOST_HEATER_ENABLED: True},
        hass=_fake_hass(),
    )
    keys = _field_keys(fields)
    assert CONF_BOOST_THRESHOLD in keys
    assert CONF_BOOST_LAG_MINUTES in keys
    assert CONF_BOOST_MAX_RUNTIME in keys


def test_boost_fan_mode_field_only_appears_when_cooler_reports_fan_modes():
    hass_no_fan = _fake_hass(fan_modes=None)
    fields_no_fan = config_flow_module._build_user_fields(
        mode="create",
        current={CONF_COOLER: "climate.fancoil", CONF_BOOST_HEATER_ENABLED: True},
        hass=hass_no_fan,
    )
    assert CONF_BOOST_FAN_MODE not in _field_keys(fields_no_fan)

    hass_with_fan = _fake_hass(fan_modes=["auto", "low", "high"])
    fields_with_fan = config_flow_module._build_user_fields(
        mode="create",
        current={CONF_COOLER: "climate.fancoil", CONF_BOOST_HEATER_ENABLED: True},
        hass=hass_with_fan,
    )
    assert CONF_BOOST_FAN_MODE in _field_keys(fields_with_fan)


def test_normalize_user_submission_defaults_toggle_to_false():
    normalized = config_flow_module._normalize_user_submission({}, mode="create")
    assert normalized[CONF_BOOST_HEATER_ENABLED] is False
    assert normalized[CONF_BOOST_THRESHOLD] == DEFAULT_BOOST_THRESHOLD
    assert normalized[CONF_BOOST_LAG_MINUTES] == DEFAULT_BOOST_LAG_MINUTES
    assert normalized[CONF_BOOST_MAX_RUNTIME] == DEFAULT_BOOST_MAX_RUNTIME


def test_normalize_user_submission_round_trips_when_enabled_with_a_cooler():
    normalized = config_flow_module._normalize_user_submission(
        {
            CONF_COOLER: "climate.fancoil",
            CONF_BOOST_HEATER_ENABLED: True,
            CONF_BOOST_THRESHOLD: 3.0,
            CONF_BOOST_LAG_MINUTES: 4.0,
            CONF_BOOST_MAX_RUNTIME: 30.0,
        },
        mode="create",
    )
    assert normalized[CONF_BOOST_HEATER_ENABLED] is True
    assert normalized[CONF_BOOST_THRESHOLD] == 3.0
    assert normalized[CONF_BOOST_LAG_MINUTES] == 4.0
    assert normalized[CONF_BOOST_MAX_RUNTIME] == 30.0


def test_normalize_user_submission_forces_toggle_off_without_a_cooler():
    """The toggle can't be enabled if no Cooler entity is submitted, even if
    the raw form data claims it's on - there'd be nothing to boost."""
    normalized = config_flow_module._normalize_user_submission(
        {CONF_BOOST_HEATER_ENABLED: True, CONF_BOOST_THRESHOLD: 9.0}, mode="create"
    )
    assert normalized[CONF_BOOST_HEATER_ENABLED] is False
    assert normalized[CONF_BOOST_THRESHOLD] == DEFAULT_BOOST_THRESHOLD


def test_normalize_user_submission_clears_stale_values_when_disabled():
    normalized = config_flow_module._normalize_user_submission(
        {
            CONF_COOLER: "climate.fancoil",
            CONF_BOOST_HEATER_ENABLED: False,
            CONF_BOOST_THRESHOLD: 9.0,
        },
        mode="create",
    )
    assert normalized[CONF_BOOST_HEATER_ENABLED] is False
    assert normalized[CONF_BOOST_THRESHOLD] == DEFAULT_BOOST_THRESHOLD


def test_normalize_user_submission_clamps_threshold_to_bounds():
    too_high = config_flow_module._normalize_user_submission(
        {
            CONF_COOLER: "climate.fancoil",
            CONF_BOOST_HEATER_ENABLED: True,
            CONF_BOOST_THRESHOLD: 999.0,
        },
        mode="create",
    )
    assert too_high[CONF_BOOST_THRESHOLD] == 15.0

    too_low = config_flow_module._normalize_user_submission(
        {
            CONF_COOLER: "climate.fancoil",
            CONF_BOOST_HEATER_ENABLED: True,
            CONF_BOOST_THRESHOLD: -5.0,
        },
        mode="create",
    )
    assert too_low[CONF_BOOST_THRESHOLD] == 0.1


def test_asymmetric_threshold_field_hidden_by_default():
    fields = config_flow_module._build_user_fields(
        mode="create",
        current={CONF_COOLER: "climate.fancoil", CONF_BOOST_HEATER_ENABLED: True},
        hass=_fake_hass(),
    )
    keys = _field_keys(fields)
    assert CONF_BOOST_ASYMMETRIC_THRESHOLD in keys
    assert CONF_BOOST_THRESHOLD_COOL not in keys
    assert CONF_BOOST_COOLDOWN_MINUTES in keys


def test_asymmetric_threshold_field_shown_once_toggle_is_on():
    fields = config_flow_module._build_user_fields(
        mode="create",
        current={
            CONF_COOLER: "climate.fancoil",
            CONF_BOOST_HEATER_ENABLED: True,
            CONF_BOOST_ASYMMETRIC_THRESHOLD: True,
        },
        hass=_fake_hass(),
    )
    assert CONF_BOOST_THRESHOLD_COOL in _field_keys(fields)


def test_normalize_defaults_asymmetric_threshold_and_cooldown():
    normalized = config_flow_module._normalize_user_submission({}, mode="create")
    assert normalized[CONF_BOOST_ASYMMETRIC_THRESHOLD] is False
    assert normalized[CONF_BOOST_THRESHOLD_COOL] is None
    assert normalized[CONF_BOOST_COOLDOWN_MINUTES] == DEFAULT_BOOST_COOLDOWN_MINUTES


def test_normalize_asymmetric_threshold_round_trips_when_enabled():
    normalized = config_flow_module._normalize_user_submission(
        {
            CONF_COOLER: "climate.fancoil",
            CONF_BOOST_HEATER_ENABLED: True,
            CONF_BOOST_ASYMMETRIC_THRESHOLD: True,
            CONF_BOOST_THRESHOLD_COOL: 2.0,
            CONF_BOOST_COOLDOWN_MINUTES: 20.0,
        },
        mode="create",
    )
    assert normalized[CONF_BOOST_ASYMMETRIC_THRESHOLD] is True
    assert normalized[CONF_BOOST_THRESHOLD_COOL] == 2.0
    assert normalized[CONF_BOOST_COOLDOWN_MINUTES] == 20.0


def test_normalize_asymmetric_threshold_off_resets_the_cool_value():
    """A stale cool threshold from a submission made while the toggle was on
    must not silently persist once it's off again."""
    normalized = config_flow_module._normalize_user_submission(
        {
            CONF_COOLER: "climate.fancoil",
            CONF_BOOST_HEATER_ENABLED: True,
            CONF_BOOST_ASYMMETRIC_THRESHOLD: False,
            CONF_BOOST_THRESHOLD_COOL: 2.0,
        },
        mode="create",
    )
    assert normalized[CONF_BOOST_ASYMMETRIC_THRESHOLD] is False
    assert normalized[CONF_BOOST_THRESHOLD_COOL] is None


def test_normalize_cooldown_clamps_to_bounds():
    too_high = config_flow_module._normalize_user_submission(
        {
            CONF_COOLER: "climate.fancoil",
            CONF_BOOST_HEATER_ENABLED: True,
            CONF_BOOST_COOLDOWN_MINUTES: 999.0,
        },
        mode="create",
    )
    assert too_high[CONF_BOOST_COOLDOWN_MINUTES] == 120.0

    too_low = config_flow_module._normalize_user_submission(
        {
            CONF_COOLER: "climate.fancoil",
            CONF_BOOST_HEATER_ENABLED: True,
            CONF_BOOST_COOLDOWN_MINUTES: -5.0,
        },
        mode="create",
    )
    assert too_low[CONF_BOOST_COOLDOWN_MINUTES] == 0.0
