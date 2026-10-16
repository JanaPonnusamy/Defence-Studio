"""Fresh sequential regression runs, exact commands, elapsed time and exit codes."""
import json, os, shutil, subprocess, sys, time
from pathlib import Path
repo=Path(__file__).resolve().parents[3]; out=Path(__file__).resolve().parent
env=os.environ.copy(); env['PYTHONPATH']=str(repo/'backend')
suites=[('backend',repo/'backend',[sys.executable,'-m','pytest','-q','-ra','--basetemp','C:/dstmp/next-regression-0945']),
 ('audio',repo/'standalone/audio-module',[sys.executable,'-m','pytest','-q','-ra']),
 ('voice',repo/'components/voice-engine',[sys.executable,'-m','pytest','-q','-ra']),
 ('visual',repo/'standalone/video-visual-module',[sys.executable,'-m','pytest','-q','-ra']),
 ('renderer',repo/'standalone/video-renderer',[sys.executable,'-m','pytest','-q','-ra']),
 ('frontend',repo/'frontend',[shutil.which('npm.cmd'),'test'])]
results=[]
for name,cwd,cmd in suites:
    started=time.monotonic()
    with open(out/(name+'-fresh.log'),'wb') as log:
        p=subprocess.run(cmd,cwd=cwd,env=env,stdout=log,stderr=subprocess.STDOUT)
    result={'suite':name,'cwd':str(cwd),'command':cmd,'exit':p.returncode,'seconds':round(time.monotonic()-started,2)}
    results.append(result)
    (out/(name+'-fresh.exit')).write_text(str(p.returncode))
    (out/'fresh-suites.json').write_text(json.dumps(results,indent=2))
    print(json.dumps(result),flush=True)
raise SystemExit(0 if all(r['exit']==0 for r in results) else 1)
