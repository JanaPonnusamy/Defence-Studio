"""Disposable schema-v1 rejection, preservation and compatibility regressions."""
import json, shutil, sqlite3, tempfile, unittest
from contextlib import closing
from pathlib import Path
from dataclasses import asdict
from asset_rights_engine import Engine, DomainError, digest
from test_engine import asset, rights, binding, usage, request

IMMUTABLE=('assets','rights_revisions','assignment_revisions','review_history','migrations')
TABLES=('assets','rights_revisions','assignments','assignment_revisions','review_state','review_history','migrations')

class DatabaseValidationTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.root=Path(self.tmp.name)
        self.e=self.fixture(self.root/'original')
    def tearDown(self): self.tmp.cleanup()
    def fixture(self,root):
        e=Engine(root); e.register_asset(asset())
        r=e.create_rights_revision('asset-1',rights()); e.review_rights('asset-1',request(r))
        a=e.create_assignment('assignment-1','asset-1',binding(),'background',usage(),'PENDING_REVIEW')
        e.review_assignment('assignment-1',request(a,'assignment-review'),binding(),usage())
        return e
    def sql(self,sql,e=None):
        with closing(sqlite3.connect((e or self.e).path)) as c:
            c.executescript(sql); c.commit()
    def edit(self,table,sql,e=None):
        actions=('update','delete') if table in IMMUTABLE else ()
        drop=''.join(f'DROP TRIGGER immutable_{table}_{a};' for a in actions)
        restore=''.join(f"CREATE TRIGGER immutable_{table}_{a} BEFORE {a} ON {table} BEGIN SELECT RAISE(ABORT,'immutable record'); END;" for a in actions)
        self.sql(drop+sql+';'+restore,e)
    def reject(self,fragment,e=None,code='VALIDATION'):
        e=e or self.e
        before=e.path.read_bytes()
        records=None
        if code!='DATABASE':
            with closing(sqlite3.connect(e.path)) as c:
                records=c.execute('SELECT * FROM assets').fetchall() if c.execute("SELECT 1 FROM sqlite_master WHERE name='assets' AND type='table'").fetchone() else None
        for operation in (e.validate_database,lambda: Engine(e.root)):
            with self.assertRaises(DomainError) as caught: operation()
            self.assertEqual(caught.exception.code,code)
            self.assertIn(fragment,caught.exception.message)
            self.assertEqual(str(caught.exception),f'{code}: {caught.exception.message}')
            self.assertEqual(e.path.read_bytes(),before,'Validation must preserve exact invalid bytes')
        if records is not None:
            with closing(sqlite3.connect(e.path)) as c:
                self.assertEqual(c.execute('SELECT * FROM assets').fetchall(),records)
        self.assertFalse(Path(str(e.path)+'-journal').exists())
    def payload(self,table,changes,e=None):
        e=e or self.e
        with closing(sqlite3.connect(e.path)) as c:
            value=json.loads(c.execute(f'SELECT payload FROM {table}').fetchone()[0])
        value.update(changes)
        encoded=json.dumps(value).replace("'","''")
        self.edit(table,f"UPDATE {table} SET payload='{encoded}',digest='{digest(value)}'",e)
    def test_empty_migration_ledger(self):
        self.edit('migrations','DELETE FROM migrations'); self.reject('Migration ledger')
    def test_missing_version_one_migration(self):
        self.edit('migrations','UPDATE migrations SET version=2'); self.reject('Migration ledger')
    def test_extra_migration_metadata(self):
        self.sql("INSERT INTO migrations VALUES(2,'2026-10-12T00:00:00Z','extra')"); self.reject('Migration ledger')
    def test_malformed_migration_timestamp(self):
        for value in ('broken','2026-10-12T00:00:00',42):
            with self.subTest(value=value):
                e=self.fixture(self.root/str(value).replace(':','_'))
                self.edit('migrations',f"UPDATE migrations SET applied_at='{value}'",e); self.reject('migrations version 1',e)
    def test_malformed_migration_checksum(self):
        self.edit('migrations',"UPDATE migrations SET checksum='bad'"); self.reject('Expected lowercase SHA-256')
    def test_wrong_migration_checksum(self):
        self.edit('migrations',f"UPDATE migrations SET checksum='{'b'*64}'"); self.reject('checksum mismatch')
    def test_malformed_revision_json(self):
        for table in ('rights_revisions','assignment_revisions'):
            with self.subTest(table=table):
                e=self.fixture(self.root/table)
                self.edit(table,f"UPDATE {table} SET payload='broken-json'",e); self.reject('Malformed JSON at line 1, column 1',e)
    def test_non_object_revision_json(self):
        self.edit('rights_revisions',"UPDATE rights_revisions SET payload='[]'"); self.reject('Expected JSON object')
    def test_duplicate_json_property(self):
        self.edit('rights_revisions',"UPDATE rights_revisions SET payload='{\"state\":\"UNKNOWN\",\"state\":\"REVOKED\"}'")
        self.reject('Duplicate JSON property')
    def test_non_finite_json(self):
        self.edit('rights_revisions',"UPDATE rights_revisions SET payload='{\"commercial\":NaN}'")
        self.reject('invalid persisted content')
    def test_missing_required_tables(self):
        for table in TABLES:
            with self.subTest(table=table):
                e=self.fixture(self.root/table); self.sql(f'DROP TABLE {table}',e); self.reject(f'Missing required table: {table}',e)
    def test_missing_required_columns(self):
        for table,column in (('assets','payload'),('rights_revisions','digest'),('assignments','asset_id'),('assignment_revisions','payload'),('review_state','decision'),('review_history','note'),('migrations','checksum')):
            with self.subTest(table=table):
                e=self.fixture(self.root/table); self.sql(f'ALTER TABLE {table} DROP COLUMN {column}',e); self.reject('Required columns differ',e)
    def test_view_replacing_table(self):
        self.sql('DROP TABLE review_state; CREATE VIEW review_state AS SELECT kind,entity_id,revision,decision FROM review_history')
        self.reject('Missing required table: review_state')
    def test_required_constraints(self):
        definitions=(
            'CREATE TABLE review_state(kind TEXT NOT NULL,entity_id TEXT NOT NULL,revision INTEGER NOT NULL,decision TEXT NOT NULL)',
            'CREATE TABLE review_state(kind TEXT,entity_id TEXT NOT NULL,revision INTEGER NOT NULL,decision TEXT NOT NULL,PRIMARY KEY(kind,entity_id))',
            'CREATE TABLE rights_revisions(entity_id TEXT NOT NULL REFERENCES assets(asset_id), revision INTEGER NOT NULL, payload TEXT NOT NULL,digest TEXT NOT NULL,PRIMARY KEY(entity_id,revision))',
            'CREATE TABLE assignments(assignment_id TEXT PRIMARY KEY,asset_id TEXT NOT NULL)',
            'CREATE TABLE review_history(sequence INTEGER PRIMARY KEY,kind TEXT NOT NULL,entity_id TEXT NOT NULL,revision INTEGER NOT NULL,reviewer TEXT NOT NULL,decision TEXT NOT NULL,note TEXT NOT NULL,content_digest TEXT NOT NULL,reviewed_at TEXT NOT NULL,idempotency_key TEXT NOT NULL UNIQUE,request_payload TEXT NOT NULL)',
            'CREATE TABLE review_history(sequence INTEGER PRIMARY KEY AUTOINCREMENT,kind TEXT NOT NULL,entity_id TEXT NOT NULL,revision INTEGER NOT NULL,reviewer TEXT NOT NULL,decision TEXT NOT NULL,note TEXT NOT NULL,content_digest TEXT NOT NULL,reviewed_at TEXT NOT NULL,idempotency_key TEXT NOT NULL,request_payload TEXT NOT NULL)')
        for i,definition in enumerate(definitions):
            with self.subTest(definition=definition):
                e=self.fixture(self.root/str(i)); table=definition.split()[2].split('(')[0]
                self.sql(f'DROP TABLE {table};'+definition,e)
                self.reject('Required',e)
    def test_missing_critical_triggers(self):
        for table in IMMUTABLE:
            for action in ('update','delete'):
                with self.subTest(table=table,action=action):
                    e=self.fixture(self.root/(table+action)); self.sql(f'DROP TRIGGER immutable_{table}_{action}',e)
                    self.reject('Missing immutability trigger',e)
    def test_noop_critical_trigger(self):
        self.sql('DROP TRIGGER immutable_rights_revisions_delete; CREATE TRIGGER immutable_rights_revisions_delete BEFORE DELETE ON rights_revisions BEGIN SELECT 1; END')
        self.reject('Invalid immutability trigger definition')
    def test_conditional_critical_trigger(self):
        self.sql("DROP TRIGGER immutable_rights_revisions_delete; CREATE TRIGGER immutable_rights_revisions_delete BEFORE DELETE ON rights_revisions WHEN 0 BEGIN SELECT RAISE(ABORT,'immutable record'); END")
        self.reject('Invalid immutability trigger definition')
    def test_trigger_wrong_table(self):
        self.sql("DROP TRIGGER immutable_rights_revisions_delete; CREATE TRIGGER immutable_rights_revisions_delete BEFORE DELETE ON assignments BEGIN SELECT RAISE(ABORT,'immutable record'); END")
        self.reject('Invalid immutability trigger definition')
    def test_trigger_wrong_event(self):
        self.sql("DROP TRIGGER immutable_rights_revisions_delete; CREATE TRIGGER immutable_rights_revisions_delete BEFORE UPDATE ON rights_revisions BEGIN SELECT RAISE(ABORT,'immutable record'); END")
        self.reject('Invalid immutability trigger definition')
    def test_cosmetic_trigger_definition(self):
        self.sql('''DROP TRIGGER immutable_rights_revisions_delete; CREATE TRIGGER "immutable_rights_revisions_delete" before delete on "rights_revisions" begin /* formatting */ select raise(ABORT,'immutable record'); end''')
        self.assertEqual(Engine(self.e.root).validate_database(),{'valid':True,'schema_version':1})
    def test_extra_unrelated_objects(self):
        self.sql('CREATE TABLE extension(value TEXT); CREATE INDEX extension_index ON extension(value)')
        self.assertTrue(Engine(self.e.root).validate_database()['valid'])
    def test_malformed_descriptor_json(self):
        self.edit('assets',"UPDATE assets SET payload='broken-json'"); self.reject('assets asset-1')
    def test_invalid_descriptor_contract(self):
        value=asdict(asset()); value['metadata_version']=2
        self.edit('assets',"UPDATE assets SET payload='"+json.dumps(value).replace("'","''")+"'")
        self.reject('Unsupported metadata version')
    def test_descriptor_identity_mismatch(self):
        value=asdict(asset()); value['asset_id']='other'
        self.edit('assets',"UPDATE assets SET payload='"+json.dumps(value).replace("'","''")+"'")
        self.reject('Asset identity mismatch')
    def test_digest_mismatch(self):
        self.edit('rights_revisions',f"UPDATE rights_revisions SET digest='{'b'*64}'"); self.reject('Content digest mismatch')
    def test_invalid_rights_contract(self):
        self.payload('rights_revisions',{'commercial':'yes'}); self.reject('Permission must be Boolean')
    def test_persisted_approval_flag(self):
        self.payload('rights_revisions',{'state':'APPROVED'}); self.reject('Approval requires review history')
    def test_invalid_binding_contract(self):
        value=asdict(binding()); value['plan_sha256']='bad'
        self.payload('assignment_revisions',{'binding':value}); self.reject('Expected lowercase SHA-256')
    def test_invalid_assignment_usage(self):
        self.payload('assignment_revisions',{'usage':{'evaluated_at':'bad'}}); self.reject('Invalid timestamp')
    def test_assignment_missing_contract_field(self):
        self.payload('assignment_revisions',{'binding':None}); self.reject('invalid persisted content')
    def test_rights_reference_mismatch(self):
        self.payload('assignment_revisions',{'rights_hash':'b'*64}); self.reject('Rights reference mismatch')
    def test_assignment_asset_mismatch(self):
        self.payload('assignment_revisions',{'asset_id':'other'}); self.reject('Assignment asset mismatch')
    def test_foreign_key_violation(self):
        self.sql("UPDATE assignments SET asset_id='other'"); self.reject('Foreign key violations')
    def test_orphan_assignment(self):
        self.sql("INSERT INTO assignments VALUES('orphan','asset-1')"); self.reject('Assignment has no revisions')
    def test_malformed_review_json(self):
        self.edit('review_history',"UPDATE review_history SET request_payload='broken-json'"); self.reject('review_history sequence')
    def test_inconsistent_review_history(self):
        self.edit('review_history',"UPDATE review_history SET reviewer='different'"); self.reject('Review request mismatch')
    def test_review_state_without_history(self):
        self.sql("INSERT INTO review_state VALUES('invented','asset-1',1,'APPROVED')"); self.reject('Missing or inconsistent review history')
    def test_inconsistent_review_state(self):
        self.sql("UPDATE review_state SET decision='REJECTED' WHERE kind='rights'"); self.reject('Missing or inconsistent review history')
    def test_missing_current_review_state(self):
        self.sql("DELETE FROM review_state WHERE kind='rights'"); self.reject('Missing current review decision')
    def test_valid_populated_database(self):
        before=self.e.path.read_bytes(); self.assertTrue(self.e.validate_database()['valid'])
        e=Engine(self.e.root); self.assertTrue(e.evaluate_assignment_readiness('assignment-1',binding(),usage()).eligible)
        self.assertEqual(e.path.read_bytes(),before)
    def test_valid_historical_stale_assignment(self):
        self.e.create_rights_revision('asset-1',rights(),1)
        e=Engine(self.e.root); self.assertTrue(e.validate_database()['valid'])
        self.assertEqual(e.get_assignment('assignment-1').state,'STALE')
        self.assertEqual(len(e.get_review_history()),2)
    def test_valid_reinitialization_and_new_revision(self):
        e=Engine(self.e.root); e.revise_assignment('assignment-1',binding(),'background',usage(),1)
        e=Engine(e.root); self.assertTrue(e.validate_database()['valid']); self.assertEqual(len(e.get_review_history()),2)
    def test_snapshot_restore(self):
        snapshot=self.e.create_database_snapshot('snapshot.sqlite3'); restored=self.root/'restored'; restored.mkdir()
        shutil.copy2(snapshot,restored/'asset_rights.sqlite3'); e=Engine(restored)
        self.assertTrue(e.evaluate_assignment_readiness('assignment-1',binding(),usage()).eligible)
        self.assertEqual(e.get_review_history(),self.e.get_review_history())
    def test_empty_new_database(self):
        self.assertEqual(Engine(self.root/'new').validate_database(),{'valid':True,'schema_version':1})
    def test_unversioned_existing_database_preserved(self):
        root=self.root/'foreign';root.mkdir(); path=root/'asset_rights.sqlite3'
        with closing(sqlite3.connect(path)) as c: c.execute('CREATE TABLE user_records(value TEXT)'); c.commit()
        before=path.read_bytes()
        with self.assertRaises(DomainError) as caught: Engine(root)
        self.assertEqual(caught.exception.code,'VALIDATION'); self.assertEqual(path.read_bytes(),before)
    def test_sqlite_error_contract(self):
        self.e.path.write_bytes(b'not a sqlite database')
        self.reject('file is not a database',code='DATABASE')

if __name__=='__main__': unittest.main()
