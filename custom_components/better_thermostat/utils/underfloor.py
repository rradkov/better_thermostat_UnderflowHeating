"""Warm Floor mode: a sustaining-heat floor for underfloor heating (UFH) heaters.

Underfloor heating has much higher thermal inertia than a radiator TRV: once
the room is satisfied and Better Thermostat backs a heater fully off, the
slab keeps cooling for a long time, and recovering from a fully-cold floor
is slow and energy-costly. Warm Floor keeps a low, continuous background
heat level in UFH-flagged heaters instead of letting them fully idle. The
passive backoff floor never pushes the room air above target; the opt-in
"sustain push" (off by default) pushes only the downstream setpoint sent to
the physical heater a small amount above target, never ``bt.bt_target_temp``
itself.

Everything here is additive: a heater only gets this behavior when its
per-heater ``heating_type`` advanced option is explicitly set to
``HeatingType.UNDERFLOOR`` (the default once the field is shown at all -
``RADIATOR``, ``CONVECTOR`` and ``AC_HVAC`` are all an equal, strict no-op
through this module). All computation reuses telemetry the BetterThermostat
entity already maintains (``heat_loss_rate``, ``heating_power``,
``temp_slope``, and, for MPC-calibrated heaters, the per-TRV learned MPC
state) — no new thermal model is introduced.
"""

from __future__ import annotations

from enum import StrEnum
import logging
from typing import Any, Final

from homeassistant.components.climate.const import HVACAction, HVACMode
from homeassistant.util import dt as dt_util

from custom_components.better_thermostat.utils.const import (
    MAX_HEAT_LOSS,
    MAX_HEATING_POWER,
    CalibrationMode,
)
from custom_components.better_thermostat.utils.helpers import (
    convert_to_float,
    normalize_calibration_mode,
)

_LOGGER = logging.getLogger(__name__)

# -- Config keys ---------------------------------------------------------
CONF_UNDERFLOOR_HEATING_ENABLED: Final = "underfloor_heating_enabled"
CONF_HEATING_TYPE: Final = "heating_type"
CONF_WARM_FLOOR_MAX_BACKOFF: Final = "warm_floor_max_backoff"
CONF_WARM_FLOOR_SUSTAIN_PUSH: Final = "warm_floor_sustain_push"
CONF_WARM_FLOOR_LEVEL: Final = "warm_floor_level"


class HeatingType(StrEnum):
    """Per-heater emitter type. Only ``UNDERFLOOR`` gets Warm Floor behavior.

    ``RADIATOR``, ``CONVECTOR`` and ``AC_HVAC`` are all equally a strict
    no-op through this module today - the split exists so a mixed
    underfloor+secondary-device instance can label each heater correctly,
    not because they're handled differently yet.
    """

    UNDERFLOOR = "underfloor"
    RADIATOR = "radiator"
    CONVECTOR = "convector"
    AC_HVAC = "ac_hvac"


# Safe runtime fallback used whenever a heater simply has no heating_type
# stored at all - notably every heater on every instance that has never
# turned on "Enable Underfloor Heating". apply_warm_floor_floor() reads this
# unconditionally on every calibration cycle regardless of that toggle, so
# this must stay a strict no-op (RADIATOR) - defaulting it to UNDERFLOOR
# would silently switch on Warm Floor for radiator-only setups that never
# opted in.
DEFAULT_HEATING_TYPE: Final = HeatingType.RADIATOR
# The per-heater field's own *displayed* default, used only once the field
# actually renders (build_underfloor_advanced_fields() and
# normalize_underfloor_advanced_submission() are both only reachable while
# the instance-level toggle is on) - "по подразбиране трябва да е под".
DEFAULT_HEATING_TYPE_WHEN_SHOWN: Final = HeatingType.UNDERFLOOR

# -- Tunable bounds --------------------------------------------------------
WARM_FLOOR_MIN_BACKOFF: Final = 0.2
WARM_FLOOR_MAX_BACKOFF_DEFAULT: Final = 1.5
WARM_FLOOR_MAX_BACKOFF_CAP: Final = 5.0
# Cap deliberately much smaller than the backoff side's - this pushes
# *above* what the user sees as target, a materially more sensitive
# direction.
DEFAULT_WARM_FLOOR_SUSTAIN_PUSH: Final = 0.0
WARM_FLOOR_SUSTAIN_PUSH_MIN: Final = 0.0
WARM_FLOOR_SUSTAIN_PUSH_CAP: Final = 1.5
# A leaky room or a weak emitter still gets *some* backoff allowance, never
# zero - this is a floor on the structural scale factor, not on the
# temperature itself.
WARM_FLOOR_MIN_BACKOFF_RATIO: Final = 0.1
# °C/min guards on the live temp_slope signal, see apply_warm_floor_floor().
OVERSHOOT_SLOPE_GUARD: Final = 0.05
UNDERSHOOT_SLOPE_GUARD: Final = 0.02
# A stale external sensor can't be trusted to prove the room has actually
# stopped rising - treat it the same as "still gaining" rather than "no
# protection needed". Fixed, no config field (see _external_sensor_is_stale).
WARM_FLOOR_SENSOR_STALE_AFTER_S: Final = 1200.0

# "Ниво на топъл под" / Warm Floor Level: a single dial that resolves to a
# (max_backoff, sustain_push) pair at config-normalize time, so
# apply_warm_floor_floor() and _sustaining_setpoint() never need to know
# levels exist - they keep reading the plain CONF_WARM_FLOOR_MAX_BACKOFF /
# CONF_WARM_FLOOR_SUSTAIN_PUSH fields exactly as before. "custom" leaves
# whatever raw values were submitted untouched.
WARM_FLOOR_LEVEL_ECO: Final = "level_1"
WARM_FLOOR_LEVEL_BALANCED: Final = "level_2"
WARM_FLOOR_LEVEL_KEEP_HOT: Final = "level_3"
WARM_FLOOR_LEVEL_CUSTOM: Final = "custom"
DEFAULT_WARM_FLOOR_LEVEL: Final = WARM_FLOOR_LEVEL_BALANCED

WARM_FLOOR_LEVEL_PRESETS: Final[dict[str, tuple[float, float]]] = {
    WARM_FLOOR_LEVEL_ECO: (3.0, 0.0),
    WARM_FLOOR_LEVEL_BALANCED: (1.5, 0.0),
    WARM_FLOOR_LEVEL_KEEP_HOT: (0.5, 0.3),
}


# ---------------------------------------------------------------------------
# Config flow helpers
# ---------------------------------------------------------------------------


def build_underfloor_user_fields(resolve) -> dict[str, Any]:
    """Return the top-level 'user' step field(s) for this feature.

    Plain ``{key: field_type}`` pairs - the caller (``config_flow.py``)
    wraps each key in its own ``vol.Optional(key, default=resolve(key, ...))``,
    same as every other field it builds. ``resolve`` is the same
    ``resolve(key, default=None)`` closure ``config_flow.py`` already builds
    for every other conditional field (e.g. the cooler resend-interval
    field), unused here but accepted for a consistent call signature.
    """
    return {CONF_UNDERFLOOR_HEATING_ENABLED: bool}


def normalize_underfloor_user_submission(data: dict[str, Any]) -> dict[str, Any]:
    """Normalize the top-level submission for this feature."""
    from custom_components.better_thermostat.config_flow import _as_bool

    return {
        CONF_UNDERFLOOR_HEATING_ENABLED: _as_bool(
            data.get(CONF_UNDERFLOOR_HEATING_ENABLED), False
        )
    }


def build_underfloor_advanced_fields(
    get_value, *, underfloor_enabled: bool
) -> dict[Any, Any]:
    """Return the per-heater 'advanced' step field(s) for this feature.

    Only shown when the parent instance has underfloor heating enabled;
    otherwise every heater implicitly stays a radiator. ``get_value(key,
    fallback)`` is the same closure ``config_flow._build_advanced_fields()``
    already builds over its ``sources`` for every other field, so this
    follows the exact existing idiom. Returned keys are already-wrapped
    ``vol.Optional(...)`` markers, matching how the caller merges the result
    straight into its ``OrderedDict`` schema via ``ordered.update(...)``.
    """
    if not underfloor_enabled:
        return {}

    from homeassistant.helpers import selector
    import voluptuous as vol

    heating_type_selector = selector.SelectSelector(
        selector.SelectSelectorConfig(
            options=[
                HeatingType.UNDERFLOOR,
                HeatingType.RADIATOR,
                HeatingType.CONVECTOR,
                HeatingType.AC_HVAC,
            ],
            mode=selector.SelectSelectorMode.DROPDOWN,
            translation_key="heating_type",
        )
    )
    warm_floor_level_selector = selector.SelectSelector(
        selector.SelectSelectorConfig(
            options=[
                WARM_FLOOR_LEVEL_ECO,
                WARM_FLOOR_LEVEL_BALANCED,
                WARM_FLOOR_LEVEL_KEEP_HOT,
                WARM_FLOOR_LEVEL_CUSTOM,
            ],
            mode=selector.SelectSelectorMode.DROPDOWN,
            translation_key="warm_floor_level",
        )
    )

    fields: dict[Any, Any] = {
        vol.Optional(
            CONF_HEATING_TYPE,
            default=get_value(CONF_HEATING_TYPE, DEFAULT_HEATING_TYPE_WHEN_SHOWN.value),
        ): heating_type_selector,
        vol.Optional(
            CONF_WARM_FLOOR_LEVEL,
            default=get_value(CONF_WARM_FLOOR_LEVEL, DEFAULT_WARM_FLOOR_LEVEL),
        ): warm_floor_level_selector,
    }

    # The raw °C fields stay available side-by-side with the Level dial
    # (not replaced by it) but only need to be shown once "Custom" is
    # selected - otherwise the Level preset drives both, resolved at
    # normalize time (see normalize_underfloor_advanced_submission()).
    warm_floor_level = get_value(CONF_WARM_FLOOR_LEVEL, DEFAULT_WARM_FLOOR_LEVEL)
    if warm_floor_level == WARM_FLOOR_LEVEL_CUSTOM:
        fields[
            vol.Optional(
                CONF_WARM_FLOOR_MAX_BACKOFF,
                default=get_value(
                    CONF_WARM_FLOOR_MAX_BACKOFF, WARM_FLOOR_MAX_BACKOFF_DEFAULT
                ),
            )
        ] = vol.All(
            vol.Coerce(float),
            vol.Range(min=WARM_FLOOR_MIN_BACKOFF, max=WARM_FLOOR_MAX_BACKOFF_CAP),
        )
        fields[
            vol.Optional(
                CONF_WARM_FLOOR_SUSTAIN_PUSH,
                default=get_value(
                    CONF_WARM_FLOOR_SUSTAIN_PUSH, DEFAULT_WARM_FLOOR_SUSTAIN_PUSH
                ),
            )
        ] = vol.All(
            vol.Coerce(float),
            vol.Range(min=WARM_FLOOR_SUSTAIN_PUSH_MIN, max=WARM_FLOOR_SUSTAIN_PUSH_CAP),
        )

    return fields


def normalize_underfloor_advanced_submission(data: dict[str, Any]) -> dict[str, Any]:
    """Normalize one heater's advanced-step submission for this feature."""
    out: dict[str, Any] = {}
    raw_type = data.get(CONF_HEATING_TYPE, DEFAULT_HEATING_TYPE_WHEN_SHOWN.value)
    try:
        out[CONF_HEATING_TYPE] = HeatingType(raw_type).value
    except ValueError:
        out[CONF_HEATING_TYPE] = DEFAULT_HEATING_TYPE_WHEN_SHOWN.value

    raw_level = data.get(CONF_WARM_FLOOR_LEVEL, DEFAULT_WARM_FLOOR_LEVEL)
    level = (
        raw_level
        if raw_level in WARM_FLOOR_LEVEL_PRESETS.keys() | {WARM_FLOOR_LEVEL_CUSTOM}
        else DEFAULT_WARM_FLOOR_LEVEL
    )
    out[CONF_WARM_FLOOR_LEVEL] = level

    if level in WARM_FLOOR_LEVEL_PRESETS:
        # A preset level drives both raw fields directly - whatever was
        # submitted for them (typically nothing, since the form hides them
        # once a preset is selected) is overwritten here, not merged.
        backoff, push = WARM_FLOOR_LEVEL_PRESETS[level]
    else:
        raw_backoff = data.get(
            CONF_WARM_FLOOR_MAX_BACKOFF, WARM_FLOOR_MAX_BACKOFF_DEFAULT
        )
        try:
            backoff = float(raw_backoff)
        except TypeError, ValueError:
            backoff = WARM_FLOOR_MAX_BACKOFF_DEFAULT

        raw_push = data.get(
            CONF_WARM_FLOOR_SUSTAIN_PUSH, DEFAULT_WARM_FLOOR_SUSTAIN_PUSH
        )
        try:
            push = float(raw_push)
        except TypeError, ValueError:
            push = DEFAULT_WARM_FLOOR_SUSTAIN_PUSH

    out[CONF_WARM_FLOOR_MAX_BACKOFF] = max(
        WARM_FLOOR_MIN_BACKOFF, min(backoff, WARM_FLOOR_MAX_BACKOFF_CAP)
    )
    out[CONF_WARM_FLOOR_SUSTAIN_PUSH] = max(
        WARM_FLOOR_SUSTAIN_PUSH_MIN, min(push, WARM_FLOOR_SUSTAIN_PUSH_CAP)
    )
    return out


# ---------------------------------------------------------------------------
# Warm Floor computation
# ---------------------------------------------------------------------------


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(value, hi))


def _get_mpc_state(bt, entity_id: str):
    """Best-effort lookup of the per-TRV MPC state, or None.

    Mirrors the same key-selection logic ``calibration.py`` uses (a shared
    group key for multi-TRV setups, an entity-specific key otherwise), reusing
    the same public key builders rather than re-deriving anything.
    """
    state_mgr = getattr(bt, "state_mgr", None)
    if state_mgr is None:
        return None
    try:
        from custom_components.better_thermostat.utils.calibration.mpc import (
            build_mpc_group_key,
            build_mpc_key,
        )
    except ImportError:
        return None

    try:
        is_multi_trv = len(bt.real_trvs) > 1
        mpc_key = (
            build_mpc_group_key(bt) if is_multi_trv else build_mpc_key(bt, entity_id)
        )
        return state_mgr.get_mpc(mpc_key)
    except AttributeError, TypeError, ValueError:
        return None


def _structural_scale(bt, entity_id: str, calibration_mode: CalibrationMode) -> float:
    """How much of the configured backoff a room's structure can safely afford.

    A leaky/drafty room or a weak UFH loop must *not* be allowed to drift far
    from target - it loses ground fast and recovers slowly, so a deep
    backoff there reproduces the "cuts too early, room cools off, long
    recovery" failure. A well-insulated room with a strong emitter can
    tolerate the full configured backoff safely.
    """
    heat_loss_source = None
    heating_power_source = None
    ka_est = None

    if calibration_mode in (
        CalibrationMode.MPC_CALIBRATION,
        CalibrationMode.MPC_V2_CALIBRATION,
    ):
        mpc_state = _get_mpc_state(bt, entity_id)
        if mpc_state is not None:
            heat_loss_source = getattr(mpc_state, "loss_est", None)
            heating_power_source = getattr(mpc_state, "gain_est", None)
            ka_est = getattr(mpc_state, "ka_est", None)

    if heat_loss_source is None:
        heat_loss_source = getattr(bt, "heat_loss_rate", None)
    if heating_power_source is None:
        heating_power_source = getattr(bt, "heating_power", None)

    # MPC Insulation (Ka) is delta-normalized (loss per degree of indoor/
    # outdoor difference) - unlike loss_est/heat_loss_rate, which reflect
    # today's specific weather and can read artificially low on a mild day
    # even for a genuinely leaky room. Cross-check: never let the structural
    # estimate read *tighter-envelope* (lower loss) than what Ka itself
    # implies at the live delta - only the other way. This mirrors
    # _structural_scale's own convention that more apparent loss means less
    # backoff allowance, so the cross-check can only tighten the floor, never
    # loosen it. A no-op whenever Ka or an outdoor reading isn't available.
    if isinstance(ka_est, (int, float)):
        outdoor_temp = getattr(bt, "last_avg_outdoor_temp", None)
        indoor_temp = getattr(bt, "cur_temp", None)
        if isinstance(outdoor_temp, (int, float)) and isinstance(
            indoor_temp, (int, float)
        ):
            # Same 5°C floor calculate_calibration's own Ka math uses, so the
            # two stay consistent at a small or inverted delta.
            delta = max(5.0, float(indoor_temp) - float(outdoor_temp))
            ka_implied_loss = float(ka_est) * delta
            if isinstance(heat_loss_source, (int, float)):
                heat_loss_source = max(float(heat_loss_source), ka_implied_loss)
            else:
                heat_loss_source = ka_implied_loss

    loss_severity = 0.0
    if isinstance(heat_loss_source, (int, float)) and MAX_HEAT_LOSS > 0:
        loss_severity = _clamp(float(heat_loss_source) / MAX_HEAT_LOSS, 0.0, 1.0)

    power_adequacy = 1.0
    if isinstance(heating_power_source, (int, float)) and MAX_HEATING_POWER > 0:
        power_adequacy = _clamp(
            float(heating_power_source) / MAX_HEATING_POWER, 0.0, 1.0
        )

    return _clamp(
        (1.0 - loss_severity) * power_adequacy, WARM_FLOOR_MIN_BACKOFF_RATIO, 1.0
    )


def estimate_solar_gain_rate(
    bt, entity_id: str, calibration_mode: CalibrationMode
) -> float | None:
    """Best-effort current solar gain rate (°C/min) for a heater's room, or None.

    Only meaningful for MPC-calibrated heaters that have actually learned a
    solar gain factor; ``None`` otherwise (before enough data exists, or for
    a non-MPC heater). Public - reused by ``boost_heater.py`` so Boost's
    termination can account for the same solar signal Warm Floor does,
    without either module owning a private copy of the lookup.
    """
    if calibration_mode not in (
        CalibrationMode.MPC_CALIBRATION,
        CalibrationMode.MPC_V2_CALIBRATION,
    ):
        return None

    mpc_state = _get_mpc_state(bt, entity_id)
    if mpc_state is None:
        return None
    solar_gain_est = getattr(mpc_state, "solar_gain_est", None)
    if not isinstance(solar_gain_est, (int, float)):
        return None

    try:
        from custom_components.better_thermostat.calibration import (
            _get_current_solar_intensity,
        )

        solar_intensity = _get_current_solar_intensity(bt)
    except ImportError, AttributeError:
        return None
    if not isinstance(solar_intensity, (int, float)):
        return None

    return float(solar_gain_est) * float(solar_intensity)


def _solar_scale(bt, entity_id: str, calibration_mode: CalibrationMode) -> float:
    """How much of the current apparent comfort is transient solar gain.

    Only meaningful for MPC-calibrated heaters that have actually learned a
    solar gain factor; everything else is a strict no-op (scale == 1.0).
    """
    solar_gain_rate = estimate_solar_gain_rate(bt, entity_id, calibration_mode)
    if solar_gain_rate is None:
        return 1.0

    mpc_state = _get_mpc_state(bt, entity_id)
    heat_loss_source = getattr(mpc_state, "loss_est", None) if mpc_state else None
    if not isinstance(heat_loss_source, (int, float)):
        heat_loss_source = getattr(bt, "heat_loss_rate", None)
    if not isinstance(heat_loss_source, (int, float)) or heat_loss_source <= 0:
        return 1.0

    solar_offset_ratio = _clamp(solar_gain_rate / float(heat_loss_source), 0.0, 1.0)
    return 1.0 - solar_offset_ratio


def _sustaining_setpoint(
    bt, entity_id: str, structural_scale: float, solar_scale: float
):
    """Return the floor-raised target temperature, or None if it can't be computed."""
    trv_state = bt.real_trvs.get(entity_id)
    if trv_state is None:
        return None

    max_backoff = trv_state.advanced.get(
        CONF_WARM_FLOOR_MAX_BACKOFF, WARM_FLOOR_MAX_BACKOFF_DEFAULT
    )
    try:
        max_backoff = float(max_backoff)
    except TypeError, ValueError:
        max_backoff = WARM_FLOOR_MAX_BACKOFF_DEFAULT

    target_temp = bt.bt_target_temp
    if not isinstance(target_temp, (int, float)):
        return None

    effective_backoff = max_backoff * structural_scale * solar_scale
    sustaining = float(target_temp) - effective_backoff

    min_temp = convert_to_float(
        trv_state.min_temp, getattr(bt, "device_name", None), "warm_floor"
    )
    if isinstance(min_temp, (int, float)):
        sustaining = max(sustaining, float(min_temp))
    sustaining = min(sustaining, float(target_temp))
    return sustaining


def _record_status(
    bt,
    entity_id: str,
    *,
    active: bool,
    sustaining_setpoint: float | None,
    sustain_push: float | None = None,
) -> None:
    """Publish the last Warm Floor decision for ``entity_id`` onto ``bt``.

    Read by ``sensor.py``'s Warm Floor status diagnostic. Only called for
    entities already confirmed ``HeatingType.UNDERFLOOR`` - a radiator TRV
    controlled by the same instance never touches this. ``sustain_push`` is a
    separate, always-non-negative field (not folded into ``backoff_c``,
    which stays negative-free by convention) - see apply_warm_floor_floor().
    """
    target = getattr(bt, "bt_target_temp", None)
    backoff_c = None
    if isinstance(target, (int, float)) and isinstance(
        sustaining_setpoint, (int, float)
    ):
        backoff_c = round(float(target) - float(sustaining_setpoint), 3)
    bt._warm_floor_status = {
        "active": bool(active),
        "entity_id": entity_id,
        "sustaining_setpoint_c": sustaining_setpoint,
        "backoff_c": backoff_c,
        "sustain_push_c": sustain_push,
    }


def _external_sensor_is_stale(
    bt, threshold_s: float = WARM_FLOOR_SENSOR_STALE_AFTER_S
) -> bool:
    """Return whether the external sensor's last real update is too old.

    True if it's older than ``threshold_s`` (fixed ~20 min), or can't be
    determined at all.

    Some real-world temperature sensors only report hourly, or on a ~0.5°C
    change, which can leave ``temp_slope`` reading as artificially flat for
    long stretches even while the room is genuinely still moving. Fail safe:
    an unknown or stale reading is treated the same as "still gaining", not
    as "no protection needed" - the opposite of the existing
    temp_slope-is-None fallback below, which is the wrong direction here.
    """
    hass = getattr(bt, "hass", None)
    sensor_entity_id = getattr(bt, "sensor_entity_id", None)
    if hass is None or not sensor_entity_id:
        return True
    state = hass.states.get(sensor_entity_id)
    if state is None or state.last_updated is None:
        return True
    age_s = (dt_util.utcnow() - state.last_updated).total_seconds()
    return age_s > threshold_s


def _has_active_valve_balance(trv_state) -> bool:
    """Return whether this cycle already computed a direct-valve balance.

    Sustain push is a setpoint-side mechanism for non-valve-capable devices;
    a heater under active valve control this cycle already has a
    graduated, working floor via ``_apply_valve_floor()``.
    """
    balance = getattr(trv_state, "calibration_balance", None)
    return isinstance(balance, dict) and bool(balance.get("apply_valve"))


def _sustain_push_amount(bt, entity_id: str, structural_scale: float) -> float | None:
    """Return the scaled sustain-push amount (°C), or None if disabled."""
    trv_state = bt.real_trvs.get(entity_id)
    if trv_state is None:
        return None
    raw_push = trv_state.advanced.get(
        CONF_WARM_FLOOR_SUSTAIN_PUSH, DEFAULT_WARM_FLOOR_SUSTAIN_PUSH
    )
    try:
        configured_push = float(raw_push)
    except TypeError, ValueError:
        configured_push = DEFAULT_WARM_FLOOR_SUSTAIN_PUSH
    if configured_push <= 0.0:
        return None
    return configured_push * structural_scale


def apply_warm_floor_floor(
    bt, entity_id: str, computed_value: float | None, *, is_offset: bool
) -> float | None:
    """Raise ``computed_value`` toward a sustaining Warm Floor level, if applicable.

    ``computed_value`` is the setpoint (``is_offset=False``, from
    ``calculate_calibration_setpoint()``) or local-calibration offset
    (``is_offset=True``, from ``calculate_calibration_local()``) that would
    otherwise be sent. Returns it unchanged whenever any guard fails, so this
    is always safe to call unconditionally at the tail of both functions.

    As a side effect, when the heater's last computed ``calibration_balance``
    carries a ``valve_percent`` (direct-valve-based control), that value is
    floored the same way - covering every ``_compute_*_balance`` code path
    from a single call site instead of editing each one individually.
    """
    if computed_value is None:
        return computed_value

    trv_state = bt.real_trvs.get(entity_id) if hasattr(bt, "real_trvs") else None
    if trv_state is None:
        return computed_value

    heating_type = trv_state.advanced.get(CONF_HEATING_TYPE, DEFAULT_HEATING_TYPE.value)
    try:
        heating_type = HeatingType(heating_type)
    except ValueError:
        heating_type = DEFAULT_HEATING_TYPE
    if heating_type != HeatingType.UNDERFLOOR:
        return computed_value

    # Never override the window/door OFF gate or the outdoor call-for-heat gate.
    if getattr(bt, "contact_open", False):
        _record_status(bt, entity_id, active=False, sustaining_setpoint=None)
        return computed_value
    if getattr(bt, "call_for_heat", True) is False:
        _record_status(bt, entity_id, active=False, sustaining_setpoint=None)
        return computed_value
    if getattr(bt, "bt_hvac_mode", None) == HVACMode.OFF:
        _record_status(bt, entity_id, active=False, sustaining_setpoint=None)
        return computed_value

    calibration_mode = normalize_calibration_mode(
        trv_state.advanced.get("calibration_mode")
    )
    if calibration_mode is None:
        calibration_mode = CalibrationMode.HEATING_POWER_CALIBRATION

    structural_scale = _structural_scale(bt, entity_id, calibration_mode)
    solar_scale = _solar_scale(bt, entity_id, calibration_mode)

    # Real-time slope guard: what the room is doing *right now* overrides the
    # structural/solar estimate above. A stale external sensor can't prove
    # the room has actually stopped rising - it gets the same treatment as
    # "still gaining", not the more permissive "no slope data" fallback.
    temp_slope = getattr(bt, "temp_slope", None)
    if _external_sensor_is_stale(bt) or (
        isinstance(temp_slope, (int, float)) and temp_slope > OVERSHOOT_SLOPE_GUARD
    ):
        # Still actively gaining (residual heat or live solar), or we simply
        # can't verify - raising the floor now would only risk overshoot.
        _record_status(bt, entity_id, active=False, sustaining_setpoint=None)
        return computed_value
    if isinstance(temp_slope, (int, float)) and temp_slope < -UNDERSHOOT_SLOPE_GUARD:
        structural_scale = WARM_FLOOR_MIN_BACKOFF_RATIO

    sustaining_setpoint = _sustaining_setpoint(
        bt, entity_id, structural_scale, solar_scale
    )
    if sustaining_setpoint is None:
        _record_status(bt, entity_id, active=False, sustaining_setpoint=None)
        return computed_value

    sustain_push_c = None
    if not is_offset:
        # Setpoint semantics: higher = more heat. Warm Floor only ever raises.
        result = max(computed_value, sustaining_setpoint)

        # Sustain push: on the four calibration modes that pin the setpoint
        # at exactly target once idle, the passive floor above is
        # structurally a no-op (nothing to raise past target). Opt-in and
        # off by default (see build_underfloor_advanced_fields()); pushes
        # only the downstream setpoint sent to the physical heater, never
        # bt.bt_target_temp itself.
        target_temp = float(bt.bt_target_temp)
        cur_temp = getattr(bt, "cur_temp", None)
        if (
            computed_value >= target_temp
            and bt.hvac_action == HVACAction.IDLE
            and isinstance(cur_temp, (int, float))
            and cur_temp >= target_temp
            and not _has_active_valve_balance(trv_state)
        ):
            push_amount = _sustain_push_amount(bt, entity_id, structural_scale)
            if push_amount is not None:
                push_ceiling = target_temp + push_amount
                max_temp = convert_to_float(
                    trv_state.max_temp, getattr(bt, "device_name", None), "warm_floor"
                )
                if isinstance(max_temp, (int, float)):
                    push_ceiling = min(push_ceiling, float(max_temp))
                if push_ceiling > result:
                    sustain_push_c = round(push_ceiling - result, 3)
                    result = push_ceiling
    else:
        # Local-calibration-offset semantics are inverted: a *higher* offset
        # makes the TRV read warmer and closes the valve, so Warm Floor must
        # *cap* the offset rather than floor it. Convert the sustaining
        # setpoint into the equivalent offset ceiling using the same
        # external-vs-internal relationship calculate_calibration_local()
        # itself uses, then never let the offset exceed it.
        trv_internal_temp = convert_to_float(
            trv_state.current_temperature,
            getattr(bt, "device_name", None),
            "warm_floor",
        )
        if not isinstance(trv_internal_temp, (int, float)):
            _record_status(bt, entity_id, active=False, sustaining_setpoint=None)
            return computed_value
        offset_ceiling = sustaining_setpoint - float(trv_internal_temp)
        result = min(computed_value, offset_ceiling)

    _apply_valve_floor(bt, entity_id, structural_scale, solar_scale)
    _record_status(
        bt,
        entity_id,
        active=(result != computed_value),
        sustaining_setpoint=sustaining_setpoint,
        sustain_push=sustain_push_c,
    )
    return result


def _apply_valve_floor(
    bt, entity_id: str, structural_scale: float, solar_scale: float
) -> None:
    """Floor a previously-computed direct-valve ``calibration_balance``, if any."""
    trv_state = bt.real_trvs.get(entity_id)
    if trv_state is None or not isinstance(trv_state.calibration_balance, dict):
        return
    balance = trv_state.calibration_balance
    if not balance.get("apply_valve"):
        return
    valve_percent = balance.get("valve_percent")
    if not isinstance(valve_percent, (int, float)):
        return

    max_backoff = trv_state.advanced.get(
        CONF_WARM_FLOOR_MAX_BACKOFF, WARM_FLOOR_MAX_BACKOFF_DEFAULT
    )
    try:
        max_backoff = float(max_backoff)
    except TypeError, ValueError:
        max_backoff = WARM_FLOOR_MAX_BACKOFF_DEFAULT

    # `structural_scale * solar_scale` is exactly the fraction of the
    # configured backoff that's currently being *allowed* (same quantity the
    # setpoint path multiplies into effective_backoff) - a well-insulated,
    # strong-emitter, sun-free room lets that fraction approach 1.0 (loose
    # floor, valve can safely idle to 0%), while a leaky room, a weak
    # emitter, or a solar-masked reading pulls it toward
    # WARM_FLOOR_MIN_BACKOFF_RATIO (tight floor). The sustaining percentage
    # must move the *opposite* way: the less backoff allowed, the more the
    # valve is forced open, not the other way around.
    backoff_allowed_fraction = _clamp(structural_scale * solar_scale, 0.0, 1.0)
    tightness = 1.0 - backoff_allowed_fraction
    peak_sustaining_pct = 20.0 * _clamp(
        max_backoff / WARM_FLOOR_MAX_BACKOFF_CAP, 0.0, 1.0
    )
    sustaining_pct = peak_sustaining_pct * tightness
    new_pct = max(float(valve_percent), sustaining_pct)
    balance["valve_percent"] = int(round(_clamp(new_pct, 0.0, 100.0)))
