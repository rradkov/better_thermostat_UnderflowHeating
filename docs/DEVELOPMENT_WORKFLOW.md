# Development workflow

This repository is the public development copy of Better Thermostat Underflow Heating.
It is used to implement, test, and validate changes before they are proposed to the
production repository:

- Development: `rradkov/better_thermostat_UnderflowHeating-dev`
- Production: `rradkov/better_thermostat_UnderflowHeating`

## Rules

1. New work starts from the DEV repository and is developed on a feature branch.
2. Every non-trivial change must include focused tests and a clear commit message
   explaining what changed and why.
3. Home Assistant validation may use this public DEV repository, but no DEV commit
   is considered a production release. Test installations must be clearly identified
   as development installations.
4. Changes are merged into the DEV `main` only after code review and passing tests.
5. A production change is created by opening a pull request from DEV `main` to the
   production repository's `main`.
6. Production `main` is the only source for releases. Do not create a release from
   the DEV repository.
7. Every production release must document additions, changes, fixes, removals,
   compatibility or migration notes, and verification results.
8. Before changing project behavior, read and update the private maintainer decision
   log with the decision, reason, expected behavior, and verification result. Keep
   that log outside the public repository.
9. `climate.tatas` is an unrelated fan-coil entity in one installation and must not
   be used as a special case or modified as part of Warm Floor work.

## Suggested contribution sequence

```text
feature branch in DEV
        -> tests and local validation
        -> optional Home Assistant DEV validation
        -> PR into DEV main
        -> PR from DEV main into production main
        -> production verification
        -> documented release
```

## Home Assistant testing

Use the DEV repository only for controlled testing. Record the tested commit,
configuration, relevant entity histories, observed behavior, and any known
limitations in the private maintainer log or the corresponding PR. Never infer success
from a calculated controller decision alone: compare the Better Thermostat state,
downstream Generic Thermostat state, physical relay state, and floor-pump state.
