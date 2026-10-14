"""Version 1 immutable, validated metadata contracts."""
from dataclasses import dataclass, asdict
from datetime import datetime
from enum import Enum
import hashlib, json, re

class DomainError(Exception):
    def __init__(self, code: str, message: str):
        self.code, self.message = code, message
        super().__init__(f"{code}: {message}")

class RightsState(str, Enum):
    UNKNOWN="UNKNOWN"
    PENDING_REVIEW="PENDING_REVIEW"
    APPROVED="APPROVED"
    REJECTED="REJECTED"
    EXPIRED="EXPIRED"
    REVOKED="REVOKED"

class AssignmentState(str, Enum):
    DRAFT="DRAFT"
    PENDING_REVIEW="PENDING_REVIEW"
    APPROVED="APPROVED"
    BLOCKED="BLOCKED"
    STALE="STALE"

def require(ok, message):
    if not ok: raise DomainError("VALIDATION", message)

def identifier(value):
    require(isinstance(value,str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}",value), "Invalid identifier")

def sha(value):
    require(isinstance(value,str) and re.fullmatch(r"[0-9a-f]{64}",value), "Expected lowercase SHA-256")

def instant(value):
    try:
        dt=datetime.fromisoformat(value.replace("Z","+00:00"))
        require(dt.tzinfo is not None and dt.utcoffset() is not None,"Timezone required")
        return dt
    except (ValueError,AttributeError,TypeError): raise DomainError("VALIDATION","Invalid timestamp")

def revision(value):
    require(type(value) is int and value>0,"Revision must be positive integer")

def canonical(value):
    if hasattr(value,"__dataclass_fields__"): value=asdict(value)
    return json.dumps(value,sort_keys=True,separators=(",",":"),ensure_ascii=False)

def digest(value): return hashlib.sha256(canonical(value).encode()).hexdigest()

@dataclass(frozen=True)
class AssetDescriptor:
    asset_id: str
    asset_type: str
    display_name: str
    description: str
    provenance: str
    original_source: str
    created_at: str
    registered_at: str
    mime_type: str|None=None
    byte_size: int|None=None
    sha256: str|None=None
    creator: str|None=None
    publisher: str|None=None
    tags: tuple[str,...]=()
    metadata_version: int=1
    validation_status: str="VALID"
    def __post_init__(self):
        identifier(self.asset_id)
        require(self.asset_type in ("image","video","audio","graphic","other"),"Unsupported asset type")
        require(type(self.metadata_version) is int and self.metadata_version==1,"Unsupported metadata version")
        require(self.validation_status=="VALID","Descriptor must pass validation")
        for v in (self.display_name,self.provenance,self.original_source): require(isinstance(v,str) and bool(v.strip()),"Required text missing")
        require(isinstance(self.description,str),"Description must be text")
        require(instant(self.created_at)<=instant(self.registered_at),"Creation after registration")
        require(self.byte_size is None or (type(self.byte_size) is int and self.byte_size>=0),"Invalid size")
        if self.sha256 is not None: sha(self.sha256)
        if self.mime_type is not None:
            require(isinstance(self.mime_type,str) and bool(re.fullmatch(r"[\w.+-]+/[\w.+-]+",self.mime_type)),"Invalid MIME")
            if self.asset_type in ("image","video","audio"): require(self.mime_type.startswith(self.asset_type+"/"),"MIME/type mismatch")
        for v in (self.creator,self.publisher): require(v is None or isinstance(v,str),"Invalid creator/publisher")
        require(isinstance(self.tags,(tuple,list)) and all(isinstance(t,str) and t.strip() for t in self.tags),"Invalid tags")
        object.__setattr__(self,"tags",tuple(self.tags))

@dataclass(frozen=True)
class RightsRecord:
    state: RightsState=RightsState.UNKNOWN
    rights_basis: str|None=None
    licence_name: str|None=None
    source_reference: str|None=None
    attribution_required: bool|None=None
    commercial: bool|None=None
    modification: bool|None=None
    distribution: bool|None=None
    territories: tuple[str,...]=()
    valid_from: str|None=None
    valid_until: str|None=None
    attribution_text: str|None=None
    evidence_notes: str|None=None
    contract_version: int=1
    def __post_init__(self):
        try: object.__setattr__(self,"state",RightsState(self.state))
        except (ValueError,TypeError): raise DomainError("VALIDATION","Unknown rights state")
        require(type(self.contract_version) is int and self.contract_version==1,"Unsupported rights contract")
        for name in ("attribution_required","commercial","modification","distribution"):
            require(getattr(self,name) is None or type(getattr(self,name)) is bool,"Permission must be Boolean or unknown")
        for name in ("rights_basis","licence_name","source_reference","attribution_text","evidence_notes"):
            require(getattr(self,name) is None or isinstance(getattr(self,name),str),"Rights text must be text")
        require(isinstance(self.territories,(list,tuple)) and all(isinstance(t,str) and re.fullmatch(r"[A-Z]{2}",t) for t in self.territories),"Territories must be uppercase two-letter codes")
        object.__setattr__(self,"territories",tuple(sorted(set(self.territories))))
        if self.valid_from is not None: instant(self.valid_from)
        if self.valid_until is not None: instant(self.valid_until)
        if self.valid_from and self.valid_until: require(instant(self.valid_from)<instant(self.valid_until),"Invalid validity interval")

@dataclass(frozen=True)
class UseContext:
    evaluated_at: str
    commercial: bool=True
    modification: bool=False
    distribution: bool=True
    territory: str|None=None
    attribution_text: str|None=None
    def __post_init__(self):
        instant(self.evaluated_at)
        for v in (self.commercial,self.modification,self.distribution): require(type(v) is bool,"Use flags must be Boolean")
        require(self.territory is None or (isinstance(self.territory,str) and bool(re.fullmatch(r"[A-Z]{2}",self.territory))),"Invalid territory")
        require(self.attribution_text is None or isinstance(self.attribution_text,str),"Invalid attribution")

@dataclass(frozen=True)
class SceneBinding:
    host_application: str
    job_id: str
    scene_id: str
    plan_version: str
    plan_sha256: str
    content_digest: str|None=None
    def __post_init__(self):
        for v in (self.host_application,self.job_id,self.scene_id,self.plan_version): identifier(v)
        sha(self.plan_sha256)
        if self.content_digest is not None: sha(self.content_digest)

@dataclass(frozen=True)
class ReviewRequest:
    reviewer: str
    decision: str
    note: str
    expected_revision: int
    content_digest: str
    idempotency_key: str
    reviewed_at: str
    def __post_init__(self):
        identifier(self.reviewer); identifier(self.idempotency_key)
        require(self.decision in ("APPROVED","REJECTED"),"Invalid review decision")
        require(isinstance(self.note,str) and bool(self.note.strip()),"Review note required")
        revision(self.expected_revision); sha(self.content_digest); instant(self.reviewed_at)

@dataclass(frozen=True)
class RevisionView:
    entity_id: str
    revision: int
    content: dict
    content_digest: str
    state: str

@dataclass(frozen=True)
class Evaluation:
    eligible: bool
    blockers: tuple[str,...]
    warnings: tuple[str,...]
    review_actions: tuple[str,...]
    rights_revision: int
    rights_hash: str
    evaluated_at: str

@dataclass(frozen=True)
class ReviewEvent:
    sequence: int
    kind: str
    entity_id: str
    revision: int
    reviewer: str
    decision: str
    note: str
    content_digest: str
    reviewed_at: str
    idempotency_key: str

@dataclass(frozen=True)
class AssignmentDescriptor:
    asset_id: str
    binding: SceneBinding
    role: str
    usage: UseContext
    rights_revision: int
    rights_hash: str
    state: AssignmentState=AssignmentState.DRAFT
    contract_version: int=1
    def __post_init__(self):
        identifier(self.asset_id); identifier(self.role)
        require(isinstance(self.binding,SceneBinding) and isinstance(self.usage,UseContext),"Typed scene binding and usage required")
        revision(self.rights_revision); sha(self.rights_hash)
        require(type(self.contract_version) is int and self.contract_version==1,"Unsupported assignment contract")
        try: object.__setattr__(self,"state",AssignmentState(self.state))
        except (ValueError,TypeError): raise DomainError("VALIDATION","Unsupported assignment state")
