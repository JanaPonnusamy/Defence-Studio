<#
.SYNOPSIS
  Self-test for the standalone Video Renderer (Phase R1).

.DESCRIPTION
  Windows PowerShell 5.1 compatible. Safe to re-run. No elevation, no network (localhost only).
    1. checks project files, Python and its packages, ffmpeg + ffprobe (ffmpeg is supplied by you, never bundled)
    2. runs the full pytest suite
    3. starts the backend on a FREE port with an isolated temp storage root, checks /health
    4. builds a real 20 s synthetic package, renders it through the API, downloads the MP4 + QC report and
       re-verifies it with ffprobe and a full ffmpeg decode
    5. negative cases: unknown job (404), invalid package (FAILED INVALID_PACKAGE), cancel stops the encoder
    6. crash test: kills the backend mid-render, restarts it, expects FAILED / JOB_INTERRUPTED and no partial output
    7. stops ONLY the processes it started and prints a PASS/FAIL/SKIP summary
  Exit code 0 only if every REQUIRED check passed.

.PARAMETER Install
  Create .venv and pip-install requirements-dev.txt first (needs network). Default: use the Python already on PATH.
.PARAMETER SkipPytest
  Skip the pytest stage (reported as SKIP; the run is then not fully verified).

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File docs\SELF_TEST_COMMANDS.ps1
#>
[CmdletBinding()]
param(
    [switch]$Install,
    [switch]$SkipPytest
)

$ErrorActionPreference = 'Continue'
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$Stamp = Get-Date -Format 'yyyyMMdd_HHmmss'
$Work = Join-Path $env:TEMP ("video_renderer_selftest_" + $Stamp)
New-Item -ItemType Directory -Force -Path $Work | Out-Null
$Results = New-Object System.Collections.ArrayList
$Started = New-Object System.Collections.ArrayList

function Add-Result([string]$Name, [string]$Status, [string]$Detail, [bool]$Required = $true) {
    [void]$Results.Add([pscustomobject]@{ Name = $Name; Status = $Status; Detail = $Detail; Required = $Required })
    $colour = switch ($Status) { 'PASS' { 'Green' } 'FAIL' { 'Red' } default { 'Yellow' } }
    Write-Host ("[{0}] {1} - {2}" -f $Status, $Name, $Detail) -ForegroundColor $colour
}

function Get-FreePort {
    $l = New-Object System.Net.Sockets.TcpListener([System.Net.IPAddress]::Loopback, 0)
    $l.Start(); $p = $l.LocalEndpoint.Port; $l.Stop(); return $p
}

function Invoke-Api([string]$Method, [string]$Url, $Body = $null) {
    # returns @{ Status = <int>; Json = <object|null> } and never throws on HTTP errors
    try {
        if ($null -ne $Body) {
            $r = Invoke-WebRequest -UseBasicParsing -Method $Method -Uri $Url -Body ($Body | ConvertTo-Json -Compress) `
                -ContentType 'application/json' -TimeoutSec 60
        } else {
            $r = Invoke-WebRequest -UseBasicParsing -Method $Method -Uri $Url -TimeoutSec 60
        }
        return @{ Status = [int]$r.StatusCode; Json = ($r.Content | ConvertFrom-Json) }
    } catch {
        $code = 0; $json = $null
        if ($_.Exception.Response) {
            $code = [int]$_.Exception.Response.StatusCode
            try {
                $sr = New-Object System.IO.StreamReader($_.Exception.Response.GetResponseStream())
                $json = $sr.ReadToEnd() | ConvertFrom-Json
            } catch { }
        }
        return @{ Status = $code; Json = $json }
    }
}

function Start-Backend([string]$Storage, [int]$Port) {
    $env:RENDERER_STORAGE_ROOT = $Storage
    $p = Start-Process -FilePath $Py -ArgumentList @('-m', 'uvicorn', 'renderer.main:app', '--host', '127.0.0.1',
        '--port', $Port, '--log-level', 'warning') -WorkingDirectory $ProjectRoot -PassThru -WindowStyle Hidden
    [void]$Started.Add($p)
    for ($i = 0; $i -lt 120; $i++) {
        Start-Sleep -Milliseconds 500
        $h = Invoke-Api GET "http://127.0.0.1:$Port/health"
        if ($h.Status -eq 200) { return $p }
        if ($p.HasExited) { break }
    }
    return $null
}

function Wait-Job-State([string]$Base, [string]$JobId, [string[]]$States, [int]$TimeoutSec = 300) {
    $end = (Get-Date).AddSeconds($TimeoutSec)
    while ((Get-Date) -lt $end) {
        $j = Invoke-Api GET "$Base/renders/$JobId"
        if ($j.Json -and ($States -contains $j.Json.status)) { return $j.Json }
        Start-Sleep -Milliseconds 400
    }
    return $null
}

try {
    # ---- 1. environment ------------------------------------------------------------------------------------
    Write-Host "== Environment ==" -ForegroundColor Cyan
    $need = @('renderer\main.py', 'renderer\render.py', 'renderer\jobs.py', 'scripts\fetch_from_vvm.py',
              'schemas\video-render-package.v1.schema.json', 'docs\CONTRACTS.md', 'requirements.txt')
    $missing = $need | Where-Object { -not (Test-Path (Join-Path $ProjectRoot $_)) }
    if ($missing) { Add-Result 'project files' 'FAIL' ("missing: " + ($missing -join ', ')) } else { Add-Result 'project files' 'PASS' 'all present' }

    $Py = $null
    $venvPy = Join-Path $ProjectRoot '.venv\Scripts\python.exe'
    if ($Install) {
        if (-not (Test-Path $venvPy)) { & python -m venv (Join-Path $ProjectRoot '.venv') }
        & $venvPy -m pip install -q -r (Join-Path $ProjectRoot 'requirements-dev.txt')
    }
    if (Test-Path $venvPy) { $Py = $venvPy } else { $cmd = Get-Command python -ErrorAction SilentlyContinue; if ($cmd) { $Py = $cmd.Source } }
    if (-not $Py) { Add-Result 'python' 'FAIL' 'python not found'; throw 'no python' }
    $ver = (& $Py --version) 2>&1
    $imp = & $Py -c "import fastapi, sqlalchemy, PIL, uvicorn, multipart, pytest, httpx, jsonschema; print('ok')" 2>&1
    if ("$imp".Trim() -eq 'ok') { Add-Result 'python + packages' 'PASS' "$ver ($Py)" } else { Add-Result 'python + packages' 'FAIL' "$imp (run with -Install)"; throw 'deps' }

    $ff = Get-Command ffmpeg -ErrorAction SilentlyContinue
    $fp = Get-Command ffprobe -ErrorAction SilentlyContinue
    if ($ff -and $fp) {
        $fv = ((& ffmpeg -hide_banner -version | Select-Object -First 1) -replace 'Copyright.*', '').Trim()
        $cfg = (& ffmpeg -hide_banner -version | Where-Object { $_ -like 'configuration:*' }) -join ' '
        $lic = if ($cfg -match '--enable-nonfree') { 'NONFREE' } elseif ($cfg -match '--enable-gpl') { if ($cfg -match '--enable-version3') { 'GPLv3' } else { 'GPL' } } else { 'LGPL/other' }
        Add-Result 'ffmpeg + ffprobe' 'PASS' "$fv [$lic] at $($ff.Source)"
    } else { Add-Result 'ffmpeg + ffprobe' 'FAIL' 'not on PATH: install ffmpeg yourself (it is never bundled); renders cannot run'; throw 'ffmpeg' }

    # ---- 2. pytest -----------------------------------------------------------------------------------------
    Write-Host "== pytest ==" -ForegroundColor Cyan
    if ($SkipPytest) {
        Add-Result 'pytest' 'SKIP' 'skipped by -SkipPytest'
    } else {
        Push-Location $ProjectRoot
        $log = Join-Path $Work 'pytest.log'
        & $Py -m pytest -q -p no:cacheprovider 2>&1 | Tee-Object -FilePath $log | Select-Object -Last 6 | ForEach-Object { Write-Host "    $_" }
        $code = $LASTEXITCODE
        Pop-Location
        $summary = (Get-Content $log | Select-Object -Last 1)
        if ($code -eq 0) { Add-Result 'pytest (full suite)' 'PASS' $summary } else { Add-Result 'pytest (full suite)' 'FAIL' "exit $code - $summary (log $log)" }
    }

    # ---- 3. backend + health -------------------------------------------------------------------------------
    Write-Host "== API ==" -ForegroundColor Cyan
    $Storage = Join-Path $Work 'storage'
    $Port = Get-FreePort
    $Base = "http://127.0.0.1:$Port"
    $Pkg = Join-Path $Work 'pkg'
    $Pkg2 = Join-Path $Work 'pkg_long'
    $mk = "import sys; sys.path.insert(0, r'$ProjectRoot'); from pathlib import Path; from renderer.synthetic import small_package; " +
          "small_package(Path(sys.argv[1]), profile={'width': 640, 'height': 360, 'fps': 25})"
    & $Py -c $mk $Pkg
    & $Py -c $mk $Pkg2
    if ((Test-Path "$Pkg\package.json") -and (Test-Path "$Pkg2\package.json")) { Add-Result 'build synthetic packages' 'PASS' '20 s, 4 shots, 2 transitions, overlays, 1 GAP (generated locally)' } else { Add-Result 'build synthetic packages' 'FAIL' 'generator failed'; throw 'pkg' }

    $srv = Start-Backend $Storage $Port
    if (-not $srv) { Add-Result 'backend start' 'FAIL' "did not answer /health on port $Port"; throw 'backend' }
    $h = Invoke-Api GET "$Base/health"
    if ($h.Json.ffmpeg.status -eq 'AVAILABLE') { Add-Result 'GET /health' 'PASS' "ffmpeg $($h.Json.ffmpeg.version) at $($h.Json.ffmpeg.ffmpeg)" } else { Add-Result 'GET /health' 'FAIL' "ffmpeg status $($h.Json.ffmpeg.status)" }

    # ---- 4. real render through the API --------------------------------------------------------------------
    $t0 = Get-Date
    $r = Invoke-Api POST "$Base/renders" @{ package_path = $Pkg; idempotency_key = "selftest-$Stamp" }
    $again = Invoke-Api POST "$Base/renders" @{ package_path = $Pkg; idempotency_key = "selftest-$Stamp" }
    if ($r.Status -eq 202 -and $again.Status -eq 200 -and $again.Json.job_id -eq $r.Json.job_id) { Add-Result 'POST /renders + idempotency key' 'PASS' "job $($r.Json.job_id)" } else { Add-Result 'POST /renders + idempotency key' 'FAIL' "status $($r.Status)/$($again.Status)" }
    $done = Wait-Job-State $Base $r.Json.job_id @('SUCCEEDED', 'FAILED', 'CANCELLED') 300
    if ($done -and $done.status -eq 'SUCCEEDED') { Add-Result 'render job' 'PASS' ("SUCCEEDED in {0:N1}s, {1}/{2} frames" -f ((Get-Date) - $t0).TotalSeconds, $done.frames_done, $done.frames_total) } else { Add-Result 'render job' 'FAIL' "$($done.status) $($done.error_code) $($done.error_message)" }
    if ($done -and $done.status -eq 'SUCCEEDED') {
        $mp4 = Join-Path $Work 'downloaded.mp4'
        Invoke-WebRequest -UseBasicParsing -Uri "$Base/renders/$($done.job_id)/output" -OutFile $mp4 -TimeoutSec 120
        $qc = (Invoke-Api GET "$Base/renders/$($done.job_id)/qc").Json
        if ($qc.passed) { Add-Result 'GET /qc' 'PASS' "all $($qc.checks.Count) checks passed; sha256 $($qc.output.sha256.Substring(0,12))..." } else { Add-Result 'GET /qc' 'FAIL' 'qc.passed is false' }
        $probe = (& ffprobe -v error -show_entries stream=codec_name,width,height,avg_frame_rate:format=duration -of json $mp4 | Out-String | ConvertFrom-Json)
        $v = $probe.streams | Where-Object { $_.codec_name -eq 'h264' }
        $a = $probe.streams | Where-Object { $_.codec_name -eq 'aac' }
        $dur = [double]$probe.format.duration
        if ($v -and $a -and $v.width -eq 640 -and $v.height -eq 360 -and $v.avg_frame_rate -eq '25/1' -and [math]::Abs($dur - 20.0) -le 0.1) { Add-Result 'independent ffprobe of downloaded MP4' 'PASS' ("h264+aac 640x360 25fps duration {0:N3}s" -f $dur) } else { Add-Result 'independent ffprobe of downloaded MP4' 'FAIL' "v=$($v.codec_name) a=$($a.codec_name) dur=$dur" }
        $decodeErr = & ffmpeg -v error -nostdin -i $mp4 -f null - 2>&1 | Out-String
        if ([string]::IsNullOrWhiteSpace($decodeErr)) { Add-Result 'full decode (ffmpeg -v error -f null)' 'PASS' 'zero errors' } else { Add-Result 'full decode (ffmpeg -v error -f null)' 'FAIL' $decodeErr.Trim() }
    }

    # ---- 5. negative cases + cancel ------------------------------------------------------------------------
    $nf = Invoke-Api GET "$Base/renders/r_does_not_exist"
    if ($nf.Status -eq 404) { Add-Result 'unknown job -> 404' 'PASS' 'NOT_FOUND' } else { Add-Result 'unknown job -> 404' 'FAIL' "status $($nf.Status)" }
    $bad = Join-Path $Work 'pkg_bad'
    Copy-Item -Recurse $Pkg $bad
    $m = Get-Content "$bad\package.json" -Raw | ConvertFrom-Json
    $m.narration.sha256 = ('0' * 64)
    ($m | ConvertTo-Json -Depth 10) | Set-Content -Encoding UTF8 "$bad\package.json"
    $br = Invoke-Api POST "$Base/renders" @{ package_path = $bad }
    $bj = Wait-Job-State $Base $br.Json.job_id @('SUCCEEDED', 'FAILED', 'CANCELLED') 60
    if ($bj -and $bj.status -eq 'FAILED' -and $bj.error_code -eq 'INVALID_PACKAGE' -and $bj.error_message -like '*sha256 mismatch*') { Add-Result 'invalid package -> FAILED INVALID_PACKAGE' 'PASS' $bj.error_message } else { Add-Result 'invalid package -> FAILED INVALID_PACKAGE' 'FAIL' "$($bj.status) $($bj.error_code)" }

    $cr = Invoke-Api POST "$Base/renders" @{ package_path = $Pkg2 }
    $cj = $null
    for ($i = 0; $i -lt 200; $i++) { $cj = (Invoke-Api GET "$Base/renders/$($cr.Json.job_id)").Json; if ($cj.status -eq 'RUNNING' -and $cj.frames_done -ge 50) { break }; Start-Sleep -Milliseconds 200 }
    $ffPid = & $Py -c "import sqlite3,sys; print(sqlite3.connect(sys.argv[1]).execute('select ffmpeg_pid from render_jobs where job_id=?',(sys.argv[2],)).fetchone()[0])" (Join-Path $Storage 'renderer.db') $cr.Json.job_id
    [void](Invoke-Api POST "$Base/renders/$($cr.Json.job_id)/cancel")
    $cd = Wait-Job-State $Base $cr.Json.job_id @('SUCCEEDED', 'FAILED', 'CANCELLED') 60
    Start-Sleep -Milliseconds 500
    $alive = $null
    if ($ffPid -match '^\d+$') { $alive = Get-Process -Id ([int]$ffPid) -ErrorAction SilentlyContinue }
    if ($cd -and $cd.status -eq 'CANCELLED' -and -not $alive) { Add-Result 'cancel stops the encoder' 'PASS' "CANCELLED at $($cd.frames_done)/$($cd.frames_total) frames, ffmpeg pid $ffPid gone" } else { Add-Result 'cancel stops the encoder' 'FAIL' "status $($cd.status), ffmpeg alive: $([bool]$alive)" }

    # ---- 6. crash mid-render -------------------------------------------------------------------------------
    $kr = Invoke-Api POST "$Base/renders" @{ package_path = $Pkg2 }
    $kj = $null
    for ($i = 0; $i -lt 200; $i++) { $kj = (Invoke-Api GET "$Base/renders/$($kr.Json.job_id)").Json; if ($kj.status -eq 'RUNNING' -and $kj.frames_done -ge 50) { break }; Start-Sleep -Milliseconds 200 }
    $kffPid = & $Py -c "import sqlite3,sys; print(sqlite3.connect(sys.argv[1]).execute('select ffmpeg_pid from render_jobs where job_id=?',(sys.argv[2],)).fetchone()[0])" (Join-Path $Storage 'renderer.db') $kr.Json.job_id
    Stop-Process -Id $srv.Id -Force      # hard kill: simulates a crash
    Start-Sleep -Seconds 1
    $srv = Start-Backend $Storage $Port
    if (-not $srv) { Add-Result 'restart after crash' 'FAIL' 'backend did not restart'; throw 'restart' }
    $kd = (Invoke-Api GET "$Base/renders/$($kr.Json.job_id)").Json
    $jobDir = Join-Path $Storage "renders\$($kr.Json.job_id)"
    $left = @(Get-ChildItem $jobDir -Filter '*.mp4' -ErrorAction SilentlyContinue)
    for ($i = 0; $i -lt 60 -and ($kffPid -match '^\d+$') -and (Get-Process -Id ([int]$kffPid) -ErrorAction SilentlyContinue); $i++) { Start-Sleep -Milliseconds 500 }
    $orphan = if ($kffPid -match '^\d+$') { Get-Process -Id ([int]$kffPid) -ErrorAction SilentlyContinue } else { $null }
    if ($kd.status -eq 'FAILED' -and $kd.error_code -eq 'JOB_INTERRUPTED' -and $left.Count -eq 0 -and -not $orphan) { Add-Result 'kill server mid-render -> JOB_INTERRUPTED' 'PASS' "FAILED/JOB_INTERRUPTED at $($kj.frames_done) frames, no mp4 left, no orphan ffmpeg" } else { Add-Result 'kill server mid-render -> JOB_INTERRUPTED' 'FAIL' "status $($kd.status) $($kd.error_code), mp4 files left: $($left.Count), orphan: $([bool]$orphan)" }
    $out = Invoke-Api GET "$Base/renders/$($kr.Json.job_id)/output"
    if ($out.Status -eq 409) { Add-Result 'interrupted job serves no output' 'PASS' '409 CONFLICT' } else { Add-Result 'interrupted job serves no output' 'FAIL' "status $($out.Status)" }
}
catch {
    if ($_.Exception.Message -notin @('no python', 'deps', 'ffmpeg', 'pkg', 'backend', 'restart')) { Add-Result 'self-test aborted' 'FAIL' $_.Exception.Message }
}
finally {
    # stop ONLY what this script started
    foreach ($p in $Started) { try { if (-not $p.HasExited) { Stop-Process -Id $p.Id -Force } } catch { } }
    Remove-Item Env:\RENDERER_STORAGE_ROOT -ErrorAction SilentlyContinue
}

Write-Host "`n== Summary ==" -ForegroundColor Cyan
$Results | Format-Table Status, Name, Detail -AutoSize -Wrap | Out-String -Width 200 | Write-Host
$fail = @($Results | Where-Object { $_.Status -eq 'FAIL' -and $_.Required }).Count
$pass = @($Results | Where-Object { $_.Status -eq 'PASS' }).Count
$skip = @($Results | Where-Object { $_.Status -eq 'SKIP' }).Count
Write-Host ("PASS={0} FAIL={1} SKIP={2}  (work folder: {3})" -f $pass, $fail, $skip, $Work)
$Results | ConvertTo-Json | Set-Content -Encoding UTF8 (Join-Path $Work 'results.json')
if ($fail -eq 0 -and $pass -gt 0) { exit 0 } else { exit 1 }
