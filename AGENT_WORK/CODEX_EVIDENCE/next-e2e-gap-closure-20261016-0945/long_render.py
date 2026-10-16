"""Ten-minute, 140-shot fixture through real renderer jobs; bounded reduced profile."""
import hashlib, json, sys, time
from pathlib import Path
repo=Path(__file__).resolve().parents[3]
sys.path.insert(0,str(repo/'standalone/video-renderer'))
from renderer.config import Settings
from renderer.db import make_session_factory, TERMINAL
from renderer.jobs import JobService
from renderer.synthetic import long_package
out=Path(__file__).resolve().parent
work=Path('E:/Jana Soft/DefenceStudio/tmp/next-long-0945')
settings=Settings(storage_root=work/'storage',workers=1)
settings.ensure_dirs()
profile={'width':640,'height':360,'fps':12}
pkg=long_package(work/'package',profile=profile)
sf=make_session_factory(settings.db_path)
svc=JobService(settings,sf)
t=time.monotonic(); job,_=svc.create(str(pkg),idempotency_key='next-long-0945')
while True:
    row=svc.get(job.job_id)
    if row.status in TERMINAL: break
    if time.monotonic()-t>1200:
        svc.cancel(job.job_id); break
    time.sleep(1)
svc.shutdown(wait=True)
row=svc.get(job.job_id)
result={'profile':profile,'scope':'synthetic 140-shot duration acceptance; reduced resolution due to <1 GiB free RAM at baseline',
        'runtime_seconds':time.monotonic()-t,'job':row.snapshot()}
if row.output_path:
    result['actual_sha256']=hashlib.sha256(Path(row.output_path).read_bytes()).hexdigest()
    result['size_bytes']=Path(row.output_path).stat().st_size
    result['qc']=json.loads(Path(row.qc_path).read_text())
fresh=JobService(settings,make_session_factory(settings.db_path))
fresh.recover()
result['restart_snapshot']=fresh.get(job.job_id).snapshot()
fresh.shutdown(wait=True)
(out/'long-render.json').write_text(json.dumps(result,indent=2))
print(json.dumps(result,indent=2))
assert row.status=='SUCCEEDED'
assert result['actual_sha256']==row.output_sha256
assert result['restart_snapshot']['output_sha256']==row.output_sha256
