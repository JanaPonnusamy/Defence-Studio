"""SYNTHETIC TEST DATA: never real-world rights evidence."""
from pathlib import Path
from tempfile import TemporaryDirectory
from asset_rights_engine import *

with TemporaryDirectory() as directory:
    engine=Engine(Path(directory))
    timestamp="2026-10-12T00:00:00Z"
    engine.register_asset(AssetDescriptor("demo-asset","image","Synthetic example","No media file","Test fixture","test:synthetic",timestamp,timestamp))
    rights=engine.create_rights_revision("demo-asset",RightsRecord(state="PENDING_REVIEW",rights_basis="Synthetic test grant",licence_name="Test-only licence",commercial=True,modification=False,distribution=True,attribution_required=True,attribution_text="Synthetic test author",evidence_notes="TEST DATA ONLY"))
    engine.review_rights("demo-asset",ReviewRequest("test-reviewer","APPROVED","Synthetic test decision",rights.revision,rights.content_digest,"rights-demo",timestamp))
    binding=SceneBinding("example-host","job-1","scene-1","1","a"*64)
    context=UseContext(timestamp,attribution_text="Synthetic test author")
    assignment=engine.create_assignment("demo-assignment","demo-asset",binding,"background",context,"PENDING_REVIEW")
    engine.review_assignment("demo-assignment",ReviewRequest("test-reviewer","APPROVED","Synthetic scene decision",assignment.revision,assignment.content_digest,"assignment-demo",timestamp),binding,context)
    print(engine.evaluate_assignment_readiness("demo-assignment",binding,context))
    print(engine.validate_database())
    snapshot=engine.create_database_snapshot("snapshot.sqlite3")
    print("Snapshot restored:",Engine(Path(directory),snapshot.name).validate_database())
