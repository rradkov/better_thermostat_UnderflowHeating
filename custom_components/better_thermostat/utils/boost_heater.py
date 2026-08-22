"""Boost: fast post-ventilation recovery on window reopen, via the Cooler entity.

When a window opens and room temperature drifts by more than a configurable
threshold, closing the window arms a boost on the instance's already-
configured Cooler entity (``self.cooler_entity_id``) — no separate device to
pick. Framed generically and symmetrically: the Cooler entity is commonly a
device that already does double duty (a fan-coil, a heat pump, an
AC-in-heat-mode unit), so a room that got *colder* than the threshold arms a
**heat** boost, and a room that got *warmer* than the threshold (the summer
case - cooling is suppressed by the same contact-open gate while a window is
open, so a hot day can overheat the room) arms a **cool** boost. Both target
the single ``bt_target_temp`` - boost's job is "get the room back to its
target," not run a separate dual-setpoint A/C feature.

Because the boost channel and the existing ``control_cooler()`` both drive
``self.cooler_entity_id``, an active boost *preempts* the normal cooler pass
for the cycles it runs (see ``utils/controlling.py::control_queue()``);
``control_cooler()`` resumes as soon as the boost ends.

Termination is anticipatory rather than a hard stop at target: it uses
``temp_slope`` (already live on the BetterThermostat entity) to stop the
instant the room's current trajectory is projected to reach target within
the device's residual lag window, rather than waiting until it's already
there — which is what prevents overshoot in either direction. A slower,
locally-tracked EMA guards against stopping early on a brief sensor blip.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import logging
import math
from time import monotonic
from typing import Any, Final

from homeassistant.components.climate.const import HVACAction, HVACMode
from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN

from custom_components.better_thermostat.utils.helpers import (
    matches_any_setpoint,
    read_setpoint_celsius,
)
from custom_components.better_thermostat.utils.hvac_action import (
    should_cool_with_tolerance,
    should_heat_with_tolerance,
)

_LOGGER = logging.getLogger(__name__)

# -- Config keys ---------------------------------------------------------
CONF_BOOST_HEATER_ENABLED: Final = "boost_heater_enabled"
CONF_BOOST_THRESHOLD: Final = "boost_threshold"
CONF_BOOST_FAN_MODE: Final = "boost_fan_mode"
CONF_BOOST_LAG_MINUTES: Final = "boost_lag_minutes"
CONF_BOOST_MAX_RUNTIME: Final = "boost_max_runtime"

DEFAULT_BOOST_THRESHOLD: Final = 5.0
DEFAULT_BOOST_FAN_MODE: Final = None  # unset = don't touch fan_mode; opt-in only
DEFAULT_BOOST_LAG_MINUTES: Final = 5.0
DEFAULT_BOOST_MAX_RUNTIME: Final = 60.0

# Sentinel meaning "leave fan_mode alone" in the config-flow selector, since
# voluptuous/HA selectors need a real string option, not a bare None.
_NO_FAN_MODE_OVERRIDE: Final = "__no_change__"

# Slow EMA time constant used for the trend-confirmation guard against
# stopping on a brief blip, matching the project's existing 1h EMA sensor.
_SLOW_EMA_TAU_S: Final = 3600.0

SETPOINT_KEYS: Final = ("temperature",)


# ---------------------------------------------------------------------------
# Boost state tracker
# ---------------------------------------------------------------------------


@dataclass
class BoostHeaterTracker:
    """Per-instance boost lifecycle and the telemetry its termination needs."""

    open_temp: float | None = None
    boost_direction: HVACMode | None = None  # None | HVACMode.HEAT | HVACMode.COOL
    previous_fan_mode: str | None = None
    boost_started_ts: float | None = None
    last_action: HVACAction = field(default=HVACAction.IDLE)
    slow_ema: float | None = None
    _slow_ema_ts: float | None = None

    @property
    def boost_active(self) -> bool:
        """Whether a boost is currently armed, in either direction."""
        return self.boost_direction is not None

    def record_window_open(self, cur_temp: float | None) -> None:
        """Record the room temperature at the moment a window-open commits."""
        self.open_temp = cur_temp

    def evaluate_on_close(
        self,
        cur_temp: float | None,
        threshold: float,
        *,
        supports_heat: bool,
        supports_cool: bool,
    ) -> HVACMode | None:
        """Arm a direction if the drift since open exceeded ``threshold``.

        Returns the armed direction (or None). The recorded open temperature
        is consumed either way, so a second close without an intervening
        open can't re-arm on stale data. A direction the device doesn't
        actually support is never armed.
        """
        open_temp = self.open_temp
        self.open_temp = None
        if open_temp is None or cur_temp is None:
            return None

        if open_temp - cur_temp > threshold and supports_heat:
            self.boost_direction = HVACMode.HEAT
            return HVACMode.HEAT
        if cur_temp - open_temp > threshold and supports_cool:
            self.boost_direction = HVACMode.COOL
            return HVACMode.COOL
        return None

    def update_slow_ema(self, value: float | None, now_ts: float) -> None:
        """Update the locally-tracked slow (1h-tau) EMA from a fresh reading."""
        if value is None:
            return
        if self.slow_ema is None or self._slow_ema_ts is None:
            self.slow_ema = value
        else:
            dt_s = max(0.0, now_ts - self._slow_ema_ts)
            alpha = 1.0 - math.exp(-dt_s / _SLOW_EMA_TAU_S) if dt_s > 0 else 0.0
            self.slow_ema = self.slow_ema + alpha * (value - self.slow_ema)
        self._slow_ema_ts = now_ts

    def clear(self) -> None:
        """Reset all boost-cycle state - called on cancellation or completion."""
        self.boost_direction = None
        self.previous_fan_mode = None
        self.boost_started_ts = None
        self.last_action = HVACAction.IDLE
        # open_temp/slow_ema deliberately survive: a fresh window-open still
        # needs the temp captured, and the slow EMA is a standing trend
        # signal, not boost-cycle-scoped state.


# ---------------------------------------------------------------------------
# Config flow helpers
# ---------------------------------------------------------------------------


def build_boost_heater_user_fields(
    hass, resolve, *, boost_heater_enabled: bool
) -> dict[Any, Any]:
    """Return the top-level 'user' step field(s) for this feature.

    Only shown when the instance-level toggle is on (which itself is only
    offered once a Cooler entity is configured - see config_flow.py). The
    boost device is that same Cooler entity, so there's no entity picker
    here. Probing its live ``fan_modes`` to conditionally show
    ``CONF_BOOST_FAN_MODE`` mirrors the existing capability-probing pattern
    ``config_flow._default_calibration_from_info()`` uses for the chosen
    TRV's adapter. Returned keys are already-wrapped ``vol.Optional(...)``
    markers, ready to merge straight into the caller's field ``OrderedDict``.
    """
    if not boost_heater_enabled:
        return {}

    from homeassistant.helpers import selector
    import voluptuous as vol

    fields: dict[Any, Any] = {
        vol.Optional(
            CONF_BOOST_THRESHOLD,
            default=resolve(CONF_BOOST_THRESHOLD, DEFAULT_BOOST_THRESHOLD),
        ): vol.All(vol.Coerce(float), vol.Range(min=0.1, max=15.0)),
        vol.Optional(
            CONF_BOOST_LAG_MINUTES,
            default=resolve(CONF_BOOST_LAG_MINUTES, DEFAULT_BOOST_LAG_MINUTES),
        ): vol.All(vol.Coerce(float), vol.Range(min=0.0, max=30.0)),
        vol.Optional(
            CONF_BOOST_MAX_RUNTIME,
            default=resolve(CONF_BOOST_MAX_RUNTIME, DEFAULT_BOOST_MAX_RUNTIME),
        ): vol.All(vol.Coerce(float), vol.Range(min=5.0, max=240.0)),
    }

    from custom_components.better_thermostat.utils.const import CONF_COOLER

    cooler_id = resolve(CONF_COOLER, None)
    fan_modes: list[str] = []
    if cooler_id and hass is not None:
        state = hass.states.get(cooler_id)
        if state is not None:
            raw_modes = state.attributes.get("fan_modes")
            if isinstance(raw_modes, (list, tuple)):
                fan_modes = [str(mode) for mode in raw_modes]

    if fan_modes:
        current_fan_mode = resolve(CONF_BOOST_FAN_MODE, None) or _NO_FAN_MODE_OVERRIDE
        fields[vol.Optional(CONF_BOOST_FAN_MODE, default=current_fan_mode)] = (
            selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=[_NO_FAN_MODE_OVERRIDE, *fan_modes],
                    mode=selector.SelectSelectorMode.DROPDOWN,
                    translation_key="boost_fan_mode",
                )
            )
        )

    return fields


def normalize_boost_heater_user_submission(
    data: dict[str, Any], *, boost_heater_enabled: bool
) -> dict[str, Any]:
    """Normalize the top-level submission for this feature.

    A stale threshold/fan-mode/etc. from a submission made while the toggle
    was on must not silently persist once it's off again (or once the
    Cooler that backs it was removed), so a disabled instance always gets a
    clean reset rather than whatever the form happened to carry.
    """
    if not boost_heater_enabled:
        return {
            CONF_BOOST_THRESHOLD: DEFAULT_BOOST_THRESHOLD,
            CONF_BOOST_FAN_MODE: None,
            CONF_BOOST_LAG_MINUTES: DEFAULT_BOOST_LAG_MINUTES,
            CONF_BOOST_MAX_RUNTIME: DEFAULT_BOOST_MAX_RUNTIME,
        }

    out: dict[str, Any] = {}

    raw_threshold = data.get(CONF_BOOST_THRESHOLD, DEFAULT_BOOST_THRESHOLD)
    try:
        out[CONF_BOOST_THRESHOLD] = max(0.1, min(float(raw_threshold), 15.0))
    except TypeError, ValueError:
        out[CONF_BOOST_THRESHOLD] = DEFAULT_BOOST_THRESHOLD

    raw_fan_mode = data.get(CONF_BOOST_FAN_MODE)
    if raw_fan_mode in (None, "", _NO_FAN_MODE_OVERRIDE):
        out[CONF_BOOST_FAN_MODE] = None
    else:
        out[CONF_BOOST_FAN_MODE] = str(raw_fan_mode)

    raw_lag = data.get(CONF_BOOST_LAG_MINUTES, DEFAULT_BOOST_LAG_MINUTES)
    try:
        out[CONF_BOOST_LAG_MINUTES] = max(0.0, min(float(raw_lag), 30.0))
    except TypeError, ValueError:
        out[CONF_BOOST_LAG_MINUTES] = DEFAULT_BOOST_LAG_MINUTES

    raw_runtime = data.get(CONF_BOOST_MAX_RUNTIME, DEFAULT_BOOST_MAX_RUNTIME)
    try:
        out[CONF_BOOST_MAX_RUNTIME] = max(5.0, min(float(raw_runtime), 240.0))
    except TypeError, ValueError:
        out[CONF_BOOST_MAX_RUNTIME] = DEFAULT_BOOST_MAX_RUNTIME

    return out


# ---------------------------------------------------------------------------
# Window-transition capture
# ---------------------------------------------------------------------------


def record_window_transition(bt, role, is_open: bool) -> None:
    """Capture the room temperature at a window open/close commit.

    Called from ``events/contact.py`` at the debounce commit point, scoped
    by the caller to ``role.kind == "window"`` only - door transitions never
    reach this function. A no-op whenever boost isn't enabled or no Cooler
    is configured to boost.
    """
    if not getattr(bt, "boost_enabled", False):
        return
    if getattr(bt, "cooler_entity_id", None) is None:
        return
    tracker: BoostHeaterTracker | None = getattr(bt, "_boost_heater_tracker", None)
    if tracker is None:
        return

    if is_open:
        tracker.record_window_open(getattr(bt, "cur_temp", None))
        return

    threshold = getattr(bt, "boost_threshold_k", None) or DEFAULT_BOOST_THRESHOLD
    state = bt.hass.states.get(bt.cooler_entity_id)
    hvac_modes = (
        state.attributes.get("hvac_modes") if state is not None else None
    ) or []
    direction = tracker.evaluate_on_close(
        getattr(bt, "cur_temp", None),
        threshold,
        supports_heat=HVACMode.HEAT in hvac_modes,
        supports_cool=HVACMode.COOL in hvac_modes,
    )
    if direction is not None:
        _LOGGER.debug(
            "better_thermostat %s: boost %s armed on window close for %s",
            getattr(bt, "device_name", None),
            direction,
            bt.cooler_entity_id,
        )


# ---------------------------------------------------------------------------
# Control loop
# ---------------------------------------------------------------------------


async def _set_fan_mode(bt, entity_id: str, fan_mode: str) -> None:
    try:
        await bt.hass.services.async_call(
            "climate",
            "set_fan_mode",
            {"entity_id": entity_id, "fan_mode": fan_mode},
            blocking=False,
        )
    except Exception:
        _LOGGER.debug(
            "better_thermostat %s: boost device %s rejected fan_mode %s",
            getattr(bt, "device_name", None),
            entity_id,
            fan_mode,
            exc_info=True,
        )


async def _restore_fan_mode(bt, entity_id: str, tracker: BoostHeaterTracker) -> None:
    if tracker.previous_fan_mode is not None:
        await _set_fan_mode(bt, entity_id, tracker.previous_fan_mode)
        tracker.previous_fan_mode = None


async def _ensure_off(bt, entity_id: str, state) -> None:
    if state.state == HVACMode.OFF:
        return
    await bt.hass.services.async_call(
        "climate",
        "set_hvac_mode",
        {"entity_id": entity_id, "hvac_mode": HVACMode.OFF},
        blocking=False,
    )


def _boost_should_stop(
    bt, tracker: BoostHeaterTracker, cur_temp: float, now_ts: float, direction: HVACMode
) -> bool:
    """Whether an active boost should stop this cycle."""
    target = bt.bt_target_temp
    tolerance = max(0.0, getattr(bt, "tolerance", 0.0) or 0.0)
    if not isinstance(target, (int, float)):
        return True
    target = float(target)

    # Safety cap: never let a boost run forever regardless of temperature -
    # guards against a stuck sensor or a device that isn't actually acting.
    max_runtime_s = (
        getattr(bt, "boost_max_runtime_minutes", None) or DEFAULT_BOOST_MAX_RUNTIME
    ) * 60.0
    if (
        tracker.boost_started_ts is not None
        and (now_ts - tracker.boost_started_ts) >= max_runtime_s
    ):
        _LOGGER.warning(
            "better_thermostat %s: boost on %s hit its max runtime (%.0f min) "
            "without reaching target - stopping regardless of temperature",
            getattr(bt, "device_name", None),
            getattr(bt, "cooler_entity_id", None),
            max_runtime_s / 60.0,
        )
        return True

    lag_minutes = (
        getattr(bt, "boost_lag_minutes", None)
        if getattr(bt, "boost_lag_minutes", None) is not None
        else DEFAULT_BOOST_LAG_MINUTES
    )
    ema_reference = getattr(bt, "cur_temp_filtered", None)
    if not isinstance(ema_reference, (int, float)):
        ema_reference = cur_temp
    temp_slope = getattr(bt, "temp_slope", None)

    if direction == HVACMode.HEAT:
        still_needs_heat = should_heat_with_tolerance(
            cur_temp, target, tolerance, tracker.last_action
        )
        tracker.last_action = (
            HVACAction.HEATING if still_needs_heat else HVACAction.IDLE
        )
        if not still_needs_heat:
            return True
        # Anticipatory early stop, only once already inside the tolerance
        # band - never cuts off while still meaningfully short of target.
        if cur_temp < target - tolerance:
            return False
        if not isinstance(temp_slope, (int, float)):
            return False  # no rate data yet: fall back to the plain check above
        projected_temp = float(ema_reference) + float(temp_slope) * float(lag_minutes)
        trend_confirmed = (
            tracker.slow_ema is None or tracker.slow_ema >= target - tolerance
        )
        return projected_temp >= target and trend_confirmed

    # HVACMode.COOL - the mirror image of the heat branch.
    still_needs_cool = should_cool_with_tolerance(
        cur_temp, target, tolerance, tracker.last_action == HVACAction.COOLING
    )
    tracker.last_action = HVACAction.COOLING if still_needs_cool else HVACAction.IDLE
    if not still_needs_cool:
        return True
    if cur_temp > target + tolerance:
        return False
    if not isinstance(temp_slope, (int, float)):
        return False
    projected_temp = float(ema_reference) + float(temp_slope) * float(lag_minutes)
    trend_confirmed = tracker.slow_ema is None or tracker.slow_ema <= target + tolerance
    return projected_temp <= target and trend_confirmed


async def control_boost(self) -> None:
    """Drive ``self.cooler_entity_id`` for an active boost.

    Called from ``control_queue()`` *instead of* ``control_cooler()`` for
    cycles where a boost is armed - the two must never both write to the
    same entity in the same cycle. ``control_cooler()`` resumes as soon as
    the boost ends (``tracker.boost_direction`` goes back to ``None``).
    """
    tracker: BoostHeaterTracker = self._boost_heater_tracker
    now_ts = monotonic()
    tracker.update_slow_ema(
        getattr(self, "cur_temp_filtered", None) or getattr(self, "cur_temp", None),
        now_ts,
    )

    state = self.hass.states.get(self.cooler_entity_id)
    if state is None or state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
        return

    if (
        self.bt_hvac_mode == HVACMode.OFF
        or self.contact_open
        or self.call_for_heat is False
    ):
        await _restore_fan_mode(self, self.cooler_entity_id, tracker)
        await _ensure_off(self, self.cooler_entity_id, state)
        tracker.clear()
        self.last_cooler_mode_decided = HVACMode.OFF
        return

    direction = tracker.boost_direction
    if direction is None:
        # Should not normally be reached (the caller only routes here while a
        # boost is armed), but ensure the device isn't left mid-boost.
        await _ensure_off(self, self.cooler_entity_id, state)
        return

    if tracker.boost_started_ts is None:
        tracker.boost_started_ts = now_ts
        boost_fan_mode = getattr(self, "boost_fan_mode", None)
        fan_modes = state.attributes.get("fan_modes")
        if (
            boost_fan_mode
            and isinstance(fan_modes, (list, tuple))
            and boost_fan_mode in fan_modes
        ):
            current_fan_mode = state.attributes.get("fan_mode")
            if current_fan_mode:
                tracker.previous_fan_mode = str(current_fan_mode)
            await _set_fan_mode(self, self.cooler_entity_id, boost_fan_mode)

    if state.state != direction:
        await self.hass.services.async_call(
            "climate",
            "set_hvac_mode",
            {"entity_id": self.cooler_entity_id, "hvac_mode": direction},
            blocking=False,
        )

    current_setpoint = read_setpoint_celsius(
        self, state, SETPOINT_KEYS, "control_boost()"
    )
    if isinstance(self.bt_target_temp, (int, float)) and not matches_any_setpoint(
        current_setpoint, {self.bt_target_temp}, 0.3
    ):
        await self.hass.services.async_call(
            "climate",
            "set_temperature",
            {"entity_id": self.cooler_entity_id, "temperature": self.bt_target_temp},
            blocking=False,
        )

    # Lets the same dual-role-device stand-down logic control_cooler() drives
    # (cooling_owns_dual_role_device()) recognize this cycle's owner.
    self.last_cooler_mode_decided = direction

    cur_temp = getattr(self, "cur_temp", None)
    if not isinstance(cur_temp, (int, float)):
        return
    if _boost_should_stop(self, tracker, float(cur_temp), now_ts, direction):
        await _restore_fan_mode(self, self.cooler_entity_id, tracker)
        await _ensure_off(self, self.cooler_entity_id, state)
        tracker.clear()
