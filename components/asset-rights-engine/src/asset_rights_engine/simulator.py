"""OFFLINE DEVELOPMENT TOOL ONLY. Synthetic policy is not authentication."""
from contextlib import contextmanager
from dataclasses import dataclass, replace
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Barrier, RLock, Thread
from typing import Iterator
from .adapter_contracts import (HostOperation, HostOperationRequest, HostPrincipal,
                                HostSceneContext, HostSceneKey, HostScope, PlanState)
from .host_adapter import AdapterGateway
from .contracts import (AssetDescriptor, DomainError, ReviewRequest, RightsRecord,
                        UseContext, identifier, require)
from .engine import Engine


class SyntheticHost:
    """In-memory fixture registry. No supplied principal is authenticated.

    Preconfigured grants simulate host policy. Production must replace this entire
    provider with authenticated authorization and authoritative resource resolution.
    The lock keeps synthetic plan state stable through the actual engine operation.
    """
    def __init__(self, scope: HostScope):
        require(isinstance(scope, HostScope), "Expected HostScope")
        self.scope = scope
        self._lock = RLock()
        self._scenes: dict[HostSceneKey, HostSceneContext] = {}
        self._assets: dict[str, HostScope] = {}
        self._targets: dict[str, tuple[HostSceneKey, str]] = {}
        self._grants: dict[HostPrincipal, frozenset[HostOperation]] = {}

    def set_scene(self, context: HostSceneContext) -> None:
        require(isinstance(context, HostSceneContext), "Expected HostSceneContext")
        if context.scene.scope != self.scope:
            raise DomainError("SCOPE_MISMATCH", "Synthetic scene outside configured scope")
        with self._lock:
            old = self._scenes.get(context.scene)
            if old and context.plan_revision < old.plan_revision:
                raise DomainError("STALE_PLAN", "Synthetic plan revision cannot move backwards")
            self._scenes[context.scene] = context

    def declare_asset_scope(self, asset_id: str, scope: HostScope) -> None:
        identifier(asset_id)
        require(isinstance(scope, HostScope), "Expected HostScope")
        with self._lock:
            if asset_id in self._assets and self._assets[asset_id] != scope:
                raise DomainError("CONFLICT", "Synthetic asset ownership cannot be reassigned")
            self._assets[asset_id] = scope

    def declare_assignment_target(self, assignment_id: str, scene: HostSceneKey, asset_id: str) -> None:
        identifier(assignment_id)
        identifier(asset_id)
        require(isinstance(scene, HostSceneKey), "Expected HostSceneKey")
        with self._lock:
            if scene.scope != self.scope or self._assets.get(asset_id) != self.scope:
                raise DomainError("SCOPE_MISMATCH", "Synthetic assignment target outside configured scope")
            target = (scene, asset_id)
            if assignment_id in self._targets and self._targets[assignment_id] != target:
                raise DomainError("CONFLICT", "Synthetic assignment identity cannot be rebound")
            self._targets[assignment_id] = target

    def grant_synthetic_operations(self, principal: HostPrincipal,
                                   operations: tuple[HostOperation, ...]) -> None:
        require(isinstance(principal, HostPrincipal), "Expected HostPrincipal")
        if principal.scope != self.scope:
            raise DomainError("SCOPE_MISMATCH", "Synthetic principal outside configured scope")
        require(isinstance(operations, tuple) and all(isinstance(op, HostOperation) for op in operations),
                "Expected tuple of HostOperation")
        with self._lock:
            self._grants[principal] = frozenset(operations)

    @contextmanager
    def operation_context(self, request: HostOperationRequest) -> Iterator[HostSceneContext]:
        require(isinstance(request, HostOperationRequest), "Expected HostOperationRequest")
        with self._lock:
            if request.expected_context.scene.scope != self.scope or request.principal.scope != self.scope:
                raise DomainError("SCOPE_MISMATCH", "Host/tenant/owner scope mismatch")
            if request.operation not in self._grants.get(request.principal, frozenset()):
                raise DomainError("HOST_POLICY_DENIED", "No synthetic operation grant; identity is not authentication")
            if self._assets.get(request.asset_id) != self.scope:
                raise DomainError("SCOPE_MISMATCH", "Asset ownership does not match host scope")
            if request.assignment_id is not None:
                target = self._targets.get(request.assignment_id)
                if target is None:
                    raise DomainError("HOST_POLICY_DENIED", "Unknown synthetic assignment target")
                if target != (request.expected_context.scene, request.asset_id):
                    raise DomainError("RESOURCE_MISMATCH", "Scene/asset substituted for declared assignment target")
            current = self._scenes.get(request.expected_context.scene)
            if current is None:
                raise DomainError("SCENE_MISMATCH", "Unknown authoritative synthetic scene")
            yield current


@dataclass(frozen=True)
class SimulationReport:
    scenarios: tuple[tuple[str, bool], ...]
    authentication: str = "NOT_IMPLEMENTED_SYNTHETIC_IDENTIFIERS_ONLY"
    storage: str = "TEMPORARY_DATABASE_DELETED_ON_EXIT"
    schema_version: int = 1


def run_simulation() -> SimulationReport:
    """Fixed synthetic data, temporary storage, no remote or persistent inputs."""
    timestamp = "2026-10-12T00:00:00Z"
    scope = HostScope("synthetic-host", "synthetic-tenant", "synthetic-owner")
    scene = HostSceneKey(scope, "synthetic-job", "synthetic-scene")
    context = HostSceneContext(scene, 1, "a" * 64, PlanState.APPROVED, True)
    principal = HostPrincipal(scope, "synthetic-reviewer")
    usage = UseContext(timestamp)
    outcomes: list[tuple[str, bool]] = []

    def expect_error(name, code, operation):
        try:
            operation()
        except DomainError as error:
            require(error.code == code, f"Scenario {name}: expected {code}, got {error.code}")
            outcomes.append((name, True))
        else:
            raise AssertionError(f"Scenario {name} unexpectedly succeeded")

    with TemporaryDirectory(prefix="are-a2-synthetic-") as directory:
        engine = Engine(Path(directory))
        host = SyntheticHost(scope)
        host.set_scene(context)
        host.grant_synthetic_operations(principal, tuple(HostOperation))
        engine.register_asset(AssetDescriptor("synthetic-asset", "image", "TEST DATA ONLY",
            "No real media or rights", "Synthetic fixture", "test:are-a2", timestamp, timestamp))
        host.declare_asset_scope("synthetic-asset", scope)
        host.declare_assignment_target("synthetic-assignment", scene, "synthetic-asset")
        gateway = AdapterGateway(engine, host)
        rights = engine.create_rights_revision("synthetic-asset", RightsRecord(
            state="PENDING_REVIEW", rights_basis="Synthetic grant", licence_name="TEST ONLY",
            commercial=True, modification=False, distribution=True, attribution_required=False,
            evidence_notes="SYNTHETIC TEST DATA, not rights evidence"))
        review = ReviewRequest(principal.principal_id, "APPROVED", "Synthetic test decision",
                               rights.revision, rights.content_digest, "synthetic-rights-review", timestamp)
        first = gateway.review_rights("synthetic-asset", context, principal, review)
        outcomes.append(("approved_context_valid", context.scene_binding().plan_version == "1"))
        unapproved = replace(context, plan_state=PlanState.DRAFT, host_asserts_approved=False)
        host.set_scene(unapproved)
        expect_error("unapproved_plan_rejected", "PLAN_NOT_APPROVED", lambda:
            gateway.create_assignment("synthetic-assignment", "synthetic-asset", unapproved, principal,
                                      "background", usage))
        host.set_scene(context)
        for label, bad_hash in (("missing_hash_rejected", None), ("malformed_hash_rejected", "bad")):
            expect_error(label, "VALIDATION", lambda h=bad_hash: replace(context, plan_sha256=h))
        other_scene = HostSceneKey(scope, "synthetic-job", "other-scene")
        substituted = replace(context, scene=other_scene)
        host.set_scene(substituted)
        expect_error("scene_substitution_rejected", "RESOURCE_MISMATCH", lambda:
            gateway.create_assignment("synthetic-assignment", "synthetic-asset", substituted, principal,
                                      "background", usage))
        latest = replace(context, plan_revision=2)
        host.set_scene(latest)
        expect_error("stale_revision_rejected_before_assignment", "STALE_PLAN", lambda:
            gateway.create_assignment("synthetic-assignment", "synthetic-asset", context, principal,
                                      "background", usage))
        assignment = gateway.create_assignment("synthetic-assignment", "synthetic-asset", latest,
                                               principal, "background", usage, "PENDING_REVIEW")
        assignment_review = ReviewRequest(principal.principal_id, "APPROVED", "Synthetic scene decision",
            assignment.revision, assignment.content_digest, "synthetic-assignment-review", timestamp)
        gateway.review_assignment(assignment.entity_id, "synthetic-asset", latest, principal,
                                  assignment_review, usage)
        ready = gateway.evaluate_assignment_readiness(assignment.entity_id, "synthetic-asset",
                                                      latest, principal, usage)
        outcomes.append(("approved_assignment_ready", ready.eligible))
        changed = replace(latest, plan_sha256="b" * 64)
        host.set_scene(changed)
        expect_error("changed_digest_old_context_rejected", "STALE_PLAN", lambda:
            gateway.review_assignment(assignment.entity_id, "synthetic-asset", latest, principal,
                                      assignment_review, usage))
        blocked = gateway.evaluate_assignment_readiness(assignment.entity_id, "synthetic-asset",
                                                        changed, principal, usage)
        outcomes.append(("changed_digest_old_assignment_blocked", not blocked.eligible and
                         "STALE_SCENE_BINDING" in blocked.blockers))
        expect_error("missing_reviewer_rejected", "VALIDATION", lambda:
            gateway.review_rights("synthetic-asset", changed, None, review))
        unknown = HostPrincipal(scope, "ungranted-synthetic-principal")
        expect_error("identifier_not_authentication", "HOST_POLICY_DENIED", lambda:
            gateway.review_rights("synthetic-asset", changed, unknown,
                                 replace(review, reviewer=unknown.principal_id)))
        for label, altered_scope in (
                ("tenant_boundary_rejected", replace(scope, tenant_id="other-tenant")),
                ("owner_boundary_rejected", replace(scope, owner_id="other-owner"))):
            outsider = replace(changed, scene=replace(scene, scope=altered_scope))
            actor = HostPrincipal(altered_scope, principal.principal_id)
            expect_error(label, "SCOPE_MISMATCH", lambda c=outsider, p=actor:
                gateway.review_rights("synthetic-asset", c, p, review))
        # Rights-review payload excludes scene context in A1. The host rechecks current
        # authorization and context, then the engine returns the original immutable event.
        replay = gateway.review_rights("synthetic-asset", changed, principal, review)
        outcomes.append(("idempotent_review_replay", first == replay and len(engine.get_review_history()) == 2))
        expect_error("idempotency_payload_conflict", "CONFLICT", lambda:
            gateway.review_rights("synthetic-asset", changed, principal, replace(review, note="Changed note")))
        # Concurrent opposite decisions over a fresh rights revision. Which wins is
        # deliberately not asserted; one success, one conflict is deterministic.
        newer = engine.create_rights_revision("synthetic-asset", RightsRecord(
            state="PENDING_REVIEW", rights_basis="Synthetic grant", licence_name="TEST ONLY",
            commercial=True, distribution=True, attribution_required=False, evidence_notes="TEST ONLY"), 1)
        gate = Barrier(2)
        results: list[str] = []
        result_lock = RLock()
        def attempt(decision):
            gate.wait(timeout=10)
            try:
                req = ReviewRequest(principal.principal_id, decision, "Synthetic concurrent decision",
                    newer.revision, newer.content_digest, "synthetic-race-" + decision, timestamp)
                gateway.review_rights("synthetic-asset", changed, principal, req)
                result = "SUCCESS"
            except DomainError as error:
                result = error.code
            with result_lock:
                results.append(result)
        threads = [Thread(target=attempt, args=(decision,)) for decision in ("APPROVED", "REJECTED")]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=15)
        require(not any(thread.is_alive() for thread in threads), "Concurrent simulator thread timed out")
        outcomes.append(("concurrent_conflict_preserves_first_decision",
                         sorted(results) == ["CONFLICT", "SUCCESS"] and len(engine.get_review_history()) == 3))
        outcomes.append(("temporary_database_integrity", engine.validate_database()["valid"]))
    outcomes.append(("temporary_database_removed", not Path(directory).exists()))
    require(all(ok for _, ok in outcomes), "A synthetic scenario failed")
    return SimulationReport(tuple(outcomes))
