"""Disposable, real-provider acceptance. Never opens a Defence Studio episode DB."""
import hashlib, json, os, pathlib, shutil, subprocess, sys, tempfile, time
import httpx

ROOT = pathlib.Path(__file__).resolve().parents[3]
OUT = pathlib.Path(__file__).resolve().parent
MODULE = ROOT / 'standalone/audio-module'
STORAGE = pathlib.Path(tempfile.mkdtemp(prefix='ds-voice-acceptance-'))
ENV = {**os.environ, 'AUDIO_MODULE_STORAGE_ROOT': str(STORAGE),
       'AUDIO_MODULE_DATABASE_URL': 'sqlite:///' + (STORAGE/'audit.sqlite3').as_posix()}
BASE = 'http://127.0.0.1:8047'
TEXT = 'This is an independent defence briefing. Radar teams completed a scheduled exercise. The report separates confirmed observations from open questions. Further details require careful review.'
server = None
log = (OUT/'server.log').open('a', encoding='utf-8')
def save(name, obj):
    (OUT/name).write_text(json.dumps(obj, indent=2, ensure_ascii=False), encoding='utf-8')
def start():
    global server
    server = subprocess.Popen([sys.executable, '-m', 'uvicorn', 'app.main:app', '--host', '127.0.0.1', '--port', '8047'], cwd=MODULE, env=ENV, stdout=log, stderr=log)
    for _ in range(120):
        if server.poll() is not None: raise RuntimeError('server failed')
        try:
            if httpx.get(BASE+'/health').status_code == 200: return
        except httpx.HTTPError: pass
        time.sleep(.25)
    raise RuntimeError('startup timeout')
def stop():
    server.terminate()
    server.wait(timeout=20)
def wait(job):
    for _ in range(240):
        value = httpx.get(BASE+'/jobs/'+job, timeout=10).json()
        if value['status'] in ('SUCCEEDED', 'FAILED'): return value
        time.sleep(.5)
    raise RuntimeError('job timeout')
try:
    start()
    save('runtime.json', {'python': sys.executable, 'storage': str(STORAGE), 'database_disposable': True})
    profile = httpx.get(BASE+'/tts/delivery-profile').json()
    save('original-profile.json', profile)
    providers = httpx.get(BASE+'/tts/providers', timeout=70).json()
    save('providers.json', providers)
    local = next(p for p in providers['providers'] if p['provider']=='local-pyttsx3')
    assert local['available'], local
    voice = next(v for v in local['voices'] if 'en-us' in v['languages'])['id']
    results = []
    for name, controls in [('neutral', {'rate_wpm':170}), ('fast', {'rate_wpm':210}), ('pitch-plus2', {'rate_wpm':170,'pitch_semitones':2})]:
        body = {'text':TEXT,'language':'en-US','voice_id':voice,'provider':'local-pyttsx3','controls':controls}
        response = httpx.post(BASE+'/tts/synthesize', json=body)
        response.raise_for_status()
        job = wait(response.json()['job_id'])
        assert job['status']=='SUCCEEDED', job
        gen = httpx.get(BASE+'/generated/'+job['result_id']).json()
        wav = OUT/(name+'.wav')
        wav.write_bytes(httpx.get(BASE+gen['stream_url']).content)
        probe = subprocess.run(['ffprobe','-v','error','-show_format','-show_streams','-of','json',str(wav)], capture_output=True,text=True,check=True)
        save(name+'-ffprobe.json',json.loads(probe.stdout))
        decode = subprocess.run(['ffmpeg','-v','error','-i',str(wav),'-f','null','-'],capture_output=True,text=True)
        assert decode.returncode==0 and not decode.stderr, decode.stderr
        result = {'name':name,'request':body,'job':job,'generated':gen,'ffmpeg_decode_exit':decode.returncode,
                  'bytes':wav.stat().st_size,'sha256':hashlib.sha256(wav.read_bytes()).hexdigest()}
        save(name+'-analysis.json',result)
        results.append(result)
        print(name, gen['duration'],gen['provider_metadata']['measurements']['pitch_hz'],flush=True)
    bad = httpx.post(BASE+'/tts/synthesize',json={'text':'தமிழ் செய்தி','language':'ta','provider':'local-pyttsx3'}).json()
    tamil = wait(bad['job_id'])
    save('tamil-result.json',tamil)
    assert tamil['status']=='FAILED' and 'No installed voice' in tamil['error_message']
    stop()
    start()
    persisted = [httpx.get(BASE+'/jobs/'+r['job']['job_id']).json() for r in results]
    for before, after in zip(results,persisted):
        assert before['job']==after
        gen=httpx.get(BASE+'/generated/'+after['result_id']).json()
        assert gen==before['generated']
    save('restart.json',{'status':'PASS','jobs':persisted,'separate_server_process':True})
    metrics = [r['generated']['provider_metadata']['measurements'] for r in results]
    compare = {'fast_duration_ratio':metrics[1]['duration_seconds']/metrics[0]['duration_seconds'],
               'pitch_median_ratio':metrics[2]['pitch_hz']['median']/metrics[0]['pitch_hz']['median'],
               'target_pitch_ratio':2**(2/12), 'human_listening':'NOT_REVIEWED',
               'reference_comparison':'BLOCKED: no authorized reference audio obtained'}
    save('comparison.json',compare)
    assert compare['fast_duration_ratio'] < .95
    assert 1.06 < compare['pitch_median_ratio'] < 1.20
    print(json.dumps(compare),flush=True)
    # Keep this process alive only if requested, for an independently driven browser test.
    if '--serve' in sys.argv:
        (OUT/'server-ready.txt').write_text(str(server.pid))
        while not (OUT/'browser-finished.txt').exists(): time.sleep(.5)
finally:
    if server and server.poll() is None: stop()
    log.close()
