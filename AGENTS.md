# Repository workflow

- The development branch is `dev` in `rradkov/better_thermostat_UnderflowHeating`.
- Publish new changes to `dev` first. Promote to `main` only after user confirmation.
- The former separate development repository was consolidated into this `dev` branch.
- Keep all behavior neutral regarding heat source, hardware and installation.
- Prefer device capabilities and configuration over room-specific assumptions.
- Follow upstream `CONTRIBUTING.md` for architecture, naming and validation;
  this fork's branch policy takes precedence over upstream branch names.
