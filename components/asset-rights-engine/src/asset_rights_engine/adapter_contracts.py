"""ARE-A2 v1: validated host assertions are never authentication evidence."""
from dataclasses import dataclass
from enum import Enum
from .contracts import DomainError, SceneBinding, identifier, revision, sha, require


class PlanState(str, Enum):
    DRAFT = "DRAFT"
    PENDING_REVIEW = "PENDING_REVIEW"
    APPROVED = "APPROVED"
    REVOKED = "REVOKED"


class HostOperation(str, Enum):
    CREATE_ASSIGNMENT = "CREATE_ASSIGNMENT"
    REVISE_ASSIGNMENT = "REVISE_ASSIGNMENT"
    REVIEW_RIGHTS = "REVIEW_RIGHTS"
    REVIEW_ASSIGNMENT = "REVIEW_ASSIGNMENT"
    EVALUATE_READINESS = "EVALUATE_READINESS"


@dataclass(frozen=True)
class HostScope:
    host_application: str
    tenant_id: str
    owner_id: str

    def __post_init__(self) -> None:
        for value in (self.host_application, self.tenant_id, self.owner_id):
            identifier(value)


@dataclass(frozen=True)
class HostSceneKey:
    scope: HostScope
    job_id: str
    scene_id: str

    def __post_init__(self) -> None:
        require(isinstance(self.scope, HostScope), "Expected HostScope")
        identifier(self.job_id)
        identifier(self.scene_id)


@dataclass(frozen=True)
class HostSceneContext:
    """Host-asserted plan state. Construction validates structure, not authority."""
    scene: HostSceneKey
    plan_revision: int
    plan_sha256: str
    plan_state: PlanState
    host_asserts_approved: bool
    scene_content_digest: str | None = None
    contract_version: int = 1

    def __post_init__(self) -> None:
        require(isinstance(self.scene, HostSceneKey), "Expected HostSceneKey")
        revision(self.plan_revision)
        sha(self.plan_sha256)
        if self.scene_content_digest is not None:
            sha(self.scene_content_digest)
        try:
            object.__setattr__(self, "plan_state", PlanState(self.plan_state))
        except (ValueError, TypeError):
            raise DomainError("VALIDATION", "Invalid plan state")
        require(type(self.host_asserts_approved) is bool, "Approval assertion must be Boolean")
        require(self.host_asserts_approved == (self.plan_state == PlanState.APPROVED),
                "Contradictory plan approval assertion")
        require(type(self.contract_version) is int and self.contract_version == 1,
                "Unsupported host adapter contract version")

    def scene_binding(self) -> SceneBinding:
        """Translate structure only; this method confers no authorization."""
        if not self.host_asserts_approved:
            raise DomainError("PLAN_NOT_APPROVED", "Host plan is not asserted approved")
        return SceneBinding(self.scene.scope.host_application, self.scene.job_id,
                            self.scene.scene_id, str(self.plan_revision), self.plan_sha256,
                            self.scene_content_digest)


@dataclass(frozen=True)
class HostPrincipal:
    """Host-supplied principal identifier, with no authenticated/authorized flag."""
    scope: HostScope
    principal_id: str

    def __post_init__(self) -> None:
        require(isinstance(self.scope, HostScope), "Expected HostScope")
        identifier(self.principal_id)


@dataclass(frozen=True)
class HostOperationRequest:
    operation: HostOperation
    expected_context: HostSceneContext
    principal: HostPrincipal
    asset_id: str
    assignment_id: str | None = None

    def __post_init__(self) -> None:
        try:
            object.__setattr__(self, "operation", HostOperation(self.operation))
        except (ValueError, TypeError):
            raise DomainError("VALIDATION", "Unsupported host operation")
        require(isinstance(self.expected_context, HostSceneContext), "Expected HostSceneContext")
        require(isinstance(self.principal, HostPrincipal), "Host principal required")
        if self.principal.scope != self.expected_context.scene.scope:
            raise DomainError("SCOPE_MISMATCH", "Principal scope differs from requested scene scope")
        identifier(self.asset_id)
        if self.operation == HostOperation.REVIEW_RIGHTS:
            require(self.assignment_id is None, "Rights review must not target an assignment")
        else:
            identifier(self.assignment_id)
