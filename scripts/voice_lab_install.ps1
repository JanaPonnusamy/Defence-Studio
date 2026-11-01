# Voice Lab: create the ISOLATED inference environment (.venv-voicelab) with pinned packages.
# Never touches backend/.venv or the system Python packages.
# Usage:  powershell -ExecutionPolicy Bypass -File scripts\voice_lab_install.ps1 [-Model parler-tts-mini-v1.1]
param([string]$Model = "")
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$venv = if ($env:DEFENCE_STUDIO_VOICE_LAB_VENV) { $env:DEFENCE_STUDIO_VOICE_LAB_VENV } else { Join-Path $root ".venv-voicelab" }
$py = Join-Path $venv "Scripts\python.exe"
if (-not (Test-Path $py)) {
    py -3 -m venv $venv
    if ($LASTEXITCODE -ne 0) { throw "could not create $venv" }
}
& $py -m pip install --upgrade pip
# Pinned: parler-tts commit from backend/config/voice_lab_models.json. Its requirements pull torch from PyPI
# (Windows PyPI wheels are CPU builds); the verified 2026-11-01 install resolved torch 2.15.0+cpu and
# transformers 4.46.1. The exact resolved set is recorded in models\voice_lab\freeze.txt.
& $py -m pip install "git+https://github.com/huggingface/parler-tts.git@d108732cd57788ec86bc857d99a6cabd66663d68" soundfile psutil
if ($LASTEXITCODE -ne 0) { throw "parler-tts install failed" }
& $py -m pip freeze | Out-File -Encoding utf8 (Join-Path $root "models\voice_lab\freeze.txt")
& $py -I -c "import torch, parler_tts; print('torch', torch.__version__, 'cuda', torch.cuda.is_available())"
if ($Model) {
    # Gated models need HF_TOKEN in the environment (owner-provided; never pasted into code or chat).
    & $py -I (Join-Path $root "backend\scripts\voice_lab_download.py") $Model
}
