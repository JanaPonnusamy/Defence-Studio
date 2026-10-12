# Defence Studio — local development launcher (Windows PowerShell)
# =====================================================================
# Loads backend\.env.local, then starts the FastAPI app on 127.0.0.1:8011
# with the portable in-repo database/storage defaults. Run from any directory:
#   powershell -ExecutionPolicy Bypass -File scripts\run_dev.ps1
# =====================================================================

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$envFile = Join-Path $root "backend\.env.local"

if (-not (Test-Path $envFile)) {
    Write-Error "Missing backend\.env.local. Copy .env.template to backend\.env.local and set DEFENCE_STUDIO_AUTH_SECRET first."
    exit 1
}

# Load KEY=VALUE lines from .env.local into the process environment.
Get-Content $envFile | ForEach-Object {
    $line = $_.Trim()
    if ($line -and -not $line.StartsWith("#") -and $line.Contains("=")) {
        $idx = $line.IndexOf("=")
        $key = $line.Substring(0, $idx).Trim()
        $val = $line.Substring($idx + 1).Trim()
        Set-Item -Path "Env:$key" -Value $val
    }
}

if (-not $env:DEFENCE_STUDIO_AUTH_SECRET) {
    Write-Error "DEFENCE_STUDIO_AUTH_SECRET is not set in backend\.env.local."
    exit 1
}

# Portable defaults (config.py resolves these relative to the project root when unset).
if (-not $env:DEFENCE_STUDIO_DB_PATH)      { $env:DEFENCE_STUDIO_DB_PATH      = Join-Path $root "data\defence_studio.sqlite3" }
if (-not $env:DEFENCE_STUDIO_STORAGE_PATH) { $env:DEFENCE_STUDIO_STORAGE_PATH = Join-Path $root "data\storage" }

# Pick a Python: prefer a local venv, else whatever "python" resolves to.
$py = Join-Path $root "backend\.venv\Scripts\python.exe"
if (-not (Test-Path $py)) { $py = "python" }

Push-Location (Join-Path $root "backend")
try {
    & $py -m uvicorn defence_studio.api.app:app --host 127.0.0.1 --port 8011
} finally {
    Pop-Location
}
