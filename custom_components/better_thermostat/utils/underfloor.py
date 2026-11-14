"""Warm Floor settings and Home Assistant configuration fields."""

from __future__ import annotations

from collections.abc import Callable, Mapping
import math
from typing import TypedDict

from homeassistant.helpers import selector
import probatio as vol

CONF_UNDERFLOOR_HEATING_ENABLED = "underfloor_heating_enabled"
CONF_HEATING_TYPE = "heating_type"
CONF_WARM_FLOOR_LEVEL = "warm_floor_level"
CONF_WARM_FLOOR_MAX_BACKOFF = "warm_floor_max_backoff"
CONF_WARM_FLOOR_SUSTAIN_PUSH = "warm_floor_sustain_push"

PRESETS = {
    "level_1": (3.0, 0.0),
    "level_2": (1.5, 0.0),
    "level_3": (0.5, 0.3),
    "auto": (1.5, 0.0),
}
LEVELS = (*PRESETS, "custom")
HEATING_TYPES = ("underfloor", "radiator", "convector", "ac_hvac")
LEGACY_BOOST_KEYS = frozenset(
    {
        "boost_heater_enabled",
        "boost_heater",
        "boost_threshold",
        "boost_asymmetric_threshold",
        "boost_threshold_cool",
        "boost_fan_mode",
        "boost_lag_minutes",
        "boost_max_runtime",
        "boost_cooldown_minutes",
    }
)


def migrate_fork_settings(settings: Mapping[str, object]) -> dict[str, object]:
    """Remove only the retired secondary Boost options, retaining room settings."""
    return {
        key: value for key, value in settings.items() if key not in LEGACY_BOOST_KEYS
    }


class WarmFloorSettings(TypedDict):
    """Persisted per-circuit options, retaining the fork's existing keys."""

    heating_type: str
    warm_floor_level: str
    warm_floor_max_backoff: float
    warm_floor_sustain_push: float


def finite_number(value: object, default: float) -> float:
    """Read a finite setting without accepting bools or containers."""
    if isinstance(value, bool) or not isinstance(value, str | int | float):
        return default
    try:
        number = float(value)
    except ValueError:
        return default
    return number if math.isfinite(number) else default


def normalize_warm_floor(data: Mapping[str, object]) -> WarmFloorSettings:
    """Normalize existing and submitted settings without changing their keys."""
    heating_type = data.get(CONF_HEATING_TYPE, "underfloor")
    level = data.get(CONF_WARM_FLOOR_LEVEL, "level_2")
    level = level if isinstance(level, str) and level in LEVELS else "level_2"
    backoff, push = PRESETS.get(level, (1.5, 0.0))
    if level == "custom":
        backoff = finite_number(data.get(CONF_WARM_FLOOR_MAX_BACKOFF), backoff)
        push = finite_number(data.get(CONF_WARM_FLOOR_SUSTAIN_PUSH), push)
    return WarmFloorSettings(
        heating_type=heating_type if isinstance(heating_type, str) else "underfloor",
        warm_floor_level=level,
        warm_floor_max_backoff=max(0.2, min(backoff, 5.0)),
        warm_floor_sustain_push=max(0.0, min(push, 1.5)),
    )


def optional_flow_temperature(value: object) -> float | None:
    """Omit an absent or unusable flow input instead of inventing a reading."""
    number = finite_number(value, math.nan)
    return number if math.isfinite(number) and 0.0 <= number <= 100.0 else None


def build_warm_floor_fields(
    resolve: Callable[[str, object], object],
) -> dict[vol.Marker, object]:
    """Build per-circuit selectors without adding an actuator-type field."""
    return {
        vol.Optional(
            CONF_HEATING_TYPE, default=resolve(CONF_HEATING_TYPE, "underfloor")
        ): selector.SelectSelector(
            selector.SelectSelectorConfig(
                options=list(HEATING_TYPES), translation_key="heating_type"
            )
        ),
        vol.Optional(
            CONF_WARM_FLOOR_LEVEL, default=resolve(CONF_WARM_FLOOR_LEVEL, "level_2")
        ): selector.SelectSelector(
            selector.SelectSelectorConfig(
                options=list(LEVELS), translation_key="warm_floor_level"
            )
        ),
        vol.Optional(
            CONF_WARM_FLOOR_MAX_BACKOFF,
            default=resolve(CONF_WARM_FLOOR_MAX_BACKOFF, 1.5),
        ): vol.All(vol.Coerce(float), vol.Range(min=0.2, max=5.0)),
        vol.Optional(
            CONF_WARM_FLOOR_SUSTAIN_PUSH,
            default=resolve(CONF_WARM_FLOOR_SUSTAIN_PUSH, 0.0),
        ): vol.All(vol.Coerce(float), vol.Range(min=0.0, max=1.5)),
    }
