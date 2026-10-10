# Defence Studio — Component Registry

**Purpose:** a living catalogue of every reusable component we build, so any
component can be lifted into another project later with its documentation
intact. The index is [`REGISTRY.md`](REGISTRY.md).

This registry exists because of a standing project rule:

> **Every component we build is kept as ONE canonical copy with a README, and is
> registered here. Even small UI primitives (input box, date picker, etc.) are
> built once and reused — never re-implemented per page or per screen.**

## The "one copy" rule (single source of truth — no drift)

"Keep one copy" means exactly one canonical implementation of each component
inside this repository. We do **not** keep a second, parallel copy of the same
component in this repo — that would drift and contradicts the core-design rule
against duplicated/parallel trees (`DEFENCE_STUDIO_CORE_PROJECT_DESIGN.md` §12).

- The **canonical copy** lives at the component's normal home in the codebase
  (e.g. `backend/defence_studio/...`, or the frontend shared-component folder
  once a frontend exists).
- The registry **points to** that canonical copy and records how to reuse it.
- Reusing a component in **another project** = copy the canonical files out of
  here at a pinned version, and register that copy in *that* project's registry.
  It is a deliberate export, not a maintained second copy living alongside this
  one.

## Every component must carry a README

A registered component is only reusable if it is documented. The README is the
component's contract — what it does, its inputs/outputs, dependencies, and
limits — and it lives **with** the component (single source of truth):

- **Backend (Python):** the module/package docstring is the README, plus the
  relevant section of `backend/CONTRACTS.md` / `backend/WORKER_CONTRACT.md` for
  components that have a formal contract.
- **Multi-file or UI components:** a dedicated `README.md` in the component's
  own folder.

Do not duplicate README prose into a second file that will drift. The registry
links to the one README; it does not restate it.

## Portability classes

Each registry entry declares how self-contained it is, so a future reuse knows
what to carry:

| Class | Meaning | How to reuse |
|---|---|---|
| **STANDALONE** | Depends only on the stdlib / its framework. | Copy the single file; nothing else needed. |
| **BUNDLE** | Needs a few named sibling files, listed in the entry. | Copy the whole listed bundle together. |
| **COUPLED** | Needs project config, DB, or auth wiring. | Port behind an interface/adapter; expect adaptation, not a clean lift. |

A component is only **portable** if it has no app-specific imports. Utilities
like the SSRF URL guard and the path-safe artifact store are deliberately
STANDALONE for this reason and are the best cross-project candidates.

## When you add or change a component

1. Give it a clear docstring/README (purpose, I/O, deps, limits).
2. Add or update its row in [`REGISTRY.md`](REGISTRY.md): name, kind, canonical
   path, portability class, external deps, README/contract reference, reuse
   status, and contract version.
3. If it is a new UI primitive, create it once as a self-contained component and
   import it everywhere — do not hand-roll another input/date control.
4. Keep the entry honest: mark `PLANNED` / `PARTIAL` / `VERIFIED` truthfully and
   never claim a component is reusable before its README and tests exist.
