#!/usr/bin/env bash
# Defence Studio — local development launcher (Git Bash / POSIX)
# =====================================================================
# Loads backend/.env.local, then starts the FastAPI app on 127.0.0.1:8011
# with the portable in-repo database/storage defaults.
#   bash scripts/run_dev.sh
# =====================================================================
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
env_file="$root/backend/.env.local"

if [[ ! -f "$env_file" ]]; then
  echo "Missing backend/.env.local. Copy .env.template to backend/.env.local and set DEFENCE_STUDIO_AUTH_SECRET first." >&2
  exit 1
fi

# Load KEY=VALUE lines (ignore comments/blank lines).
set -a
# shellcheck disable=SC1090
while IFS= read -r line; do
  [[ -z "$line" || "$line" == \#* || "$line" != *=* ]] && continue
  export "$line"
done < "$env_file"
set +a

if [[ -z "${DEFENCE_STUDIO_AUTH_SECRET:-}" ]]; then
  echo "DEFENCE_STUDIO_AUTH_SECRET is not set in backend/.env.local." >&2
  exit 1
fi

export DEFENCE_STUDIO_DB_PATH="${DEFENCE_STUDIO_DB_PATH:-$root/data/defence_studio.sqlite3}"
export DEFENCE_STUDIO_STORAGE_PATH="${DEFENCE_STUDIO_STORAGE_PATH:-$root/data/storage}"
# Local jobs need a consumer. Set this to 0 when using the separate worker.
export DEFENCE_STUDIO_RESEARCH_WORKER="${DEFENCE_STUDIO_RESEARCH_WORKER:-1}"

py="$root/backend/.venv/Scripts/python.exe"
[[ -x "$py" ]] || py="python"

cd "$root/backend"
exec "$py" -m uvicorn defence_studio.api.app:app --host 127.0.0.1 --port 8011
