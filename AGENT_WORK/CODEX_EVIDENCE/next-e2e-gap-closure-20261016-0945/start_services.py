"""Start only isolated, task-owned loopback services; never terminate other processes."""
import json, os, socket, subprocess, sys, time, urllib.request
from pathlib import Path
repo = Path(__file__).resolve().parents[3]
out = Path(__file__).resolve().parent
base = Path('C:/dstmp/next-modules-20261016-0945')
specs = [('audio-module','app.main:app',8001, {'AUDIO_MODULE_STORAGE_ROOT':str(base/'audio'), 'AUDIO_MODULE_DATABASE_URL':f'sqlite:///{(base/"audio.db").as_posix()}'}),
 ('video-visual-module','app.main:app',8765, {'VIDEO_MODULE_STORAGE_ROOT':str(base/'visual')}),
 ('video-renderer','renderer.main:app',8780, {'RENDERER_STORAGE_ROOT':str(base/'renderer')})]
base.mkdir(parents=True, exist_ok=True)
results=[]
for name, app, port, extra in specs:
    with socket.socket() as s:
        if s.connect_ex(('127.0.0.1',port)) == 0:
            results.append({'service':name,'blocked':'port already owned'}); continue
    env=os.environ.copy(); env.update(extra)
    log=open(out/(name+'-startup.log'),'ab')
    p=subprocess.Popen([sys.executable,'-m','uvicorn',app,'--host','127.0.0.1','--port',str(port)],cwd=repo/'standalone'/name,env=env,stdout=log,stderr=log,creationflags=subprocess.CREATE_NO_WINDOW)
    result={'service':name,'pid':p.pid,'port':port,'storage':extra}
    for _ in range(45):
        try:
            with urllib.request.urlopen(f'http://127.0.0.1:{port}/health',timeout=2) as r:
                result['health']=json.load(r)
            break
        except Exception:
            if p.poll() is not None:
                result['exit']=p.returncode; break
            time.sleep(1)
    results.append(result)
(out/'service-startup.json').write_text(json.dumps(results,indent=2))
print(json.dumps(results,indent=2))
