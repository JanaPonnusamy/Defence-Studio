# Voice Lab environment check (read-only; prints JSON, never prints secrets).
$root = Split-Path -Parent $PSScriptRoot
Push-Location (Join-Path $root "backend")
try { python scripts\voice_lab_env_check.py } finally { Pop-Location }
