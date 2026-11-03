# Start the ESIC Compliance Audit module on this computer only (127.0.0.1).
# The access token is printed below and kept in storage\access_token.txt (git-ignored).
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
if (-not (Test-Path ".venv\Scripts\python.exe")) { throw "Create the venv first: see README.md (Install)." }
$port = if ($env:ESIC_AUDIT_PORT) { $env:ESIC_AUDIT_PORT } else { "8765" }
& .venv\Scripts\python -c "from app.config import load_settings; s = load_settings(); print('Access token file:', s.storage_dir / 'access_token.txt'); print('Portal:', s.login_url)"
Write-Host "Open http://127.0.0.1:$port/  (Ctrl+C to stop)"
& .venv\Scripts\python -m uvicorn --factory app.main:app_factory --host 127.0.0.1 --port $port
