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
    WarmFloorThermalInput,
    advance_warm_floor,
    compute_warm_floor_profile,
)
from .calibration.mpc import build_mpc_group_key, build_mpc_key
from .helpers import convert_to_float_celsius
from .underfloor import finite_number, normalize_warm_floor

if TYPE_CHECKING:
    from ..climate import BetterThermostat
    from ..trv import Trv


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
        long_average=trv.warm_floor_long_average,
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
    # One-hour baseline follows held readings in elapsed time; it is an
    # estimate, never a fabricated new room-sensor report.
    previous_time = trv.warm_floor_average_monotonic
    if trv.warm_floor_long_average is None:
        trv.warm_floor_long_average = room
    elif previous_time is not None and not hold:
        alpha = 1.0 - math.exp(
            -max(0.0, snapshot.now_monotonic - previous_time) / 3600.0
        )
        trv.warm_floor_long_average += alpha * (room - trv.warm_floor_long_average)
    trv.warm_floor_average_monotonic = snapshot.now_monotonic
    profile = compute_warm_floor_profile(thermal)
    ceiling = min(snapshot.heat_target_temperature + 5.0, trv.max_temp)
    blocked = blocked or room >= ceiling
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
    generic = any(
        item["trv"] == entity_id and item["integration"] == "generic_thermostat"
        for item in host.all_trvs
    )
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
            calibration_offset = min(
                calibration_offset, sustaining - trv.current_temperature
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
