"""Fresh OS-process revocation check requested by the independent review."""
import json,os,sys
from pathlib import Path
repo=Path(__file__).resolve().parents[3]; out=Path(__file__).resolve().parent
sys.path.insert(0,str(repo/'backend'))
from defence_studio.services.integration import IntegrationStale
from defence_studio.services.render import RenderService
from defence_studio.services.package import PackageService
from defence_studio.services.youtube import YouTubeService
previous=json.loads((out/'real-rights-revocation.json').read_text())
db=Path(previous['database']); root=db.parent/'store'; job=previous['job_id']; owner='e2e-acceptance'
render=RenderService(db,root); package=PackageService(db,root)
checks=[]
for name,call in [('new_render',lambda:render.start_render(job,owner,'en','restart-revoked',audio_version=1,visual_version=1,background=False)),
                  ('render_download',lambda:render.render_file(job,owner,'en',1,'subtitled')),
                  ('package_download',lambda:package.package_file(job,owner,'en',1,'video_branded')),
                  ('final_export_gate',lambda:package.approved_render(job,owner,'en')),
                  ('streaming_upload_path',lambda:YouTubeService(db,root)._video_path({
                      'job_id':job,'owner_id':owner,'language':'en','package_version':1}))]:
    try: call()
    except IntegrationStale as exc: checks.append({'gate':name,'blocked':True,'reason':str(exc)})
    else: checks.append({'gate':name,'blocked':False})
result={'pid':os.getpid(),'fresh_os_process':True,'database':str(db),'job_id':job,'checks':checks}
(out/'rights-restart-check.json').write_text(json.dumps(result,indent=2))
print(json.dumps(result,indent=2))
assert all(c['blocked'] for c in checks)
