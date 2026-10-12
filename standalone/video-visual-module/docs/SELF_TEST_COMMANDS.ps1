<#
.SYNOPSIS
  Self-test for the standalone Video Visual / Scene Composition Module.

.DESCRIPTION
  Windows PowerShell 5.1 compatible. Safe to re-run. Does not require elevation.
    1. checks module files and Python, creates/validates .venv, installs requirements
    2. validates configuration and initialises (migrates) the database
    3. runs the pytest suite
    4. starts the backend on a FREE port (isolated temp storage root unless -UseRealStorage)
    5. checks /health, /docs, the UI, then exercises real API workflows:
       project -> import generated PNG -> dedup -> map + stat -> scenes from narration ->
       build visual plan (with real local AI generation) -> poll job -> shots -> preview ->
       direct AI generation -> costs -> jobs -> diagnostics/logs
    6. negative cases: corrupt / wrong-type uploads, bad URL, unknown project, occupied port
    7. stops ONLY the backend process(es) it started and prints a PASS/FAIL/SKIP/BLOCKED summary
  Exit code 0 only if every REQUIRED check passed.

.PARAMETER UseRealStorage
  Run the live backend against the module's real storage/ folder instead of an isolated temp folder.
.PARAMETER SkipPytest
  Skip the pytest stage (reported as SKIP; makes the run non-required for that check).
.PARAMETER SkipInstall
  Do not run pip install (packages must already be importable).

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File docs\SELF_TEST_COMMANDS.ps1
#>
[CmdletBinding()]
param(
    [switch]$UseRealStorage,
    [switch]$SkipPytest,
    [switch]$SkipInstall
)

$ErrorActionPreference = 'Continue'

$ModuleRoot = Split-Path -Parent $PSScriptRoot
$VenvPy = Join-Path $ModuleRoot '.venv\Scripts\python.exe'
$Stamp = Get-Date -Format 'yyyyMMdd_HHmmss'
$WorkDir = Join-Path $env:TEMP "vvm_selftest_$Stamp"
New-Item -ItemType Directory -Force -Path $WorkDir | Out-Null

$Results = New-Object System.Collections.ArrayList
$Started = New-Object System.Collections.ArrayList   # processes WE started (only these are stopped)

function Add-Result {
    param([string]$Name, [ValidateSet('PASS', 'FAIL', 'SKIP', 'BLOCKED')][string]$Status, [string]$Detail = '', [bool]$Required = $true)
    [void]$Results.Add([pscustomobject]@{ Check = $Name; Status = $Status; Required = $Required; Detail = $Detail })
    $color = 'Gray'
    if ($Status -eq 'PASS') { $color = 'Green' } elseif ($Status -eq 'FAIL') { $color = 'Red' } elseif ($Status -eq 'BLOCKED') { $color = 'Yellow' }
    Write-Host ("[{0,-7}] {1}  {2}" -f $Status, $Name, $Detail) -ForegroundColor $color
}

function Test-Check {
    param([string]$Name, [scriptblock]$Body, [bool]$Required = $true)
    try {
        $detail = & $Body
        if ($null -eq $detail) { $detail = '' }
        Add-Result -Name $Name -Status 'PASS' -Detail ([string]$detail) -Required $Required
        return $true
    } catch {
        Add-Result -Name $Name -Status 'FAIL' -Detail ($_.Exception.Message) -Required $Required
        return $false
    }
}

function Assert-True { param([bool]$Cond, [string]$Msg) if (-not $Cond) { throw $Msg } }

function Invoke-Py {
    # Runs the venv python with a script passed via a temp file; returns @{Code; Out}
    param([string]$Code, [hashtable]$EnvVars = @{})
    $f = Join-Path $WorkDir ("py_" + [guid]::NewGuid().ToString('N') + '.py')
    $prefix = "import sys`nsys.path.insert(0, r'" + $ModuleRoot + "')`n"
    Set-Content -Path $f -Value ($prefix + $Code) -Encoding UTF8
    $saved = @{}
    foreach ($k in $EnvVars.Keys) { $saved[$k] = [Environment]::GetEnvironmentVariable($k, 'Process'); [Environment]::SetEnvironmentVariable($k, $EnvVars[$k], 'Process') }
    Push-Location $ModuleRoot
    try {
        $out = & $VenvPy $f 2>&1 | ForEach-Object { "$_" }
        $code = $LASTEXITCODE
    } finally {
        Pop-Location
        foreach ($k in $EnvVars.Keys) { [Environment]::SetEnvironmentVariable($k, $saved[$k], 'Process') }
    }
    return @{ Code = $code; Out = ($out -join "`n") }
}

# ----------------------------------------------------------------------------- HTTP helpers (HttpClient: PS 5.1 has no -Form)
Add-Type -AssemblyName System.Net.Http
$Http = New-Object System.Net.Http.HttpClient
$Http.Timeout = [TimeSpan]::FromSeconds(60)
$script:BaseUrl = ''

function Invoke-Api {
    param([string]$Method, [string]$Path, $Json = $null, [System.Net.Http.HttpContent]$Content = $null, [hashtable]$Headers = @{})
    $req = New-Object System.Net.Http.HttpRequestMessage ([System.Net.Http.HttpMethod]::new($Method)), ($script:BaseUrl + $Path)
    foreach ($h in $Headers.Keys) { [void]$req.Headers.TryAddWithoutValidation($h, $Headers[$h]) }
    if ($null -ne $Json) {
        $body = $Json | ConvertTo-Json -Depth 20 -Compress
        $req.Content = New-Object System.Net.Http.StringContent($body, [System.Text.Encoding]::UTF8, 'application/json')
    } elseif ($null -ne $Content) { $req.Content = $Content }
    $resp = $Http.SendAsync($req).GetAwaiter().GetResult()
    $bytes = $resp.Content.ReadAsByteArrayAsync().GetAwaiter().GetResult()
    $ctype = ''
    if ($resp.Content.Headers.ContentType) { $ctype = $resp.Content.Headers.ContentType.MediaType }
    $text = ''
    $obj = $null
    if ($ctype -like '*json*') {
        $text = [System.Text.Encoding]::UTF8.GetString($bytes)
        if ($text) { $obj = $text | ConvertFrom-Json }
    } elseif ($ctype -like 'text/*') { $text = [System.Text.Encoding]::UTF8.GetString($bytes) }
    return [pscustomobject]@{ Status = [int]$resp.StatusCode; Json = $obj; Text = $text; Bytes = $bytes; ContentType = $ctype }
}

function New-MultipartImport {
    param([hashtable]$Fields, [byte[]]$FileBytes, [string]$FileName, [string]$Mime = 'image/png')
    $mp = New-Object System.Net.Http.MultipartFormDataContent
    foreach ($k in $Fields.Keys) { $mp.Add((New-Object System.Net.Http.StringContent([string]$Fields[$k])), $k) }
    if ($null -ne $FileBytes) {
        $fc = New-Object System.Net.Http.ByteArrayContent(, $FileBytes)
        $fc.Headers.ContentType = [System.Net.Http.Headers.MediaTypeHeaderValue]::Parse($Mime)
        $mp.Add($fc, 'file', $FileName)
    }
    return , $mp   # comma: MultipartFormDataContent is IEnumerable and would otherwise be unrolled
}

function Wait-Job {
    param([string]$JobId, [int]$TimeoutSec = 180)
    $sw = [Diagnostics.Stopwatch]::StartNew()
    while ($sw.Elapsed.TotalSeconds -lt $TimeoutSec) {
        $j = (Invoke-Api GET "/jobs/$JobId").Json
        if ($j.status -in @('SUCCEEDED', 'FAILED', 'CANCELLED')) { return $j }
        Start-Sleep -Milliseconds 300
    }
    throw "job $JobId did not finish within $TimeoutSec s"
}

function Get-FreePort {
    $l = New-Object System.Net.Sockets.TcpListener([System.Net.IPAddress]::Loopback, 0)
    $l.Start(); $p = $l.LocalEndpoint.Port; $l.Stop(); return $p
}

function New-TestPng {
    param([int]$W = 1280, [int]$H = 720, [string]$Label = 'self-test')
    Add-Type -AssemblyName System.Drawing
    $bmp = New-Object System.Drawing.Bitmap $W, $H
    $g = [System.Drawing.Graphics]::FromImage($bmp)
    $g.Clear([System.Drawing.Color]::FromArgb(20, 60, 110))
    $g.FillRectangle([System.Drawing.Brushes]::DarkOrange, [int]($W * 0.55), [int]($H * 0.4), [int]($W * 0.3), [int]($H * 0.25))
    $g.FillEllipse([System.Drawing.Brushes]::LightGray, [int]($W * 0.1), [int]($H * 0.5), [int]($W * 0.2), [int]($H * 0.2))
    $font = New-Object System.Drawing.Font('Arial', 28)
    $g.DrawString("$Label $(Get-Date -Format o)", $font, [System.Drawing.Brushes]::White, 20, 20)
    $ms = New-Object System.IO.MemoryStream
    $bmp.Save($ms, [System.Drawing.Imaging.ImageFormat]::Png)
    $g.Dispose(); $bmp.Dispose()
    return $ms.ToArray()
}

function Start-Backend {
    param([int]$Port, [string]$StorageRoot, [string]$Tag)
    $out = Join-Path $WorkDir "uvicorn_$Tag.out.log"
    $err = Join-Path $WorkDir "uvicorn_$Tag.err.log"
    $saved = $env:VIDEO_MODULE_STORAGE_ROOT
    # Phase 2: never hand a provider credential to the self-test backend (no paid calls, ever)
    $savedKeys = @{}
    foreach ($k in @('OPENAI_API_KEY', 'VIDEO_MODULE_OPENAI_API_KEY', 'VIDEO_MODULE_ALLOW_PLACEHOLDER_FALLBACK')) {
        $savedKeys[$k] = [Environment]::GetEnvironmentVariable($k, 'Process')
        [Environment]::SetEnvironmentVariable($k, $null, 'Process')
    }
    # Audit fix: an EMPTY key variable overrides a key in the module .env file (a removed one does not).
    # PS 5.1 cannot hold an empty env var via $env:, so set a single space; the adapter strips it -> no key.
    [Environment]::SetEnvironmentVariable('OPENAI_API_KEY', ' ', 'Process')
    [Environment]::SetEnvironmentVariable('VIDEO_MODULE_OPENAI_API_KEY', ' ', 'Process')
    if ($StorageRoot) { $env:VIDEO_MODULE_STORAGE_ROOT = $StorageRoot }
    try {
        $p = Start-Process -FilePath $VenvPy -ArgumentList @('-m', 'uvicorn', 'app.main:app', '--host', '127.0.0.1', '--port', "$Port", '--log-level', 'warning') `
            -WorkingDirectory $ModuleRoot -RedirectStandardOutput $out -RedirectStandardError $err -PassThru -WindowStyle Hidden
    } finally {
        $env:VIDEO_MODULE_STORAGE_ROOT = $saved
        foreach ($k in $savedKeys.Keys) { [Environment]::SetEnvironmentVariable($k, $savedKeys[$k], 'Process') }
    }
    $null = $p.Handle   # PS 5.1: cache the handle so ExitCode is available after exit
    [void]$Started.Add($p)
    return @{ Process = $p; Out = $out; Err = $err }
}

function Stop-Started {
    foreach ($p in $Started) {
        try { if (-not $p.HasExited) { Stop-Process -Id $p.Id -Force -ErrorAction Stop; $p.WaitForExit(10000) | Out-Null } } catch { }
    }
}

# ============================================================================= 0. environment
Write-Host "Video Visual Module self-test  (module: $ModuleRoot)" -ForegroundColor Cyan
Write-Host "Work dir: $WorkDir"
$isAdmin = $false
try { $isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator) } catch { }
Add-Result 'Elevation status (informational)' 'PASS' ("Running as Administrator: $isAdmin (elevation is NOT required)") $false
Add-Result 'PowerShell version (informational)' 'PASS' ("$($PSVersionTable.PSVersion) / $($PSVersionTable.PSEdition)") $false

try {
    [void](Test-Check 'Module files present' {
        $need = @('app\main.py', 'app\config.py', 'app\db\models.py', 'alembic.ini', 'alembic\env.py', 'requirements.txt', 'ui\index.html', 'ui\app.js', 'tests\conftest.py')
        $missing = @($need | Where-Object { -not (Test-Path (Join-Path $ModuleRoot $_)) })
        Assert-True ($missing.Count -eq 0) ("missing: " + ($missing -join ', '))
        "$($need.Count) key files found"
    })

    $sysPy = $null
    foreach ($cand in @('py', 'python', 'python3')) {
        $cmd = Get-Command $cand -ErrorAction SilentlyContinue
        if ($cmd) { $sysPy = $cmd.Source; break }
    }
    $venvOk = Test-Check 'Python virtual environment' {
        if (-not (Test-Path $VenvPy)) {
            Assert-True ($null -ne $sysPy) 'no system Python found to create .venv'
            if ($sysPy -like '*py.exe' -and (Split-Path $sysPy -Leaf) -eq 'py.exe') { & $sysPy -3 -m venv (Join-Path $ModuleRoot '.venv') } else { & $sysPy -m venv (Join-Path $ModuleRoot '.venv') }
            Assert-True (Test-Path $VenvPy) 'venv creation failed'
            $created = 'created'
        } else { $created = 'existing' }
        $v = (& $VenvPy -c "import sys; print('%d.%d.%d' % sys.version_info[:3])") | Select-Object -Last 1
        $parts = $v.Split('.')
        Assert-True (([int]$parts[0] -eq 3) -and ([int]$parts[1] -ge 10)) "Python $v < 3.10"
        "$created .venv, Python $v"
    }
    if (-not $venvOk) { throw 'Cannot continue without a working venv.' }

    if ($SkipInstall) {
        Add-Result 'Install requirements' 'SKIP' '-SkipInstall given' $false
    } else {
        Push-Location $ModuleRoot
        & $VenvPy -m pip install -q --disable-pip-version-check -r requirements.txt | Out-Host
        $pipCode = $LASTEXITCODE
        Pop-Location
        if ($pipCode -eq 0) { Add-Result 'Install requirements' 'PASS' 'pip install -r requirements.txt OK' }
        else { Add-Result 'Install requirements' 'BLOCKED' "pip exit $pipCode (offline?) - relying on already-installed packages" $false }
    }
    [void](Test-Check 'Required packages importable' {
        $r = Invoke-Py "import fastapi, uvicorn, sqlalchemy, alembic, pydantic, pydantic_settings, PIL, multipart, httpx, pytest; print('ok')"
        Assert-True ($r.Code -eq 0) $r.Out
        'fastapi, uvicorn, sqlalchemy, alembic, pydantic, Pillow, pytest'
    })

    # Live-server storage: isolated temp unless -UseRealStorage
    $LiveStorage = $null
    if (-not $UseRealStorage) { $LiveStorage = Join-Path $WorkDir 'storage' }

    [void](Test-Check 'Configuration valid' {
        $code = @"
import json
from app.config import get_settings
from app.generation.registry import list_providers
s = get_settings()
provs = {p['name']: p for p in list_providers()}
names = set(provs)
assert s.default_image_provider in names, 'default provider not enabled'
assert all(d.exists() for d in s.all_storage_dirs()), 'storage dirs missing'
assert s.max_upload_bytes > 0 and s.min_shot_seconds <= s.max_shot_seconds
assert provs['placeholder-procedural']['generation_kind'] == 'PLACEHOLDER' and not provs['placeholder-procedural']['is_real_ai']
assert provs['mock-local']['generation_kind'] == 'TEST_FIXTURE'
oa = provs.get('openai-images', {})
key_set = s.openai_api_key is not None and bool(s.openai_api_key.get_secret_value())
assert oa.get('state') == ('AVAILABLE' if key_set else 'NOT_CONFIGURED'), oa.get('state')
print(json.dumps({'storage_root': str(s.storage_root), 'providers': {n: p['generation_kind'] + '/' + p['state'] for n, p in sorted(provs.items())},
                  'openai_key_configured': key_set, 'api_key_set': bool(s.api_key)}))
"@
        $envs = @{}
        if ($LiveStorage) { $envs['VIDEO_MODULE_STORAGE_ROOT'] = $LiveStorage }
        $r = Invoke-Py $code $envs
        Assert-True ($r.Code -eq 0) $r.Out
        ($r.Out -split "`n")[-1]
    })

    [void](Test-Check 'Database init / migrations (idempotent)' {
        $code = "from app.db.init_db import init_db; init_db(); init_db(); print('alembic head applied twice OK')"
        $envs = @{}
        if ($LiveStorage) { $envs['VIDEO_MODULE_STORAGE_ROOT'] = $LiveStorage }
        $r = Invoke-Py $code $envs
        Assert-True ($r.Code -eq 0) $r.Out
        ($r.Out -split "`n")[-1]
    })

    # ========================================================================= pytest
    if ($SkipPytest) {
        Add-Result 'pytest suite' 'SKIP' '-SkipPytest given' $false
    } else {
        [void](Test-Check 'pytest suite' {
            Push-Location $ModuleRoot
            $log = Join-Path $WorkDir 'pytest.log'
            & $VenvPy -m pytest -q -p no:cacheprovider 2>&1 | ForEach-Object { "$_" } | Tee-Object -FilePath $log | Select-Object -Last 3 | Out-Host
            $code = $LASTEXITCODE
            Pop-Location
            $summary = (Get-Content $log | Where-Object { $_ -match '(passed|failed|error)' } | Select-Object -Last 1)
            Assert-True ($code -eq 0) "pytest exit $code : $summary"
            $summary
        })
    }

    # ========================================================================= restart-recovery seed
    # Plant a RUNNING job (as if a previous process died mid-job) in the live DB BEFORE the backend starts.
    $ctxRecovery = @{ job = $null }
    if ($LiveStorage) {
        $seedOk = Test-Check 'Seed interrupted RUNNING job (restart-recovery setup)' {
            $code = @"
from app.db.init_db import init_db
from app.db.base import SessionLocal
from app.db.models import Job
init_db()
db = SessionLocal()
j = Job(job_type='build_visual_plan', status='RUNNING', progress=0.5, stage='planning', payload={'project_id': 'none'})
db.add(j); db.commit(); print(j.job_id)
"@
            $r = Invoke-Py $code @{ VIDEO_MODULE_STORAGE_ROOT = $LiveStorage }
            Assert-True ($r.Code -eq 0) $r.Out
            $ctxRecovery.job = ($r.Out -split "`n")[-1].Trim()
            "job $($ctxRecovery.job) left RUNNING"
        }
    } else {
        Add-Result 'Seed interrupted RUNNING job (restart-recovery setup)' 'SKIP' '-UseRealStorage: not planting fake jobs in real storage' $false
    }

    # ========================================================================= live backend
    $port = Get-FreePort
    $script:BaseUrl = "http://127.0.0.1:$port"
    $be = Start-Backend -Port $port -StorageRoot $LiveStorage -Tag 'main'
    $up = Test-Check "Backend starts on free port $port" {
        $sw = [Diagnostics.Stopwatch]::StartNew()
        $ok = $false
        while ($sw.Elapsed.TotalSeconds -lt 60) {
            if ($be.Process.HasExited) { throw ("backend exited early: " + (Get-Content $be.Err -Raw)) }
            try { $h = Invoke-Api GET '/health'; if ($h.Status -eq 200) { $ok = $true; break } } catch { }
            Start-Sleep -Milliseconds 500
        }
        Assert-True $ok 'health endpoint not reachable within 60s'
        "PID $($be.Process.Id), up in $([math]::Round($sw.Elapsed.TotalSeconds,1)) s, storage: $(if ($LiveStorage) { $LiveStorage } else { 'module storage/' })"
    }

    if (-not $up) {
        foreach ($n in @('GET /health', 'GET /docs', 'UI served', 'Workflow')) { Add-Result $n 'BLOCKED' 'backend did not start' }
    } else {
        [void](Test-Check 'GET /health' {
            $h = Invoke-Api GET '/health'
            Assert-True ($h.Status -eq 200 -and $h.Json.status -eq 'ok' -and $h.Json.database -eq 'ok') "status $($h.Status) $($h.Text)"
            "service=$($h.Json.service) version=$($h.Json.version) db=$($h.Json.database)"
        })
        if ($ctxRecovery.job) {
            [void](Test-Check 'Restart recovery: interrupted RUNNING job -> FAILED (never SUCCEEDED)' {
                $j = (Invoke-Api GET "/jobs/$($ctxRecovery.job)").Json
                Assert-True ($j.status -eq 'FAILED' -and $j.error_code -eq 'JOB_INTERRUPTED' -and $null -eq $j.result_id) "status=$($j.status) code=$($j.error_code)"
                "status=FAILED error_code=JOB_INTERRUPTED result_id=null"
            })
        }
        [void](Test-Check 'GET /docs + /openapi.json' {
            $d = Invoke-Api GET '/docs'
            Assert-True ($d.Status -eq 200 -and $d.Text -match 'swagger') "docs status $($d.Status)"
            $o = Invoke-Api GET '/openapi.json'
            $paths = @($o.Json.paths.PSObject.Properties.Name)
            foreach ($p in @('/projects', '/assets/import', '/assets/generate', '/scenes', '/visual-plans/build', '/shots/{visual_plan_id}', '/jobs/{job_id}', '/costs/{project_id}')) {
                Assert-True ($paths -contains $p) "openapi missing $p"
            }
            "$($paths.Count) paths documented"
        })
        [void](Test-Check 'UI served' {
            $u = Invoke-Api GET '/ui/'
            Assert-True ($u.Status -eq 200 -and $u.Text -match 'Visual Composition Studio') "ui status $($u.Status)"
            $js = Invoke-Api GET '/ui/app.js'
            $css = Invoke-Api GET '/ui/styles.css'
            Assert-True ($js.Status -eq 200 -and $css.Status -eq 200) 'ui assets missing'
            "index.html, app.js ($($js.Bytes.Length) B), styles.css"
        })

        # ---------------------------------------------------------------- workflow
        $ctx = @{}
        $wfOk = Test-Check 'Workflow: create project' {
            $r = Invoke-Api POST '/projects' @{ name = "Self-test $Stamp"; ai_image_budget_usd = 2.0 }
            Assert-True ($r.Status -eq 201) "status $($r.Status) $($r.Text)"
            $ctx.pid = $r.Json.project_id
            "project_id=$($ctx.pid)"
        }
        if ($wfOk) {
            $png = New-TestPng -Label 'Kessan Strait fleet'
            [void](Test-Check 'Workflow: import generated test image' {
                $meta = '{"tags":["fleet","kessan","strait","frigate","navy"],"protected_regions":[{"x":0.0,"y":0.0,"w":0.6,"h":0.12,"label":"caption"}]}'
                $mp = New-MultipartImport @{ project_id = $ctx.pid; asset_type = 'SOURCE_PHOTO'; source_type = 'EXTERNAL_SOURCE'; rights_status = 'LICENSED'; licence = 'Self-test licence'; title = 'Kessan Strait fleet frigate'; attribution = 'Self-test image'; source_url = 'https://example.org/selftest.png'; extra_metadata = $meta } $png 'selftest.png'
                $r = Invoke-Api POST '/assets/import' -Content $mp
                Assert-True ($r.Status -eq 201) "status $($r.Status) $($r.Text)"
                $sha = ([System.BitConverter]::ToString((New-Object Security.Cryptography.SHA256Managed).ComputeHash($png))).Replace('-', '').ToLower()
                Assert-True ($r.Json.checksum_sha256 -eq $sha) 'checksum mismatch'
                Assert-True ($r.Json.width -eq 1280 -and $r.Json.height -eq 720) 'dimensions wrong'
                $ctx.asset = $r.Json.asset_id
                "asset_id=$($ctx.asset) sha256 verified, rights='$($r.Json.rights_label)'"
            })
            [void](Test-Check 'Workflow: duplicate import is deduplicated' {
                $mp = New-MultipartImport @{ project_id = $ctx.pid; asset_type = 'SOURCE_PHOTO'; source_type = 'EXTERNAL_SOURCE'; rights_status = 'LICENSED'; licence = 'x' } $png 'again.png'
                $r = Invoke-Api POST '/assets/import' -Content $mp
                Assert-True ($r.Status -eq 200 -and $r.Json.deduplicated -eq $true -and $r.Json.asset_id -eq $ctx.asset) "status $($r.Status) dedup=$($r.Json.deduplicated)"
                'same asset_id returned, deduplicated=true'
            })
            [void](Test-Check 'Workflow: map + statistic graphics' {
                $m = Invoke-Api POST '/assets/maps' @{ project_id = $ctx.pid; tags = @('strait'); spec = @{ title = 'Kessan Strait'; data_source = 'Self-test coordinates'; markers = @(@{ label = 'Port Arvel'; lat = 44.6; lon = 33.5 }, @{ label = 'Lunet'; lat = 45.0; lon = 35.1 }) } }
                Assert-True ($m.Status -eq 201) "map $($m.Status) $($m.Text)"
                $s = Invoke-Api POST '/assets/stats' @{ project_id = $ctx.pid; tags = @('warships'); spec = @{ graphic_type = 'STAT_CARD'; title = 'Warships deployed'; data = @(@{ label = 'warships'; value = 40; unit = 'ships'; source = 'Self-test release'; as_of = '2026-09-30'; confidence = 'OFFICIAL' }) } }
                Assert-True ($s.Status -eq 201) "stat $($s.Status) $($s.Text)"
                "map=$($m.Json.asset_id) stat=$($s.Json.asset_id)"
            })
            [void](Test-Check 'Workflow: create scenes from narration' {
                $narr = "The fleet is concentrated near the Kessan Strait this week. Officials say 40 warships have been deployed.`n`nAccording to a released document, the order was signed in 2025. Strikes escalated along the coast overnight. In conclusion, tensions remain high."
                $r = Invoke-Api POST '/scenes' @{ project_id = $ctx.pid; narration = $narr; replace_existing = $true }
                Assert-True ($r.Status -eq 201 -and $r.Json.scene_count -ge 2) "status $($r.Status) $($r.Text)"
                $segs = 0; foreach ($s in $r.Json.scenes) { $segs += @($s.narration_segments).Count }
                "$($r.Json.scene_count) scenes, $segs narration segments"
            })
            $built = Test-Check 'Workflow: build visual plan + poll job' {
                $sw = [Diagnostics.Stopwatch]::StartNew()
                $j = Invoke-Api POST '/visual-plans/build' @{ project_id = $ctx.pid; options = @{ generate_ai = $true; ai_provider = 'placeholder-procedural'; max_ai_images = 2 } } -Headers @{ 'Idempotency-Key' = "selftest-$Stamp" }
                Assert-True ($j.Status -eq 202 -and $j.Json.status -eq 'QUEUED') "status $($j.Status) $($j.Text)"
                $done = Wait-Job $j.Json.job_id
                Assert-True ($done.status -eq 'SUCCEEDED') "job $($done.status): $($done.error_code) $($done.error_message)"
                $ctx.plan = $done.result_id
                "job $($j.Json.job_id) SUCCEEDED in $([math]::Round($sw.Elapsed.TotalSeconds,2)) s -> plan $($ctx.plan)"
            }
            if ($built) {
                [void](Test-Check 'Workflow: fetch plan, timeline + shots' {
                    $p = Invoke-Api GET "/visual-plans/$($ctx.plan)?include_timeline=false"
                    Assert-True ($p.Status -eq 200 -and $p.Json.status -eq 'BUILT') "plan $($p.Status)"
                    $t = Invoke-Api GET "/visual-plans/$($ctx.plan)/timeline"
                    Assert-True ($t.Json.schema -eq 'video-visual-timeline' -and $t.Json.version -eq '1.1') 'timeline schema mismatch'
                    $s = Invoke-Api GET "/shots/$($ctx.plan)"
                    Assert-True ($s.Status -eq 200 -and $s.Json.shot_count -ge 4) "shots $($s.Status) count $($s.Json.shot_count)"
                    $ctx.shot = $s.Json.shots[0].shot_id
                    $gap = @($s.Json.shots | Where-Object { $_.status -eq 'GAP' }).Count
                    $ph = @($s.Json.shots | Where-Object { $_.visual_type -eq 'PLACEHOLDER_IMAGE' })
                    $ai = @($s.Json.shots | Where-Object { $_.source_type -eq 'AI_GENERATED' }).Count
                    $gen = @($s.Json.shots | Where-Object { $_.visual_type -in @('PLACEHOLDER_IMAGE', 'AI_IMAGE') -and $_.intent -in @('EVIDENCE', 'STATISTIC', 'GEOGRAPHIC_CONTEXT', 'LOCATION', 'TIMELINE', 'COMPARISON') }).Count
                    Assert-True ($gen -eq 0) 'generated imagery used for a factual intent'
                    Assert-True ($ai -eq 0) 'placeholder provider output was labelled as AI'
                    foreach ($x in $ph) {
                        Assert-True ($x.editorial_label -eq 'PLACEHOLDER' -and (@($x.overlays | Where-Object { $_.text -like '*NOT AI*' }).Count -ge 1)) 'placeholder shot not labelled'
                    }
                    Assert-True ($p.Json.stats.real_ai_images_generated_in_plan -eq 0) 'placeholder counted as real AI'
                    Assert-True (@($t.Json.assets.PSObject.Properties | Where-Object { $_.Value.provenance_class -eq 'PLACEHOLDER' -and $_.Value.evidence_eligible }).Count -eq 0) 'placeholder evidence-eligible'
                    "$($s.Json.shot_count) shots ($($ph.Count) PLACEHOLDER-labelled, 0 AI, $gap explicit GAPs), timeline v$($t.Json.version), timing=$($t.Json.timing.timing_source), visual changes=$($p.Json.stats.visual_change_count), warnings=$(@($p.Json.warnings).Count)"
                })
                [void](Test-Check 'Workflow: shot preview + contact sheet are real PNGs' {
                    $pv = Invoke-Api GET "/shots/$($ctx.plan)/$($ctx.shot)/preview?t=1"
                    $sig = [System.BitConverter]::ToString($pv.Bytes[0..7])
                    Assert-True ($pv.Status -eq 200 -and $sig -eq '89-50-4E-47-0D-0A-1A-0A') "preview $($pv.Status) sig $sig"
                    $cs = Invoke-Api GET "/visual-plans/$($ctx.plan)/contact-sheet"
                    Assert-True ($cs.Status -eq 200 -and $cs.Bytes.Length -gt 1000) "contact sheet $($cs.Status)"
                    "preview $($pv.Bytes.Length) B, contact sheet $($cs.Bytes.Length) B"
                })
            } else { Add-Result 'Workflow: fetch plan, timeline + shots' 'BLOCKED' 'plan build failed' }
            [void](Test-Check 'Workflow: placeholder generation is classified PLACEHOLDER (not AI)' {
                # the legacy name 'local-procedural' is an alias of placeholder-procedural
                $g = Invoke-Api POST '/assets/generate' @{ project_id = $ctx.pid; prompt = 'Illustrative naval task force at dawn'; provider = 'local-procedural'; purpose = 'HOOK'; resolution = '1280x720' }
                Assert-True ($g.Status -eq 202) "status $($g.Status) $($g.Text)"
                $done = Wait-Job $g.Json.job_id
                Assert-True ($done.status -eq 'SUCCEEDED') "$($done.error_code) $($done.error_message)"
                $a = (Invoke-Api GET "/assets/$($done.result_id)").Json
                Assert-True ($a.generation_kind -eq 'PLACEHOLDER' -and $a.provenance_class -eq 'PLACEHOLDER' -and $a.asset_type -eq 'PLACEHOLDER_IMAGE') 'not classified PLACEHOLDER'
                Assert-True ($a.source_type -ne 'AI_GENERATED' -and $a.is_real_ai -eq $false -and $a.evidence_eligible -eq $false -and $a.editorial_category -eq 'PLACEHOLDER') 'placeholder presented as AI/evidence'
                Assert-True ($null -eq $a.generation_cost -and $a.cost_status -eq 'NOT_APPLICABLE' -and $a.width -eq 1280) 'placeholder cost/metadata wrong'
                $f = Invoke-Api GET "/assets/$($a.asset_id)/file"
                Assert-True ($f.Status -eq 200 -and $f.Bytes.Length -gt 5000) 'generated file missing'
                "asset $($a.asset_id) by $($a.provider)/$($a.model): kind=$($a.generation_kind), $($f.Bytes.Length) B, cost=$($a.cost_status) (no amount)"
            })
            [void](Test-Check 'Provider states without credentials (no network, no paid call)' {
                $st = (Invoke-Api GET '/providers/openai-images/status?probe=true').Json
                Assert-True ($st.state -eq 'NOT_CONFIGURED' -and $st.probe.probed -eq $false) "state=$($st.state) probed=$($st.probe.probed)"
                $r = Invoke-Api POST '/assets/generate' @{ project_id = $ctx.pid; prompt = 'x'; provider = 'openai-images' }
                Assert-True ($r.Status -eq 409 -and $r.Json.error_code -eq 'PROVIDER_NOT_CONFIGURED') "real-AI request not refused: $($r.Status) $($r.Text)"
                $fb = Invoke-Api POST '/assets/generate' @{ project_id = $ctx.pid; prompt = 'Patrol boats at dawn'; provider = 'openai-images'; resolution = '640x360'; allow_placeholder_fallback = $true }
                $done = Wait-Job $fb.Json.job_id
                Assert-True ($done.status -eq 'SUCCEEDED') "fallback $($done.status) $($done.error_code)"
                $a = (Invoke-Api GET "/assets/$($done.result_id)").Json
                Assert-True ($a.is_fallback -and $a.fallback_from -eq 'openai-images' -and $a.generation_kind -eq 'PLACEHOLDER' -and $a.title.StartsWith('[FALLBACK PLACEHOLDER]')) "fallback not labelled: $($a.title) kind=$($a.generation_kind) from=$($a.fallback_from)"
                $gens = (Invoke-Api GET "/generations?project_id=$($ctx.pid)").Json
                $nc = @($gens | Where-Object { $_.provider -eq 'openai-images' -and $_.provider_state -eq 'NOT_CONFIGURED' }).Count
                Assert-True ($nc -ge 1) 'NOT_CONFIGURED generation state not recorded'
                "openai-images=$($st.state) (probe skipped: no key); plain request -> 409 PROVIDER_NOT_CONFIGURED; explicit fallback -> labelled PLACEHOLDER asset $($a.asset_id.Substring(0,8))"
            })
            [void](Test-Check 'Preview capabilities reported honestly' {
                $c = (Invoke-Api GET '/capabilities').Json
                Assert-True ($null -ne $c.previews.video_frames -and $null -ne $c.previews.pdf_pages) 'capabilities missing'
                Assert-True ($c.previews.basemap_tiles.available -eq $false) 'basemap network should be off by default'
                "video_frames=$($c.previews.video_frames.available) ($($c.previews.video_frames.backend)); pdf_pages=$($c.previews.pdf_pages.available) ($($c.previews.pdf_pages.backend)); basemap=$($c.previews.basemap_tiles.reason)"
            })
            [void](Test-Check 'Workflow: cost report (no invented costs)' {
                $c = (Invoke-Api GET "/costs/$($ctx.pid)").Json
                Assert-True ($c.ai_image_count -eq 0 -and $c.ai_generation_cost -eq 0) "placeholder counted as AI: ai_image_count=$($c.ai_image_count) cost=$($c.ai_generation_cost)"
                Assert-True ($c.placeholder_image_count -ge 2 -and $c.fallback_placeholder_count -ge 1) "placeholder count $($c.placeholder_image_count)"
                Assert-True ($c.totals_complete -eq $true -and $null -eq $c.cost_per_finished_minute -and $null -eq $c.cost_per_generated_image) 'invented per-minute/per-image cost'
                Assert-True (@($c.records | Where-Object { $null -ne $_.estimated_cost }).Count -eq 0) 'a placeholder record carries a cost amount'
                "real AI images=$($c.ai_image_count) known cost=`$$($c.ai_generation_cost) placeholders=$($c.placeholder_image_count) (fallback $($c.fallback_placeholder_count)) unknown-cost=$($c.unknown_cost_count) totals_complete=$($c.totals_complete) per-minute=null"
            })
            [void](Test-Check 'Workflow: jobs list + diagnostics + logs' {
                $j = Invoke-Api GET "/jobs?project_id=$($ctx.pid)"
                Assert-True ($j.Status -eq 200 -and @($j.Json).Count -ge 2) 'jobs list'
                $d = Invoke-Api GET '/diagnostics'
                Assert-True ($d.Status -eq 200) 'diagnostics'
                $l = Invoke-Api GET '/diagnostics/logs?lines=50'
                Assert-True ($l.Status -eq 200) 'logs'
                "$(@($j.Json).Count) jobs; jobs_by_status=$(($d.Json.jobs_by_status | ConvertTo-Json -Compress))"
            })

            # ------------------------------------------------------------ negative cases
            [void](Test-Check 'Reject corrupt (truncated) PNG upload' {
                $bad = $png[0..([int]($png.Length / 2))]
                $r = Invoke-Api POST '/assets/import' -Content (New-MultipartImport @{ project_id = $ctx.pid; asset_type = 'SOURCE_PHOTO' } ([byte[]]$bad) 'truncated.png')
                Assert-True ($r.Status -eq 422 -and $r.Json.error_code -eq 'ASSET_INVALID') "status $($r.Status) $($r.Text)"
                "422 ASSET_INVALID: $($r.Json.error_message)"
            })
            [void](Test-Check 'Reject wrong-type upload (text as image)' {
                $txt = [System.Text.Encoding]::UTF8.GetBytes('this is not an image at all')
                $r = Invoke-Api POST '/assets/import' -Content (New-MultipartImport @{ project_id = $ctx.pid; asset_type = 'IMAGE' } $txt 'fake.png')
                Assert-True ($r.Status -eq 422 -and $r.Json.error_code -eq 'ASSET_INVALID') "status $($r.Status)"
                '422 ASSET_INVALID'
            })
            [void](Test-Check 'Reject unsafe source URL + unknown project' {
                $r = Invoke-Api POST '/assets/import' -Content (New-MultipartImport @{ project_id = $ctx.pid; asset_type = 'IMAGE'; source_url = 'javascript:alert(1)' } (New-TestPng 64 64 'u') 'u.png')
                Assert-True ($r.Status -eq 422 -and $r.Json.error_code -eq 'INVALID_URL') "url status $($r.Status)"
                $r2 = Invoke-Api GET '/projects/00000000-dead-beef-0000-000000000000'
                Assert-True ($r2.Status -eq 404 -and $r2.Json.error_code -eq 'NOT_FOUND' -and $r2.Text -notmatch 'Traceback') "404 status $($r2.Status)"
                'INVALID_URL 422, NOT_FOUND 404, no stack trace'
            })
        } else {
            Add-Result 'Workflow' 'BLOCKED' 'project creation failed'
        }

        # ---------------------------------------------------------------- occupied port
        [void](Test-Check 'Occupied port is detected (second server fails cleanly)' {
            $listener = New-Object System.Net.Sockets.TcpListener([System.Net.IPAddress]::Loopback, 0)
            $listener.Start()
            $busy = $listener.LocalEndpoint.Port
            try {
                $store2 = Join-Path $WorkDir 'storage_port_test'
                $b2 = Start-Backend -Port $busy -StorageRoot $store2 -Tag 'busy'
                $exited = $b2.Process.WaitForExit(30000)
                if (-not $exited) { Stop-Process -Id $b2.Process.Id -Force -ErrorAction SilentlyContinue; throw "server on occupied port $busy did not exit within 30s" }
                $err = Get-Content $b2.Err -Raw
                $b2.Process.WaitForExit()
                $ec = $b2.Process.ExitCode
                Assert-True (($null -ne $ec) -and ($ec -ne 0)) "exit code '$ec' (expected non-zero)"
                Assert-True ($err -match '10048|address already in use|error while attempting to bind|only one usage') 'no bind error reported'
                $fp = Get-FreePort
                Assert-True ($fp -ne $busy) 'free-port finder returned the busy port'
                "port $busy busy -> uvicorn exit code $ec with bind error; free-port finder chose $fp"
            } finally { $listener.Stop() }
        })
    }
} catch {
    Add-Result 'Self-test harness' 'FAIL' $_.Exception.Message
} finally {
    Stop-Started
    $still = @($Started | Where-Object { -not $_.HasExited })
    if ($still.Count -eq 0) { Add-Result 'Stopped only the backend process(es) this script started' 'PASS' ("PIDs: " + (($Started | ForEach-Object { $_.Id }) -join ', ')) }
    else { Add-Result 'Stopped only the backend process(es) this script started' 'FAIL' ("still running: " + (($still | ForEach-Object { $_.Id }) -join ', ')) }
    $Http.Dispose()
}

# ============================================================================= summary
$req = @($Results | Where-Object { $_.Required })
$pass = @($Results | Where-Object { $_.Status -eq 'PASS' }).Count
$fail = @($Results | Where-Object { $_.Status -eq 'FAIL' }).Count
$skip = @($Results | Where-Object { $_.Status -eq 'SKIP' }).Count
$blocked = @($Results | Where-Object { $_.Status -eq 'BLOCKED' }).Count
$reqNotPass = @($req | Where-Object { $_.Status -ne 'PASS' }).Count
Write-Host ''
Write-Host '================ SELF-TEST SUMMARY ================' -ForegroundColor Cyan
$Results | Format-Table -AutoSize Status, Required, Check | Out-Host
Write-Host ("PASS={0}  FAIL={1}  SKIP={2}  BLOCKED={3}  (required checks not passing: {4})" -f $pass, $fail, $skip, $blocked, $reqNotPass)
Write-Host "Administrator: $isAdmin (not required).  Logs and artefacts: $WorkDir"
$Results | ConvertTo-Json -Depth 4 | Set-Content -Path (Join-Path $WorkDir 'selftest_results.json') -Encoding UTF8
if ($reqNotPass -eq 0) { Write-Host 'RESULT: PASS' -ForegroundColor Green; exit 0 }
Write-Host 'RESULT: FAIL' -ForegroundColor Red
exit 1
