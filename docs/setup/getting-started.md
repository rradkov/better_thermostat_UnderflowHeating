---
title: Getting started
sidebar: 
    order: 1
description: Install Better Thermostat and create your first room in a few minutes.
---

Installing Better Thermostat and setting up your first room takes a few minutes.

## 1) Install the integration

This fork requires **Home Assistant 2026.9.0 or later**. In HACS, add
`https://github.com/rradkov/better_thermostat_UnderflowHeating` as a custom repository
with category **Integration**, then install Better Thermostat. Use an official
release for production; `dev` is for controlled development testing.

This fork and upstream share the same integration domain. Keep one implementation
installed; see [Upgrade and migration](upgrading.md) when replacing an installation.

<a href="https://my.home-assistant.io/redirect/hacs_repository/?owner=rradkov&amp;repository=better_thermostat_UnderflowHeating&amp;category=integration"><img alt="Open HACS repository" src="https://my.home-assistant.io/badges/hacs_repository.svg" /></a>

After installation, remember to restart Home Assistant.

## 2) Add Better Thermostat

Go to **Settings** → **Devices & Services** → **Integrations** → **Add Integration** → search for **Better Thermostat**.

Or click this button to start directly:

<a href="https://my.home-assistant.io/redirect/config_flow_start/?domain=better_thermostat" target="_blank"><img src="https://my.home-assistant.io/badges/config_flow_start.svg" alt="Start Better Thermostat configuration flow"/></a>

## 3) Gather your devices

Before setting up a room, make sure you have these devices ready in Home Assistant:

- **A supported climate heater** (required), such as a radiator thermostat or a Generic Thermostat controlling an underfloor relay
- **A separate room temperature sensor** (required: the sensor inside the radiator valve sits too close to the heat source to read the room, and the setup form will not let you finish without one)
- *Optional:* A window sensor (to automatically pause heating when you air out the room)
- *Optional:* A weather integration or outdoor temperature sensor (to stop heating when it's warm outside)

## 4) Follow the configuration walkthrough

The [Configuration walkthrough](/setup/configuration-walkthrough/) goes through every option in the setup form.

## 5) Apply recommended defaults

If you are unsure what to pick, start from [Recommended settings](/optimal-settings/recommended-settings/) and adjust from there.

## Underfloor heating

Enable Underfloor Heating and select the relevant heater type and Warm Floor
level. See the [configuration guide](../Configuration/configuration.md#underfloor-heating-warm-floor-mode).
Secondary-device Boost is unavailable in official release `1.9.3.1.7`; the standard
Boost temperature preset remains available.
