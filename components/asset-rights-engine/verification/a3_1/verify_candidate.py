from pathlib import Path
import sys,json,inspect,hashlib,tempfile,subprocess,zipfile
import asset_rights_engine as package
from asset_rights_engine import Engine
root=Path(__file__).resolve().parents[2]
assert 'candidate-env' in package.__file__, package.__file__
before=json.loads((root/'verification/a3_1/public-api-before.json').read_text())
after={n:str(inspect.signature(getattr(Engine,n))) for n in dir(Engine) if not n.startswith('_') and callable(getattr(Engine,n))}
assert before==after
sys.path.insert(0,str(root/'tests'))
from test_engine import binding,usage
legacy=[]
for environment in ('.venv','.venv-a2'):
 with tempfile.TemporaryDirectory(dir=root/'verification/a3_1/tmp') as directory:
  result=subprocess.run([str(root/environment/'Scripts/python.exe'),'-I','-B',str(root/'verification/a3_1/legacy_fixture.py'),directory],check=True,capture_output=True,text=True)
  path=Path(directory)/'asset_rights.sqlite3';data=path.read_bytes()
  engine=Engine(Path(directory))
  assert engine.validate_database()=={'valid':True,'schema_version':1}
  assert engine.evaluate_assignment_readiness('assignment-1',binding(),usage()).eligible
  assert data==path.read_bytes()
  snapshot=engine.create_database_snapshot('copy.sqlite3'); copy=Engine(Path(directory),snapshot.name)
  assert copy.get_review_history()==engine.get_review_history()
  legacy.append({'environment':environment,'accepted':json.loads(result.stdout),'candidate_valid':True,'database_bytes_preserved':True,'snapshot_restore':True})
wheel=root/'verification/a3_1/candidate/asset_rights_engine-1.1.0-py3-none-any.whl'
with zipfile.ZipFile(wheel) as archive:
 for p in (root/'src/asset_rights_engine').glob('*'):
  if p.is_file(): assert archive.read('asset_rights_engine/'+p.name)==p.read_bytes(),p.name
with zipfile.ZipFile(root/'dist/asset_rights_engine-1.1.0-py3-none-any.whl') as archive:
 old=archive.read('asset_rights_engine/engine.py').decode().replace('\r\n','\n')
 new=(root/'src/asset_rights_engine/engine.py').read_text()
 assert old[old.index('    def register_asset'):]==new[new.index('    def register_asset'):]
allowed={'src/asset_rights_engine/engine.py','README.md','ARCHITECTURE.md','CONTRACTS.md','SECURITY.md','CHANGELOG.md'}
protected={name.replace('\\','/'):sha for name,sha in json.loads((root/'verification/a3_1/protected-before.json').read_text()).items()}
changed=[];missing=[]
for name,sha in protected.items():
 p=root/name
 if not p.exists(): missing.append(name)
 elif hashlib.sha256(p.read_bytes()).hexdigest()!=sha: changed.append(name)
assert not missing,missing
assert set(changed)==allowed,changed
report={'installed_module':package.__file__,'runtime_base_version':package.__version__,'public_signatures_unchanged':len(after),'business_methods_unchanged':True,'legacy_compatibility':legacy,'candidate_sha256':hashlib.sha256(wheel.read_bytes()).hexdigest(),'schema_and_migration_unchanged':True,'protected_files':len(protected),'authorized_changed_existing_files':changed,'missing_files':missing,'accepted_wheel_sha256':{n:protected[n] for n in protected if n.startswith('dist/') and n.endswith('.whl')},'persistent_data_sha256':{n:protected[n] for n in protected if n.startswith('data/')}}
(root/'verification/a3_1/compatibility-and-preservation.json').write_text(json.dumps(report,indent=2))
print(json.dumps(report,indent=2))
