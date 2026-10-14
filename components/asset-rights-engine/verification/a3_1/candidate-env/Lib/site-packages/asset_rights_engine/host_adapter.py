"""Optional host gateway. A trusted provider is a production prerequisite."""
from contextlib import AbstractContextManager, contextmanager
from typing import Iterator, Protocol
from .adapter_contracts import (HostOperation, HostOperationRequest, HostPrincipal,
                                HostSceneContext)
from .contracts import DomainError, Evaluation, ReviewEvent, ReviewRequest, RevisionView, UseContext, require
from .engine import Engine


class HostAdapter(Protocol):
    def operation_context(self, request: HostOperationRequest) -> AbstractContextManager[HostSceneContext]:
        """Authenticate/authorize, verify resource ownership and current approved plan.

        A production implementation must derive identity from an authenticated session,
        independently authorize the operation and asset/assignment scope, load the
        authoritative scene, and keep its state stable until the context exits. Reject
        by raising DomainError. Returning a DTO alone is not proof of authorization.
        The component cannot verify that an implementation fulfills these obligations.
        """
        ...


class AdapterGateway:
    """Use only behind a trusted host boundary; never accept providers from end users."""
    def __init__(self, engine: Engine, host: HostAdapter):
        require(isinstance(engine, Engine), "Expected Engine")
        require(callable(getattr(host, "operation_context", None)), "Host adapter provider required")
        self._engine, self._host = engine, host

    @contextmanager
    def _context(self, request: HostOperationRequest) -> Iterator[HostSceneContext]:
        with self._host.operation_context(request) as current:
            require(isinstance(current, HostSceneContext), "Host provider returned invalid context")
            if current.scene != request.expected_context.scene:
                raise DomainError("SCENE_MISMATCH", "Authoritative host scene identity differs")
            if not current.host_asserts_approved or not request.expected_context.host_asserts_approved:
                raise DomainError("PLAN_NOT_APPROVED", "Assignment boundary requires an approved plan")
            if current != request.expected_context:
                raise DomainError("STALE_PLAN", "Expected plan differs from authoritative host context")
            yield current

    def create_assignment(self, assignment_id: str, asset_id: str,
                          expected_context: HostSceneContext, principal: HostPrincipal,
                          role: str, usage: UseContext, state: str = "DRAFT") -> RevisionView:
        request = HostOperationRequest(HostOperation.CREATE_ASSIGNMENT, expected_context,
                                       principal, asset_id, assignment_id)
        with self._context(request) as current:
            return self._engine.create_assignment(assignment_id, asset_id, current.scene_binding(),
                                                   role, usage, state)

    def _check_assignment(self, request: HostOperationRequest) -> None:
        view = self._engine.get_assignment(request.assignment_id)
        if view.content["asset_id"] != request.asset_id:
            raise DomainError("RESOURCE_MISMATCH", "Assignment belongs to another asset")
        binding = view.content["binding"]
        scene = request.expected_context.scene
        if (binding["host_application"], binding["job_id"], binding["scene_id"]) != (
                scene.scope.host_application, scene.job_id, scene.scene_id):
            raise DomainError("SCENE_MISMATCH", "Assignment belongs to another scene")

    def revise_assignment(self, assignment_id: str, asset_id: str,
                          expected_context: HostSceneContext, principal: HostPrincipal,
                          role: str, usage: UseContext, expected_revision: int,
                          state: str = "PENDING_REVIEW") -> RevisionView:
        request = HostOperationRequest(HostOperation.REVISE_ASSIGNMENT, expected_context,
                                       principal, asset_id, assignment_id)
        with self._context(request) as current:
            self._check_assignment(request)
            return self._engine.revise_assignment(assignment_id, current.scene_binding(), role,
                                                   usage, expected_revision, state)

    @staticmethod
    def _review_identity(principal: HostPrincipal, review: ReviewRequest) -> None:
        require(isinstance(principal, HostPrincipal), "Host principal required")
        require(isinstance(review, ReviewRequest), "Expected ReviewRequest")
        if principal.principal_id != review.reviewer:
            raise DomainError("PRINCIPAL_MISMATCH", "Review identity differs from host principal")

    def review_rights(self, asset_id: str, expected_context: HostSceneContext,
                      principal: HostPrincipal, review: ReviewRequest) -> ReviewEvent:
        self._review_identity(principal, review)
        request = HostOperationRequest(HostOperation.REVIEW_RIGHTS, expected_context,
                                       principal, asset_id)
        with self._context(request):
            return self._engine.review_rights(asset_id, review)

    def review_assignment(self, assignment_id: str, asset_id: str,
                          expected_context: HostSceneContext, principal: HostPrincipal,
                          review: ReviewRequest, usage: UseContext) -> ReviewEvent:
        self._review_identity(principal, review)
        request = HostOperationRequest(HostOperation.REVIEW_ASSIGNMENT, expected_context,
                                       principal, asset_id, assignment_id)
        with self._context(request) as current:
            self._check_assignment(request)
            return self._engine.review_assignment(assignment_id, review, current.scene_binding(), usage)

    def evaluate_assignment_readiness(self, assignment_id: str, asset_id: str,
                                      expected_context: HostSceneContext, principal: HostPrincipal,
                                      usage: UseContext) -> Evaluation:
        request = HostOperationRequest(HostOperation.EVALUATE_READINESS, expected_context,
                                       principal, asset_id, assignment_id)
        with self._context(request) as current:
            self._check_assignment(request)
            return self._engine.evaluate_assignment_readiness(assignment_id,
                                                               current.scene_binding(), usage)
