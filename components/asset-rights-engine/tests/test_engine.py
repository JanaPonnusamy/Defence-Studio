import unittest, tempfile, sqlite3, threading
from contextlib import closing
from pathlib import Path
from dataclasses import replace
from asset_rights_engine import *
from asset_rights_engine.evaluator import evaluate_effective_rights

T="2026-10-12T00:00:00Z"
H="a"*64

def asset(**kw):
    return AssetDescriptor(**(dict(asset_id="asset-1",asset_type="image",display_name="Test only",description="Synthetic metadata",provenance="Test fixture",original_source="test:fixture",created_at=T,registered_at=T)|kw))
def rights(**kw):
    return RightsRecord(**(dict(state="PENDING_REVIEW",rights_basis="Test grant",licence_name="Test-only licence",evidence_notes="Synthetic test evidence",commercial=True,modification=True,distribution=True,attribution_required=False)|kw))
def binding(**kw): return SceneBinding(**(dict(host_application="test-host",job_id="job-1",scene_id="scene-1",plan_version="1",plan_sha256=H)|kw))
def usage(**kw): return UseContext(**(dict(evaluated_at=T)|kw))
def request(view,key="review-1",**kw): return ReviewRequest(**(dict(reviewer="reviewer-1",decision="APPROVED",note="Synthetic test review",expected_revision=view.revision,content_digest=view.content_digest,idempotency_key=key,reviewed_at=T)|kw))

class Contracts(unittest.TestCase):
    def test_asset_valid(self): self.assertEqual(asset().validation_status,"VALID")
    def test_asset_invalid(self):
        for kw in ({"asset_id":"../x"},{"byte_size":-1},{"byte_size":True},{"sha256":"x"},{"metadata_version":2},{"asset_type":"executable"},{"mime_type":"video/mp4"},{"mime_type":42},{"registered_at":"2020-01-01T00:00:00Z"},{"created_at":"bad"},{"tags":("",)}):
            with self.subTest(kw=kw),self.assertRaises(DomainError): asset(**kw)
    def test_rights_validation(self):
        for kw in ({"state":"invented"},{"commercial":"yes"},{"contract_version":2},{"territories":("usa",)},{"valid_until":"bad"},{"valid_until":""},{"valid_from":T,"valid_until":T}):
            with self.subTest(kw=kw),self.assertRaises(DomainError): rights(**kw)
    def test_unknown_licence_not_inferred(self):
        result=evaluate_effective_rights(RightsRecord(source_reference="https://example.invalid/public.jpg"),usage())
        self.assertFalse(result.eligible)
    def test_arbitrary_licence_is_only_declared_metadata(self): self.assertEqual(rights(licence_name="Unknown custom grant").licence_name,"Unknown custom grant")
    def test_binding_validation(self):
        for kw in ({"plan_version":""},{"plan_sha256":"bad"},{"content_digest":"bad"},{"host_application":"../host"}):
            with self.subTest(kw=kw),self.assertRaises(DomainError): binding(**kw)
    def test_review_validation(self):
        v=RevisionView("x",1,{},H,"PENDING_REVIEW")
        for kw in ({"note":""},{"expected_revision":0},{"expected_revision":True},{"content_digest":"bad"},{"reviewer":""},{"decision":"maybe"}):
            with self.subTest(kw=kw),self.assertRaises(DomainError): request(v,**kw)

class EngineTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.e=Engine(self.root);self.e.register_asset(asset())
    def tearDown(self): self.tmp.cleanup()
    def approved_rights(self,**kw):
        v=self.e.create_rights_revision("asset-1",rights(**kw));self.e.review_rights("asset-1",request(v));return v
    def assignment(self,**kw):
        return self.e.create_assignment("assignment-1","asset-1",binding(),"background",usage(**kw),"PENDING_REVIEW")
    def test_initialization(self): self.assertEqual(self.e.validate_database(),{"valid":True,"schema_version":1})
    def test_register_idempotent(self): self.e.register_asset(asset());self.assertEqual(len(self.e.list_assets()),1)
    def test_register_conflict(self):
        with self.assertRaises(DomainError): self.e.register_asset(asset(display_name="changed"))
    def test_no_media_required(self): self.assertEqual(self.e.get_asset("asset-1").original_source,"test:fixture")
    def test_no_caller_approval(self):
        with self.assertRaises(DomainError): self.e.create_rights_revision("asset-1",rights(state="APPROVED"))
    def test_review_success(self): self.approved_rights();self.assertTrue(self.e.evaluate_effective_rights("asset-1",usage()).eligible)
    def test_commercial_missing(self): self.approved_rights(commercial=None);self.assertIn("COMMERCIAL_NOT_PERMITTED",self.e.evaluate_effective_rights("asset-1",usage()).blockers)
    def test_noncommercial(self): self.approved_rights(commercial=False);self.assertTrue(self.e.evaluate_effective_rights("asset-1",usage(commercial=False)).eligible)
    def test_unknown_pending_rejected_expired_revoked(self):
        for status in ("UNKNOWN","PENDING_REVIEW","REJECTED","EXPIRED","REVOKED"):
            current=len([1 for _ in range(self.e.get_rights("asset-1").revision)]) if status!="UNKNOWN" else 0
            self.e.create_rights_revision("asset-1",rights(state=status),current)
            self.assertFalse(self.e.evaluate_effective_rights("asset-1",usage()).eligible)
    def test_expired_by_date(self): self.approved_rights(valid_until=T);self.assertIn("RIGHTS_EXPIRED",self.e.evaluate_effective_rights("asset-1",usage()).blockers)
    def test_not_yet_valid(self): self.approved_rights(valid_from="2027-01-01T00:00:00Z");self.assertFalse(self.e.evaluate_effective_rights("asset-1",usage()).eligible)
    def test_attribution(self):
        self.approved_rights(attribution_required=True,attribution_text="Credit Author")
        self.assertFalse(self.e.evaluate_effective_rights("asset-1",usage()).eligible)
        self.assertTrue(self.e.evaluate_effective_rights("asset-1",usage(attribution_text="Credit Author")).eligible)
    def test_incomplete_attribution_review(self):
        v=self.e.create_rights_revision("asset-1",rights(attribution_required=True))
        with self.assertRaises(DomainError): self.e.review_rights("asset-1",request(v))
        self.assertEqual(len(self.e.get_review_history()),0)
    def test_restrictions(self):
        self.approved_rights(modification=False,distribution=False,territories=("IN",))
        result=self.e.evaluate_effective_rights("asset-1",usage(modification=True,territory="US"))
        self.assertEqual(set(result.blockers),{"MODIFICATION_NOT_PERMITTED","DISTRIBUTION_NOT_PERMITTED","TERRITORY_RESTRICTED"})
    def test_determinism(self): self.approved_rights();self.assertEqual(self.e.evaluate_effective_rights("asset-1",usage()),self.e.evaluate_effective_rights("asset-1",usage()))
    def test_stale_review_revision(self):
        v=self.e.create_rights_revision("asset-1",rights());self.e.create_rights_revision("asset-1",rights(),1)
        with self.assertRaises(DomainError): self.e.review_rights("asset-1",request(v))
    def test_stale_digest(self):
        v=self.e.create_rights_revision("asset-1",rights())
        with self.assertRaises(DomainError): self.e.review_rights("asset-1",request(v,content_digest="b"*64))
    def test_idempotency(self):
        v=self.e.create_rights_revision("asset-1",rights());r=request(v)
        first=self.e.review_rights("asset-1",r);self.assertEqual(first,self.e.review_rights("asset-1",r));self.assertEqual(len(self.e.get_review_history()),1)
        with self.assertRaises(DomainError): self.e.review_rights("asset-1",replace(r,note="different"))
    def test_review_rejection(self):
        v=self.e.create_rights_revision("asset-1",rights());self.e.review_rights("asset-1",request(v,decision="REJECTED"));self.assertEqual(self.e.get_rights("asset-1").state,"REJECTED")
    def test_concurrent_review(self):
        v=self.e.create_rights_revision("asset-1",rights());out=[];gate=threading.Barrier(2)
        def run(key):
            gate.wait()
            try: self.e.review_rights("asset-1",request(v,key));out.append("success")
            except DomainError as e: out.append(e.code)
        threads=[threading.Thread(target=run,args=("key-"+str(i),)) for i in range(2)]
        for t in threads:t.start()
        for t in threads:t.join()
        self.assertCountEqual(out,["success","CONFLICT"]);self.assertEqual(len(self.e.get_review_history()),1)
    def test_approved_assignment_restart(self):
        self.approved_rights();v=self.assignment();self.e.review_assignment(v.entity_id,request(v,"assignment-review"),binding(),usage())
        self.e=Engine(self.root);result=self.e.evaluate_assignment_readiness(v.entity_id,binding(),usage());self.assertTrue(result.eligible);self.assertIn("MEDIA_AVAILABILITY_UNVERIFIED",result.warnings)
    def test_assignment_pending(self): self.approved_rights();self.assignment();self.assertFalse(self.e.evaluate_assignment_readiness("assignment-1",binding(),usage()).eligible)
    def test_stale_scene(self):
        self.approved_rights();v=self.assignment();self.e.review_assignment(v.entity_id,request(v,"ar"),binding(),usage())
        for b in (binding(plan_sha256="b"*64),binding(plan_version="2"),binding(content_digest="c"*64)):
            self.assertIn("STALE_SCENE_BINDING",self.e.evaluate_assignment_readiness(v.entity_id,b,usage()).blockers)
    def test_changed_rights(self):
        self.approved_rights();v=self.assignment();self.e.review_assignment(v.entity_id,request(v,"ar"),binding(),usage());self.e.create_rights_revision("asset-1",rights(),1)
        self.assertIn("STALE_RIGHTS_REVISION",self.e.evaluate_assignment_readiness(v.entity_id,binding(),usage()).blockers)
    def test_changed_assignment(self):
        self.approved_rights();v=self.assignment();self.e.review_assignment(v.entity_id,request(v,"ar"),binding(),usage());v2=self.e.revise_assignment(v.entity_id,binding(),"foreground",usage(),1)
        self.assertEqual(v2.revision,2);self.assertFalse(self.e.evaluate_assignment_readiness(v.entity_id,binding(),usage()).eligible)
        with self.assertRaises(DomainError): self.e.review_assignment(v.entity_id,request(v,"old-ar"),binding(),usage())
    def test_changed_usage(self): self.approved_rights();v=self.assignment();self.assertIn("USE_CONTEXT_CHANGED",self.e.evaluate_assignment_readiness(v.entity_id,binding(),usage(modification=True)).blockers)
    def test_blocked_assignment(self):
        self.approved_rights();v=self.e.create_assignment("blocked","asset-1",binding(),"background",usage(),"BLOCKED")
        with self.assertRaises(DomainError): self.e.review_assignment(v.entity_id,request(v,"ar"),binding(),usage())
    def test_assignment_missing_attribution(self):
        self.approved_rights(attribution_required=True,attribution_text="Credit");v=self.assignment()
        with self.assertRaises(DomainError): self.e.review_assignment(v.entity_id,request(v,"ar"),binding(),usage())
    def test_transaction_rollback(self):
        self.approved_rights()
        with self.e._connection(True) as c:
            c.execute("CREATE TRIGGER fail_revision BEFORE INSERT ON assignment_revisions BEGIN SELECT RAISE(ABORT,'test fault'); END")
        with self.assertRaises(DomainError): self.assignment()
        self.assertEqual(self.e.list_assignments(),())
    def test_foreign_keys(self):
        with self.assertRaises(DomainError),self.e._connection(True) as c:c.execute("INSERT INTO assignments VALUES(?,?)",("bad","missing"))
    def test_immutable_history(self):
        self.approved_rights();self.assignment()
        for table in ("assets","rights_revisions","assignment_revisions","review_history","migrations"):
            for sql in (f"DELETE FROM {table}",f"UPDATE {table} SET "+("asset_id=asset_id" if table=="assets" else "version=version" if table=="migrations" else "entity_id=entity_id")):
                with self.subTest(sql=sql),self.assertRaises(DomainError),self.e._connection(True) as c:c.execute(sql)
    def test_snapshot_restore(self):
        self.approved_rights();self.assignment();path=self.e.create_database_snapshot("backup.sqlite3");restored=Engine(self.root,"backup.sqlite3")
        self.assertEqual(restored.list_assets(),self.e.list_assets());self.assertEqual(restored.get_review_history(),self.e.get_review_history());self.assertTrue(restored.validate_database()["valid"])
        with self.assertRaises(DomainError):self.e.create_database_snapshot("backup.sqlite3")
    def test_path_restriction(self):
        for name in ("../external.sqlite3",str(self.root/"absolute.sqlite3")):
            with self.assertRaises(DomainError): Engine(self.root,name)
            with self.assertRaises(DomainError): self.e.create_database_snapshot(name)
    def test_schema_rejection(self):
        p=self.root/"future.sqlite3"
        with closing(sqlite3.connect(p, isolation_level=None)) as c:c.execute("PRAGMA user_version=9")
        with self.assertRaises(DomainError):Engine(self.root,"future.sqlite3")
    def test_unversioned_database_preserved(self):
        p=self.root/"foreign.sqlite3"
        with closing(sqlite3.connect(p, isolation_level=None)) as c:c.execute("CREATE TABLE foreign_data(value)");c.execute("INSERT INTO foreign_data VALUES(42)")
        with self.assertRaises(DomainError):Engine(self.root,"foreign.sqlite3")
        with closing(sqlite3.connect(p, isolation_level=None)) as c:self.assertEqual(c.execute("SELECT value FROM foreign_data").fetchone()[0],42)
    def test_migration_rerun_preserves_data(self): self.approved_rights();Engine(self.root);self.assertEqual(len(self.e.get_review_history()),1)
    def test_missing_trigger_detected(self):
        with closing(sqlite3.connect(self.e.path, isolation_level=None)) as c:c.execute("DROP TRIGGER immutable_review_history_delete")
        with self.assertRaises(DomainError):self.e.validate_database()
    def test_wal_snapshot_includes_committed_rows(self):
        with closing(sqlite3.connect(self.e.path,isolation_level=None)) as keeper:
            keeper.execute("PRAGMA journal_mode=WAL")
            self.approved_rights()
            self.e.create_database_snapshot("wal-snapshot.sqlite3")
            restored=Engine(self.root,"wal-snapshot.sqlite3")
            self.assertEqual(len(restored.get_review_history()),1)
            self.assertEqual(restored.get_rights("asset-1").state,"APPROVED")
    def test_schema_migration_mirror(self):
        from importlib.resources import files
        self.assertEqual(files("asset_rights_engine").joinpath("schema.sql").read_text(),(Path(__file__).parents[1]/"migrations/001_initial.sql").read_text())
    def test_missing_rights_blocks_assignment(self):
        with self.assertRaises(DomainError):self.assignment()
        self.assertEqual(self.e.list_assignments(),())
    def test_approval_does_not_survive_rights_revision(self):
        self.approved_rights();v=self.assignment();self.e.review_assignment(v.entity_id,request(v,"ar"),binding(),usage())
        self.e.create_rights_revision("asset-1",rights(),1)
        self.assertEqual(self.e.get_assignment(v.entity_id).state,"STALE")
    def test_review_cannot_be_used_before_timestamp(self):
        self.approved_rights()
        self.assertIn("REVIEW_AFTER_EVALUATION",self.e.evaluate_effective_rights("asset-1",usage(evaluated_at="2026-10-11T00:00:00Z")).blockers)
    def test_core_isolation(self):
        import sys
        self.assertFalse(any(n.lower().startswith(("defencestudio","qvault","whisper_studio")) for n in sys.modules))
        self.assertEqual(self.e.path.parent,self.root.resolve())

if __name__=="__main__":unittest.main()
