"""Transactional standalone component; no media IO or host imports."""
from datetime import datetime, timezone
from pathlib import Path
from contextlib import contextmanager, closing
from importlib.resources import files
import sqlite3, json, hashlib, re
from .contracts import *
from .evaluator import evaluate_effective_rights

class Engine:
    def __init__(self, data_directory: Path, database_name: str="asset_rights.sqlite3"):
        self.root=Path(data_directory).resolve()
        require(Path(database_name).name==database_name and database_name.endswith(".sqlite3"),"Database name must be a local SQLite basename")
        try: self.root.mkdir(parents=True,exist_ok=True)
        except OSError as e: raise DomainError("FILESYSTEM",str(e)) from e
        self.path=self.root/database_name
        require(self.path.resolve().parent==self.root,"Database escapes configured directory")
        self._initialize()

    @contextmanager
    def _connection(self, write=False):
        con=None
        try:
            con=sqlite3.connect(self.path,timeout=10)
            con.execute("PRAGMA foreign_keys=ON")
            con.row_factory=sqlite3.Row
            con.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            yield con
            if write: con.commit()
        except sqlite3.Error as e:
            if con: con.rollback()
            raise DomainError("DATABASE",str(e)) from e
        except Exception:
            if con: con.rollback()
            raise
        finally:
            if con: con.close()

    def _initialize(self):
        with self._connection(True) as c:
            version=c.execute("PRAGMA user_version").fetchone()[0]
            require(version in (0,1),"Unsupported schema version")
            schema=files("asset_rights_engine").joinpath("schema.sql").read_text()
            checksum=hashlib.sha256(schema.encode()).hexdigest()
            if version==0:
                require(not c.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall(),"Refusing to initialize existing unversioned database")
                for statement in schema.split(";"):
                    if statement.strip(): c.execute(statement)
                for table in ("assets","rights_revisions","assignment_revisions","review_history","migrations"):
                    for action in ("UPDATE","DELETE"):
                        c.execute(f"CREATE TRIGGER immutable_{table}_{action.lower()} BEFORE {action} ON {table} BEGIN SELECT RAISE(ABORT,'immutable record'); END")
                c.execute("INSERT INTO migrations VALUES(1,?,?)",(datetime.now(timezone.utc).isoformat(),checksum))
                c.execute("PRAGMA user_version=1")
            self._validate_database(c, schema)

    @staticmethod
    def _sql_tokens(sql):
        """Compare supported schema definitions, ignoring cosmetic SQL spelling."""
        tokens=re.findall(r"--[^\n]*|/\*[\s\S]*?\*/|'(?:''|[^'])*'|\"(?:\"\"|[^\"])*\"|`[^`]*`|\[[^]]*\]|[A-Za-z_][A-Za-z_0-9]*|[^\s]",sql or "")
        result=[]
        for token in tokens:
            if token.startswith(("--","/*")) or token==";": continue
            if token.startswith("'"): result.append(token)
            elif token.startswith(('"','`','[')): result.append(token[1:-1].lower())
            else: result.append(token.lower())
        if result[:5]==["create","table","if","not","exists"]: del result[2:5]
        return result

    @staticmethod
    def _stored(context, operation):
        try: return operation()
        except DomainError as e:
            raise DomainError("VALIDATION",f"{context}: {e.message}") from e
        except json.JSONDecodeError as e:
            raise DomainError("VALIDATION",f"{context}: Malformed JSON at line {e.lineno}, column {e.colno}: {e.msg}") from e
        except (ValueError,TypeError,KeyError,AttributeError,RecursionError) as e:
            # Do not expose payload text or turn unrelated programming errors into success.
            raise DomainError("VALIDATION",f"{context}: invalid persisted content ({type(e).__name__})") from e

    @classmethod
    def _payload(cls, raw, context):
        def parse():
            def unique(pairs):
                obj={}
                for key,value in pairs:
                    require(key not in obj,"Duplicate JSON property")
                    obj[key]=value
                return obj
            def constant(value): raise ValueError("Non-finite JSON number")
            value=json.loads(raw,object_pairs_hook=unique,parse_constant=constant)
            require(isinstance(value,dict),"Expected JSON object")
            return value
        return cls._stored(context,parse)

    def _validate_database(self,c,schema):
        require(c.execute("PRAGMA user_version").fetchone()[0]==1,"Unsupported schema")
        require(c.execute("PRAGMA integrity_check").fetchone()[0]=="ok","Integrity check failed")
        # The package schema is the schema-v1 authority, not an editable ledger checksum.
        # Compare all required tables/columns/constraints, while allowing extra objects.
        with closing(sqlite3.connect(":memory:")) as expected:
            expected.executescript(schema)
            for name,sql in expected.execute("SELECT name,sql FROM sqlite_master WHERE type='table' AND name!='sqlite_sequence'"):
                actual=c.execute("SELECT type,sql FROM sqlite_master WHERE name=?",(name,)).fetchone()
                require(actual is not None and actual["type"]=="table",f"Missing required table: {name}")
                wanted=list(expected.execute(f'PRAGMA table_xinfo("{name}")'))
                found=[tuple(row) for row in c.execute(f'PRAGMA table_xinfo("{name}")')]
                require(found==wanted,f"Required columns differ: {name}")
                require(self._sql_tokens(actual["sql"])==self._sql_tokens(sql),f"Required schema definition differs: {name}")
        for table in ("assets","rights_revisions","assignment_revisions","review_history","migrations"):
            for action in ("update","delete"):
                name=f"immutable_{table}_{action}"
                row=c.execute("SELECT tbl_name,sql FROM sqlite_master WHERE type='trigger' AND name=?",(name,)).fetchone()
                require(row is not None,f"Missing immutability trigger: {name}")
                expected=f"CREATE TRIGGER {name} BEFORE {action} ON {table} BEGIN SELECT RAISE(ABORT,'immutable record'); END"
                require(row["tbl_name"]==table and self._sql_tokens(row["sql"])==self._sql_tokens(expected),f"Invalid immutability trigger definition: {name}")
        ledger=c.execute("SELECT version,applied_at,checksum FROM migrations").fetchall()
        require(len(ledger)==1 and type(ledger[0]["version"]) is int and ledger[0]["version"]==1,"Migration ledger must contain exactly schema version 1")
        self._stored("migrations version 1",lambda: instant(ledger[0]["applied_at"]))
        self._stored("migrations version 1",lambda: sha(ledger[0]["checksum"]))
        require(ledger[0]["checksum"]==hashlib.sha256(schema.encode()).hexdigest(),"Schema migration checksum mismatch")
        require(not c.execute("PRAGMA foreign_key_check").fetchall(),"Foreign key violations")
        self._validate_contents(c)
        return {"valid":True,"schema_version":1}

    def _validate_contents(self,c):
        for row in c.execute("SELECT * FROM assets"):
            context=f"assets {row['asset_id']}"
            payload=self._payload(row["payload"],context)
            asset=self._stored(context,lambda: AssetDescriptor(**payload))
            require(asset.asset_id==row["asset_id"],f"{context}: Asset identity mismatch")
        for row in c.execute("SELECT * FROM assignments"):
            self._stored("assignments identity",lambda: identifier(row["assignment_id"]))
            require(c.execute("SELECT 1 FROM assignment_revisions WHERE entity_id=?",(row["assignment_id"],)).fetchone() is not None,"Assignment has no revisions")
        for table in ("rights_revisions","assignment_revisions"):
            for row in c.execute(f"SELECT * FROM {table}"):
                context=f"{table} {row['entity_id']} revision {row['revision']}"
                payload=self._payload(row["payload"],context)
                self._stored(context,lambda: revision(row["revision"]))
                require(self._stored(context,lambda: digest(payload))==row["digest"],f"{context}: Content digest mismatch")
                if table=="rights_revisions":
                    record=self._stored(context,lambda: RightsRecord(**payload))
                    require(record.state!=RightsState.APPROVED,f"{context}: Approval requires review history")
                else:
                    def assignment():
                        values=dict(payload)
                        values["binding"]=SceneBinding(**values["binding"])
                        values["usage"]=UseContext(**values["usage"])
                        return AssignmentDescriptor(**values)
                    record=self._stored(context,assignment)
                    require(record.state in (AssignmentState.DRAFT,AssignmentState.PENDING_REVIEW,AssignmentState.BLOCKED),f"{context}: Invalid persisted assignment state")
                    asset=c.execute("SELECT asset_id FROM assignments WHERE assignment_id=?",(row["entity_id"],)).fetchone()
                    require(asset is not None and asset[0]==record.asset_id,f"{context}: Assignment asset mismatch")
                    rights=c.execute("SELECT digest FROM rights_revisions WHERE entity_id=? AND revision=?",(record.asset_id,record.rights_revision)).fetchone()
                    require(rights is not None and rights[0]==record.rights_hash,f"{context}: Rights reference mismatch")
        histories={}
        for row in c.execute("SELECT * FROM review_history ORDER BY sequence"):
            context=f"review_history sequence {row['sequence']}"
            payload=self._payload(row["request_payload"],context)
            request=self._stored(context,lambda: ReviewRequest(**payload["request"]))
            require(set(payload)=={"kind","entity_id","request","binding","context"},f"{context}: Review payload fields differ")
            require(row["kind"] in ("rights","assignment") and payload["kind"]==row["kind"] and payload["entity_id"]==row["entity_id"],f"{context}: Review identity mismatch")
            fields=("reviewer","decision","note","content_digest","reviewed_at","idempotency_key")
            require(all(row[key]==getattr(request,key) for key in fields) and row["revision"]==request.expected_revision,f"{context}: Review request mismatch")
            table="rights_revisions" if row["kind"]=="rights" else "assignment_revisions"
            revision_row=c.execute(f"SELECT digest FROM {table} WHERE entity_id=? AND revision=?",(row["entity_id"],row["revision"])).fetchone()
            require(revision_row is not None and revision_row[0]==request.content_digest,f"{context}: Reviewed revision mismatch")
            if payload["binding"] is not None: self._stored(context,lambda: SceneBinding(**payload["binding"]))
            if payload["context"] is not None: self._stored(context,lambda: UseContext(**payload["context"]))
            require(row["kind"]!="rights" or (payload["binding"] is None and payload["context"] is None),f"{context}: Unexpected rights review context")
            key=(row["kind"],row["entity_id"],row["revision"])
            require(key not in histories,f"{context}: Duplicate revision review")
            histories[key]=row["decision"]
        states={}
        for row in c.execute("SELECT * FROM review_state"):
            key=(row["kind"],row["entity_id"],row["revision"])
            require(key in histories and histories[key]==row["decision"],"review_state: Missing or inconsistent review history")
            table="rights_revisions" if row["kind"]=="rights" else "assignment_revisions"
            latest=c.execute(f"SELECT MAX(revision) FROM {table} WHERE entity_id=?",(row["entity_id"],)).fetchone()[0]
            require(row["revision"]==latest,"review_state: Stale reviewed revision")
            states[key]=row["decision"]
        for key,decision in histories.items():
            table="rights_revisions" if key[0]=="rights" else "assignment_revisions"
            latest=c.execute(f"SELECT MAX(revision) FROM {table} WHERE entity_id=?",(key[1],)).fetchone()[0]
            require(key[2]!=latest or states.get(key)==decision,"review_state: Missing current review decision")

    def validate_database(self) -> dict:
        with self._connection() as c:
            schema=files("asset_rights_engine").joinpath("schema.sql").read_text()
            return self._validate_database(c,schema)

    def register_asset(self, asset: AssetDescriptor) -> AssetDescriptor:
        require(isinstance(asset,AssetDescriptor),"Expected AssetDescriptor")
        with self._connection(True) as c:
            existing=c.execute("SELECT payload FROM assets WHERE asset_id=?",(asset.asset_id,)).fetchone()
            if existing:
                require(existing[0]==canonical(asset),"Asset ID already registered with different metadata")
            else: c.execute("INSERT INTO assets VALUES(?,?)",(asset.asset_id,canonical(asset)))
        return asset

    def get_asset(self, asset_id: str) -> AssetDescriptor:
        identifier(asset_id)
        with self._connection() as c:
            row=c.execute("SELECT payload FROM assets WHERE asset_id=?",(asset_id,)).fetchone()
            if not row: raise DomainError("NOT_FOUND","Asset not found")
            return AssetDescriptor(**json.loads(row[0]))

    def list_assets(self) -> tuple[AssetDescriptor,...]:
        with self._connection() as c: return tuple(AssetDescriptor(**json.loads(r[0])) for r in c.execute("SELECT payload FROM assets ORDER BY asset_id"))

    def _view(self,c,kind,entity_id):
        table="rights_revisions" if kind=="rights" else "assignment_revisions"
        row=c.execute(f"SELECT * FROM {table} WHERE entity_id=? ORDER BY revision DESC LIMIT 1",(entity_id,)).fetchone()
        if not row: raise DomainError("NOT_FOUND","Revision not found")
        state=c.execute("SELECT revision,decision FROM review_state WHERE kind=? AND entity_id=?",(kind,entity_id)).fetchone()
        payload=json.loads(row["payload"])
        status=payload.get("state","PENDING_REVIEW")
        if state and state[0]==row["revision"]: status=state[1] if kind=="rights" or state[1]=="APPROVED" else "BLOCKED"
        if kind=="assignment":
            current=c.execute("SELECT revision,digest FROM rights_revisions WHERE entity_id=? ORDER BY revision DESC LIMIT 1",(payload["asset_id"],)).fetchone()
            if current and (current[0]!=payload["rights_revision"] or current[1]!=payload["rights_hash"]): status="STALE"
        return RevisionView(entity_id,row["revision"],payload,row["digest"],status)

    def get_rights(self,asset_id: str) -> RevisionView:
        with self._connection() as c: return self._view(c,"rights",asset_id)

    def get_assignment(self,assignment_id: str) -> RevisionView:
        with self._connection() as c: return self._view(c,"assignment",assignment_id)

    def create_rights_revision(self, asset_id: str, record: RightsRecord, expected_revision: int=0) -> RevisionView:
        require(isinstance(record,RightsRecord),"Expected RightsRecord")
        require(record.state!=RightsState.APPROVED,"Approval requires review_rights")
        require(type(expected_revision) is int and expected_revision>=0,"Invalid expected revision")
        self.get_asset(asset_id)
        with self._connection(True) as c:
            current=c.execute("SELECT COALESCE(MAX(revision),0) FROM rights_revisions WHERE entity_id=?",(asset_id,)).fetchone()[0]
            if current!=expected_revision: raise DomainError("CONFLICT","Rights revision changed")
            c.execute("INSERT INTO rights_revisions VALUES(?,?,?,?)",(asset_id,current+1,canonical(record),digest(record)))
            c.execute("DELETE FROM review_state WHERE kind='rights' AND entity_id=?",(asset_id,))
            return self._view(c,"rights",asset_id)

    def _rights_review(self,c,view):
        row=c.execute("SELECT h.* FROM review_state s JOIN review_history h ON h.kind=s.kind AND h.entity_id=s.entity_id AND h.revision=s.revision WHERE s.kind='rights' AND s.entity_id=? AND s.revision=?",(view.entity_id,view.revision)).fetchone()
        return self._event(row) if row else None

    def evaluate_effective_rights(self,asset_id: str,context: UseContext) -> Evaluation:
        with self._connection() as c:
            view=self._view(c,"rights",asset_id)
            return evaluate_effective_rights(RightsRecord(**view.content),context,rights_revision=view.revision,review=self._rights_review(c,view))

    def _assignment_payload(self,c,asset_id,binding,role,usage,state):
        require(isinstance(binding,SceneBinding) and isinstance(usage,UseContext),"Typed binding and usage required")
        identifier(role)
        require(state in ("DRAFT","PENDING_REVIEW","BLOCKED"),"Assignment approval requires review")
        rights=self._view(c,"rights",asset_id)
        return asdict(AssignmentDescriptor(asset_id,binding,role,usage,rights.revision,rights.content_digest,AssignmentState(state)))

    def create_assignment(self,assignment_id: str,asset_id: str,binding: SceneBinding,role: str,usage: UseContext,state: str="DRAFT") -> RevisionView:
        identifier(assignment_id)
        with self._connection(True) as c:
            payload=self._assignment_payload(c,asset_id,binding,role,usage,state)
            if c.execute("SELECT 1 FROM assignments WHERE assignment_id=?",(assignment_id,)).fetchone(): raise DomainError("CONFLICT","Assignment exists")
            c.execute("INSERT INTO assignments VALUES(?,?)",(assignment_id,asset_id))
            c.execute("INSERT INTO assignment_revisions VALUES(?,1,?,?)",(assignment_id,canonical(payload),digest(payload)))
            return self._view(c,"assignment",assignment_id)

    def revise_assignment(self,assignment_id: str,binding: SceneBinding,role: str,usage: UseContext,expected_revision: int,state: str="PENDING_REVIEW") -> RevisionView:
        revision(expected_revision)
        with self._connection(True) as c:
            old=self._view(c,"assignment",assignment_id)
            if old.revision!=expected_revision: raise DomainError("CONFLICT","Assignment revision changed")
            payload=self._assignment_payload(c,old.content["asset_id"],binding,role,usage,state)
            c.execute("INSERT INTO assignment_revisions VALUES(?,?,?,?)",(assignment_id,old.revision+1,canonical(payload),digest(payload)))
            c.execute("DELETE FROM review_state WHERE kind='assignment' AND entity_id=?",(assignment_id,))
            return self._view(c,"assignment",assignment_id)

    def _readiness(self,c,view,binding,context,require_review=True):
        payload=view.content; rights=self._view(c,"rights",payload["asset_id"])
        asset=c.execute("SELECT payload FROM assets WHERE asset_id=?",(payload["asset_id"],)).fetchone()
        AssetDescriptor(**json.loads(asset[0]))
        result=evaluate_effective_rights(RightsRecord(**rights.content),context,rights_revision=rights.revision,review=self._rights_review(c,rights))
        blockers=list(result.blockers)
        if rights.revision!=payload["rights_revision"] or rights.content_digest!=payload["rights_hash"]: blockers.append("STALE_RIGHTS_REVISION")
        if asdict(binding)!=payload["binding"]: blockers.append("STALE_SCENE_BINDING")
        declared=dict(payload["usage"]); actual=asdict(context)
        declared.pop("evaluated_at"); actual.pop("evaluated_at")
        if declared!=actual: blockers.append("USE_CONTEXT_CHANGED")
        if payload["state"]=="BLOCKED" or view.state=="BLOCKED": blockers.append("ASSIGNMENT_BLOCKED")
        if require_review and view.state!="APPROVED": blockers.append("ASSIGNMENT_NOT_APPROVED")
        if require_review:
            reviewed=c.execute("SELECT reviewed_at FROM review_history WHERE kind='assignment' AND entity_id=? AND revision=?",(view.entity_id,view.revision)).fetchone()
            if reviewed and instant(reviewed[0])>instant(context.evaluated_at): blockers.append("ASSIGNMENT_REVIEW_AFTER_EVALUATION")
        return Evaluation(not blockers,tuple(blockers),("MEDIA_AVAILABILITY_UNVERIFIED",),tuple("RESOLVE_"+b for b in blockers),rights.revision,rights.content_digest,context.evaluated_at)

    def evaluate_assignment_readiness(self,assignment_id: str,current_binding: SceneBinding,context: UseContext) -> Evaluation:
        require(isinstance(current_binding,SceneBinding) and isinstance(context,UseContext),"Typed context required")
        with self._connection() as c: return self._readiness(c,self._view(c,"assignment",assignment_id),current_binding,context)

    def _review(self,kind,entity_id,request,binding=None,context=None):
        require(isinstance(request,ReviewRequest),"Expected ReviewRequest")
        payload=canonical({"kind":kind,"entity_id":entity_id,"request":asdict(request),"binding":asdict(binding) if binding else None,"context":asdict(context) if context else None})
        with self._connection(True) as c:
            previous=c.execute("SELECT * FROM review_history WHERE idempotency_key=?",(request.idempotency_key,)).fetchone()
            if previous:
                if previous["request_payload"]!=payload: raise DomainError("CONFLICT","Idempotency key payload differs")
                return self._event(previous)
            view=self._view(c,kind,entity_id)
            if view.revision!=request.expected_revision: raise DomainError("CONFLICT","Review revision changed")
            if view.content_digest!=request.content_digest: raise DomainError("CONFLICT","Reviewed digest changed")
            if c.execute("SELECT 1 FROM review_state WHERE kind=? AND entity_id=? AND revision=?",(kind,entity_id,view.revision)).fetchone(): raise DomainError("CONFLICT","Revision already reviewed")
            if request.decision=="APPROVED":
                if kind=="rights":
                    record=RightsRecord(**view.content)
                    require(record.state in (RightsState.PENDING_REVIEW,),"Only pending rights can be approved")
                    require(all(isinstance(x,str) and x.strip() for x in (record.rights_basis,record.licence_name,record.evidence_notes)),"Rights evidence required")
                    require(record.attribution_required is not None and (not record.attribution_required or bool(record.attribution_text and record.attribution_text.strip())),"Attribution information required")
                else:
                    require(isinstance(binding,SceneBinding) and isinstance(context,UseContext),"Current binding and context required")
                    require(context.evaluated_at==request.reviewed_at,"Review context must use review timestamp")
                    result=self._readiness(c,view,binding,context,False)
                    require(result.eligible,"Assignment blocked: "+",".join(result.blockers))
            c.execute("INSERT INTO review_history(kind,entity_id,revision,reviewer,decision,note,content_digest,reviewed_at,idempotency_key,request_payload) VALUES(?,?,?,?,?,?,?,?,?,?)",(kind,entity_id,view.revision,request.reviewer,request.decision,request.note,request.content_digest,request.reviewed_at,request.idempotency_key,payload))
            c.execute("INSERT INTO review_state VALUES(?,?,?,?) ON CONFLICT(kind,entity_id) DO UPDATE SET revision=excluded.revision,decision=excluded.decision",(kind,entity_id,view.revision,request.decision))
            return self._event(c.execute("SELECT * FROM review_history WHERE idempotency_key=?",(request.idempotency_key,)).fetchone())

    def review_rights(self,asset_id: str,request: ReviewRequest) -> ReviewEvent: return self._review("rights",asset_id,request)
    def review_assignment(self,assignment_id: str,request: ReviewRequest,current_binding: SceneBinding|None=None,context: UseContext|None=None) -> ReviewEvent: return self._review("assignment",assignment_id,request,current_binding,context)
    @staticmethod
    def _event(row): return ReviewEvent(**{k:row[k] for k in ReviewEvent.__dataclass_fields__})
    def get_review_history(self) -> tuple[ReviewEvent,...]:
        with self._connection() as c: return tuple(self._event(r) for r in c.execute("SELECT * FROM review_history ORDER BY sequence"))
    def list_assignments(self) -> tuple[RevisionView,...]:
        with self._connection() as c: return tuple(self._view(c,"assignment",r[0]) for r in c.execute("SELECT assignment_id FROM assignments ORDER BY assignment_id"))
    def create_database_snapshot(self,name: str) -> Path:
        require(Path(name).name==name and name.endswith(".sqlite3"),"Snapshot must be local basename")
        target=self.root/name
        require(target.resolve().parent==self.root and not target.exists(),"Snapshot exists or escapes configured directory")
        try:
            with target.open("xb"): pass
            with self._connection() as source:
                dest=sqlite3.connect(target)
                try: source.backup(dest)
                finally: dest.close()
        except (OSError,sqlite3.Error) as e: raise DomainError("SNAPSHOT",str(e)) from e
        return target
