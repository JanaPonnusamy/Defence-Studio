"""Read-only protected DB and independent full media validation."""
import hashlib,json,sqlite3,subprocess,time
from pathlib import Path
repo=Path(__file__).resolve().parents[3]; out=Path(__file__).resolve().parent
before=json.loads((out/'protected-hashes-before.json').read_text(encoding='utf-8-sig'))
preservation={r['Path']:hashlib.sha256(Path(r['Path']).read_bytes()).hexdigest().upper()==r['Hash'] for r in before}
db=repo/'AGENT_WORK/CURSOR_EVIDENCE/episode/ds.sqlite3'
protected_hash=hashlib.sha256(db.read_bytes()).hexdigest()
historical=json.loads((repo/'AGENT_WORK/CODEX_EVIDENCE/master-readiness-20261015/protected-status.json').read_text())
preservation['protected_episode_matches_retained_snapshot']=protected_hash==historical['sha256']
with sqlite3.connect(db.as_uri()+'?mode=ro&immutable=1',uri=True) as c:
    columns=[r[1] for r in c.execute('pragma table_info(script_revisions)')]
    fields=[k for k in ['language','version','status'] if k in columns]
    scripts=c.execute('select '+','.join(fields)+' from script_revisions where job_id=?',('522dc4ce-16d0-4680-a450-fcd841180c69',)).fetchall()
result={'protected_files_unchanged':preservation,'protected_script_fields':fields,'protected_script_rows':scripts,'artifacts':[]}
long=json.loads((out/'long-render.json').read_text())
paths=[Path(long['qc']['output']['path'])]
renderer_db=repo/'tmp/next-long-0945/storage/renderer.db'
with sqlite3.connect(renderer_db.as_uri()+'?mode=ro&immutable=1',uri=True) as c:
    persisted=c.execute('select status,output_path,output_sha256 from render_jobs where job_id=?',
                        (long['job']['job_id'],)).fetchone()
result['long_render_fresh_process_persisted_row']=persisted
assert persisted[0]=='SUCCEEDED' and persisted[2]==long['actual_sha256']
fixture=json.loads((out/'fixture-e2e.json').read_text())
if fixture.get('job_id'):
    paths+=list((Path('C:/dstmp/next-fixture-0945/store/jobs')/fixture['job_id']/'render/en').glob('*-subtitled.mp4'))
    with sqlite3.connect(Path(fixture['database']).as_uri()+'?mode=ro&immutable=1',uri=True) as c:
        uri=c.execute("select uri from integration_artifacts where job_id=? and kind='publish_package' and status='APPROVED' order by version desc",(fixture['job_id'],)).fetchone()[0]
    store=Path('C:/dstmp/next-fixture-0945/store')
    package=json.loads((store/uri).read_text())
    branded=package['files']['video_branded']
    paths.append(store/branded['uri'])
    assert hashlib.sha256(paths[-1].read_bytes()).hexdigest()==branded['sha256']
for path in paths:
    t=time.monotonic()
    probe_cmd=['ffprobe','-v','error','-show_streams','-show_format','-of','json',str(path)]
    probe=subprocess.run(probe_cmd,capture_output=True,text=True)
    decode_cmd=['ffmpeg','-v','error','-i',str(path),'-f','null','-']
    decode=subprocess.run(decode_cmd,capture_output=True,text=True)
    result['artifacts'].append({'path':str(path),'size':path.stat().st_size,'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),
        'probe_command':probe_cmd,'probe_exit':probe.returncode,'probe':json.loads(probe.stdout),
        'decode_command':decode_cmd,'decode_exit':decode.returncode,'decode_stderr':decode.stderr,'seconds':time.monotonic()-t})
(out/'artifact-and-protection-validation.json').write_text(json.dumps(result,indent=2))
print(json.dumps({k:v for k,v in result.items() if k!='artifacts'},indent=2))
print('validated artifacts:',len(result['artifacts']))
assert all(preservation.values())
assert any(r[fields.index('language')]=='en' and r[fields.index('status')]=='AWAITING_REVIEW' for r in scripts)
assert all(a['probe_exit']==a['decode_exit']==0 and not a['decode_stderr'] for a in result['artifacts'])
