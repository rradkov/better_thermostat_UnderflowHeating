# Warm Floor port to upstream 2.0.0 — in progress

This branch is **not installable as the fork upgrade yet**. It starts at
upstream tag `2.0.0` (`aed2036b`) and targets the fork's `dev`, never `main`.
The behavior to port is from fork `dev` at `3967b2a9` (1.9.3.1.8).

## Completed

- Isolated worktree and branch `codex/warm-floor-v2`.
- Pure per-circuit phase state and explicit time/observation input in
  `core/warm_floor.py`, with no Home Assistant imports or device writes.
- Ten phase requirements: sparse reports, mid-phase updates, above-target
  pulses, four-minute minimums, blocks, hold, normal-heating handover,
  recovery, unknown feedback, and independent circuits.
- Ten checks passed using direct execution of the pure module and test bodies;
  this was not a pytest/Home Assistant integration run.
- Ruff check/format and Pyrefly pass for the new module and focused tests.

## Required before publishing to dev

- Port thermal demand, Auto/1/2/3/Custom profiles, backoff/push, optional
  water/outdoor inputs and offset/valve semantics from the source dev.
- Connect phase decisions to upstream snapshots/intents, safety hull,
  paced device writes and periodic scheduling. Currently the phase module is
  standalone and has no effect on runtime control.
- Preserve Warm Floor settings through upstream's typed entry parser and
  config/options flows. Remove legacy secondary Boost settings/entities.
- Port per-circuit planned/commanded/reported diagnostics with truthful
  command results, restart recovery and stable user-facing targets.
- Preserve sparse valid readings without an age-only block; actual unavailable
  readings follow upstream fallback/hold. Never fabricate measurements.
- Keep upstream Boost preset and normal cooler support. Do not restore the
  fork's secondary-device Boost code, service, UI or hooks.
- Update translated UI, documentation, metadata and dev/main CI triggers.
- Run upstream tests, Warm Floor scenarios, migration and device integration
  tests in Linux and a test Home Assistant instance before promotion.
- Retain fork dev history by merging it into the completed migration branch
  with the upstream-based final tree, then publish to dev without force-pushing.
  Inspect both parents and the complete diff before that merge.

## Local validation environment

`uv sync --frozen --python C:\Python314\python.exe` succeeded with Python
3.14.7 and locked Home Assistant 2026.11.1. The repository's `.python-version`
requests 3.14.10, unavailable to the host's current uv interpreter discovery;
an explicit supported interpreter was used without editing the pin.

Normal pytest fails before collection importing `homeassistant.runner`:
`ModuleNotFoundError: No module named 'fcntl'`. No WSL distributions or Docker
were found. Do not replace/mock fcntl or disable HA tests to claim validation.

No changes have been published to dev/main and no live HA configuration was
modified by this migration work.
