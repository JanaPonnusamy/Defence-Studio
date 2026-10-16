"""Disposable copy of the retained fixture. No production DB or worker is used."""
import json
import os
import shutil
import sqlite3
import subprocess
from pathlib import Path

repo = Path(__file__).resolve().parents[3]
out = Path(__file__).resolve().parent
old = repo / 'AGENT_WORK/CODEX_EVIDENCE/next-e2e-gap-closure-20261016-0945/fixture-e2e.json'
report = json.loads(old.read_text())
work = Path('C:/dstmp/production-browser-20261016-1155')
work.mkdir(parents=True, exist_ok=False)
db = work / 'ds.sqlite3'
with sqlite3.connect(Path(report['database']).as_uri() + '?mode=ro&immutable=1', uri=True) as src:
    with sqlite3.connect(db) as dst:
        src.backup(dst)
shutil.copytree('C:/dstmp/next-fixture-0945/store', work / 'store')
env = os.environ.copy()
env.update(DEFENCE_STUDIO_DB_PATH=str(db), DEFENCE_STUDIO_STORAGE_PATH=str(work / 'store'),
           DEFENCE_STUDIO_AUTH_SECRET='production-browser-isolated-test-secret-20261016',
           DEFENCE_STUDIO_DEV_MODE='1', DEFENCE_STUDIO_DEV_AUTH_BYPASS='0',
           DEFENCE_STUDIO_VOICE_WORKER='0', DEFENCE_STUDIO_YT_WORKER='0',
           PYTHONPATH=str(repo / 'backend'))
log = (out / 'browser-server.log').open('w')
proc = subprocess.Popen(['C:/dstmp/next-clean-0945/Scripts/python.exe', '-m', 'uvicorn',
                         'defence_studio.api.app:app', '--host', '127.0.0.1', '--port', '8037'],
                        cwd=repo, env=env, stdout=log, stderr=subprocess.STDOUT,
                        creationflags=subprocess.CREATE_NO_WINDOW)
state = {'pid': proc.pid, 'database': str(db), 'storage': str(work / 'store'),
         'job_id': report['job_id'], 'owner': 'e2e-acceptance',
         'scope': 'real Chrome/app endpoints; copied synthetic fixture, simulated prior approvals; auth bypass disabled'}
(out / 'browser-state.json').write_text(json.dumps(state, indent=2))
print(json.dumps(state))
