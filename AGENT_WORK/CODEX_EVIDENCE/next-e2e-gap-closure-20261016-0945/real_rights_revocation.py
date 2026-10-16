"""Real-output revocation acceptance on an isolated copy of the completed fixture job."""
import json,shutil,sqlite3,sys
from pathlib import Path
repo=Path(__file__).resolve().parents[3]; out=Path(__file__).resolve().parent
sys.path.insert(0,str(repo/'backend'))
from defence_studio.services.integration import IntegrationStale
from defence_studio.services.render import RenderService
from defence_studio.services.package import PackageService
report=json.loads((out/'fixture-e2e.json').read_text())
base=Path('C:/dstmp/next-revocation-0945'); base.mkdir(exist_ok=False)
db=base/'copy.sqlite3'; root=base/'store'
with sqlite3.connect(Path(report['database']).as_uri()+'?mode=ro&immutable=1',uri=True) as source:
    with sqlite3.connect(db) as dest: source.backup(dest)
job=report['job_id']; owner='e2e-acceptance'
shutil.copytree(Path('C:/dstmp/next-fixture-0945/store/jobs')/job,root/'jobs'/job)
svc=PackageService(db,root)
v=svc.get_integration(job,owner,'en','visual_timeline',1)
aid=v['input_binding']['assets'][0]['asset_id']
before=svc.package_file(job,owner,'en',1,'video_branded')
assert before[0], 'expected real pre-revocation media'
svc.set_rights(job,owner,aid,{'schema_version':'defence-studio.asset-rights.v1','rights_basis':'creator_owned',
    'attribution_required':False,'permitted_uses':['broadcast','web'],'commercial_use':'prohibited'},'revocation-acceptance')
render=RenderService(db,root)
checks=[]
for name,call in [
 ('new_render',lambda:render.start_render(job,owner,'en','revoked-live-output',audio_version=1,visual_version=1,background=False)),
 ('render_download',lambda:render.render_file(job,owner,'en',1,'subtitled')),
 ('package_download',lambda:svc.package_file(job,owner,'en',1,'video_branded')),
 ('final_export_gate',lambda:svc.approved_render(job,owner,'en')),
 ('fresh_process_equivalent_service',lambda:PackageService(db,root).package_file(job,owner,'en',1,'video_branded'))]:
    try: call()
    except IntegrationStale as exc: checks.append({'gate':name,'blocked':True,'reason':str(exc)})
    else: checks.append({'gate':name,'blocked':False})
result={'scope':'isolated copied fixture DB/store; actual previously rendered media','database':str(db),
        'job_id':job,'pre_revocation_video_bytes':len(before[0]),'checks':checks}
(out/'real-rights-revocation.json').write_text(json.dumps(result,indent=2))
print(json.dumps(result,indent=2))
assert all(c['blocked'] for c in checks)
