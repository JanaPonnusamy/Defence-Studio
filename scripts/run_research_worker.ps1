# Defence Studio — research queue consumer (Windows PowerShell)
# =====================================================================
# Loads backend\.env.local, then starts the existing durable research worker
# with the portable in-repo database/storage defaults. Run from any directory:
#   powershell -ExecutionPolicy Bypass -File scripts\run_research_worker.ps1
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
$env:DEFENCE_STUDIO_STARTUP_DIAGNOSTICS = "1"

# Prefer the repo venv, but only if that interpreter actually starts. A venv
# created under another Windows account has a python.exe that exists and
# still fails (base interpreter path denied).
function Test-Python([string]$Candidate) {
    if ($Candidate -like "*\python.exe" -and -not (Test-Path -LiteralPath $Candidate)) { return $false }
    & $Candidate -c "import sys" 1>$null 2>$null
    return ($LASTEXITCODE -eq 0)
}
$py = Join-Path $root "backend\.venv\Scripts\python.exe"
if (-not (Test-Python $py)) {
    if (Test-Python "py") { $py = "py" }
    elseif (Test-Python "python") { $py = "python" }
    else {
        Write-Error "No working Python interpreter. Recreate backend\.venv with: py -3 -m venv backend\.venv"
        exit 1
    }
    Write-Warning "backend\.venv Python is missing or unusable on this account. Using '$py'."
}

Push-Location (Join-Path $root "backend")
try {
    & $py -u -m defence_studio.research_worker
} finally {
    Pop-Location
}
