"""Warm Floor mode: a sustaining-heat floor for underfloor heating (UFH) heaters.

Underfloor heating has much higher thermal inertia than a radiator TRV: once
the room is satisfied and Better Thermostat backs a heater fully off, the
slab keeps cooling for a long time, and recovering from a fully-cold floor
is slow and energy-costly. Warm Floor keeps a low, continuous background
heat level in UFH-flagged heaters instead of letting them fully idle. For
Generic Thermostat heaters, the ON phase must command the downstream setpoint
above ``bt.bt_target_temp``. This is required because the Generic Thermostat
turns its relay on by comparing its own setpoint with the room temperature.
The room target itself is never changed.

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
import math
from time import monotonic
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
    convert_to_float_celsius,
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
WARM_FLOOR_MIN_RISING_DUTY_RATIO: Final = 0.15
UNDERSHOOT_SLOPE_GUARD: Final = 0.02
# A stale external sensor can't be trusted to prove the room has actually
# stopped rising - treat it the same as "still gaining" rather than "no
# protection needed". Fixed, no config field (see _external_sensor_is_stale).
WARM_FLOOR_SENSOR_STALE_AFTER_S: Final = 1200.0
# Typical electrothermal UFH actuators need several minutes to travel fully
# in either direction.  A shorter ON/OFF window is not merely inefficient:
# the actuator may not move far enough to produce any useful heat.
WARM_FLOOR_ACTUATOR_TRAVEL_S: Final = 4 * 60
# These are physical bounds for the dynamic controller, not level schedules.
# The selected level supplies only a baseline intensity. The actual ON and
# pause durations are recalculated from live telemetry on every control tick.
WARM_FLOOR_DUTY_MIN_PAUSE_S: Final = WARM_FLOOR_ACTUATOR_TRAVEL_S
WARM_FLOOR_DUTY_MIN_ON_S: Final = WARM_FLOOR_ACTUATOR_TRAVEL_S
WARM_FLOOR_DUTY_MAX_ON_S: Final = 12 * 60
WARM_FLOOR_DUTY_MIN_INTENSITY: Final = 0.15
WARM_FLOOR_DUTY_PAUSE_RESPONSE_S: Final = 15 * 60
# Broad, source-neutral normalisation bounds for an optional water/buffer
# temperature. They are deliberately only a bounded capacity hint; room
# response remains the authoritative feedback signal.
WARM_FLOOR_WATER_LOW_C: Final = 20.0
WARM_FLOOR_WATER_HIGH_C: Final = 50.0
WARM_FLOOR_WATER_WEIGHT: Final = 0.60
# Outdoor temperature is a feed-forward heat-loss hint, not an HVAC cutoff.
WARM_FLOOR_OUTDOOR_WARM_C: Final = 20.0
WARM_FLOOR_OUTDOOR_COLD_C: Final = -5.0
WARM_FLOOR_OUTDOOR_WEIGHT: Final = 0.50

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
WARM_FLOOR_LEVEL_AUTO: Final = "auto"
DEFAULT_WARM_FLOOR_LEVEL: Final = WARM_FLOOR_LEVEL_BALANCED

WARM_FLOOR_LEVEL_PRESETS: Final[dict[str, tuple[float, float]]] = {
    WARM_FLOOR_LEVEL_ECO: (3.0, 0.0),
    WARM_FLOOR_LEVEL_BALANCED: (1.5, 0.0),
    WARM_FLOOR_LEVEL_KEEP_HOT: (0.5, 0.3),
    WARM_FLOOR_LEVEL_AUTO: (1.5, 0.0),
}

# Warm Floor levels are baselines, not fixed ON/OFF schedules. A higher level
# remains more assertive under the same telemetry, while cooling/load signals
# can make all levels converge toward a longer maintenance pulse. Positive
# slope, warmer short EMA, and a larger margin above target reduce intensity.
WARM_FLOOR_LEVEL_INTENSITY: Final[dict[str, float]] = {
    WARM_FLOOR_LEVEL_ECO: 0.20,
    WARM_FLOOR_LEVEL_BALANCED: 0.45,
    WARM_FLOOR_LEVEL_KEEP_HOT: 0.70,
    WARM_FLOOR_LEVEL_CUSTOM: 0.45,
}

# Auto is deliberately conservative when it first takes ownership near the
# room target.  A slow slab is cheaper to keep warm than to recover from cold,
# but the policy must step down only after the room proves that it is warming.
WARM_FLOOR_AUTO_NEAR_TARGET_MARGIN_C: Final = 0.25
WARM_FLOOR_AUTO_EMA_WARMING_DELTA_C: Final = 0.10
WARM_FLOOR_AUTO_EMA_STRONG_WARMING_DELTA_C: Final = 0.25
WARM_FLOOR_AUTO_EMA_COOLING_DELTA_C: Final = -0.10
WARM_FLOOR_AUTO_WARMING_SLOPE_K_MIN: Final = 0.005
WARM_FLOOR_AUTO_COOLING_SLOPE_K_MIN: Final = -0.005
WARM_FLOOR_AUTO_POLICY_CONFIRM_S: Final = 10 * 60
WARM_FLOOR_AUTO_STRONGER_CONFIRM_S: Final = 5 * 60
WARM_FLOOR_AUTO_MIN_DWELL_S: Final = 15 * 60


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
                WARM_FLOOR_LEVEL_AUTO,
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
    control_method: str | None = None,
    reason: str | None = None,
    valve_min_pct: float | None = None,
    duty_period_s: int | None = None,
    duty_on_s: int | None = None,
    demand_factor: float | None = None,
) -> None:
    """Publish the last Warm Floor decision for ``entity_id`` onto ``bt``.

    Read by ``sensor.py``'s Warm Floor status diagnostic. Only called for
    entities already confirmed ``HeatingType.UNDERFLOOR`` - a radiator TRV
    controlled by the same instance never touches this. ``sustain_push`` is a
    separate, always-non-negative field (not folded into ``backoff_c``,
    which stays negative-free by convention) - see apply_warm_floor_floor().
    """
    target = getattr(bt, "bt_target_temp", None)
    trv_state = getattr(bt, "real_trvs", {}).get(entity_id)
    warm_floor_level = None
    status_duty_period_s = None
    status_duty_on_s = None
    if trv_state is not None:
        warm_floor_level = trv_state.advanced.get(
            CONF_WARM_FLOOR_LEVEL, DEFAULT_WARM_FLOOR_LEVEL
        )
    backoff_c = None
    if isinstance(target, (int, float)) and isinstance(
        sustaining_setpoint, (int, float)
    ):
        backoff_c = round(float(target) - float(sustaining_setpoint), 3)
    effective_period_s = duty_period_s or status_duty_period_s
    effective_on_s = duty_on_s or status_duty_on_s
    duty_phase = None
    duty_elapsed_s = None
    duty_remaining_s = None
    if effective_period_s and effective_on_s:
        if reason == "duty_cycle_on":
            duty_phase = "on"
        elif reason in {
            "outside_duty_window",
            "idle_release",
            "temperature_rising",
            "rising",
        }:
            duty_phase = "off"
        # The phase/reason pair must describe one control decision. The
        # status entity shows status_age_s when the next periodic evaluation
        # has not happened yet; it must not invent a newer wall-clock phase.
    phase_started_at = getattr(trv_state, "warm_floor_phase_started_at", None)
    phase_minimum_until = getattr(trv_state, "warm_floor_phase_minimum_until", None)
    now = dt_util.utcnow().timestamp()
    if isinstance(phase_started_at, (int, float)):
        duty_elapsed_s = max(0, int(now - float(phase_started_at)))
    if isinstance(phase_minimum_until, (int, float)):
        duty_remaining_s = max(0, int(float(phase_minimum_until) - now))
    hvac_action = getattr(bt, "hvac_action", None)
    if isinstance(hvac_action, HVACAction):
        hvac_action = hvac_action.value
    status = {
        "active": bool(active),
        "entity_id": entity_id,
        "sustaining_setpoint_c": sustaining_setpoint,
        "backoff_c": backoff_c,
        "sustain_push_c": sustain_push,
        "control_method": control_method,
        "reason": reason,
        "warm_floor_level": warm_floor_level,
        "duty_period_s": effective_period_s,
        "duty_on_s": effective_on_s,
        "duty_phase": duty_phase,
        "duty_elapsed_s": duty_elapsed_s,
        "duty_remaining_s": duty_remaining_s,
        "actuator_phase": getattr(trv_state, "warm_floor_actuator_phase", None),
        "actuator_phase_started_at": getattr(
            trv_state, "warm_floor_phase_started_at", None
        ),
        "actuator_minimum_until": getattr(
            trv_state, "warm_floor_phase_minimum_until", None
        ),
        "actuator_guard": getattr(trv_state, "warm_floor_actuator_guard", None),
        "phase_recovered": bool(
            getattr(trv_state, "warm_floor_phase_recovered", False)
        ),
        "current_temp_c": getattr(bt, "cur_temp", None),
        "target_temp_c": target,
        "hvac_action": hvac_action,
        "valve_min_pct": valve_min_pct,
        "demand_factor": demand_factor,
        "preheat_risk": getattr(bt, "_warm_floor_preheat_risk", None),
        "outdoor_temp_c": getattr(bt, "_warm_floor_outdoor_temp_c", None),
        "outdoor_temp_source": getattr(bt, "_warm_floor_outdoor_source", None),
        "flow_temp_c": getattr(bt, "_warm_floor_flow_temp_c", None),
        "flow_temp_source": getattr(bt, "_warm_floor_flow_source", None),
        "auto_policy": getattr(bt, "_warm_floor_auto_policy", None),
        "updated_at": dt_util.utcnow().isoformat(),
    }
    # Keep one status per controlled heater.  A Better Thermostat may control
    # several Generic Thermostats; storing only the last call made the
    # diagnostic sensor look as if the other heaters had not been evaluated.
    statuses = getattr(bt, "_warm_floor_statuses", None)
    if not isinstance(statuses, dict):
        statuses = {}
        bt._warm_floor_statuses = statuses
    statuses[entity_id] = status

    # Backwards-compatible view for existing integrations/tests and callers
    # that expect the historical single-status dictionary.
    bt._warm_floor_status = status


def record_warm_floor_blocked_status(bt, entity_id: str, reason: str) -> None:
    """Refresh a Warm Floor diagnostic when control cannot reach the heater.

    A periodic control cycle can legitimately skip an unavailable heater.  Do
    not leave the status sensor showing the last ON/OFF decision in that case:
    publish the blocked reason and a fresh timestamp instead.  This is only a
    diagnostic update; it never sends a device command or changes user intent.
    """
    trv_state = getattr(bt, "real_trvs", {}).get(entity_id)
    if trv_state is None:
        return
    raw_heating_type = (trv_state.advanced or {}).get(
        CONF_HEATING_TYPE, DEFAULT_HEATING_TYPE.value
    )
    heating_type = getattr(raw_heating_type, "value", raw_heating_type)
    try:
        heating_type = HeatingType(heating_type)
    except ValueError:
        heating_type = DEFAULT_HEATING_TYPE
    if heating_type != HeatingType.UNDERFLOOR:
        return
    _record_status(
        bt,
        entity_id,
        active=False,
        sustaining_setpoint=None,
        control_method=None,
        reason=reason,
    )


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


def _is_generic_thermostat(trv_state) -> bool:
    """Return whether the heater is an ON/OFF Generic Thermostat helper."""
    integration = str(getattr(trv_state, "integration", "") or "").lower()
    return integration in {"generic", "generic_thermostat"}


def _warm_floor_state_temperature(
    bt, entity_id: str | None, *, weather: bool = False
) -> float | None:
    """Read one configured temperature source without changing BT state."""
    hass = getattr(bt, "hass", None)
    if hass is None or not isinstance(entity_id, str) or not entity_id:
        return None
    try:
        state = hass.states.get(entity_id)
    except Exception:
        return None
    if state is None or state.state in {"unknown", "unavailable", None}:
        return None
    value = state.state
    unit = state.attributes.get("unit_of_measurement") if state.attributes else None
    if weather and state.attributes:
        value = state.attributes.get("temperature")
        unit = state.attributes.get("temperature_unit", unit)
    return convert_to_float_celsius(
        value,
        getattr(bt, "device_name", "Better Thermostat"),
        "warm_floor.temperature_source",
        unit_of_measurement=unit,
    )


def _warm_floor_outdoor_temperature(bt) -> tuple[float | None, str | None]:
    """Resolve the current outdoor temperature from existing BT configuration."""
    sensor = getattr(bt, "outdoor_sensor", None)
    value = _warm_floor_state_temperature(bt, sensor)
    if value is not None:
        return value, "outdoor_sensor"
    weather = getattr(bt, "weather_entity", None)
    value = _warm_floor_state_temperature(bt, weather, weather=True)
    if value is not None:
        return value, "weather_entity"
    return None, None


def _warm_floor_flow_temperature(bt) -> tuple[float | None, str | None]:
    """Resolve configured live water temperature, then its static fallback."""
    sensor = getattr(bt, "flow_temp_sensor", None)
    value = _warm_floor_state_temperature(bt, sensor)
    if value is not None:
        return value, "flow_temp_sensor"
    static = getattr(bt, "flow_temp_static_c", None)
    if isinstance(static, (int, float)) and math.isfinite(float(static)):
        return float(static), "flow_temp_static_c"
    return None, None


def _warm_floor_auto_candidate(bt, demand_factor: float) -> str:
    """Select an Auto policy candidate from room response and demand.

    EMA separation and slope are the primary direction signals. Demand,
    outdoor temperature, and flow temperature remain confirmation/forward
    hints through ``demand_factor``; none of them bypasses the strict
    above-target guard in the caller.
    """
    current = getattr(bt, "cur_temp", None)
    target = getattr(bt, "bt_target_temp", None)
    margin = (
        float(current) - float(target)
        if isinstance(current, (int, float)) and isinstance(target, (int, float))
        else None
    )
    short_ema = getattr(bt, "cur_temp_filtered", None)
    if not isinstance(short_ema, (int, float)):
        short_ema = getattr(bt, "external_temp_ema", None)
    long_ema = getattr(bt, "external_temp_ema_1h", None)
    ema_delta = (
        float(short_ema) - float(long_ema)
        if isinstance(short_ema, (int, float))
        and isinstance(long_ema, (int, float))
        else None
    )
    slope = getattr(bt, "temp_slope", None)

    cooling = (
        isinstance(ema_delta, (int, float))
        and ema_delta <= WARM_FLOOR_AUTO_EMA_COOLING_DELTA_C
    ) or (
        isinstance(slope, (int, float))
        and slope <= WARM_FLOOR_AUTO_COOLING_SLOPE_K_MIN
    ) or demand_factor >= 0.65
    warming = (
        isinstance(ema_delta, (int, float))
        and ema_delta >= WARM_FLOOR_AUTO_EMA_WARMING_DELTA_C
        and isinstance(slope, (int, float))
        and slope >= WARM_FLOOR_AUTO_WARMING_SLOPE_K_MIN
    )
    strong_warming = (
        warming
        and isinstance(ema_delta, (int, float))
        and ema_delta >= WARM_FLOOR_AUTO_EMA_STRONG_WARMING_DELTA_C
        and isinstance(slope, (int, float))
        and slope >= 0.01
    )

    if cooling:
        return WARM_FLOOR_LEVEL_KEEP_HOT
    if (
        isinstance(margin, (int, float))
        and 0.0 < margin <= WARM_FLOOR_AUTO_NEAR_TARGET_MARGIN_C
        and not warming
    ):
        return WARM_FLOOR_LEVEL_KEEP_HOT
    if strong_warming and demand_factor < 0.65:
        return WARM_FLOOR_LEVEL_ECO
    if warming and demand_factor < 0.65:
        return WARM_FLOOR_LEVEL_BALANCED
    if demand_factor >= 0.30:
        return WARM_FLOOR_LEVEL_BALANCED
    return WARM_FLOOR_LEVEL_ECO


def _warm_floor_auto_step(current: str, candidate: str) -> str:
    """Move at most one Auto policy level per confirmed transition."""
    order = [
        WARM_FLOOR_LEVEL_ECO,
        WARM_FLOOR_LEVEL_BALANCED,
        WARM_FLOOR_LEVEL_KEEP_HOT,
    ]
    try:
        current_index = order.index(current)
        candidate_index = order.index(candidate)
    except ValueError:
        return WARM_FLOOR_LEVEL_KEEP_HOT
    if candidate_index == current_index:
        return current
    return order[current_index + (1 if candidate_index > current_index else -1)]


def _warm_floor_auto_policy(bt, demand_factor: float) -> str:
    """Return a hysteretic, conservative Auto policy."""
    now = monotonic()
    current = getattr(bt, "_warm_floor_auto_policy", None)
    valid = {
        WARM_FLOOR_LEVEL_ECO,
        WARM_FLOOR_LEVEL_BALANCED,
        WARM_FLOOR_LEVEL_KEEP_HOT,
    }
    if current not in valid:
        # Bootstrap conservatively. The first eligible above-target window
        # gets a strong baseline, then later ticks may step down after proof
        # of warming. This state is policy-only; the caller still enforces
        # current_temperature > target_temperature before sending anything.
        current = WARM_FLOOR_LEVEL_KEEP_HOT
        bt._warm_floor_auto_policy = current
        bt._warm_floor_auto_policy_since = now
        bt._warm_floor_auto_candidate = current
        bt._warm_floor_auto_candidate_since = now
        return current

    candidate = _warm_floor_auto_candidate(bt, demand_factor)
    if candidate == current:
        bt._warm_floor_auto_candidate = current
        bt._warm_floor_auto_candidate_since = now
        return current

    pending = getattr(bt, "_warm_floor_auto_candidate", None)
    if pending != candidate:
        bt._warm_floor_auto_candidate = candidate
        bt._warm_floor_auto_candidate_since = now
        return current

    candidate_since = getattr(bt, "_warm_floor_auto_candidate_since", now)
    policy_since = getattr(bt, "_warm_floor_auto_policy_since", now)
    current_index = [
        WARM_FLOOR_LEVEL_ECO,
        WARM_FLOOR_LEVEL_BALANCED,
        WARM_FLOOR_LEVEL_KEEP_HOT,
    ].index(current)
    candidate_index = [
        WARM_FLOOR_LEVEL_ECO,
        WARM_FLOOR_LEVEL_BALANCED,
        WARM_FLOOR_LEVEL_KEEP_HOT,
    ].index(candidate)
    confirmation_s = (
        WARM_FLOOR_AUTO_STRONGER_CONFIRM_S
        if candidate_index > current_index
        else WARM_FLOOR_AUTO_POLICY_CONFIRM_S
    )
    if (
        now - candidate_since >= confirmation_s
        and now - policy_since >= WARM_FLOOR_AUTO_MIN_DWELL_S
    ):
        current = _warm_floor_auto_step(current, candidate)
        bt._warm_floor_auto_policy = current
        bt._warm_floor_auto_policy_since = now
        bt._warm_floor_auto_candidate = current
        bt._warm_floor_auto_candidate_since = now
    return current


def _warm_floor_level_intensity(
    bt_or_trv, trv_state=None, demand_factor: float = 0.0
) -> float:
    """Return the selected level's baseline, or a smoothed Auto baseline."""
    # Keep the small public test/helper API backwards compatible: older
    # callers passed only the TRV state. Auto needs the BT instance, while
    # fixed levels do not.
    bt = bt_or_trv if trv_state is not None else None
    if trv_state is None:
        trv_state = bt_or_trv
    level = trv_state.advanced.get(CONF_WARM_FLOOR_LEVEL, DEFAULT_WARM_FLOOR_LEVEL)
    if str(level) == WARM_FLOOR_LEVEL_AUTO:
        if bt is None:
            return WARM_FLOOR_LEVEL_INTENSITY[DEFAULT_WARM_FLOOR_LEVEL]
        policy = _warm_floor_auto_policy(bt, demand_factor)
        desired = WARM_FLOOR_LEVEL_INTENSITY[policy]
        previous = getattr(bt, "_warm_floor_auto_intensity", None)
        if isinstance(previous, (int, float)):
            desired = float(previous) + 0.25 * (desired - float(previous))
        bt._warm_floor_auto_intensity = desired
        bt._warm_floor_auto_policy = policy
        return desired
    if bt is not None:
        bt._warm_floor_auto_policy = None
    return WARM_FLOOR_LEVEL_INTENSITY.get(
        str(level),
        WARM_FLOOR_LEVEL_INTENSITY[DEFAULT_WARM_FLOOR_LEVEL],
    )


def _warm_floor_demand_factor(
    bt,
    entity_id: str | None = None,
    calibration_mode: CalibrationMode | None = None,
) -> float:
    """Estimate current slab-heating demand from live controller telemetry.

    The selected Warm Floor level remains the baseline. This factor adapts
    maintenance intensity to cooling and to learned thermal load; ordinary
    room-demand error is deliberately excluded because below-target heating
    belongs to the normal Better Thermostat controller.
    """
    demand = 0.0

    outdoor_temp, outdoor_source = _warm_floor_outdoor_temperature(bt)
    flow_temp, flow_source = _warm_floor_flow_temperature(bt)
    bt._warm_floor_outdoor_temp_c = outdoor_temp
    bt._warm_floor_outdoor_source = outdoor_source
    bt._warm_floor_flow_temp_c = flow_temp
    bt._warm_floor_flow_source = flow_source

    # These are deliberately bounded feed-forward hints. They never start
    # Warm Floor by themselves and never override the strict above-target
    # guard in apply_warm_floor_floor(). The room's measured response below
    # remains the authoritative feedback signal.
    if isinstance(outdoor_temp, (int, float)):
        outdoor_coldness = _clamp(
            (WARM_FLOOR_OUTDOOR_WARM_C - float(outdoor_temp))
            / (WARM_FLOOR_OUTDOOR_WARM_C - WARM_FLOOR_OUTDOOR_COLD_C),
            0.0,
            1.0,
        )
        demand = max(demand, WARM_FLOOR_OUTDOOR_WEIGHT * outdoor_coldness)

    if isinstance(flow_temp, (int, float)):
        water_capacity = _clamp(
            (float(flow_temp) - WARM_FLOOR_WATER_LOW_C)
            / (WARM_FLOOR_WATER_HIGH_C - WARM_FLOOR_WATER_LOW_C),
            0.0,
            1.0,
        )
        demand = max(demand, WARM_FLOOR_WATER_WEIGHT * (1.0 - water_capacity))

    slope = getattr(bt, "temp_slope", None)
    if isinstance(slope, (int, float)) and slope < 0.0:
        demand = max(
            demand,
            _clamp(abs(float(slope)) / UNDERSHOOT_SLOPE_GUARD, 0.0, 1.0),
        )

    # The short EMA reacts to the current room movement; the one-hour EMA
    # provides the slower baseline. Their separation catches sustained
    # warming/cooling even when the instantaneous slope is flat or noisy.
    short_ema = getattr(bt, "cur_temp_filtered", None)
    if not isinstance(short_ema, (int, float)):
        short_ema = getattr(bt, "external_temp_ema", None)
    long_ema = getattr(bt, "external_temp_ema_1h", None)
    if isinstance(short_ema, (int, float)) and isinstance(long_ema, (int, float)):
        ema_delta = float(short_ema) - float(long_ema)
        if ema_delta < 0.0:
            demand = max(demand, _clamp(abs(ema_delta) / 0.5, 0.0, 1.0))
        elif ema_delta > 0.0:
            # A warmer short EMA indicates solar/residual warming. Reduce an
            # already-demanding maintenance cycle, but keep the baseline
            # selected by Eco/Balanced/Keep it Hot available.
            demand *= _clamp(0.5 / ema_delta, WARM_FLOOR_MIN_RISING_DUTY_RATIO, 1.0)

    heat_loss = getattr(bt, "heat_loss_rate", None)
    heating_power = getattr(bt, "heating_power", None)
    if isinstance(heat_loss, (int, float)) and isinstance(heating_power, (int, float)):
        if float(heat_loss) > 0.0 and float(heating_power) > 0.0:
            load_ratio = float(heat_loss) / float(heating_power)
            demand = max(demand, _clamp((load_ratio - 0.5) / 0.5, 0.0, 1.0))

    # MPC keeps a per-heater estimate that is more specific than the
    # instance-level fallback above. Use it for the Generic Thermostat pulse
    # when available, so the duty cycle follows the same learned plant data as
    # the structural Warm Floor floor. Missing or invalid estimates remain a
    # no-op for non-MPC and not-yet-trained installations.
    if (
        entity_id is not None
        and calibration_mode
        in (CalibrationMode.MPC_CALIBRATION, CalibrationMode.MPC_V2_CALIBRATION)
    ):
        mpc_state = _get_mpc_state(bt, entity_id)
        mpc_loss = getattr(mpc_state, "loss_est", None) if mpc_state else None
        mpc_power = getattr(mpc_state, "gain_est", None) if mpc_state else None
        if (
            isinstance(mpc_loss, (int, float))
            and isinstance(mpc_power, (int, float))
            and float(mpc_loss) > 0.0
            and float(mpc_power) > 0.0
        ):
            mpc_load_ratio = float(mpc_loss) / float(mpc_power)
            demand = max(
                demand, _clamp((mpc_load_ratio - 0.5) / 0.5, 0.0, 1.0)
            )
    # A predictive maintenance signal lets Warm Floor act before normal
    # heating has to start, but only while the room is still above target.
    # It is deliberately derived from relative trend/thermal evidence rather
    # than a fixed temperature threshold.  The caller still enforces the hard
    # current_temperature > target_temperature boundary.
    preheat_risk = _warm_floor_preheat_risk(bt)
    bt._warm_floor_preheat_risk = preheat_risk
    return _clamp(max(demand, preheat_risk), 0.0, 1.0)


def _warm_floor_preheat_risk(bt) -> float:
    """Estimate imminent normal-heating risk from relative diagnostics.

    This is not a second heating controller and does not use a fixed room
    temperature threshold.  It compares the remaining above-target margin with
    the measured/learned cooling trend.  EMA-1h is converted to a rate using
    its own one-hour meaning; heat-loss, delivered power, and MPC disturbance
    provide additional confirmation when available.
    """
    current = getattr(bt, "cur_temp", None)
    target = getattr(bt, "bt_target_temp", None)
    if not isinstance(current, (int, float)) or not isinstance(target, (int, float)):
        return 0.0
    margin = float(current) - float(target)
    if margin <= 0.0:
        return 0.0

    short_ema = getattr(bt, "cur_temp_filtered", None)
    if not isinstance(short_ema, (int, float)):
        short_ema = getattr(bt, "external_temp_ema", None)
    long_ema = getattr(bt, "external_temp_ema_1h", None)
    ema_delta = (
        float(short_ema) - float(long_ema)
        if isinstance(short_ema, (int, float))
        and isinstance(long_ema, (int, float))
        else None
    )
    slope = getattr(bt, "temp_slope", None)
    slope = float(slope) if isinstance(slope, (int, float)) else None

    # Build a cooling-rate estimate only from signals that point toward the
    # target. EMA-1h is explicitly a one-hour baseline, so its separation is
    # converted to the same per-minute unit as temp_slope.
    cooling_rates: list[float] = []
    if slope is not None and slope < 0.0:
        cooling_rates.append(abs(slope))
    if ema_delta is not None and ema_delta < 0.0:
        cooling_rates.append(abs(ema_delta) / 60.0)

    heat_loss = getattr(bt, "heat_loss_rate", None)
    heating_power = getattr(bt, "heating_power", None)
    if (
        isinstance(heat_loss, (int, float))
        and isinstance(heating_power, (int, float))
        and float(heat_loss) > float(heating_power)
    ):
        cooling_rates.append(float(heat_loss) - float(heating_power))

    mpc_disturbance = getattr(bt, "mpc_v2_D_hat_K_per_min", None)
    if isinstance(mpc_disturbance, (int, float)) and float(mpc_disturbance) > 0.0:
        cooling_rates.append(float(mpc_disturbance))

    if not cooling_rates:
        return 0.0

    cooling_rate = max(cooling_rates)
    # Compare the expected trend over the EMA horizon with the remaining
    # margin. This is a relative proximity score, not a Celsius threshold.
    trend_margin = cooling_rate * 60.0
    proximity = trend_margin / (trend_margin + margin)

    evidence = 0.0
    if slope is not None and slope < 0.0:
        evidence = max(evidence, _clamp(abs(slope) / cooling_rate, 0.0, 1.0))
    if ema_delta is not None and ema_delta < 0.0:
        evidence = max(
            evidence,
            _clamp((abs(ema_delta) / 60.0) / cooling_rate, 0.0, 1.0),
        )
    if (
        isinstance(heat_loss, (int, float))
        and isinstance(heating_power, (int, float))
        and float(heat_loss) > float(heating_power)
    ):
        evidence = max(
            evidence,
            _clamp(
                (float(heat_loss) - float(heating_power)) / cooling_rate,
                0.0,
                1.0,
            ),
        )
    if isinstance(mpc_disturbance, (int, float)) and float(mpc_disturbance) > 0.0:
        evidence = max(
            evidence, _clamp(float(mpc_disturbance) / cooling_rate, 0.0, 1.0)
        )
    return _clamp(proximity * evidence, 0.0, 1.0)


def _warm_floor_rising_factor(bt) -> float:
    """Reduce maintenance intensity while live telemetry shows warming."""
    factor = 1.0
    slope = getattr(bt, "temp_slope", None)
    if isinstance(slope, (int, float)) and slope > 0.0:
        factor *= _clamp(
            1.0 - float(slope) / 0.10 * 0.65,
            WARM_FLOOR_MIN_RISING_DUTY_RATIO,
            1.0,
        )

    short_ema = getattr(bt, "cur_temp_filtered", None)
    if not isinstance(short_ema, (int, float)):
        short_ema = getattr(bt, "external_temp_ema", None)
    long_ema = getattr(bt, "external_temp_ema_1h", None)
    if isinstance(short_ema, (int, float)) and isinstance(long_ema, (int, float)):
        ema_delta = float(short_ema) - float(long_ema)
        if ema_delta > 0.0:
            factor *= _clamp(1.0 - ema_delta / 0.5 * 0.45, 0.45, 1.0)

    current = getattr(bt, "cur_temp", None)
    target = getattr(bt, "bt_target_temp", None)
    if isinstance(current, (int, float)) and isinstance(target, (int, float)):
        excess = max(0.0, float(current) - float(target))
        factor *= _clamp(1.0 - excess / 2.0 * 0.35, 0.65, 1.0)
    return _clamp(factor, WARM_FLOOR_MIN_RISING_DUTY_RATIO, 1.0)


def _effective_duty_profile(
    bt, trv_state, demand_factor: float
) -> tuple[int, int, float]:
    """Calculate a dynamic ON/pause profile from level and live telemetry."""
    demand_factor = _clamp(demand_factor, 0.0, 1.0)
    level_intensity = _warm_floor_level_intensity(bt, trv_state, demand_factor)
    # Cooling/load demand raises the selected baseline toward full intensity.
    intensity = level_intensity + (1.0 - level_intensity) * demand_factor
    intensity *= _warm_floor_rising_factor(bt)
    # Even under strong solar/residual warming, keep a minimum maintenance
    # cadence so the slab is never abandoned for a long period. This is a
    # lower bound on dynamic intensity, not a fixed level schedule.
    intensity = _clamp(intensity, WARM_FLOOR_DUTY_MIN_INTENSITY, 1.0)

    on_s = int(
        round(
            WARM_FLOOR_DUTY_MIN_ON_S
            + (WARM_FLOOR_DUTY_MAX_ON_S - WARM_FLOOR_DUTY_MIN_ON_S)
            * intensity
        )
    )
    # There is deliberately no configured maximum pause. The pause follows
    # the current dynamic intensity continuously; only the actuator travel
    # time is a hard mechanical minimum between pulses.
    pause_s = int(
        round(
            WARM_FLOOR_DUTY_MIN_PAUSE_S
            + WARM_FLOOR_DUTY_PAUSE_RESPONSE_S * (1.0 - intensity) ** 2
        )
    )
    period_s = on_s + pause_s
    return period_s, on_s, intensity


def _latch_generic_actuator_phase(trv_state, requested_on: bool) -> tuple[bool, str | None]:
    """Latch a Generic Thermostat phase for the actuator travel time.

    The wall-clock duty profile is only allowed to choose the next phase after
    the current thermal actuator had time to complete its movement.  This is
    intentionally runtime state, not a user setting: the public default
    protects the common four-minute electrothermal actuator.
    """
    now = dt_util.utcnow().timestamp()
    requested_phase = "on" if requested_on else "off"
    current_phase = getattr(trv_state, "warm_floor_actuator_phase", None)
    minimum_until = getattr(trv_state, "warm_floor_phase_minimum_until", None)

    # After a restart the runtime latch is gone.  Use the downstream report as
    # the safest available phase hint and hold it for one complete travel
    # interval before accepting a new duty decision.
    if current_phase not in {"on", "off"}:
        reported_action = getattr(trv_state, "hvac_action", None)
        if isinstance(reported_action, HVACAction):
            reported_action = reported_action.value
        reported_action = str(reported_action or "").lower()
        if reported_action not in {
            HVACAction.HEATING.value,
            HVACAction.IDLE.value,
            HVACAction.OFF.value,
        }:
            # A newly discovered test double/device has no trustworthy
            # downstream phase to recover.  Start from the deterministic
            # duty decision; real HA reports one of the states above.
            current_phase = requested_phase
            trv_state.warm_floor_actuator_phase = current_phase
            trv_state.warm_floor_phase_started_at = now
            trv_state.warm_floor_phase_minimum_until = (
                now + WARM_FLOOR_ACTUATOR_TRAVEL_S if requested_on else now
            )
            trv_state.warm_floor_phase_recovered = False
            trv_state.warm_floor_actuator_guard = None
            return requested_on, None
        current_phase = "on" if reported_action == HVACAction.HEATING.value else "off"
        trv_state.warm_floor_actuator_phase = current_phase
        trv_state.warm_floor_phase_started_at = now
        trv_state.warm_floor_phase_minimum_until = (
            now + WARM_FLOOR_ACTUATOR_TRAVEL_S
        )
        trv_state.warm_floor_phase_recovered = True
        minimum_until = trv_state.warm_floor_phase_minimum_until

    if isinstance(minimum_until, (int, float)) and now < float(minimum_until):
        if requested_phase != current_phase:
            trv_state.warm_floor_actuator_guard = (
                "minimum_on_hold"
                if current_phase == "on"
                else (
                    "recovery_hold"
                    if getattr(trv_state, "warm_floor_phase_recovered", False)
                    else "minimum_off_hold"
                )
            )
            return current_phase == "on", trv_state.warm_floor_actuator_guard
        trv_state.warm_floor_actuator_guard = "recovery_hold" if (
            getattr(trv_state, "warm_floor_phase_recovered", False)
        ) else None
        return current_phase == "on", trv_state.warm_floor_actuator_guard

    trv_state.warm_floor_phase_recovered = False
    trv_state.warm_floor_actuator_guard = None
    if requested_phase != current_phase:
        trv_state.warm_floor_actuator_phase = requested_phase
        trv_state.warm_floor_phase_started_at = now
        trv_state.warm_floor_phase_minimum_until = (
            now + WARM_FLOOR_ACTUATOR_TRAVEL_S
        )
        trv_state.warm_floor_force_setpoint_write = True
    return requested_on, None


def _interrupt_generic_actuator_phase(trv_state, reason: str) -> None:
    """Abort Warm Floor immediately for a safety or normal-demand reason."""
    if not _is_generic_thermostat(trv_state):
        return
    now = dt_util.utcnow().timestamp()
    if (
        getattr(trv_state, "warm_floor_actuator_phase", None) == "off"
        and isinstance(
            getattr(trv_state, "warm_floor_phase_minimum_until", None),
            (int, float),
        )
        and now < float(trv_state.warm_floor_phase_minimum_until)
    ):
        trv_state.warm_floor_actuator_guard = reason
        return
    trv_state.warm_floor_actuator_phase = "off"
    trv_state.warm_floor_phase_started_at = now
    trv_state.warm_floor_phase_minimum_until = now + WARM_FLOOR_ACTUATOR_TRAVEL_S
    trv_state.warm_floor_actuator_guard = reason
    trv_state.warm_floor_force_setpoint_write = True


def _generic_duty_cycle_is_on(period_s: int, on_s: int) -> bool:
    """Return the current deterministic Warm Floor ON/OFF window."""
    phase = dt_util.utcnow().timestamp() % period_s
    return phase < on_s


def _apply_generic_duty_cycle(
    bt, entity_id: str, result: float, structural_scale: float, demand_factor: float
) -> tuple[float, str | None, int | None, int | None]:
    """Apply a short sustaining setpoint pulse to an ON/OFF heater.

    Generic Thermostat has no valve percentage to floor. Its only actuator is
    the temperature comparison, so the ON window raises the downstream
    setpoint above both the live room temperature and the Better Thermostat
    target. A room above target reduces maintenance demand through the duty
    profile and slope/EMA inputs; it is not by itself a reason to disable
    Warm Floor. Only the absolute safety ceiling and duty phase suppress it.
    """
    trv_state = bt.real_trvs.get(entity_id)
    target = getattr(bt, "bt_target_temp", None)
    current = getattr(bt, "cur_temp", None)
    if trv_state is None or not _is_generic_thermostat(trv_state):
        return result, None, None, None
    if not isinstance(target, (int, float)) or not isinstance(current, (int, float)):
        return result, None, None, None
    # Never replace a normal heating command with a maintenance pulse. The
    # pulse is only for the satisfied/idle part of the control cycle.
    if getattr(bt, "hvac_action", None) != HVACAction.IDLE:
        _interrupt_generic_actuator_phase(trv_state, "normal_heat_demand")
        return result, None, None, None
    if current <= float(target):
        _interrupt_generic_actuator_phase(
            trv_state, "below_target" if current < float(target) else "at_target"
        )
        return result, None, None, None
    # A positive slope is already folded into ``demand_factor`` by the
    # caller. Keep the maintenance pulse available here; it is modulated by
    # the duty profile rather than blanket-disabled above target.

    period_s, base_on_s, _intensity = _effective_duty_profile(
        bt, trv_state, demand_factor
    )
    # A cooling room needs more sustaining time. A rising/solar-heated room
    # gets a shorter/less frequent profile through demand_factor, but it must
    # still be able to maintain the slab above the room target.
    on_s = min(period_s, base_on_s)
    # The Generic Thermostat integration commonly exposes a 0.5 °C target
    # step. A +0.1 °C pulse would then be rounded back to the current target
    # and would never turn the relay on. Reserve room for the next valid
    # device step. The room target is not a hard Warm Floor ceiling: that
    # would disable the feature exactly when solar gain raises the room above
    # target. Keep only a broad absolute guard, bounded by the actuator's own
    # maximum, so an extreme temperature still fails safe.
    target_step = getattr(trv_state, "target_temp_step", None)
    if not isinstance(target_step, (int, float)) or target_step <= 0:
        target_step = getattr(bt, "bt_target_temp_step", None)
    if not isinstance(target_step, (int, float)) or target_step <= 0:
        target_step = 0.5
    max_temp = convert_to_float(
        trv_state.max_temp,
        getattr(bt, "device_name", None),
        "warm_floor absolute ceiling",
    )
    absolute_ceiling = float(target) + WARM_FLOOR_MAX_BACKOFF_CAP
    if isinstance(max_temp, (int, float)):
        absolute_ceiling = min(absolute_ceiling, float(max_temp))
    if float(current) >= absolute_ceiling:
        # A safety ceiling is allowed to interrupt a pulse immediately.  The
        # next normal cycle must still observe the full closing interval.
        _interrupt_generic_actuator_phase(trv_state, "safety_ceiling")
        return result, "outside_duty_window", period_s, on_s

    requested_on = _generic_duty_cycle_is_on(period_s, on_s)
    phase_on, _phase_guard = _latch_generic_actuator_phase(
        trv_state, requested_on
    )
    if not phase_on:
        return result, "outside_duty_window", period_s, on_s

    # Generic Thermostat turns on only when its target exceeds its measured
    # temperature. Keep the pulse just above the live reading, never above
    # the absolute Warm Floor safety ceiling.
    pulse_setpoint = min(
        absolute_ceiling,
        max(
            float(current) + 0.1,
            math.ceil((float(current) + 0.001) / float(target_step))
            * float(target_step),
        ),
    )
    if pulse_setpoint <= result:
        if float(result) > absolute_ceiling:
            # A stale downstream Generic Thermostat command can be well above
            # the current Warm Floor safety band after a restart or power
            # recovery. Keep the phase diagnostic as a release so the caller
            # clamps it back to the room target instead of treating it as an
            # already-valid floor value.
            return result, "outside_duty_window", period_s, on_s
        # The normal calibration result (or an explicitly configured sustain
        # push) may already be above the Better Thermostat target.  It is
        # still the Warm Floor ON command when the duty window is open; do
        # not mislabel it as ``already_at_floor`` and do not discard the
        # active-phase diagnostic merely because no additional raise was
        # needed.
        if float(result) > float(target) and float(result) <= absolute_ceiling:
            return result, "duty_cycle_on", period_s, on_s
        return result, None, period_s, on_s
    return pulse_setpoint, "duty_cycle_on", period_s, on_s


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
        _interrupt_generic_actuator_phase(trv_state, "contact_open")
        _record_status(bt, entity_id, active=False, sustaining_setpoint=None, reason="contact_open")
        return computed_value
    if getattr(bt, "call_for_heat", True) is False:
        _interrupt_generic_actuator_phase(trv_state, "outdoor_cutoff")
        _record_status(bt, entity_id, active=False, sustaining_setpoint=None, reason="outdoor_cutoff")
        return computed_value
    if getattr(bt, "bt_hvac_mode", None) == HVACMode.OFF:
        _interrupt_generic_actuator_phase(trv_state, "hvac_off")
        _record_status(bt, entity_id, active=False, sustaining_setpoint=None, reason="hvac_off")
        return computed_value

    # Warm Floor is a maintenance feature, never a second heating controller.
    # Normal Better Thermostat heating owns every state at or below target;
    # Warm Floor is permitted only while the measured room temperature is
    # strictly above the user's target. Apply this guard before calculating
    # backoff, sustain push, valve floor, or a Generic Thermostat duty pulse so
    # no actuator type can receive a maintenance command in the normal-demand
    # region.
    current_temp = getattr(bt, "cur_temp", None)
    target_temp = getattr(bt, "bt_target_temp", None)
    if not isinstance(current_temp, (int, float)) or not isinstance(
        target_temp, (int, float)
    ):
        _interrupt_generic_actuator_phase(trv_state, "temperature_unavailable")
        _record_status(
            bt,
            entity_id,
            active=False,
            sustaining_setpoint=None,
            reason="temperature_unavailable",
        )
        return computed_value
    if float(current_temp) <= float(target_temp):
        reason = "below_target" if float(current_temp) < float(target_temp) else "at_target"
        _interrupt_generic_actuator_phase(trv_state, reason)
        _record_status(
            bt,
            entity_id,
            active=False,
            sustaining_setpoint=None,
            reason=reason,
        )
        return computed_value

    calibration_mode = normalize_calibration_mode(
        trv_state.advanced.get("calibration_mode")
    )
    if calibration_mode is None:
        calibration_mode = CalibrationMode.HEATING_POWER_CALIBRATION

    structural_scale = _structural_scale(bt, entity_id, calibration_mode)
    solar_scale = _solar_scale(bt, entity_id, calibration_mode)

    # Real-time slope guard: what the room is doing *right now* modulates the
    # structural/solar estimate above. A stale external sensor can't prove
    # the room has actually stopped rising - it gets the same treatment as
    # "still gaining", not the more permissive "no slope data" fallback.
    temp_slope = getattr(bt, "temp_slope", None)
    # Some temperature integrations publish only on a material change, so
    # their raw entity can legitimately be old while Better Thermostat's EMA
    # and slope are being refreshed by its own telemetry loop. In that case
    # the derived slope is the signal we can act on. Only fail closed for a
    # stale raw sensor when no usable slope exists at all.
    sensor_stale_without_slope = _external_sensor_is_stale(bt) and not isinstance(
        temp_slope, (int, float)
    )
    if sensor_stale_without_slope:
        # We cannot verify whether the room is still gaining. Fail closed for
        # unknown/stale telemetry, but do not treat a valid positive slope as
        # proof that the slab should be allowed to cool completely.
        _record_status(
            bt,
            entity_id,
            active=False,
            sustaining_setpoint=None,
            reason="sensor_stale",
        )
        return computed_value
    if isinstance(temp_slope, (int, float)) and temp_slope < -UNDERSHOOT_SLOPE_GUARD:
        structural_scale = WARM_FLOOR_MIN_BACKOFF_RATIO

    demand_factor = _warm_floor_demand_factor(bt, entity_id, calibration_mode)
    if isinstance(temp_slope, (int, float)) and temp_slope > 0.0:
        # Positive slope usually means residual heat or solar gain. It should
        # make maintenance pulses less frequent/shorter, not remove them:
        # the slab still needs heat so it is ready when the gain disappears.
        rising_ratio = _clamp(
            OVERSHOOT_SLOPE_GUARD / float(temp_slope),
            WARM_FLOOR_MIN_RISING_DUTY_RATIO,
            1.0,
        )
        demand_factor *= rising_ratio
    if demand_factor > 0.0:
        structural_scale = max(
            WARM_FLOOR_MIN_BACKOFF_RATIO,
            structural_scale * (1.0 - 0.75 * demand_factor),
        )

    sustaining_setpoint = _sustaining_setpoint(
        bt, entity_id, structural_scale, solar_scale
    )
    if sustaining_setpoint is None:
        _record_status(bt, entity_id, active=False, sustaining_setpoint=None, reason="no_sustaining_floor")
        return computed_value

    sustain_push_c = None
    duty_reason = None
    duty_period_s = None
    duty_on_s = None
    if not is_offset:
        # Setpoint semantics: higher = more heat. Warm Floor only ever raises.
        result = max(computed_value, sustaining_setpoint)

        # A configured sustain amount may extend the generic ON command, but
        # the generic duty-cycle pulse below is mandatory even when this
        # optional amount is zero.  The downstream actuator setpoint may be
        # above target; bt.bt_target_temp remains the user's room target.
        target_temp = float(bt.bt_target_temp)
        cur_temp = getattr(bt, "cur_temp", None)
        if (
            computed_value >= target_temp
            and bt.hvac_action == HVACAction.IDLE
            and isinstance(cur_temp, (int, float))
            and cur_temp > target_temp
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

        result, duty_reason, duty_period_s, duty_on_s = _apply_generic_duty_cycle(
            bt, entity_id, result, structural_scale, demand_factor
        )
        # The duty window is a real control edge, not merely diagnostic data.
        # Remember the last evaluated phase per heater so the controlling
        # layer can send the first ON/OFF command after a restart or a missed
        # window even when its cached setpoint happens to compare equal.
        if _is_generic_thermostat(trv_state):
            duty_phase = (
                "on" if duty_reason == "duty_cycle_on" else "off"
                if duty_reason in {"outside_duty_window", "rising"}
                else None
            )
            if duty_phase is not None:
                previous_phase = getattr(trv_state, "warm_floor_duty_phase", None)
                trv_state.warm_floor_duty_phase = duty_phase
                if previous_phase != duty_phase:
                    trv_state.warm_floor_force_setpoint_write = True
        # A Generic Thermostat turns its relay on from the downstream
        # setpoint alone.  If the Better Thermostat controller is idle and the
        # room is already strictly above its user target, an old setpoint above the
        # room temperature is therefore a real heating command, even when
        # Warm Floor is outside its ON window.  Release that stale command
        # unless the explicitly configured sustain push is the reason to keep
        # it above target.  This is deliberately limited to Generic
        # Thermostat heaters; valve/setpoint devices have their own idle
        # semantics and must not be clamped here.
        if (
            _is_generic_thermostat(trv_state)
            and duty_reason in {"outside_duty_window", "rising"}
            and sustain_push_c is None
            and bt.hvac_action == HVACAction.IDLE
            and isinstance(cur_temp, (int, float))
            and cur_temp > target_temp
            and result >= target_temp
        ):
            # Generic Thermostat keeps its previous heating action when the
            # target is exactly equal to the measured temperature, especially
            # with zero hot tolerance.  Release one valid device step below
            # the room target so the downstream relay has an unambiguous OFF
            # condition.  This changes only the downstream actuator command;
            # the Better Thermostat user target and preset remain unchanged.
            release_step = getattr(trv_state, "target_temp_step", None)
            if not isinstance(release_step, (int, float)) or release_step <= 0:
                release_step = getattr(bt, "bt_target_temp_step", None)
            if not isinstance(release_step, (int, float)) or release_step <= 0:
                release_step = 0.5
            min_temp = convert_to_float(
                trv_state.min_temp,
                getattr(bt, "device_name", None),
                "warm_floor release",
            )
            result = target_temp - float(release_step)
            if isinstance(min_temp, (int, float)):
                result = max(result, float(min_temp))
            duty_reason = "idle_release"
        if duty_reason == "duty_cycle_on":
            sustain_push_c = round(result - float(bt.bt_target_temp), 3)
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
            _record_status(
                bt,
                entity_id,
                active=False,
                sustaining_setpoint=None,
                reason="internal_temperature_unavailable",
            )
            return computed_value
        offset_ceiling = sustaining_setpoint - float(trv_internal_temp)
        result = min(computed_value, offset_ceiling)

    _apply_valve_floor(
        bt, entity_id, structural_scale, solar_scale, demand_factor
    )
    valve_min_pct = None
    balance = getattr(trv_state, "calibration_balance", None)
    if isinstance(balance, dict) and balance.get("apply_valve"):
        current_valve = balance.get("valve_percent")
        if isinstance(current_valve, (int, float)):
            valve_min_pct = round(float(current_valve), 1)
    control_method = None
    if duty_reason in {
        "duty_cycle_on",
        "outside_duty_window",
        "rising",
        "idle_release",
    }:
        control_method = "generic_duty_cycle"
    elif _has_active_valve_balance(trv_state):
        control_method = "valve_minimum"
    elif result != computed_value:
        control_method = "setpoint_floor"
    if duty_reason == "duty_cycle_on":
        reason = "duty_cycle_on"
    elif duty_reason == "outside_duty_window":
        reason = "outside_duty_window"
    elif duty_reason == "rising":
        reason = "temperature_rising"
    elif duty_reason == "idle_release":
        reason = "idle_release"
    elif valve_min_pct is not None:
        reason = "valve_minimum_applied"
    elif result != computed_value:
        reason = "setpoint_floor_applied"
    else:
        reason = "already_at_floor"
    _record_status(
        bt,
        entity_id,
        active=(
            duty_reason == "duty_cycle_on"
            or (
                duty_reason not in {
                    "duty_cycle_on",
                    "outside_duty_window",
                    "idle_release",
                    "rising",
                }
                and result != computed_value
            )
            or _has_active_valve_balance(trv_state)
        ),
        sustaining_setpoint=sustaining_setpoint,
        sustain_push=sustain_push_c,
        control_method=control_method,
        reason=reason,
        valve_min_pct=valve_min_pct,
        duty_period_s=duty_period_s,
        duty_on_s=duty_on_s,
        demand_factor=demand_factor,
    )
    return result


def _apply_valve_floor(
    bt,
    entity_id: str,
    structural_scale: float,
    solar_scale: float,
    demand_factor: float = 0.0,
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
    # A larger configured backoff is the gentler level, while a smaller
    # backoff is the more aggressive "Keep Hot" level. Therefore the valve
    # floor moves in the opposite direction to max_backoff.
    peak_sustaining_pct = 20.0 * (
        1.0 - _clamp(max_backoff / WARM_FLOOR_MAX_BACKOFF_CAP, 0.0, 1.0)
    )
    dynamic_intensity = 0.75 + 0.25 * _clamp(demand_factor, 0.0, 1.0)
    sustaining_pct = peak_sustaining_pct * tightness * dynamic_intensity
    new_pct = max(float(valve_percent), sustaining_pct)
    balance["valve_percent"] = int(round(_clamp(new_pct, 0.0, 100.0)))
