"""ARE-A3 evidence probes only; no package, source or persistent-data mutation."""
from pathlib import Path
from tempfile import TemporaryDirectory
from contextlib import contextmanager, closing
from dataclasses import replace
from unittest.mock import patch
import hashlib, importlib.metadata, inspect, json, sqlite3, sys, zipfile
from asset_rights_engine import *
from asset_rights_engine.adapter_contracts import HostScope, HostSceneKey, HostSceneContext, HostPrincipal, PlanState, HostOperation
from asset_rights_engine.host_adapter import AdapterGateway
from asset_rights_engine.simulator import SyntheticHost, run_simulation

ROOT = Path(__file__).resolve().parents[2]
SCRATCH = ROOT / "verification/a3/tmp"
T = "2026-10-12T00:00:00Z"
SCOPE = HostScope("audit-synthetic-host", "audit-synthetic-tenant", "audit-synthetic-owner")
SCENE = HostSceneKey(SCOPE, "audit-job", "audit-scene")
CONTEXT = HostSceneContext(SCENE, 1, "a"*64, PlanState.APPROVED, True)
PRINCIPAL = HostPrincipal(SCOPE, "audit-synthetic-reviewer")
results = {}


def setup(directory):
    engine = Engine(Path(directory))
    engine.register_asset(AssetDescriptor("audit-asset", "image", "AUDIT TEST DATA", "Synthetic",
                                        "Audit fixture", "test:are-a3", T, T))
    rights = engine.create_rights_revision("audit-asset", RightsRecord(state="PENDING_REVIEW",
        rights_basis="Synthetic grant", licence_name="TEST ONLY", commercial=True,
        distribution=True, attribution_required=False, evidence_notes="SYNTHETIC, NO REAL RIGHTS"))
    req = ReviewRequest(PRINCIPAL.principal_id, "APPROVED", "Synthetic audit decision", rights.revision,
                        rights.content_digest, "audit-review", T)
    host = SyntheticHost(SCOPE)
    host.set_scene(CONTEXT)
    host.declare_asset_scope("audit-asset", SCOPE)
    host.declare_assignment_target("audit-assignment", SCENE, "audit-asset")
    host.grant_synthetic_operations(PRINCIPAL, tuple(HostOperation))
    return engine, rights, req, host


# Audit the archives without extraction or installation.
for version in ("1.0.0", "1.1.0"):
    path = ROOT / f"dist/asset_rights_engine-{version}-py3-none-any.whl"
    with zipfile.ZipFile(path) as wheel:
        metadata = wheel.read(f"asset_rights_engine-{version}.dist-info/METADATA").decode()
        matches = []
        for name in wheel.namelist():
            if name.startswith("asset_rights_engine/") and name.endswith((".py", ".sql")):
                if version == "1.0.0" and name.endswith("__init__.py"):
                    continue
                source = ROOT / "src" / name
                matches.append((name, source.exists() and source.read_bytes() == wheel.read(name)))
        results["wheel_"+version] = {"sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "version_metadata_matches": f"Version: {version}" in metadata, "source_comparison": matches}
        assert all(ok for _, ok in matches)
results["installed_package"] = {"version": __import__("asset_rights_engine").__version__,
    "location": str(Path(__import__("asset_rights_engine").__file__).resolve()),
    "runtime_dependencies": importlib.metadata.requires("asset-rights-engine") or [],
    "engine_public_signatures": {name: str(inspect.signature(getattr(Engine,name)))
        for name in dir(Engine) if not name.startswith("_") and callable(getattr(Engine,name))},
    "exported_contract_names": [name for name in ("AssetDescriptor", "AssignmentDescriptor", "RightsRecord",
        "RightsState", "AssignmentState", "UseContext", "SceneBinding", "ReviewRequest", "RevisionView",
        "Evaluation", "ReviewEvent", "DomainError", "digest", "Engine", "evaluate_effective_rights") if name in globals()]}

with TemporaryDirectory(dir=SCRATCH, prefix="direct-") as directory:
    engine, rights, req, host = setup(directory)
    event = engine.review_rights("audit-asset", req)
    view = engine.create_assignment("audit-assignment", "audit-asset", CONTEXT.scene_binding(), "background", UseContext(T))
    results["P01_direct_engine_bypass"] = {"review_accepted_without_host": event.decision,
        "assignment_created_without_host": view.entity_id, "authentication_performed": False}

with TemporaryDirectory(dir=SCRATCH, prefix="spoof-") as directory:
    engine, rights, req, host = setup(directory)
    forged_copy = HostPrincipal(SCOPE, PRINCIPAL.principal_id)
    event = AdapterGateway(engine, host).review_rights("audit-asset", CONTEXT, forged_copy, req)
    results["P02_synthetic_identifier_copy"] = {"copied_granted_identifier_accepted": event.decision == "APPROVED",
        "conclusion": "Synthetic grants match identifiers; not authentication. Documented limitation."}

with TemporaryDirectory(dir=SCRATCH, prefix="exit-failure-") as directory:
    engine, rights, req, host = setup(directory)
    class ExitFailingProvider:
        @contextmanager
        def operation_context(self, request):
            with host.operation_context(request) as current:
                yield current
                raise DomainError("AUDIT_HOST_EXIT_FAILURE", "Synthetic failure after engine commit")
    try:
        AdapterGateway(engine, ExitFailingProvider()).review_rights("audit-asset", CONTEXT, PRINCIPAL, req)
    except DomainError as error:
        code = error.code
    history = engine.get_review_history()
    replay = AdapterGateway(engine, host).review_rights("audit-asset", CONTEXT, PRINCIPAL, req)
    results["P03_post_commit_host_failure"] = {"returned_error": code, "committed_history_count": len(history),
        "exact_retry_returned_committed_event": replay == history[0], "history_after_retry": len(engine.get_review_history())}
    assert code == "AUDIT_HOST_EXIT_FAILURE" and len(history) == 1 and replay == history[0]

with TemporaryDirectory(dir=SCRATCH, prefix="unstable-provider-") as directory:
    engine, rights, req, host = setup(directory)
    class UnstableProvider:
        current = CONTEXT
        @contextmanager
        def operation_context(self, request): yield self.current
    provider = UnstableProvider()
    original = engine.create_assignment
    def changing_call(*args, **kwargs):
        provider.current = replace(CONTEXT, plan_revision=2, plan_sha256="b"*64)
        return original(*args, **kwargs)
    with patch.object(engine, "create_assignment", side_effect=changing_call):
        view = AdapterGateway(engine, provider).create_assignment("audit-assignment", "audit-asset", CONTEXT,
                                                                PRINCIPAL, "background", UseContext(T))
    results["P04_unstable_provider"] = {"provider_current_revision": provider.current.plan_revision,
        "stored_plan_version": view.content["binding"]["plan_version"],
        "conclusion": "Gateway does not enforce stability on a noncompliant provider; host must."}

with TemporaryDirectory(dir=SCRATCH, prefix="missing-migration-") as directory:
    path = Path(directory)/"asset_rights.sqlite3"
    with closing(sqlite3.connect(path,isolation_level=None)) as connection:
        connection.execute("CREATE TABLE migrations(version INTEGER PRIMARY KEY, applied_at TEXT, checksum TEXT)")
        connection.execute("PRAGMA user_version=1")
    try:
        Engine(Path(directory))
    except Exception as error:
        results["P05_missing_migration_row"] = {"exception_type":type(error).__name__, "message":str(error),
            "structured_domain_error":isinstance(error,DomainError)}
    assert results["P05_missing_migration_row"]["exception_type"] == "TypeError"

with TemporaryDirectory(dir=SCRATCH, prefix="missing-table-") as directory:
    engine, rights, req, host = setup(directory)
    with closing(sqlite3.connect(engine.path,isolation_level=None)) as connection:
        connection.execute("DROP TABLE review_state")
    validation = engine.validate_database()
    try: engine.get_rights("audit-asset")
    except DomainError as error: failure = {"code":error.code,"message":error.message}
    results["P06_missing_required_table"] = {"validation":validation, "subsequent_operation":failure}
    assert validation["valid"] is True and failure["code"] == "DATABASE"

with TemporaryDirectory(dir=SCRATCH, prefix="trigger-name-") as directory:
    engine, rights, req, host = setup(directory)
    with closing(sqlite3.connect(engine.path,isolation_level=None)) as connection:
        connection.execute("DROP TRIGGER immutable_rights_revisions_delete")
        connection.execute("CREATE TRIGGER immutable_rights_revisions_delete BEFORE DELETE ON rights_revisions BEGIN SELECT 1; END")
    validation = engine.validate_database()
    with closing(sqlite3.connect(engine.path,isolation_level=None)) as connection:
        connection.execute("DELETE FROM rights_revisions")
        count = connection.execute("SELECT COUNT(*) FROM rights_revisions").fetchone()[0]
    results["P07_privileged_trigger_replacement"] = {"validation":validation,"revision_rows_after_delete":count,
        "conclusion":"Validation checks trigger names, not definitions; no privileged tamper-proof guarantee exists."}

with TemporaryDirectory(dir=SCRATCH, prefix="invalid-json-") as directory:
    engine, rights, req, host = setup(directory)
    with closing(sqlite3.connect(engine.path,isolation_level=None)) as connection:
        connection.execute("DROP TRIGGER immutable_rights_revisions_update")
        connection.execute("UPDATE rights_revisions SET payload='broken-json'")
        connection.execute("CREATE TRIGGER immutable_rights_revisions_update BEFORE UPDATE ON rights_revisions BEGIN SELECT RAISE(ABORT,'immutable record'); END")
    try: engine.validate_database()
    except Exception as error:
        results["P08_invalid_json_error"]={"exception_type":type(error).__name__,"structured_domain_error":isinstance(error,DomainError)}
    assert results["P08_invalid_json_error"]["exception_type"] == "JSONDecodeError"

# Existing schema matches its mirror and public records lack identity/tenant provenance fields.
results["schema_mirror"] = (ROOT/"src/asset_rights_engine/schema.sql").read_bytes() == (ROOT/"migrations/001_initial.sql").read_bytes()
results["review_event_fields"] = list(ReviewEvent.__dataclass_fields__)
results["P09_simulator"] = {"scenarios":run_simulation().scenarios,"authentication":"NOT_IMPLEMENTED"}
results["P10_documentation_version"] = {"readme_header":(ROOT/"README.md").read_text().splitlines()[2],
    "metadata_version":importlib.metadata.version("asset-rights-engine")}
print(json.dumps(results,indent=2))
(ROOT/"verification/a3/probe-results.json").write_text(json.dumps(results,indent=2),encoding="utf-8")
