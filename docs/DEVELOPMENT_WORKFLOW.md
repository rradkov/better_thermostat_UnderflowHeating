# Development workflow

Development and production share one repository:
`rradkov/better_thermostat_UnderflowHeating`.

- `dev` contains development work and controlled Home Assistant validation.
- `main` contains confirmed production changes and is the source for releases.
- The former `better_thermostat_UnderflowHeating-dev` repository is consolidated
  into `dev` and is no longer the development destination.

## Rules

1. Commit and publish every new change to `dev` first.
2. Include focused tests for behavior changes and a clear commit message.
3. Promote changes to `main` only after validation and user confirmation.
4. Create official releases from the confirmed `main` commit. Keep the manifest
   and project versions synchronized and document changes, compatibility,
   migration notes, and verification results.
5. Keep maintainer decision logs and installation-specific observations outside
   the public repository. Never publish local test checkouts or credentials.
6. Keep behavior neutral regarding heat sources and installations. Select devices
   and capabilities through configuration rather than installation-specific IDs.

## Home Assistant testing

Record the tested commit, configuration, observed behavior, and limitations in
the private maintainer log. Compare calculated decisions with actual downstream
device behavior; a calculated demand alone does not prove successful heating.

## Optional secondary-device Boost

The post-window secondary-device Boost is experimental and must remain suspended
in the official Warm Floor release. Its development implementation is retained
for work on `dev`. Production suspension must cover configuration, saved enabled
settings, the manual test service, status sensor, window events, and control-loop
routing. The existing temperature preset named Boost is a separate feature.
