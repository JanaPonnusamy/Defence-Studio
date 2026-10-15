"""Read protected current status only; mutate exclusively new disposable databases."""
import hashlib, json, os, sqlite3, subprocess, sys, tempfile
from pathlib import Path

OUT=Path(__file__).resolve().parent
ROOT=OUT.parents[2]
protected=ROOT/'AGENT_WORK/CURSOR_EVIDENCE/episode/ds.sqlite3'
before=hashlib.sha256(protected.read_bytes()).hexdigest()
with sqlite3.connect(protected.as_uri()+'?mode=ro',uri=True) as db:
    db.row_factory=sqlite3.Row
    rows=[dict(r) for r in db.execute('SELECT language,version,status,revision_sha256 FROM script_revisions WHERE job_id=?',
         ('522dc4ce-16d0-4680-a450-fcd841180c69',))]
assert any(r['language']=='en' and r['version']==1 and r['status']=='AWAITING_REVIEW' for r in rows)
assert before==hashlib.sha256(protected.read_bytes()).hexdigest()
(OUT/'protected-status.json').write_text(json.dumps({'sha256':before,'revisions':rows,'history_accessed':False},indent=2))

with tempfile.TemporaryDirectory(prefix='ds-master-gates-') as folder:
    os.environ['DEFENCE_STUDIO_AUTH_SECRET']='disposable-gate-test-secret-32-bytes-long'
    os.environ['DEFENCE_STUDIO_DB_PATH']=str(Path(folder)/'audit.sqlite3')
    os.environ['DEFENCE_STUDIO_STORAGE_PATH']=str(Path(folder)/'storage')
    os.environ['DEFENCE_STUDIO_SCRIPT_PROVIDER']='offline'
    sys.path.insert(0,str(ROOT/'backend/tests'))
    # The project helper creates signed bearer tokens; conftest isolates its own default app DB.
    from conftest import access_token, SECRET
    from defence_studio.api.app import create_app
    from fastapi.testclient import TestClient
    app=create_app(Path(folder)/'audit.sqlite3',SECRET,Path(folder)/'storage')
    headers={'Authorization':'Bearer '+access_token('master-auditor'),'Idempotency-Key':'master-audit'}
    results=[]
    with TestClient(app) as client:
        response=client.post('/api/jobs',json={'topic':'Synthetic audit gates','language':'en'},headers=headers)
        assert response.status_code in (200,201),response.text
        job=response.json()['job_id']
        base='/api/jobs/'+job
        cases=[('GET',base,None,{},401),
               ('GET',base,None,{'Authorization':'Bearer '+access_token('other')},404),
               ('GET',base+'/script/export/en',None,headers,409),
               ('POST',base+'/scene/generate',{'language':'en','target_total_seconds':300,'words_per_minute':150,'default_transition':'cut'},headers,409),
               ('POST',base+'/audio/en/generate',{},headers,409),
               ('POST',base+'/visual/en/plan',{'options':{}},headers,409),
               ('POST',base+'/render/en/start',{'audio_version':1,'visual_version':1},headers,409),
               ('POST',base+'/qc/en/run',{'render_version':1},headers,404),
               ('POST',base+'/package/en/build',{'overrides':{}},headers,409)]
        for method,url,body,h,expected in cases:
            r=client.request(method,url,headers=h,json=body)
            results.append({'method':method,'path':url.replace(job,'DISPOSABLE_JOB'),'expected':expected,'actual':r.status_code})
            assert r.status_code==expected,(url,r.status_code,r.text)
    (OUT/'direct-api-gates.json').write_text(json.dumps(results,indent=2))

tracked=subprocess.check_output(['git','ls-files'],cwd=ROOT,text=True).splitlines()
inventory={'tracked_site_packages':sum('/site-packages/' in p for p in tracked),
           'tracked_databases':[p for p in tracked if p.endswith(('.db','.sqlite3','.sqlite'))],
           'tracked_env_files':[p for p in tracked if p=='.env' or p.endswith('/.env')],
           'staged':subprocess.check_output(['git','diff','--cached','--name-only'],cwd=ROOT,text=True).splitlines()}
(OUT/'repository-hygiene.json').write_text(json.dumps(inventory,indent=2))
print('PASS: current protected revision unchanged; nine direct API negative gates; repository inventory recorded')
