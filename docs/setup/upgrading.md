---
title: Upgrade and migration
description: Install the official Warm Floor release and understand its compatibility changes.
---

## Official release and development

The repository is `rradkov/better_thermostat_UnderflowHeating`.
Use the latest official release for production. Development uses its `dev`
branch; confirmed releases come from `main`. The former separate repository
ending in `-dev` is no longer the development destination.

## Requirements

Release **1.9.3.1.6** requires **Home Assistant 2026.9.0 or later** and the Python
runtime supported by that release. Check compatibility before updating.

This fork and upstream Better Thermostat use the same `better_thermostat`
integration domain. Install one implementation at a time.

## Update through HACS

1. Keep a backup of the current Home Assistant configuration.
2. Add `https://github.com/rradkov/better_thermostat_UnderflowHeating` as a HACS
   custom repository with category **Integration**, if it is not already present.
3. Select the official release and download the update.
4. Restart Home Assistant so the new integration and service registration load.
5. Check the integration version, room targets, downstream devices, and Warm
   Floor Status diagnostics after startup settles.

For a manual update, replace the `custom_components/better_thermostat` directory
with the matching directory from the official release, then restart Home Assistant.
Do not extract the entire repository into the integration directory.

## Warm Floor configuration

Existing room entries and Warm Floor selections are retained. Enable Underfloor
Heating and select the underfloor type only for the relevant emitters. Optional
outdoor and water-temperature inputs remain optional.

Maintenance is eligible **at or above** the room target; below target, normal
heating owns the actuator. Slow Generic Thermostat actuator phases use an internal
four-minute minimum ON and OFF interval, with safety and normal-heating overrides.
The interval is not a configurable setting in this release.

## Secondary-device Boost suspension

The experimental post-window recovery feature is unavailable in this official
release. Its setup fields, test service, status sensor, window hooks, and control
routing are disabled, including for previously enabled configurations.

Disable or revise automations that call `better_thermostat.test_boost` or rely on
its diagnostic entity. The integration no longer creates that status sensor;
old entity references should not be used as a production control signal.
Normal secondary-device cooling and the standard **Boost temperature preset**
remain available. Further secondary-device Boost work belongs on `dev`.

## Verification and rollback

Compare Better Thermostat decisions with the downstream climate state and actual
actuator behavior. An `active` diagnostic alone does not prove water flow, pump
operation, floor temperature, or energy savings.

If an update causes a regression, select the previously used release in HACS,
download it, and restart Home Assistant. Preserve diagnostic information and
report the affected version and device capabilities without publishing credentials.

See the [release notes](../releases/1.9.3.1.6.md),
[configuration guide](../Configuration/configuration.md), and
[development workflow](../DEVELOPMENT_WORKFLOW.md).
