"""Translate pure Warm Floor decisions into ordinary calibration values.

No device service is called here. The normal control pipeline applies its
safety hull, device limits, write pacing and acknowledgement handling.
"""

from __future__ import annotations

from dataclasses import asdict, replace
import math
from typing import TYPE_CHECKING

from homeassistant.components.climate.const import HVACAction

from ..calibration import effective_room_temperature
from ..core.desired import TrvDesired
from ..core.fsm.control_mode import ControlMode
from ..core.snapshot import HvacMode, WorldSnapshot
from ..core.warm_floor import (
    WarmFloorObservation,
    WarmFloorState,
    WarmFloorThermalInput,
    advance_warm_floor,
    compute_warm_floor_profile,
    profile_for_model_demand,
)
from ..core.warm_floor_policy import (
    MAX_OFF_SECONDS,
    FloorReport,
    WarmFloorPolicyState,
    advance_warm_floor_policy,
)
from ..core.warm_floor_prediction import predict_warm_floor
from .calibration.mpc import build_mpc_group_key, build_mpc_key
from .helpers import convert_to_float_celsius
from .underfloor import finite_number, normalize_warm_floor
from .watcher import room_sensor_reading

if TYPE_CHECKING:
    from ..climate import BetterThermostat
    from ..trv import Trv


def _room_report(host: BetterThermostat, room: float) -> FloorReport | None:
    """Read a genuine accepted sensor report, never the control tick's time."""
    sensor = getattr(host, "sensor_entity_id", None)
    state = host.hass.states.get(sensor) if sensor else None
    if state is None:
        return None
    reading = room_sensor_reading(host, state)
    if reading is None or not math.isclose(reading, room, abs_tol=0.005):
        return None
    return FloorReport(state.last_reported.timestamp(), reading)


def record_warm_floor_write(
    trv: Trv,
    channel: str,
    result: str,
    command: float | None = None,
    now_monotonic: float | None = None,
) -> None:
    """Record adapter outcomes independently for each requested channel."""
    if not trv.warm_floor_status:
        return
    trv.warm_floor_status[f"{channel}_write_result"] = result
    trv.warm_floor_status[f"{channel}_command"] = command
    if channel == "setpoint" and result in ("sent", "held"):
        phase = trv.warm_floor_status.get("phase")
        if phase in ("on", "off") and now_monotonic is not None:
            if phase != trv.warm_floor_commanded_phase:
                trv.warm_floor_state = replace(
                    trv.warm_floor_state,
                    started_monotonic=now_monotonic,
                    recovered=False,
                )
                trv.warm_floor_status["remaining_seconds"] = trv.warm_floor_status.get(
                    f"{phase}_seconds"
                )
            trv.warm_floor_commanded_phase = str(phase)
        elif phase is None:
            trv.warm_floor_commanded_phase = None
    outcomes = {
        trv.warm_floor_status.get(f"{name}_write_result")
        for name in ("setpoint", "offset", "valve")
    }
    trv.warm_floor_status["write_result"] = (
        "failed"
        if "failed" in outcomes
        else "pending"
        if "pending" in outcomes
        else "sent"
        if "sent" in outcomes
        else result
    )


def apply_warm_floor(
    host: BetterThermostat,
    entity_id: str,
    snapshot: WorldSnapshot,
    desired: TrvDesired,
    temperature: float | None,
    calibration_offset: float | None,
) -> tuple[float | None, float | None]:
    """Adjust calibration only when the upstream intent permits maintenance."""
    if not getattr(host, "warm_floor_enabled", False):
        return temperature, calibration_offset
    trv = host.real_trvs[entity_id]
    if trv.advanced.get("heating_type") != "underfloor":
        return temperature, calibration_offset
    settings = normalize_warm_floor(trv.advanced)
    generic = any(
        item["trv"] == entity_id and item["integration"] == "generic_thermostat"
        for item in host.all_trvs
    )
    configured_backoff = settings["warm_floor_max_backoff"]
    room = snapshot.room_temperature
    hold = host.kernel_state.control_mode.mode == ControlMode.HOLD
    if host.kernel_state.control_mode.mode == ControlMode.SENSOR_FALLBACK:
        room = effective_room_temperature(host)
    blocked = (
        desired.hvac_mode not in (HvacMode.HEAT, HvacMode.HEAT_COOL)
        or desired.suppression is not None
    )
    if room is None or not math.isfinite(room):
        hold = True
        room = snapshot.heat_target_temperature
    normal = room < snapshot.heat_target_temperature or host.hvac_action in (
        HVACAction.HEATING,
        HVACAction.COOLING,
    )
    loss, power, insulation, solar, disturbance = (
        host.heat_loss_rate,
        host.heating_power,
        None,
        None,
        None,
    )
    balance = trv.calibration_balance
    if balance is not None:
        if balance["controller"] == "mpc_calibration":
            key = (
                build_mpc_group_key(host)
                if len(host.real_trvs) > 1
                else build_mpc_key(host, entity_id)
            )
            mpc = host.state_mgr.get_mpc(key)
            loss = mpc.loss_est if mpc.loss_est is not None else loss
            power = mpc.gain_est if mpc.gain_est is not None else power
            insulation = mpc.ka_est
            if mpc.solar_gain_est is not None:
                solar = mpc.solar_gain_est * snapshot.solar_intensity
        elif balance["controller"] == "mpc_v2_calibration":
            disturbance = balance["debug"]["D_hat_K_per_min"]
            time_constant = balance["debug"]["tau_room_min"]
            if math.isfinite(time_constant) and time_constant > 0:
                insulation = 1.0 / time_constant
    water = getattr(host, "warm_floor_water_temperature", None)
    sensor = getattr(host, "warm_floor_water_sensor", None)
    if sensor:
        reported = host.hass.states.get(sensor)
        if reported is not None:
            live_water = convert_to_float_celsius(
                reported.state,
                host.device_name,
                "warm_floor water",
                unit_of_measurement=reported.attributes.get("unit_of_measurement"),
            )
            if live_water is not None:
                water = live_water
    thermal = WarmFloorThermalInput(
        room,
        snapshot.heat_target_temperature,
        slope_kelvin_per_min=snapshot.temperature_slope,
        short_average=snapshot.room_temperature_filtered,
        long_average=host.room_temperature_ema_1h,
        slow_slope_kelvin_per_min=host.temperature_slope_1h,
        heat_loss=loss,
        heating_power=power,
        insulation=insulation,
        solar_gain=solar,
        disturbance=disturbance,
        outdoor_temperature=snapshot.outdoor_temperature,
        flow_temperature=water,
        level=settings["warm_floor_level"],
        max_backoff=settings["warm_floor_max_backoff"],
    )
    profile = compute_warm_floor_profile(thermal)
    ceiling = min(snapshot.heat_target_temperature + 5.0, trv.max_temp)
    blocked = blocked or room >= ceiling
    policy = settings["warm_floor_level"]
    policy_reason = "fixed"
    slow_slope = thermal.slow_slope_kelvin_per_min
    prediction = None
    model_demand = None
    model_source = None
    if (
        settings["warm_floor_level"] != "custom"
        and balance is not None
        and balance["controller"] in ("mpc_calibration", "mpc_v2_calibration")
        and math.isfinite(balance["valve_percent"])
        and 0.0 <= balance["valve_percent"] <= 100.0
        and host.kernel_state.control_mode.mode == ControlMode.OPTIMAL
    ):
        # Calibration has already run once for this cycle (and once per group).
        # Consume its distributed command; never step or train another model.
        model_demand = balance["valve_percent"] / 100.0
        model_source = "bt_" + balance["controller"]
        profile = profile_for_model_demand(profile, model_demand)
        policy_reason = (
            "suspended"
            if blocked or hold
            else "normal_heating"
            if normal
            else "model_heating"
            if model_demand > 0.0
            else "model_coasting"
        )
        trv.warm_floor_policy_state = WarmFloorPolicyState()
    elif settings["warm_floor_level"] != "custom":
        if settings["warm_floor_level"] == "auto":
            profile = replace(
                profile, off_seconds=min(MAX_OFF_SECONDS, profile.off_seconds)
            )
        if (
            blocked
            or hold
            or host.kernel_state.control_mode.mode != ControlMode.OPTIMAL
        ):
            trv.warm_floor_policy_state = WarmFloorPolicyState()
            policy_reason = "suspended"
            if policy == "auto":
                policy = "level_2"
        else:
            policy_decision = advance_warm_floor_policy(
                thermal,
                trv.warm_floor_policy_state,
                _room_report(host, room),
                eligible=not normal,
            )
            trv.warm_floor_policy_state = policy_decision.state
            profile, policy, policy_reason = (
                policy_decision.profile,
                policy_decision.policy,
                policy_decision.reason,
            )
            settings = normalize_warm_floor(
                {**trv.advanced, "warm_floor_level": policy}
            )
            thermal = replace(thermal, max_backoff=settings["warm_floor_max_backoff"])
            slow_slope = policy_decision.slow_slope
            if not normal:
                prediction = predict_warm_floor(
                    thermal,
                    profile,
                    trv.warm_floor_state if generic else WarmFloorState(),
                    snapshot.now_monotonic,
                )
                profile = prediction.profile
                if prediction.advanced_start:
                    policy_reason = "anticipating"
    state = host.hass.states.get(entity_id)
    reported_action = state.attributes.get("hvac_action") if state is not None else None
    feedback = (
        True
        if reported_action == "heating"
        else False
        if reported_action in ("idle", "off")
        else None
    )
    decision = advance_warm_floor(
        WarmFloorObservation(
            snapshot.now_monotonic,
            room,
            snapshot.heat_target_temperature,
            profile.on_seconds,
            profile.off_seconds,
            blocked,
            hold,
            normal,
            feedback,
            model_demand,
        ),
        trv.warm_floor_state,
    )
    trv.warm_floor_state = decision.state
    trv.warm_floor_status = {
        "reason": decision.reason,
        "phase": decision.state.phase.value
        if decision.reason in ("startup", "maintenance")
        else None,
        "remaining_seconds": decision.remaining_seconds,
        "on_seconds": profile.on_seconds,
        "off_seconds": profile.off_seconds,
        "intensity": profile.intensity,
        "decision_level": policy,
        "decision_reason": policy_reason,
        "slow_trend_kelvin_per_min": slow_slope,
        # Retain the published key for existing templates. A shared EMA has
        # no independent raw-report observation window to report here.
        "trend_observation_span_seconds": None,
        "trend_source": "bt_temperature_ema_1h",
        "control_source": model_source or "bt_shared_thermal_estimates",
        "model_demand_percent": model_demand * 100.0
        if model_demand is not None
        else None,
        "forecast": {
            key: value for key, value in asdict(prediction).items() if key != "profile"
        }
        if prediction is not None
        else None,
        "reported_heating": feedback,
        "write_result": "not_requested",
        "requested_setpoint": None,
        "setpoint_write_result": "not_requested",
        "offset_write_result": "not_requested",
        "valve_write_result": "not_requested",
        "thermal_inputs": asdict(thermal),
    }
    if blocked or hold or normal:
        if (
            room >= ceiling
            and not hold
            and desired.suppression is None
            and desired.hvac_mode != HvacMode.OFF
        ):
            trv.warm_floor_status["reason"] = "temperature_ceiling"
            temperature = min(
                temperature
                if temperature is not None
                else snapshot.heat_target_temperature,
                snapshot.heat_target_temperature,
            )
        return temperature, calibration_offset
    sustaining = (
        snapshot.heat_target_temperature
        - settings["warm_floor_max_backoff"] * profile.structural_scale
    )
    if generic:
        step = max(0.01, finite_number(trv.target_temp_step, 0.5))
        measured = (
            trv.current_temperature if trv.current_temperature is not None else room
        )
        # Device and room sensors need not be the same sensor.
        if decision.maintenance_on:
            # Reserve headroom for the underlying thermostat's hysteresis as
            # well as its grid. It still owns the relay and may refuse the
            # request; only its reported action confirms heating.
            pulse = math.ceil((max(room, measured) + max(step, 0.5)) / step) * step
            temperature = min(
                ceiling,
                max(
                    pulse,
                    snapshot.heat_target_temperature
                    + settings["warm_floor_sustain_push"],
                ),
            )
            if temperature <= measured:
                trv.warm_floor_status["reason"] = "device_limit"
        else:
            # A Generic Thermostat's own hysteresis may retain heating at
            # exactly the target, so move one complete valid step below it.
            temperature = (
                math.floor(
                    (
                        min(room, measured, snapshot.heat_target_temperature)
                        - max(step, 0.5)
                    )
                    / step
                )
                * step
            )
    elif model_demand is not None:
        # Native setpoint, offset and valve channels already carry the same
        # predictive command. A maintenance floor would defeat model coasting.
        trv.warm_floor_status["phase"] = "model"
        trv.warm_floor_status["remaining_seconds"] = None
    else:
        trv.warm_floor_status["phase"] = "sustaining"
        trv.warm_floor_status["remaining_seconds"] = None
        if temperature is not None:
            temperature = max(temperature, sustaining)
            if temperature >= snapshot.heat_target_temperature:
                temperature = max(
                    temperature,
                    snapshot.heat_target_temperature
                    + settings["warm_floor_sustain_push"] * profile.structural_scale,
                )
        if calibration_offset is not None and trv.current_temperature is not None:
            offset_floor = sustaining
            if thermal.level == "auto":
                # Offsets actuate in the opposite direction to setpoints:
                # stronger maintenance lowers the temperature seen by the TRV.
                offset_floor = snapshot.heat_target_temperature - (
                    configured_backoff * profile.structural_scale * profile.intensity
                )
            calibration_offset = min(
                calibration_offset, offset_floor - trv.current_temperature
            )
        if balance is not None and balance.get("apply_valve"):
            balance["valve_percent"] = max(
                balance["valve_percent"], 5.0 + 15.0 * profile.intensity
            )
    trv.warm_floor_status["requested_setpoint"] = temperature
    trv.warm_floor_status["requested_calibration_offset"] = calibration_offset
    trv.warm_floor_status["requested_valve_percent"] = (
        balance["valve_percent"]
        if balance is not None and balance.get("apply_valve")
        else None
    )
    trv.warm_floor_status["setpoint_write_result"] = (
        "pending" if temperature is not None else "not_requested"
    )
    trv.warm_floor_status["offset_write_result"] = (
        "pending" if calibration_offset is not None else "not_requested"
    )
    trv.warm_floor_status["valve_write_result"] = (
        "pending"
        if balance is not None and balance.get("apply_valve")
        else "not_requested"
    )
    trv.warm_floor_status["write_result"] = "pending"
    return temperature, calibration_offset
