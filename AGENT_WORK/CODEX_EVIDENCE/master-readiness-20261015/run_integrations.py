"""Start only isolated loopback services, then exercise real media handoffs."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import httpx

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[2]
TEMP = Path(tempfile.mkdtemp(prefix='ds-master-integration-'))
processes, logs = [], []
env = dict(os.environ)
for key in list(env):
    if any(s in key for s in ('OPENAI', 'YOUTUBE', 'LIVE_', 'SEARCH_API')):
        env.pop(key)
env.update(DEFENCE_STUDIO_SCRIPT_PROVIDER='offline')

def start(name, module, port, extra):
    python = Path(tempfile.gettempdir()) / f'defence-master-{name}-20261015/Scripts/python.exe'
    cwd = ROOT / ('backend' if name == 'backend' else f'standalone/{module}')
    log = (OUT / f'{name}-service-retry.log').open('w', encoding='utf-8')
    logs.append(log)
    entry = 'renderer.main:app' if name == 'render' else ('defence_studio.api.app:app' if name == 'backend' else 'app.main:app')
    proc = subprocess.Popen([str(python), '-m', 'uvicorn', entry, '--host', '127.0.0.1', '--port', str(port)],
                            cwd=cwd, env={**env, **extra}, stdout=log, stderr=log)
    processes.append(proc)
    url = f'http://127.0.0.1:{port}'
    for _ in range(12):
        if proc.poll() is not None:
            raise RuntimeError(f'{name} failed startup; see service log')
        try:
            response = httpx.get(url + '/health', timeout=10, trust_env=False)
            if response.status_code == 200:
                return response.json()
        except httpx.HTTPError:
            pass
        time.sleep(.25)
    raise RuntimeError(f'{name} startup timeout')

try:
    health = {}
    health['audio'] = start('audio','audio-module',8051,{
        'AUDIO_MODULE_STORAGE_ROOT':str(TEMP/'audio'),
        'AUDIO_MODULE_DATABASE_URL':'sqlite:///'+(TEMP/'audio.sqlite3').as_posix()})
    health['visual'] = start('visual','video-visual-module',8052,{'VIDEO_MODULE_STORAGE_ROOT':str(TEMP/'visual')})
    health['render'] = start('render','video-renderer',8063,{'RENDERER_STORAGE_ROOT':str(TEMP/'render')})
    (OUT/'isolated-health.json').write_text(json.dumps(health,indent=2))
    env.update(DEFENCE_STUDIO_AUDIO_MODULE_URL='http://127.0.0.1:8051',
               DEFENCE_STUDIO_VISUAL_MODULE_URL='http://127.0.0.1:8052',
               DEFENCE_STUDIO_RENDERER_URL='http://127.0.0.1:8063')
    # Actual environment variable name is verified in renderer_module.py.
    tests=['tests/test_audio_rm4.py::test_real_audio_module_integration_english',
           'tests/test_visual_rm3.py::test_real_visual_module_integration',
           'tests/test_render_rm5.py::test_real_end_to_end_audio_visual_render_subtitles']
    command=[sys.executable,'-m','pytest','-q','-ra','-p','no:cacheprovider',*tests]
    with (OUT/'real-module-tests.log').open('w') as log:
        result=subprocess.run(command,cwd=ROOT/'backend',env=env,stdout=log,stderr=log,timeout=900)
    (OUT/'real-module-tests.exit').write_text(str(result.returncode))
    video=Path(tempfile.gettempdir())/'ds_rm5_e2e.mp4'
    if result.returncode == 0 and video.exists():
        shutil.copy2(video, OUT/'real-module-subtitled.mp4')
    (OUT/'integration-runtime.json').write_text(json.dumps({'disposable_root':str(TEMP),'command':command,
        'fixture_limit':'Script provider/reviews are isolated synthetic fixtures, not a live OpenAI/editorial approval. Media services are real.'},indent=2))
    sys.exit(result.returncode)
finally:
    for proc in reversed(processes):
        if proc.poll() is None:
            proc.terminate()
            proc.wait(timeout=30)
    for log in logs:
        log.close()
