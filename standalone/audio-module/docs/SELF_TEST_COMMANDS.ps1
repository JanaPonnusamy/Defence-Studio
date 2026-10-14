<#
.SYNOPSIS
  Repeatable, safe admin/self-test pass for the standalone Voice Audio Lab / Audio Module.

.DESCRIPTION
  Mirrors the checks performed manually and documented in ADMIN_SELF_TEST_REPORT.md:
    - reports (never requires or requests) administrator privilege
    - checks Python/venv/deps, audio devices, ffmpeg
    - creates/validates the venv and installs requirements.txt
    - regenerates the test fixture and runs the full pytest suite
    - starts the FastAPI app on a free port, hits /health, /docs, /ui/
    - runs a real end-to-end API pipeline smoke test (upload -> process -> profile -> plan -> TTS x2)
    - shuts the test server down cleanly and frees the port
    - logs everything to storage/logs/self_test_<timestamp>.log
    - exits 0 on full success, nonzero on any critical failure

  Does NOT self-elevate, does NOT bypass UAC/any Windows prompt, does NOT kill any process other
  than the uvicorn instance this script itself started on its own chosen port, does NOT run git
  commands, does NOT touch anything outside this module's own folder.

.NOTES
  Run from a normal (non-elevated) PowerShell. Safe to re-run any number of times.
#>

[CmdletBinding()]
param(
    [int]$Port = 8030
)

$ErrorActionPreference = "Stop"
$ScriptRoot = Split-Path -Parent $PSScriptRoot  # .../audio-module (docs/ is one level down)
Set-Location $ScriptRoot

$timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
$logDir = Join-Path $ScriptRoot "storage\logs"
if (-not (Test-Path $logDir)) { New-Item -ItemType Directory -Path $logDir -Force | Out-Null }
$logFile = Join-Path $logDir "self_test_$timestamp.log"

$script:CriticalFailure = $false
$script:ServerProc = $null

function Write-Log {
    param([string]$Message, [string]$Level = "INFO")
    $line = "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') | $Level | $Message"
    Write-Host $line
    Add-Content -Path $logFile -Value $line
}

function Invoke-Check {
    param([string]$Name, [scriptblock]$Action, [bool]$Critical = $true)
    Write-Log "CHECK START: $Name"
    try {
        & $Action
        Write-Log "CHECK OK: $Name"
        return $true
    } catch {
        Write-Log "CHECK FAILED: $Name :: $($_.Exception.Message)" "ERROR"
        if ($Critical) { $script:CriticalFailure = $true }
        return $false
    }
}

Write-Log "=== Voice Audio Lab / Audio Module self-test starting ==="
Write-Log "Project root: $ScriptRoot"

# 1. Administrator privilege check (report only -- never elevate, never bypass)
$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
    [Security.Principal.WindowsBuiltInRole]::Administrator)
Write-Log "Administrator (elevated) shell: $isAdmin"
if (-not $isAdmin) {
    Write-Log "Not elevated. To open an elevated PowerShell manually: Start-Process powershell -Verb RunAs"
    Write-Log "Proceeding non-elevated -- this module does not require admin rights on Windows."
}

# 2. Environment checks (non-critical individually; informational)
Invoke-Check "Python available" { python --version | Out-Null } $false
Invoke-Check "ffmpeg on PATH" {
    $ff = Get-Command ffmpeg -ErrorAction SilentlyContinue
    if (-not $ff) { Write-Log "ffmpeg not found on PATH (not required by this module, but noted)" "WARN" }
} $false
Invoke-Check "Audio output device present" {
    $devices = Get-PnpDevice -Class AudioEndpoint -Status OK -ErrorAction SilentlyContinue
    if (-not $devices) { Write-Log "No active audio endpoint devices found" "WARN" }
    else { Write-Log "Audio devices: $($devices.FriendlyName -join ', ')" }
} $false

# 3. venv + dependencies
$venvPython = Join-Path $ScriptRoot ".venv\Scripts\python.exe"
Invoke-Check "venv exists or created" {
    if (-not (Test-Path $venvPython)) {
        Write-Log "Creating venv..."
        python -m venv (Join-Path $ScriptRoot ".venv")
    }
    if (-not (Test-Path $venvPython)) { throw "venv python not found after creation attempt" }
}

Invoke-Check "pip install -r requirements.txt" {
    & $venvPython -m pip install --upgrade pip --quiet
    & $venvPython -m pip install -r (Join-Path $ScriptRoot "requirements.txt") --quiet
    if ($LASTEXITCODE -ne 0) { throw "pip install exited with code $LASTEXITCODE" }
}

Invoke-Check "installed package versions logged" {
    $frozen = & $venvPython -m pip freeze
    $frozen | Where-Object { $_ -match "fastapi|uvicorn|sqlalchemy|pydantic|numpy|scipy|librosa|soundfile|pyttsx3|pytest" } |
        ForEach-Object { Write-Log "  $_" }
} $false

# 4. Fixture generation
Invoke-Check "generate test fixture" {
    & $venvPython (Join-Path $ScriptRoot "fixtures\generate_fixture.py")
    if ($LASTEXITCODE -ne 0) { throw "fixture generation exited with code $LASTEXITCODE" }
}

# 5. Full pytest suite
Invoke-Check "pytest full suite" {
    & $venvPython -m pytest -q 2>&1 | Tee-Object -Variable pytestOutput | ForEach-Object { Write-Log "  $_" }
    if ($LASTEXITCODE -ne 0) { throw "pytest exited with code $LASTEXITCODE (see log for failing tests)" }
}

# 6. Start the server on the requested port (default 8030), confirm it's free first
Invoke-Check "start server on port $Port" {
    $existing = Get-NetTCPConnection -LocalPort $Port -ErrorAction SilentlyContinue
    if ($existing) {
        Write-Log "Port $Port is busy (PID $($existing[0].OwningProcess)) -- choose a different -Port" "ERROR"
        throw "Port $Port already in use"
    }
    $serverLog = Join-Path $logDir "self_test_server_$timestamp.log"
    $script:ServerProc = Start-Process -FilePath $venvPython `
        -ArgumentList "-m", "uvicorn", "app.main:app", "--port", "$Port" `
        -RedirectStandardOutput $serverLog -RedirectStandardError "$serverLog.err" `
        -WorkingDirectory $ScriptRoot -PassThru -WindowStyle Hidden
    Start-Sleep -Seconds 3
    $health = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/health" -TimeoutSec 10
    if ($health.status -ne "ok") { throw "Health check did not report ok: $health" }
    Write-Log "Server healthy: $($health | ConvertTo-Json -Compress)"
}

if (-not $script:CriticalFailure) {
    Invoke-Check "/docs reachable" {
        $resp = Invoke-WebRequest -Uri "http://127.0.0.1:$Port/docs" -UseBasicParsing -TimeoutSec 10
        if ($resp.StatusCode -ne 200) { throw "/docs returned $($resp.StatusCode)" }
    }
    Invoke-Check "/ui/ reachable" {
        $resp = Invoke-WebRequest -Uri "http://127.0.0.1:$Port/ui/" -UseBasicParsing -TimeoutSec 10
        if ($resp.StatusCode -ne 200) { throw "/ui/ returned $($resp.StatusCode)" }
    }

    # 7. Real end-to-end pipeline smoke test via the live API
    Invoke-Check "end-to-end pipeline smoke test" {
        $fixtureWav = Join-Path $ScriptRoot "fixtures\test_fixture_speech_like.wav"
        $fixtureTxtPath = Join-Path $ScriptRoot "fixtures\test_fixture_transcript.txt"

        # Windows PowerShell 5.1's Invoke-RestMethod has no -Form parameter
        # (that is PowerShell 6+ only), so multipart upload is done with the
        # curl.exe shipped in-box on Windows 10/11 (C:\Windows\System32\curl.exe)
        # instead -- no third-party tool required. JSON values are written to
        # temp files and read with curl's "@file"/"<file" syntax rather than
        # embedded inline, because PowerShell's native-argument marshalling
        # can mangle embedded double quotes/braces passed as inline strings
        # (found and worked around during this self-test).
        $curl = (Get-Command curl.exe -ErrorAction Stop).Source
        $rightsFile = Join-Path $env:TEMP "self_test_rights_$timestamp.json"
        '{"authorized": true, "notes": "self-test script"}' | Set-Content -Path $rightsFile -Encoding ascii -NoNewline
        try {
            $refJson = & $curl -s -X POST "http://127.0.0.1:$Port/references" `
                -F "title=SelfTestScript Reference" `
                -F "language=en" `
                -F "rights_metadata=<$rightsFile" `
                -F "transcript_format=plain" `
                -F "transcript_text=<$fixtureTxtPath" `
                -F "audio_file=@$fixtureWav;type=audio/wav"
            if ($LASTEXITCODE -ne 0) { throw "curl upload failed with exit code $LASTEXITCODE" }
        } finally {
            Remove-Item -Path $rightsFile -Force -ErrorAction SilentlyContinue
        }
        $ref = $refJson | ConvertFrom-Json
        if (-not $ref.reference_id) { throw "reference creation did not return a reference_id: $refJson" }
        Write-Log "  reference created: $($ref.reference_id)"

        $job = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/references/$($ref.reference_id)/process" -Method Post -TimeoutSec 10
        $deadline = (Get-Date).AddSeconds(60)
        do {
            Start-Sleep -Milliseconds 500
            $jobStatus = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/jobs/$($job.job_id)" -TimeoutSec 10
        } while ($jobStatus.status -notin @("SUCCEEDED", "FAILED") -and (Get-Date) -lt $deadline)
        if ($jobStatus.status -ne "SUCCEEDED") { throw "process job did not succeed: $($jobStatus | ConvertTo-Json -Compress)" }
        Write-Log "  process job succeeded: alignment/analysis populated"

        $refFinal = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/references/$($ref.reference_id)" -TimeoutSec 10
        if (-not $refFinal.alignment_id -or -not $refFinal.analysis_id) {
            throw "alignment_id/analysis_id not populated on reference after SUCCEEDED job"
        }

        $profileBody = @{ profile_name = "SelfTestScript Profile"; analysis_ids = @($refFinal.analysis_id) } | ConvertTo-Json
        $profileJob = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/profiles/build" -Method Post -Body $profileBody -ContentType "application/json" -TimeoutSec 10
        Start-Sleep -Seconds 2
        $profileJobStatus = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/jobs/$($profileJob.job_id)" -TimeoutSec 10
        if ($profileJobStatus.status -ne "SUCCEEDED") { throw "profile build job did not succeed" }
        Write-Log "  profile build succeeded: $($profileJobStatus.result_id)"

        foreach ($provider in @("mock-tts", "local-pyttsx3")) {
            $ttsBody = @{ text = "Self test synthesis check."; provider = $provider } | ConvertTo-Json
            $ttsJob = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/tts/synthesize" -Method Post -Body $ttsBody -ContentType "application/json" -TimeoutSec 10
            Start-Sleep -Seconds 2
            $ttsJobStatus = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/jobs/$($ttsJob.job_id)" -TimeoutSec 10
            if ($ttsJobStatus.status -ne "SUCCEEDED") { throw "$provider synthesis job did not succeed" }
            $generated = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/generated/$($ttsJobStatus.result_id)" -TimeoutSec 10
            $expectedMock = ($provider -eq "mock-tts")
            if ($generated.provider_metadata.is_mock -ne $expectedMock) {
                throw "$provider reported is_mock=$($generated.provider_metadata.is_mock), expected $expectedMock"
            }
            Write-Log "  $provider synthesis OK (is_mock=$($generated.provider_metadata.is_mock), validation=$($generated.validation_status))"
        }
    }
}

# 8. Clean shutdown
Invoke-Check "clean shutdown" {
    if ($script:ServerProc -and -not $script:ServerProc.HasExited) {
        Stop-Process -Id $script:ServerProc.Id -Force -ErrorAction SilentlyContinue
        Start-Sleep -Seconds 1
        $stillUp = Get-NetTCPConnection -LocalPort $Port -ErrorAction SilentlyContinue
        if ($stillUp) { throw "Port $Port still bound after shutdown attempt" }
    }
} $false

Write-Log "=== Self-test complete. Critical failure: $script:CriticalFailure ==="
Write-Log "Full log: $logFile"

if ($script:CriticalFailure) {
    Write-Host "RESULT: FAILED (see $logFile)" -ForegroundColor Red
    exit 1
} else {
    Write-Host "RESULT: PASSED (see $logFile)" -ForegroundColor Green
    exit 0
}
