"""Compare HEAD with scoped fixes using isolated fixture data; never edits production."""
import json
import subprocess
import sys
from pathlib import Path
repo=Path(__file__).resolve().parents[3]
out=Path(__file__).resolve().parent
sys.path[:0]=[str(repo/'backend'),str(repo/'backend/tests')]
import conftest  # disposable defaults before app imports
from test_content_flow import build_content_fixture,MockContentProvider
from test_script_phase3 import GOOD_CONFIG
import defence_studio.services.script as module
from defence_studio.acquisition.source_policy import SourcePolicy
old_policy=json.loads(subprocess.check_output(['git','show','HEAD:backend/config/source_policy.json'],text=True))
url='https://www.pib.gov.in/PressReleaseIframePage.aspx?PRID=2323814&lang=2&reg=48'
result={'baseline_commit':subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),
 'pib_before_allowed':SourcePolicy.from_dict(old_policy).entry_for(url) is not None,
 'pib_after_allowed':SourcePolicy.load().entry_for(url) is not None}
work=Path('C:/dstmp/content-baseline-20261016-1705');work.mkdir(parents=True,exist_ok=False)
db,root,job,owner=build_content_fixture(work)
old_code=subprocess.check_output(['git','show','HEAD:backend/defence_studio/services/script.py'],text=True)
namespace=module.__dict__.copy()
exec(compile(old_code,'baseline-script.py','exec'),namespace)
config={**GOOD_CONFIG,'language':'en','terminology':{}}
baseline=namespace['ScriptService'](db,root,provider=MockContentProvider())
revision=baseline.generate(job,owner,config,'known-key')
leaked=baseline.generate(job,'other-owner',config,'known-key')
result['baseline_other_owner_replay_returned_revision']=leaked['version']==revision['version']
try: module.ScriptService(db,root).generate(job,'other-owner',config,'known-key')
except Exception as exc: result['fixed_other_owner_replay_exception']=type(exc).__name__
else: raise AssertionError('fixed replay must deny other owner')
(out/'baseline-reproduction.json').write_text(json.dumps(result,indent=2))
print(json.dumps(result,indent=2))
assert not result['pib_before_allowed'] and result['pib_after_allowed']
assert result['baseline_other_owner_replay_returned_revision']
