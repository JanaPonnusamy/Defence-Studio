"""One reusable, topic-independent package-readiness evaluator and policy-config validator.

Every video topic goes through the same function with the same mandatory criteria; thresholds come
from validated configuration, never from topic names. A failing criterion yields a precise reason and
a corrective action; nothing here relaxes a threshold or hides a failure.
"""

from __future__ import annotations

REQUIRED_SECTIONS = ("version", "claim", "relevance", "corroboration", "readiness", "scoring")


class PolicyConfigError(ValueError):
    pass


def validate_policy_config(cfg: dict) -> dict:
    """Fail fast on a malformed policy file (wrong types, impossible thresholds)."""
    for key in REQUIRED_SECTIONS:
        if key not in cfg:
            raise PolicyConfigError(f"evidence policy missing section {key!r}")
    rd = cfg["readiness"]
    for key in ("min_accepted_claims", "min_accepted_sources"):
        if not isinstance(rd.get(key), int) or isinstance(rd.get(key), bool) or rd[key] < 1:
            raise PolicyConfigError(f"readiness.{key} must be an integer >= 1")
    if not isinstance(rd.get("required_source_categories_any", []), list):
        raise PolicyConfigError("readiness.required_source_categories_any must be a list")
    cor = cfg["corroboration"]
    if not isinstance(cor.get("quantitative_min_independent_groups"), int) or cor["quantitative_min_independent_groups"] < 1:
        raise PolicyConfigError("corroboration.quantitative_min_independent_groups must be an integer >= 1")
    sc = cfg["scoring"]
    weights = sc.get("weights", {})
    if not weights or any((not isinstance(v, (int, float)) or v < 0) for v in weights.values()):
        raise PolicyConfigError("scoring.weights must be non-negative numbers")
    if round(sum(weights.values()), 6) != 100:
        raise PolicyConfigError("scoring.weights must sum to 100")
    if not 0 <= sc.get("auto_accept_min_score", -1) <= 100:
        raise PolicyConfigError("scoring.auto_accept_min_score must be within 0..100")
    for cls in cfg.get("claim_classes", []):
        if not cls.get("id") or int(cls.get("min_independent_groups", 1)) < 1:
            raise PolicyConfigError(f"claim class {cls.get('id')!r} is invalid")
    return cfg


def evaluate_readiness(cfg: dict, *, accepted_claims: int, supporting_sources: set, supporting_categories: set,
                       missing_publisher_sources: set, anchors: list, stale_decisions: bool,
                       versions: set, unresolved_conflicts: list, claims_without_disposition: int) -> list[dict]:
    """Return the failed criteria (empty list == ready from the policy point of view)."""
    rd = cfg["readiness"]
    failed: list[dict] = []

    def fail(criterion, required, actual, action):
        failed.append({"criterion": criterion, "required": required, "actual": actual, "action": action})

    if not anchors:
        fail("topic_anchors", "at least one distinctive topic term", 0,
             "give the job a more specific topic or add a topic profile in the evidence policy")
    if stale_decisions:
        fail("decisions_current", "decisions bound to the current analysis", "stale",
             "re-run the evidence policy on the current analysis")
    if len(versions) > 1:
        fail("policy_version_consistency", "one policy version per run", sorted(versions),
             "re-run the evidence policy so every decision uses one version")
    if claims_without_disposition:
        fail("every_claim_has_disposition", 0, claims_without_disposition,
             "re-run the evidence policy; every claim must receive a disposition")
    if accepted_claims < int(rd["min_accepted_claims"]):
        fail("min_accepted_claims", rd["min_accepted_claims"], accepted_claims,
             "acquire more allowlisted sources for this topic or change the owner policy threshold")
    if len(supporting_sources) < int(rd["min_accepted_sources"]):
        fail("min_accepted_sources", rd["min_accepted_sources"], len(supporting_sources),
             "enable and acquire additional sources in config/source_policy.json that cover the topic")
    required = rd.get("required_source_categories_any") or []
    if required and not (supporting_categories & set(required)):
        fail("required_source_categories_any", required, sorted(c for c in supporting_categories if c),
             "include an accepted source from a required category")
    if rd.get("require_source_publisher") and missing_publisher_sources:
        fail("source_publisher_known", 0, sorted(missing_publisher_sources),
             "set the publisher in the source registry entry")
    if unresolved_conflicts:
        fail("no_unresolved_material_conflicts", 0, unresolved_conflicts,
             "resolve or exclude the conflicting claims before generation")
    return failed
