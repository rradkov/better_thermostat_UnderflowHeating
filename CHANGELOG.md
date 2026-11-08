# Changelog

## Unreleased

### Fixed

- Anchor each Generic Thermostat Warm Floor phase to its own start time.
  Recalculating the duty profile no longer shifts the entire cycle; a stronger
  maintenance profile can shorten a running pause while preserving actuator
  travel time.
- Release the downstream setpoint below the room target during OFF phases,
  including exact-target conditions and configured sustaining pushes, so an
  already-heating Generic Thermostat can actually stop.
- Report the remaining time for the full Warm Floor phase instead of only
  its minimum actuator travel interval.

## 1.9.3.1.7 — 2026-11-05

### Fixed

- Fix Child Lock restoration on Home Assistant 2026.11 so Better Thermostat
  entities can start successfully. Retain compatibility with older supported
  Home Assistant versions; backport upstream PR #2858 and its regression tests.

### Changed

- Extend Warm Floor maintenance pauses according to the room's excess above
  target and measured warming trends, retaining the four-minute minimum pulse.

See the [release notes](docs/releases/1.9.3.1.7.md) for compatibility, upgrade
instructions, and verification results.

## 1.9.3.1.6

See the [Warm Floor release notes](docs/releases/1.9.3.1.6.md).
