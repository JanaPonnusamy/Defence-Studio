"""Real isolated renderer acceptance; never deletes or reuses an existing root."""
import hashlib
import json
import sys
import time
from pathlib import Path

repo = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(repo / 'standalone/video-renderer'))
from renderer.config import Settings
from renderer.db import make_session_factory, TERMINAL
from renderer.jobs import JobService
from renderer.synthetic import small_package, long_package

kind = sys.argv[1]
assert kind in ('small', 'long')
work = repo / 'tmp' / ('production-1080-' + kind + '-20261016-1155')
work.mkdir(parents=True, exist_ok=False)
settings = Settings(storage_root=work / 'storage', workers=1)
settings.ensure_dirs()
profile = {'width': 1920, 'height': 1080, 'fps': 25}
pkg = (small_package if kind == 'small' else long_package)(work / 'package', profile=profile)
svc = JobService(settings, make_session_factory(settings.db_path))
started = time.monotonic()
job, _ = svc.create(str(pkg), idempotency_key='production-1080-' + kind)
last = -1
while True:
    row = svc.get(job.job_id)
    if row.status in TERMINAL:
        break
    bucket = int(row.progress * 10)
    if bucket != last:
        print(json.dumps(row.snapshot()), flush=True)
        last = bucket
    if time.monotonic() - started > 3600:
        svc.cancel(job.job_id)
        break
    time.sleep(1)
svc.shutdown(wait=True)
row = svc.get(job.job_id)
result = {'scope': 'synthetic renderer acceptance; tone audio, no narration/human approval',
          'profile': profile, 'runtime_seconds': time.monotonic() - started,
          'storage_root': str(settings.storage_root), 'job': row.snapshot()}
if row.output_path:
    result['output_path'] = row.output_path
    with Path(row.output_path).open('rb') as f:
        result['actual_sha256'] = hashlib.file_digest(f, 'sha256').hexdigest()
    result['qc'] = json.loads(Path(row.qc_path).read_text())
fresh = JobService(settings, make_session_factory(settings.db_path))
fresh.recover()
result['restart_snapshot'] = fresh.get(job.job_id).snapshot()
fresh.shutdown(wait=True)
Path(__file__).with_name(kind + '-1080.json').write_text(json.dumps(result, indent=2))
print(json.dumps({k: v for k, v in result.items() if k != 'qc'}, indent=2), flush=True)
assert row.status == 'SUCCEEDED', (row.error_code, row.error_message)
assert result['actual_sha256'] == row.output_sha256
assert result['restart_snapshot']['output_sha256'] == row.output_sha256
assert all(c['ok'] for c in result['qc']['checks'])
