$ErrorActionPreference = 'Stop'
$auditRoot = 'E:\Jana Soft\DefenceStudio\analysis\phase0-baseline'
$env:PYTHONDONTWRITEBYTECODE = '1'
$env:QVAULT_DB_BACKEND = 'sqlite'
$env:QVAULT_DATABASE_URL = 'sqlite:///E:/Jana Soft/DefenceStudio/analysis/phase0-baseline/baseline.db'
$env:QVAULT_STORAGE_DIR = "$auditRoot\storage"
$env:QVAULT_OUTPUT_DIR = "$auditRoot\output"
$env:QVAULT_BRANDING_CONFIG_PATH = "$auditRoot\branding.json"
$env:QVAULT_YTDLP_COOKIES_FILE = ''
$env:QVAULT_YTDLP_COOKIES_FROM_BROWSER = ''
Set-Location 'E:\QVault\backend'
& '.\.venv\Scripts\python.exe' -c "from app.core.app import app; print('ROUTE_COUNT', len(app.routes))" *> "$auditRoot\backend-import.log"
"import_exit=$LASTEXITCODE"
& '.\.venv\Scripts\python.exe' -m pytest -q -p no:cacheprovider *> "$auditRoot\pytest.log"
"pytest_exit=$LASTEXITCODE"
Get-Content "$auditRoot\backend-import.log" -Tail 4
Get-Content "$auditRoot\pytest.log" -Tail 25
