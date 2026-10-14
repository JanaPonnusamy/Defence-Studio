import inspect
import tempfile
import unittest
from contextlib import contextmanager
from dataclasses import FrozenInstanceError, replace
from pathlib import Path
from threading import Thread
from unittest.mock import patch

from asset_rights_engine import (AssetDescriptor, DomainError, Engine, ReviewRequest,
                                 RightsRecord, UseContext)
from asset_rights_engine.adapter_contracts import (HostOperation, HostOperationRequest,
    HostPrincipal, HostSceneContext, HostSceneKey, HostScope, PlanState)
from asset_rights_engine.host_adapter import AdapterGateway
from asset_rights_engine.simulator import SyntheticHost

T = "2026-10-12T00:00:00Z"
SCOPE = HostScope("fixture-host", "fixture-tenant", "fixture-owner")
SCENE = HostSceneKey(SCOPE, "fixture-job", "fixture-scene")
CONTEXT = HostSceneContext(SCENE, 1, "a" * 64, PlanState.APPROVED, True)
PRINCIPAL = HostPrincipal(SCOPE, "fixture-reviewer")
USAGE = UseContext(T)


def review(view, key="fixture-review", **kwargs):
    return ReviewRequest(**(dict(reviewer=PRINCIPAL.principal_id, decision="APPROVED",
        note="SYNTHETIC TEST REVIEW", expected_revision=view.revision,
        content_digest=view.content_digest, idempotency_key=key, reviewed_at=T) | kwargs))


class AdapterContractTests(unittest.TestCase):
    def test_valid_translation_preserves_a1_binding(self):
        binding = CONTEXT.scene_binding()
        self.assertEqual((binding.host_application, binding.job_id, binding.scene_id,
                          binding.plan_version, binding.plan_sha256),
                         (SCOPE.host_application, SCENE.job_id, SCENE.scene_id, "1", "a" * 64))
    def test_context_is_immutable(self):
        with self.assertRaises(FrozenInstanceError): CONTEXT.plan_revision = 2
    def test_invalid_identifiers(self):
        for bad in (None, "", "../scene", "space here", 1, "x" * 129):
            with self.subTest(value=bad), self.assertRaises(DomainError):
                replace(SCENE, scene_id=bad)
    def test_invalid_scope_identifiers(self):
        for field in ("host_application", "tenant_id", "owner_id"):
            with self.subTest(field=field), self.assertRaises(DomainError):
                replace(SCOPE, **{field: ""})
    def test_invalid_hashes(self):
        for bad in (None, "", "A" * 64, "g" * 64, "a" * 63, 123):
            with self.subTest(value=bad), self.assertRaises(DomainError):
                replace(CONTEXT, plan_sha256=bad)
    def test_invalid_revisions(self):
        for bad in (None, 0, -1, True, "1", 1.0):
            with self.subTest(value=bad), self.assertRaises(DomainError):
                replace(CONTEXT, plan_revision=bad)
    def test_contradictory_approval(self):
        for kwargs in ({"host_asserts_approved": False}, {"plan_state": "DRAFT"},
                       {"plan_state": "PENDING_REVIEW"}, {"plan_state": "REVOKED"},
                       {"host_asserts_approved": 1}, {"plan_state": "invalid"}):
            with self.subTest(kwargs=kwargs), self.assertRaises(DomainError):
                replace(CONTEXT, **kwargs)
    def test_unapproved_structurally_valid_but_binding_rejected(self):
        for state in (PlanState.DRAFT, PlanState.PENDING_REVIEW, PlanState.REVOKED):
            ctx = replace(CONTEXT, plan_state=state, host_asserts_approved=False)
            with self.assertRaises(DomainError): ctx.scene_binding()
    def test_missing_principal(self):
        for bad in (None, "", "../reviewer", "reviewer name"):
            with self.subTest(value=bad), self.assertRaises(DomainError):
                replace(PRINCIPAL, principal_id=bad)
    def test_principal_is_not_authentication(self):
        self.assertFalse(hasattr(PRINCIPAL, "authenticated"))
        self.assertFalse(hasattr(PRINCIPAL, "authorized"))
    def test_nested_types_and_contract_version(self):
        for kwargs in ({"scene": {}}, {"contract_version": 2},
                       {"contract_version": True}, {"scene_content_digest": "bad"}):
            with self.subTest(kwargs=kwargs), self.assertRaises(DomainError):
                replace(CONTEXT, **kwargs)
    def test_content_digest_preserved(self):
        self.assertEqual(replace(CONTEXT, scene_content_digest="b" * 64).scene_binding().content_digest,
                         "b" * 64)
    def test_operation_request_validation(self):
        args = dict(operation=HostOperation.CREATE_ASSIGNMENT, expected_context=CONTEXT,
                    principal=PRINCIPAL, asset_id="engine-asset", assignment_id="engine-assignment")
        HostOperationRequest(**args)
        for kwargs in ({"operation": "invalid"}, {"principal": None}, {"asset_id": ""},
                       {"assignment_id": None}, {"expected_context": None}):
            with self.subTest(kwargs=kwargs), self.assertRaises(DomainError):
                HostOperationRequest(**(args | kwargs))
    def test_rights_request_cannot_target_assignment(self):
        with self.assertRaises(DomainError):
            HostOperationRequest(HostOperation.REVIEW_RIGHTS, CONTEXT, PRINCIPAL,
                                 "engine-asset", "engine-assignment")
    def test_principal_scope_mismatch(self):
        principal = replace(PRINCIPAL, scope=replace(SCOPE, tenant_id="other-tenant"))
        with self.assertRaises(DomainError):
            HostOperationRequest(HostOperation.CREATE_ASSIGNMENT, CONTEXT, principal,
                                 "engine-asset", "engine-assignment")


class HostGatewayTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="are-a2-tests-")
        self.addCleanup(self.tmp.cleanup)
        self.engine = Engine(Path(self.tmp.name))
        self.host = SyntheticHost(SCOPE)
        self.host.set_scene(CONTEXT)
        self.host.grant_synthetic_operations(PRINCIPAL, tuple(HostOperation))
        self.host.declare_asset_scope("engine-asset", SCOPE)
        self.host.declare_assignment_target("engine-assignment", SCENE, "engine-asset")
        self.gateway = AdapterGateway(self.engine, self.host)
        self.engine.register_asset(AssetDescriptor("engine-asset", "image", "TEST ONLY", "Synthetic",
            "Synthetic fixture", "test:adapter", T, T))
        self.rights = self.engine.create_rights_revision("engine-asset", RightsRecord(
            state="PENDING_REVIEW", rights_basis="Test grant", licence_name="TEST ONLY",
            commercial=True, distribution=True, modification=True, attribution_required=False,
            evidence_notes="Synthetic fixture"))
    def approve_rights(self):
        return self.gateway.review_rights("engine-asset", CONTEXT, PRINCIPAL, review(self.rights))
    def create(self, context=CONTEXT, principal=PRINCIPAL):
        return self.gateway.create_assignment("engine-assignment", "engine-asset", context,
                                              principal, "background", USAGE, "PENDING_REVIEW")
    def test_valid_assignment_and_readiness(self):
        self.approve_rights(); view = self.create()
        self.gateway.review_assignment(view.entity_id, "engine-asset", CONTEXT, PRINCIPAL,
                                       review(view, "assignment-review"), USAGE)
        self.assertTrue(self.gateway.evaluate_assignment_readiness(view.entity_id, "engine-asset",
                                                                  CONTEXT, PRINCIPAL, USAGE).eligible)
    def test_unapproved_rejected_before_engine(self):
        context = replace(CONTEXT, plan_state=PlanState.DRAFT, host_asserts_approved=False)
        self.host.set_scene(context)
        with patch.object(self.engine, "create_assignment") as operation:
            with self.assertRaises(DomainError) as error: self.create(context)
            self.assertEqual(error.exception.code, "PLAN_NOT_APPROVED")
            operation.assert_not_called()
    def test_expected_approved_but_authoritative_unapproved(self):
        self.host.set_scene(replace(CONTEXT, plan_state=PlanState.REVOKED, host_asserts_approved=False))
        with self.assertRaises(DomainError) as error: self.create()
        self.assertEqual(error.exception.code, "PLAN_NOT_APPROVED")
        self.assertEqual(self.engine.list_assignments(), ())
    def test_revision_mismatch_before_engine(self):
        self.host.set_scene(replace(CONTEXT, plan_revision=2))
        with patch.object(self.engine, "create_assignment") as operation:
            with self.assertRaises(DomainError) as error: self.create()
            self.assertEqual(error.exception.code, "STALE_PLAN")
            operation.assert_not_called()
    def test_digest_mismatch_before_engine(self):
        self.host.set_scene(replace(CONTEXT, plan_sha256="b" * 64))
        with self.assertRaises(DomainError) as error: self.create()
        self.assertEqual(error.exception.code, "STALE_PLAN")
    def test_content_digest_change_detected(self):
        self.host.set_scene(replace(CONTEXT, scene_content_digest="c" * 64))
        with self.assertRaises(DomainError) as error: self.create()
        self.assertEqual(error.exception.code, "STALE_PLAN")
    def test_scene_substitution_before_engine(self):
        other = replace(CONTEXT, scene=replace(SCENE, scene_id="other-scene"))
        self.host.set_scene(other)
        with patch.object(self.engine, "create_assignment") as operation:
            with self.assertRaises(DomainError) as error: self.create(other)
            self.assertEqual(error.exception.code, "RESOURCE_MISMATCH")
            operation.assert_not_called()
    def test_tenant_and_owner_boundaries_before_engine(self):
        for field in ("host_application", "tenant_id", "owner_id"):
            altered_scope = replace(SCOPE, **{field: "outsider"})
            context = replace(CONTEXT, scene=replace(SCENE, scope=altered_scope))
            principal = replace(PRINCIPAL, scope=altered_scope)
            with self.subTest(field=field), patch.object(self.engine, "create_assignment") as operation:
                with self.assertRaises(DomainError) as error: self.create(context, principal)
                self.assertEqual(error.exception.code, "SCOPE_MISMATCH")
                operation.assert_not_called()
    def test_asset_scope_boundary(self):
        self.host.declare_asset_scope("foreign-asset", replace(SCOPE, tenant_id="foreign-tenant"))
        with self.assertRaises(DomainError) as error:
            self.gateway.review_rights("foreign-asset", CONTEXT, PRINCIPAL, review(self.rights))
        self.assertEqual(error.exception.code, "SCOPE_MISMATCH")
    def test_missing_reviewer_and_mismatched_identity(self):
        with self.assertRaises(DomainError):
            self.gateway.review_rights("engine-asset", CONTEXT, None, review(self.rights))
        with self.assertRaises(DomainError) as error:
            self.gateway.review_rights("engine-asset", CONTEXT, PRINCIPAL,
                                      review(self.rights, reviewer="another-reviewer"))
        self.assertEqual(error.exception.code, "PRINCIPAL_MISMATCH")
        self.assertEqual(self.engine.get_review_history(), ())
    def test_supplied_identifier_does_not_grant_policy(self):
        ungranted = HostPrincipal(SCOPE, "ungranted-reviewer")
        with self.assertRaises(DomainError) as error:
            self.gateway.review_rights("engine-asset", CONTEXT, ungranted,
                                      review(self.rights, reviewer=ungranted.principal_id))
        self.assertEqual(error.exception.code, "HOST_POLICY_DENIED")
    def test_policy_is_operation_specific(self):
        self.host.grant_synthetic_operations(PRINCIPAL, (HostOperation.EVALUATE_READINESS,))
        with self.assertRaises(DomainError) as error: self.approve_rights()
        self.assertEqual(error.exception.code, "HOST_POLICY_DENIED")
    def test_idempotent_replay_and_conflict(self):
        first = self.approve_rights()
        self.assertEqual(first, self.approve_rights())
        with self.assertRaises(DomainError) as error:
            self.gateway.review_rights("engine-asset", CONTEXT, PRINCIPAL,
                                      review(self.rights, note="Changed payload"))
        self.assertEqual(error.exception.code, "CONFLICT")
        self.assertEqual(len(self.engine.get_review_history()), 1)
    def test_replay_rechecks_current_host_policy(self):
        self.approve_rights()
        self.host.grant_synthetic_operations(PRINCIPAL, ())
        with self.assertRaises(DomainError) as error: self.approve_rights()
        self.assertEqual(error.exception.code, "HOST_POLICY_DENIED")
        self.assertEqual(len(self.engine.get_review_history()), 1)
    def test_review_context_stale_blocks_even_replay(self):
        self.approve_rights(); view = self.create(); req = review(view, "ar")
        self.gateway.review_assignment(view.entity_id, "engine-asset", CONTEXT, PRINCIPAL, req, USAGE)
        changed = replace(CONTEXT, plan_sha256="b" * 64)
        self.host.set_scene(changed)
        with self.assertRaises(DomainError) as error:
            self.gateway.review_assignment(view.entity_id, "engine-asset", CONTEXT, PRINCIPAL, req, USAGE)
        self.assertEqual(error.exception.code, "STALE_PLAN")
        result = self.gateway.evaluate_assignment_readiness(view.entity_id, "engine-asset", changed,
                                                            PRINCIPAL, USAGE)
        self.assertFalse(result.eligible)
        self.assertIn("STALE_SCENE_BINDING", result.blockers)
        self.assertEqual(len(self.engine.get_review_history()), 2)
    def test_revise_assignment_requires_new_review(self):
        self.approve_rights(); view = self.create()
        self.gateway.review_assignment(view.entity_id, "engine-asset", CONTEXT, PRINCIPAL,
                                       review(view, "ar"), USAGE)
        changed = replace(CONTEXT, plan_revision=2)
        self.host.set_scene(changed)
        newer = self.gateway.revise_assignment(view.entity_id, "engine-asset", changed, PRINCIPAL,
                                                "background", USAGE, 1)
        self.assertEqual(newer.revision, 2)
        self.assertFalse(self.gateway.evaluate_assignment_readiness(view.entity_id, "engine-asset",
                                                                    changed, PRINCIPAL, USAGE).eligible)
        self.gateway.review_assignment(view.entity_id, "engine-asset", changed, PRINCIPAL,
                                       review(newer, "ar2"), USAGE)
        self.assertTrue(self.gateway.evaluate_assignment_readiness(view.entity_id, "engine-asset",
                                                                   changed, PRINCIPAL, USAGE).eligible)
    def test_engine_rights_checks_not_bypassed(self):
        view = self.create()
        with self.assertRaises(DomainError):
            self.gateway.review_assignment(view.entity_id, "engine-asset", CONTEXT, PRINCIPAL,
                                           review(view, "ar"), USAGE)
        self.assertEqual(self.engine.get_review_history(), ())
    def test_unknown_provider_and_invalid_provider_response(self):
        with self.assertRaises(DomainError): AdapterGateway(self.engine, None)
        class BadProvider:
            @contextmanager
            def operation_context(self, request): yield None
        gateway = AdapterGateway(self.engine, BadProvider())
        with self.assertRaises(DomainError):
            gateway.create_assignment("engine-assignment", "engine-asset", CONTEXT, PRINCIPAL,
                                      "background", USAGE)
        self.assertEqual(self.engine.list_assignments(), ())
    def test_provider_cannot_substitute_scene(self):
        class WrongSceneProvider:
            @contextmanager
            def operation_context(self, request):
                yield replace(CONTEXT, scene=replace(SCENE, scene_id="wrong-scene"))
        gateway = AdapterGateway(self.engine, WrongSceneProvider())
        with self.assertRaises(DomainError) as error:
            gateway.create_assignment("engine-assignment", "engine-asset", CONTEXT, PRINCIPAL,
                                      "background", USAGE)
        self.assertEqual(error.exception.code, "SCENE_MISMATCH")
    def test_assignment_asset_crosscheck(self):
        self.create()
        self.engine.register_asset(AssetDescriptor("second-asset", "image", "TEST ONLY", "Synthetic",
            "Fixture", "test:second", T, T))
        self.host.declare_asset_scope("second-asset", SCOPE)
        # A deliberately broken resource map is still cross-checked against A1 content.
        class BrokenMapProvider:
            @contextmanager
            def operation_context(self, request): yield CONTEXT
        gateway = AdapterGateway(self.engine, BrokenMapProvider())
        with self.assertRaises(DomainError) as error:
            gateway.evaluate_assignment_readiness("engine-assignment", "second-asset", CONTEXT,
                                                  PRINCIPAL, USAGE)
        self.assertEqual(error.exception.code, "RESOURCE_MISMATCH")
    def test_scene_binding_crosscheck(self):
        self.create()
        other = replace(CONTEXT, scene=replace(SCENE, scene_id="other-scene"))
        class BrokenMapProvider:
            @contextmanager
            def operation_context(self, request): yield other
        gateway = AdapterGateway(self.engine, BrokenMapProvider())
        with self.assertRaises(DomainError) as error:
            gateway.evaluate_assignment_readiness("engine-assignment", "engine-asset", other,
                                                  PRINCIPAL, USAGE)
        self.assertEqual(error.exception.code, "SCENE_MISMATCH")
    def test_host_state_is_locked_through_engine_operation(self):
        original = self.engine.create_assignment
        acquired = []
        def call(*args, **kwargs):
            def probe():
                ok = self.host._lock.acquire(blocking=False)
                acquired.append(ok)
                if ok: self.host._lock.release()
            thread = Thread(target=probe); thread.start(); thread.join(timeout=5)
            self.assertFalse(thread.is_alive())
            return original(*args, **kwargs)
        with patch.object(self.engine, "create_assignment", side_effect=call): self.create()
        self.assertEqual(acquired, [False])
    def test_temporary_storage_only(self):
        self.assertEqual(self.engine.path.parent, Path(self.tmp.name).resolve())
        self.assertEqual(self.engine.validate_database()["schema_version"], 1)
    def test_a1_signatures_remain_compatible(self):
        self.assertEqual(list(inspect.signature(Engine.create_assignment).parameters),
                         ["self", "assignment_id", "asset_id", "binding", "role", "usage", "state"])
        self.assertEqual(list(inspect.signature(Engine.review_assignment).parameters),
                         ["self", "assignment_id", "request", "current_binding", "context"])
        self.assertEqual(inspect.signature(Engine.create_rights_revision).parameters["expected_revision"].default, 0)
    def test_resource_mappings_cannot_silently_rebind(self):
        with self.assertRaises(DomainError):
            self.host.declare_assignment_target("engine-assignment", replace(SCENE, scene_id="other"), "engine-asset")
        with self.assertRaises(DomainError):
            self.host.declare_asset_scope("engine-asset", replace(SCOPE, tenant_id="other"))
    def test_unknown_resource_denied(self):
        with self.assertRaises(DomainError) as error:
            self.gateway.create_assignment("unknown-assignment", "engine-asset", CONTEXT,
                                          PRINCIPAL, "background", USAGE)
        self.assertEqual(error.exception.code, "HOST_POLICY_DENIED")
