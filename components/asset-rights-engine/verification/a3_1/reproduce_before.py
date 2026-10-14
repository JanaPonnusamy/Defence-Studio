from pathlib import Path
import hashlib,json,inspect,tempfile,sqlite3
from asset_rights_engine import Engine
root=Path.cwd()
protected={str(p.relative_to(root)):hashlib.sha256(p.read_bytes()).hexdigest() for p in root.rglob('*') if p.is_file() and not any(x.startswith('.venv') or x in ('build','__pycache__','a3_1') for x in p.relative_to(root).parts)}
(root/'verification/a3_1/protected-before.json').write_text(json.dumps(protected,indent=2))
(root/'verification/a3_1/public-api-before.json').write_text(json.dumps({n:str(inspect.signature(getattr(Engine,n))) for n in dir(Engine) if not n.startswith('_') and callable(getattr(Engine,n))},indent=2))
results=[]
for name,sql in [('empty ledger','DROP TRIGGER immutable_migrations_delete; DELETE FROM migrations'),('missing review_state','DROP TABLE review_state'),('no-op trigger',"DROP TRIGGER immutable_rights_revisions_delete; CREATE TRIGGER immutable_rights_revisions_delete BEFORE DELETE ON rights_revisions BEGIN SELECT 1; END"),('malformed revision JSON',"DROP TRIGGER immutable_rights_revisions_update; INSERT INTO assets VALUES('a','{}'); INSERT INTO rights_revisions VALUES('a',1,'broken-json','bad')")]:
 with tempfile.TemporaryDirectory() as d:
  e=Engine(Path(d))
  with sqlite3.connect(e.path) as c:
   c.executescript(sql)
   if name=='empty ledger': c.execute("CREATE TRIGGER immutable_migrations_delete BEFORE DELETE ON migrations BEGIN SELECT RAISE(ABORT,'immutable record'); END")
   if name=='malformed revision JSON': c.execute("CREATE TRIGGER immutable_rights_revisions_update BEFORE UPDATE ON rights_revisions BEGIN SELECT RAISE(ABORT,'immutable record'); END")
  c.close()
  try: result=Engine(Path(d)).validate_database() if name=='empty ledger' else e.validate_database()
  except Exception as ex: result={'exception':type(ex).__name__,'message':str(ex)}
  results.append({'probe':name,'result':result})
(root/'verification/a3_1/reproduced-before.json').write_text(json.dumps(results,indent=2))
print(json.dumps(results,indent=2))

