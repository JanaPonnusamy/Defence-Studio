"""Fresh-process application read after the fixture harness exited."""
import base64,hashlib,hmac,json,os,sys,time
from pathlib import Path
repo=Path(__file__).resolve().parents[3]; out=Path(__file__).resolve().parent
sys.path.insert(0,str(repo/'backend'))
report=json.loads((out/'fixture-e2e.json').read_text())
db=Path(report['database']); root=Path('C:/dstmp/next-fixture-0945/store')
os.environ['DEFENCE_STUDIO_AUTH_SECRET']='isolated-restart-check-32-byte-test-secret'
os.environ['DEFENCE_STUDIO_DB_PATH']=str(db)
os.environ['DEFENCE_STUDIO_STORAGE_PATH']=str(root)
from defence_studio.services.youtube import YouTubeService
from defence_studio.services.jobs import JobService
from defence_studio.api.app import create_app
from fastapi.testclient import TestClient
job=report['job_id']; owner='e2e-acceptance'
svc=YouTubeService(db,root)
app=create_app(db,os.environ['DEFENCE_STUDIO_AUTH_SECRET'],root)
with TestClient(app) as client:
    # Unauthenticated approval is rejected at the server, never forwarded to review writers.
    url=f'/api/jobs/{job}/script/revisions/en/1/approve'
    body={'revision_sha256':'0'*64,'note':'unauthorized isolated test'}
    response=client.post(url,json=body,headers={'Idempotency-Key':'unauthorized-probe'})
    assert response.status_code==401, response.text
    enc=lambda x:base64.urlsafe_b64encode(json.dumps(x).encode()).rstrip(b'=').decode()
    signing=enc({'alg':'HS256','typ':'JWT'})+'.'+enc({'sub':'other-owner','exp':int(time.time())+60})
    signature=base64.urlsafe_b64encode(hmac.new(os.environ['DEFENCE_STUDIO_AUTH_SECRET'].encode(),signing.encode(),hashlib.sha256).digest()).rstrip(b'=').decode()
    other=client.post(url,json=body,headers={'Authorization':'Bearer '+signing+'.'+signature,'Idempotency-Key':'other-owner-probe'})
    assert other.status_code==404, other.text
    package=svc.package_status(job,owner,'en')['items'][-1]
    gate=svc.approved_render(job,owner,'en')
    body=svc.read_integration_body(job,owner,'en','publish_package',package['version'])
    checks=[]
    for name,ref in body['files'].items():
        if isinstance(ref,dict) and 'uri' in ref and 'sha256' in ref:
            data,mime=svc.package_file(job,owner,'en',package['version'],name)
            checks.append({'name':name,'download_gate_passed':True,'mime':mime,
                           'sha256_matches':hashlib.sha256(data).hexdigest()==ref['sha256']})
    assert checks and all(c['sha256_matches'] for c in checks)
result={'pid':os.getpid(),'database':str(db),'job_id':job,'package_status':package['status'],'approved_render':gate['render_version'],
        'unauthenticated_approval_probe_status':response.status_code,'other_owner_approval_status':other.status_code,'artifact_checks':checks,
        'job_status':JobService(db).get(job,owner)['status']}
(out/'restart-check.json').write_text(json.dumps(result,indent=2))
print(json.dumps(result,indent=2))
