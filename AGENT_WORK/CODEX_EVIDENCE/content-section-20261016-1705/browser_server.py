"""Isolated content test app. Fixture research/review + mocked model, never a paid provider."""
import json
import os
import sys
import time
from pathlib import Path
repo=Path(__file__).resolve().parents[3]
out=Path(__file__).resolve().parent
work=Path('C:/dstmp/content-browser-20261016-1705')
work.mkdir(parents=True,exist_ok=False)
os.environ.update(DEFENCE_STUDIO_AUTH_SECRET='content-browser-isolated-signing-secret-20261016',
 DEFENCE_STUDIO_DB_PATH=str(work/'defaults.sqlite3'),DEFENCE_STUDIO_STORAGE_PATH=str(work/'default-store'),
 DEFENCE_STUDIO_DEV_MODE='0',DEFENCE_STUDIO_DEV_AUTH_BYPASS='0',DEFENCE_STUDIO_VOICE_WORKER='0',DEFENCE_STUDIO_YT_WORKER='0')
sys.path[:0]=[str(repo/'backend'),str(repo/'backend/tests')]
from test_content_flow import build_content_fixture, MockContentProvider
from test_script_phase3 import _Result
from defence_studio.providers.openai_text import ProviderError
from defence_studio.services.script import ScriptService
from defence_studio.api.app import create_app
import uvicorn
db,root,job,owner=build_content_fixture(work)
mode_file=work/'mock-mode.txt'
mode_file.write_text('valid')
class BrowserMockProvider(MockContentProvider):
    def generate(self,**kwargs):
        mode=mode_file.read_text().strip()
        with (work/'calls.jsonl').open('a') as f: f.write(json.dumps({'mode':mode})+'\n')
        time.sleep(.25)
        if mode=='error': raise ProviderError('test_unavailable','Mock content provider unavailable',retryable=False)
        if mode=='empty': return _Result('')
        result=super().generate(**kwargs)
        if mode=='invalid':
            body=json.loads(result.text)
            body['sections'][0]['evidence']['claim_ids']=['claim-does-not-exist']
            return _Result(json.dumps(body))
        return result
ScriptService._resolve_provider=lambda self,owner_id=None: BrowserMockProvider()
app=create_app(db,os.environ['DEFENCE_STUDIO_AUTH_SECRET'],root)
state={'pid':os.getpid(),'database':str(db),'root':str(root),'job_id':job,'owner':owner,
       'mode_file':str(mode_file),'calls_file':str(work/'calls.jsonl'),
       'scope':'real Chrome + app HTTP; research transport, review and model are controlled fixtures'}
(out/'browser-state.json').write_text(json.dumps(state,indent=2))
uvicorn.run(app,host='127.0.0.1',port=8038)
