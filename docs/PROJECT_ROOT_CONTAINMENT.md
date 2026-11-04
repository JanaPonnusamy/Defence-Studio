# Project root containment

Permanent rule for Defence Era. The canonical project root is the Git
repository that contains this file. On this workstation that directory is
`D:\jana soft\DefenceEra`. Code must not hardcode that drive letter. The
resolver in `tools/project_paths.py` finds the root from its own location.

## What stays inside the root

Defence Era-owned source, applications, virtual environments where practical,
dependency lockfiles, project-owned models and model configuration, agent
workspaces and handoff notes, task artifacts, logs, temporary development
files, build output, test databases and fixtures, generated media, downloaded
project resources, scripts, documentation, backups, and configuration
templates.

## What stays outside

Windows, Python, Git, Node, drivers, system services, FFmpeg, Chrome, and
other third-party installations stay where their installers put them. Their
project-specific configuration, scripts, data, logs, and generated files stay
inside the root. Cursor, Claude, and Codex store their own host metadata in
the user profile. That metadata is an external tool dependency and is not
copied into the repository.

## Directory map

Existing directories are reused. Empty duplicates of those directories are
not created.

| Role | Location |
|---|---|
| Web application | `frontend/` |
| Standalone applications | `standalone/` |
| Backend | `backend/` |
| Shared components | `components/` |
| Agent workspaces and evidence | `AGENT_WORK/` |
| Models | `models/` |
| Databases, storage, backups | `data/` |
| Generated review media | `analysis/` |
| Logs (rotated, gitignored) | `logs/` |
| Task temp and pytest temp (gitignored) | `tmp/runtime/`, `tmp/pytest/` |
| Tests | each module's `tests/` |
| Launchers | `scripts/` |
| Path resolver | `tools/project_paths.py` |
| Documentation | `docs/` |
| App configuration | `backend/config/` |

There is no desktop application yet, so `apps/desktop` is not created.

## Runtime paths

`tools/project_paths.py` is the only resolver. `backend/defence_studio/project_paths.py`
re-exports it. `database_path()` and `storage_root()` in
`backend/defence_studio/config.py` reject a database or storage path that
resolves outside the root.

Use `temporary_directory(prefix)` or `task_directory(prefix)` for project
temp. Each call creates a unique directory under `tmp/runtime/`. Cleanup
deletes that directory only. A task must not delete `tmp/`, `tmp/runtime/`,
or another task's directory.

Pytest session databases for the backend, audio module, and visual module
use `tmp/pytest/<unique>/`. Passing `--basetemp` to an outside directory is
rejected.

When `DEFENCE_STUDIO_STARTUP_DIAGNOSTICS=1` (set by `scripts/run_dev.ps1` and
`scripts/run_research_worker.ps1`), the process attaches a rotating log at
`logs/defence_studio.log` (5 MB, 5 backups).

A file written beside an artifact with `tempfile` `dir=` set to that
artifact's parent is allowed only because the parent is already inside the
root. `tempfile.gettempdir()`, `%TEMP%`, and `%LOCALAPPDATA%` are not.

## Git

The repository boundary is the project root. Track source, scripts,
documentation, required configuration, and reproducible fixtures. Do not
track virtual environments, `tmp/`, `logs/`, secrets, local databases, model
weights, or large generated media. `.gitignore` already ignores `data/*`
databases and `tmp/`. `logs/` is ignored by this policy.

Do not add a symlink or junction that points at a project dependency outside
the root. System tools may live outside; the project does not disguise that
with a link.

`backend/config/root_containment_allowlist.txt` lists historical source files
whose text still names a pre-migration workspace. It is not a list of write
locations.

## Checks

`python scripts/check_root_containment.py` fails when product source calls an
uncontrolled temp directory, hardcodes a non-system absolute path, or contains
a symlink or junction that leaves the root. The check skips `tests/`,
`AGENT_WORK/` (historical evidence), virtual environments, and
`site-packages`. GitHub Actions runs it from
`.github/workflows/root-containment.yml`.

`python scripts/inventory_external_resources.py` rewrites
`docs/root_containment/EXTERNAL_RESOURCE_INVENTORY.md`. It does not move files.

## Migration

Do not bulk-move. Inventory first. Move one reviewed group at a time. Copy,
compare SHA-256, update references, start the affected application, and only
then delete the original. Never move Windows or another application's files,
and never commit secrets.

The 2026-10-14 component consolidation already copied
`E:\DefenceStudio-Components\asset-rights-engine` and
`E:\Jana Soft\defence-align` into `components/` and removed those originals
after a byte compare. This policy does not repeat that copy.

## Legitimate external dependencies

| Dependency | Why it stays outside |
|---|---|
| Windows, fonts under `%WINDIR%\Fonts` | Operating system |
| Python, Git, Node | Language and version-control tools |
| FFmpeg / FFprobe | Third-party media tool, named by `RENDERER_FFMPEG` or `PATH` |
| Google Chrome | Browser used by scripted checks |
| Cursor, Claude, Codex host data | IDE metadata, not project source |
| OpenAI, YouTube, yt-dlp network services | Remote services, not local trees |

Project configuration for those tools stays in this repository.
