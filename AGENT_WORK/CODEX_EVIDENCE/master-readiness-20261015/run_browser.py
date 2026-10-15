import json, os, subprocess, sys, tempfile, time
from pathlib import Path
import httpx

OUT=Path(__file__).resolve().parent
ROOT=OUT.parents[2]
temp=Path(tempfile.mkdtemp(prefix='ds-master-browser-'))
env={**os.environ,'DEFENCE_STUDIO_DB_PATH':str(temp/'db.sqlite3'),
     'DEFENCE_STUDIO_STORAGE_PATH':str(temp/'storage'),
     'DEFENCE_STUDIO_AUTH_SECRET':'disposable-master-browser-secret-32-bytes-long',
     'DEFENCE_STUDIO_DEV_MODE':'1','DEFENCE_STUDIO_DEV_AUTH_BYPASS':'1',
     'DEFENCE_STUDIO_DEV_AUTH_OWNER':'e2e-operator','DEFENCE_STUDIO_SCRIPT_PROVIDER':'offline'}
for key in list(env):
    if 'OPENAI' in key or 'YOUTUBE' in key: env.pop(key)
seed=subprocess.run([sys.executable,str(ROOT/'frontend/tests/browser/seed_release_job.py')],env=env,
                    capture_output=True,text=True,check=True)
data=json.loads(seed.stdout)
(OUT/'browser-seed.json').write_text(json.dumps(data,indent=2))
with (OUT/'browser-server.log').open('w') as log:
    proc=subprocess.Popen([sys.executable,'-m','uvicorn','defence_studio.api.app:app','--host','127.0.0.1','--port','8054'],
                          env=env,stdout=log,stderr=log)
    try:
        for _ in range(100):
            try:
                if httpx.get('http://127.0.0.1:8054/health',timeout=1).status_code==200: break
            except httpx.HTTPError: pass
            time.sleep(.3)
        browser_env={**env,'BASE':'http://127.0.0.1:8054','JOB':data['populated_job'],'EMPTY_JOB':data['empty_job']}
        for name,port in [('release',9351),('release-tabs',9352)]:
            folder=OUT/name
            folder.mkdir(exist_ok=True)
            with (OUT/(name+'.log')).open('w') as result_log:
                r=subprocess.run(['node',str(ROOT/f'frontend/tests/browser/{name}.mjs')],
                    env={**browser_env,'OUT':str(folder),'CDP_PORT':str(port)},stdout=result_log,stderr=result_log,timeout=240)
            (OUT/(name+'.exit')).write_text(str(r.returncode))
    finally:
        proc.terminate()
        proc.wait(timeout=30)
