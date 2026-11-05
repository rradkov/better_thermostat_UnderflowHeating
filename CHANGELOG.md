# Changelog

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
