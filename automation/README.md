# NEXORA Development Framework (NDF)

An enterprise development automation framework for the NEXORA platform. NDF is a
self-contained **platform module** (peer of Authentication, Sync, Procurement) that
automates versioning, changelog/release generation, documentation governance, GitHub
synchronization, architecture tracking and project governance.

- **Runtime:** Python 3.12, **standard library only** (no third-party dependencies).
- **Independence:** lives entirely under `automation/`; never modifies application code.
- **Configuration-driven:** all paths, modules and mappings live in `ndf.config.toml`.

See the full design in [`docs/NDF_Technical_Architecture.md`](../docs/NDF_Technical_Architecture.md).

## Commands

```bash
# Version management (VERSION.md)
python -m automation.version bump minor
python -m automation.version show
python -m automation.version init

# Documentation index (docs/INDEX.md, ADR & rule indexes)
python -m automation.docs build

# Release (CHANGELOG.md + RELEASE_NOTES.md, optional tag)
python -m automation.release build
python -m automation.release build --tag

# GitHub sync (detect → group → commit → push, optional tag)
python -m automation.github sync
python -m automation.github sync --no-push        # commit only
python -m automation.github sync --tag

# Project dashboard (PROJECT_STATUS.md)
python -m automation.dashboard build

# Business rule registry (docs/BUSINESS_RULES/)
python -m automation.rules register --name "90 Day Average" --module Procurement --priority High
python -m automation.rules list

# Architecture Decision Records (docs/ADR/) — unified dispatcher
python -m automation adr new "Business Cycle"
python -m automation adr list

# AI documentation archive (docs/AI_ARCHIVE/)
python -m automation ai add --source Claude --title "Procurement FDD" --kind output
python -m automation ai approve AI-0001
python -m automation ai list

# Repository health (docs/REPORTS/repo_health.md)
python -m automation health report
```

The unified dispatcher `python -m automation <group> <action>` accepts every group
above; the individual `python -m automation.<group>` entry points are equivalent.

## Architecture (layers)

```
commands → services → builders / providers → core    (+ templates as data)
```

- **core** — config, paths, logging, typed models, results, exceptions, DI container.
- **providers** — filesystem, git, clock, environment (the only side effects).
- **services** — one capability each (version, changelog, release, github, docs, adr,
  rules, dashboard, ai archive, health).
- **builders / templates** — pure model→text rendering.
- **commands** — argv parsing + orchestration.

## Extending to a new vertical

Adding Inventory / Sales / Hospital / Garments / Manufacturing requires **configuration
only** (no code change):

1. Add it under `[[modules.items]]` → appears in the dashboard and health checks.
2. Add a prefix under `[rules.prefixes]` (e.g. `IN-BR`) → `rules register --module Inventory`.
3. Author docs with standard frontmatter → indexed by `docs build`.
