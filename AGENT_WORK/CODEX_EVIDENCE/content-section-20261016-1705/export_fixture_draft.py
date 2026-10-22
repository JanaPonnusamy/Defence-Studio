"""Read only this run's fixture draft, never a production job."""
import json
import sys
from pathlib import Path
repo=Path(__file__).resolve().parents[3]
out=Path(__file__).resolve().parent
sys.path[:0]=[str(repo/'backend'),str(repo/'backend/tests')]
import conftest
from defence_studio.services.script import ScriptService
from defence_studio.services.review import ReviewService
state=json.loads((out/'browser-state.json').read_text())
assert state['database'].startswith('C:\\dstmp\\content-browser-20261016-1705\\')
service=ScriptService(Path(state['database']),Path(state['root']))
draft=service.get_revision(state['job_id'],state['owner'],'en',1)
sources=ReviewService(Path(state['database']),Path(state['root'])).list_sources(state['job_id'],state['owner'])
(out/'fixture-draft.json').write_text(json.dumps({'scope':state['scope'],'draft':draft,'sources':sources},indent=2),encoding='utf-8')
text='# Controlled test draft — not a live AI generation\n\nResearch HTTP, review approvals and model response were fixtures. This short draft proves persistence/display, not a finished production script.\n\n'
text+='## '+draft['title']+'\n\n'
for section in draft['sections']:
    text+=section['narration']+'\n\n'+section['evidence'].get('qualification','')+'\n\n'
text+='Sources: https://www.pib.gov.in/PressReleaseIframePage.aspx?PRID=2323814&lang=2&reg=48\n'
(out/'fixture-draft.md').write_text(text,encoding='utf-8')
print('Read saved English v1; '+str(draft['word_count']['words'])+' words; '+draft['status'])
