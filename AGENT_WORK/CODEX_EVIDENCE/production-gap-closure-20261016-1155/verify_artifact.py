"""Fresh OS-process artifact/recovery verification and read-only protected-state check."""
import hashlib
import json
import sqlite3
import subprocess
import sys
from pathlib import Path
repo=Path(__file__).resolve().parents[3]
out=Path(__file__).resolve().parent
sys.path.insert(0,str(repo/'standalone/video-renderer'))
from renderer.config import Settings
from renderer.db import make_session_factory
from renderer.jobs import JobService
result=json.loads((out/'long-1080.json').read_text())
path=Path(result['output_path'])
settings=Settings(storage_root=Path(result['storage_root']),workers=1)
svc=JobService(settings,make_session_factory(settings.db_path))
svc.recover()
row=svc.get(result['job']['job_id'])
with path.open('rb') as f: sha=hashlib.file_digest(f,'sha256').hexdigest()
probe_cmd=['ffprobe','-v','error','-show_streams','-show_format','-of','json',str(path)]
probe=subprocess.run(probe_cmd,capture_output=True,text=True,check=True)
decode_cmd=['ffmpeg','-hide_banner','-v','error','-threads','2','-i',str(path),'-f','null','-']
decode=subprocess.run(decode_cmd,capture_output=True,text=True,timeout=600)
audio_cmd=['ffmpeg','-hide_banner','-i',str(path),'-vn','-af','volumedetect','-f','null','-']
audio=subprocess.run(audio_cmd,capture_output=True,text=True,timeout=180)
(out/'audio-continuity.log').write_text(audio.stderr)
protected=[]
for name in ['data/defence_studio.sqlite3','AGENT_WORK/CURSOR_EVIDENCE/episode/ds.sqlite3']:
    p=repo/name
    with p.open('rb') as f: h=hashlib.file_digest(f,'sha256').hexdigest()
    protected.append({'path':str(p),'sha256':h})
episode=repo/'AGENT_WORK/CURSOR_EVIDENCE/episode/ds.sqlite3'
with sqlite3.connect(episode.as_uri()+'?mode=ro&immutable=1',uri=True) as c:
    revisions=c.execute('SELECT language,version,status FROM script_revisions WHERE job_id=?',
                        ('522dc4ce-16d0-4680-a450-fcd841180c69',)).fetchall()
old=repo/'tmp/next-long-0945/storage/renders/r_764e7fd6b58744/output.mp4'
with old.open('rb') as f: old_sha=hashlib.file_digest(f,'sha256').hexdigest()
verification={'fresh_os_process':True,'job':row.snapshot(),'sha256':sha,'size_bytes':path.stat().st_size,
 'probe_command':probe_cmd,'probe_exit':probe.returncode,'probe':json.loads(probe.stdout),
 'decode_command':decode_cmd,'decode_exit':decode.returncode,'decode_stderr':decode.stderr,
 'audio_command':audio_cmd,'audio_exit':audio.returncode,'protected_files':protected,
 'protected_revisions':revisions,'previous_360p_sha256':old_sha}
(out/'artifact-and-protection.json').write_text(json.dumps(verification,indent=2))
svc.shutdown(wait=True)
assert sha==row.output_sha256==result['actual_sha256']
assert row.status=='SUCCEEDED' and row.frames_done==row.frames_total==15000
assert decode.returncode==0 and not decode.stderr.strip() and audio.returncode==0
assert revisions==[('en',1,'AWAITING_REVIEW')]
assert protected[0]['sha256']=='360d3870a70d35c418de478cf291f28672abc2d24276e04db12e01837276d125'
assert protected[1]['sha256']=='94c3ef0f41c26c298bc9ff04ac5e1afa80e6fe614f16360499ebd2ee17a9e68c'
assert old_sha=='a6cc376ee3c1d26e8a53f55390e2ea29931ded0a6a256804466600c854e9723b'
print('Fresh-process checksum, recovery, full decode, audio and protected state verified')
