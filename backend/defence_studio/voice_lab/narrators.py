"""Narrator identities, consent records and per-narrator model versions (VL-2).

Identity separation is enforced in the database: every model version carries an
``identity_key`` that is UNIQUE across all narrators (base speaker or checkpoint hash),
so one voice identity can never be attached to two narrators.
"""
from __future__ import annotations

import hashlib
import re
from contextlib import closing

from defence_studio.voice_lab.common import VoiceLabError, canon, loads, new_id, now
from defence_studio.voice_lab.context import Ctx

ROLES = {"NARRATOR", "REFERENCE_SPEAKER"}
LANG = re.compile(r"^[a-z]{2,3}(-[A-Za-z0-9]{2,8})?$")


class Narrators:
    def __init__(self, ctx: Ctx):
        self.ctx = ctx

    # ------------------------------------------------------------- consents
    def create_consent(self, owner_id, *, subject_name, subject_role, scope, document_ref, document_sha256=None,
                       granted_at=None, expires_at=None):
        if subject_role not in ROLES:
            raise VoiceLabError(422, f"subject_role must be one of {sorted(ROLES)}")
        if not (subject_name or "").strip() or not (document_ref or "").strip():
            raise VoiceLabError(422, "subject_name and document_ref (the signed consent record reference) are required")
        if not isinstance(scope, list) or not scope or not set(scope) <= {"TRAINING", "GENERATION", "COMMERCIAL",
                                                                           "ANALYSIS"}:
            raise VoiceLabError(422, "scope must be a non-empty list of TRAINING/GENERATION/COMMERCIAL/ANALYSIS")
        cid = new_id("consent")
        with self.ctx.tx() as c:
            c.execute("INSERT INTO vl_consent_records VALUES(?,?,?,?,?,?,?,?,?,NULL,?)",
                      (cid, owner_id, subject_name.strip(), subject_role, canon(sorted(scope)), document_ref.strip(),
                       document_sha256, granted_at or now(), expires_at, now()))
        return self.consent(cid, owner_id)

    def consent(self, cid, owner_id):
        with closing(self.ctx.conn()) as c:
            r = c.execute("SELECT * FROM vl_consent_records WHERE consent_id=? AND owner_id=?", (cid, owner_id)).fetchone()
        if not r:
            raise VoiceLabError(404, "consent record not found")
        d = dict(r)
        d["scope"] = loads(d.pop("scope_json"), [])
        d["valid"] = d["revoked_at"] is None and (d["expires_at"] is None or d["expires_at"] > now())
        return d

    def list_consents(self, owner_id):
        with closing(self.ctx.conn()) as c:
            ids = [r[0] for r in c.execute("SELECT consent_id FROM vl_consent_records WHERE owner_id=? ORDER BY created_at",
                                           (owner_id,))]
        return [self.consent(i, owner_id) for i in ids]

    def revoke_consent(self, cid, owner_id):
        self.consent(cid, owner_id)
        with self.ctx.tx() as c:
            c.execute("UPDATE vl_consent_records SET revoked_at=? WHERE consent_id=? AND revoked_at IS NULL", (now(), cid))
        return self.consent(cid, owner_id)

    def require_consent(self, cid, owner_id, needed: str):
        if not cid:
            raise VoiceLabError(409, {"code": "CONSENT_MISSING", "detail": f"a consent record with {needed} scope is required"})
        con = self.consent(cid, owner_id)
        if not con["valid"]:
            raise VoiceLabError(409, {"code": "CONSENT_INVALID", "detail": "consent is revoked or expired"})
        if needed not in con["scope"]:
            raise VoiceLabError(409, {"code": "CONSENT_SCOPE", "detail": f"consent does not cover {needed}"})
        return con

    # ------------------------------------------------------------ narrators
    def create(self, owner_id, *, display_name, gender_label=None, languages=("ta",), description="", consent_id=None):
        name = (display_name or "").strip()
        if not name or len(name) > 120:
            raise VoiceLabError(422, "display_name is required (max 120 chars)")
        langs = list(dict.fromkeys(languages or []))
        if not langs or not all(LANG.match(l) for l in langs):
            raise VoiceLabError(422, "languages must be BCP-47-like codes, e.g. ['ta']")
        if consent_id:
            self.consent(consent_id, owner_id)
        nid, ts = new_id("narr"), now()
        try:
            with self.ctx.tx() as c:
                c.execute("INSERT INTO vl_voice_profiles VALUES(?,?,?,?,?,?,'ACTIVE',?,NULL,?,?)",
                          (nid, owner_id, name, (gender_label or None), canon(langs), description or "", consent_id, ts, ts))
        except Exception as e:
            if "UNIQUE" in str(e):
                raise VoiceLabError(409, f"a narrator named {name!r} already exists") from None
            raise
        return self.get(nid, owner_id)

    def get(self, nid, owner_id):
        with closing(self.ctx.conn()) as c:
            r = c.execute("SELECT * FROM vl_voice_profiles WHERE narrator_id=? AND owner_id=?", (nid, owner_id)).fetchone()
            if not r:
                raise VoiceLabError(404, "narrator not found")
            counts = c.execute(
                "SELECT (SELECT COUNT(*) FROM vl_voice_datasets WHERE narrator_id=?) ds,"
                "(SELECT COUNT(*) FROM vl_model_versions WHERE narrator_id=?) mv,"
                "(SELECT COUNT(*) FROM vl_training_jobs WHERE narrator_id=?) tj,"
                "(SELECT COUNT(*) FROM vl_generation_jobs WHERE narrator_id=?) gj", (nid,) * 4).fetchone()
        d = dict(r)
        d["languages"] = loads(d.pop("languages_json"), [])
        d.update(dataset_count=counts["ds"], model_version_count=counts["mv"], training_job_count=counts["tj"],
                 generation_count=counts["gj"])
        d["trained"] = self._has_trained(nid)
        return d

    def _has_trained(self, nid):
        with closing(self.ctx.conn()) as c:
            return bool(c.execute("SELECT 1 FROM vl_model_versions WHERE narrator_id=? AND kind='FINE_TUNED'",
                                  (nid,)).fetchone())

    def list(self, owner_id, include_archived=False):
        q = "SELECT narrator_id FROM vl_voice_profiles WHERE owner_id=?" + ("" if include_archived else " AND status='ACTIVE'")
        with closing(self.ctx.conn()) as c:
            ids = [r[0] for r in c.execute(q + " ORDER BY created_at", (owner_id,))]
        return [self.get(i, owner_id) for i in ids]

    def update(self, nid, owner_id, **fields):
        cur = self.get(nid, owner_id)
        allowed = {"display_name", "gender_label", "description", "languages", "consent_id"}
        bad = set(fields) - allowed
        if bad:
            raise VoiceLabError(422, f"cannot update {sorted(bad)}")
        if "consent_id" in fields and fields["consent_id"]:
            self.consent(fields["consent_id"], owner_id)
        if "languages" in fields:
            if not fields["languages"] or not all(LANG.match(l) for l in fields["languages"]):
                raise VoiceLabError(422, "invalid languages")
            fields["languages_json"] = canon(list(dict.fromkeys(fields.pop("languages"))))
        if not fields:
            return cur
        sets = ", ".join(f"{k}=?" for k in fields)
        with self.ctx.tx() as c:
            c.execute(f"UPDATE vl_voice_profiles SET {sets}, updated_at=? WHERE narrator_id=?", (*fields.values(), now(), nid))
        return self.get(nid, owner_id)

    def set_status(self, nid, owner_id, status):
        self.get(nid, owner_id)
        with self.ctx.tx() as c:
            c.execute("UPDATE vl_voice_profiles SET status=?, updated_at=? WHERE narrator_id=?", (status, now(), nid))
        return self.get(nid, owner_id)

    # ------------------------------------------------------- model versions
    def add_base_version(self, nid, owner_id, *, model_id, speaker, description, f0_range=None, note=""):
        """Attach a base-model speaker (untrained) to a narrator. One identity, one narrator."""
        n = self.get(nid, owner_id)
        if n["status"] != "ACTIVE":
            raise VoiceLabError(409, "narrator is archived")
        m = self.ctx.model(model_id)
        if not m.get("runtime"):
            raise VoiceLabError(409, f"{model_id} has no integrated runtime ({m['commercial_status']})")
        if not (speaker or "").strip() or not (description or "").strip():
            raise VoiceLabError(422, "speaker and description are required")
        key = f"{model_id}:speaker:{speaker.strip().lower()}"
        cfg = {"speaker": speaker.strip(), "description": description.strip(), "seed_policy": "fixed_per_request"}
        if f0_range:
            cfg.update({k: f0_range[k] for k in ("f0_median_hz", "f0_min_hz", "f0_max_hz") if k in f0_range})
        return self._insert_version(nid, model_id, "BASE_SPEAKER", key, cfg, note=note)

    def add_trained_version(self, c, nid, *, model_id, checkpoint_rel_path, checkpoint_sha256, parent_version_id,
                            training_job_id, cfg, metrics):
        key = f"{model_id}:ckpt:{checkpoint_sha256}"
        return self._insert_version(nid, model_id, "FINE_TUNED", key, cfg, parent=parent_version_id,
                                    job=training_job_id, ckpt=(checkpoint_rel_path, checkpoint_sha256),
                                    metrics=metrics, conn=c)

    def _insert_version(self, nid, model_id, kind, key, cfg, *, parent=None, job=None, ckpt=(None, None),
                        metrics=None, note="", conn=None):
        mvid = new_id("mv")

        def ins(c):
            clash = c.execute("SELECT narrator_id FROM vl_model_versions WHERE identity_key=?", (key,)).fetchone()
            if clash:
                raise VoiceLabError(409, {"code": "IDENTITY_IN_USE",
                                          "detail": "this voice identity is already attached to another narrator"
                                          if clash[0] != nid else "this identity is already a version of this narrator",
                                          "narrator_id": clash[0] if clash[0] == nid else None})
            ver = (c.execute("SELECT COALESCE(MAX(version),0) FROM vl_model_versions WHERE narrator_id=?",
                             (nid,)).fetchone()[0]) + 1
            c.execute("INSERT INTO vl_model_versions VALUES(?,?,?,?,?,?,?,?,?,?,?,?,'CANDIDATE',?,?)",
                      (mvid, nid, model_id, ver, kind, key, parent, job, ckpt[0], ckpt[1], canon(cfg),
                       canon(metrics) if metrics else None, note, now()))
        if conn is not None:
            ins(conn)
        else:
            with self.ctx.tx() as c:
                ins(c)
        return mvid

    def version(self, mvid, owner_id=None):
        with closing(self.ctx.conn()) as c:
            r = c.execute("SELECT v.*, p.owner_id, p.active_model_version_id FROM vl_model_versions v "
                          "JOIN vl_voice_profiles p USING(narrator_id) WHERE v.model_version_id=?", (mvid,)).fetchone()
        if not r or (owner_id and r["owner_id"] != owner_id):
            raise VoiceLabError(404, "model version not found")
        d = dict(r)
        d["config"] = loads(d.pop("config_json"), {})
        d["metrics"] = loads(d.pop("metrics_json"), None)
        d["active"] = d.pop("active_model_version_id") == mvid
        model = self.ctx.catalogue().get(d["model_id"], {})
        d["commercial_status"] = model.get("commercial_status")
        d["production_allowed"] = bool(model.get("production_allowed"))
        d["identity_note"] = ("Base-model speaker; NOT trained on this narrator's recordings" if d["kind"] == "BASE_SPEAKER"
                              else "Fine-tuned on this narrator's approved dataset")
        return d

    def versions(self, nid, owner_id):
        self.get(nid, owner_id)
        with closing(self.ctx.conn()) as c:
            ids = [r[0] for r in c.execute("SELECT model_version_id FROM vl_model_versions WHERE narrator_id=? "
                                           "ORDER BY version", (nid,))]
        return [self.version(i, owner_id) for i in ids]

    def activate(self, nid, owner_id, mvid):
        """Activate a version; activating an older one is the rollback path. Nothing is deleted."""
        v = self.version(mvid, owner_id)
        if v["narrator_id"] != nid:
            raise VoiceLabError(409, {"code": "IDENTITY_MISMATCH", "detail": "version belongs to another narrator"})
        with self.ctx.tx() as c:
            c.execute("UPDATE vl_model_versions SET status='RETIRED' WHERE narrator_id=? AND status='ACTIVE'", (nid,))
            c.execute("UPDATE vl_model_versions SET status='ACTIVE' WHERE model_version_id=?", (mvid,))
            c.execute("UPDATE vl_voice_profiles SET active_model_version_id=?, updated_at=? WHERE narrator_id=?",
                      (mvid, now(), nid))
        return self.get(nid, owner_id)

    def resolve_version(self, nid, owner_id, mvid=None):
        n = self.get(nid, owner_id)
        if n["status"] != "ACTIVE":
            raise VoiceLabError(409, "narrator is archived")
        mvid = mvid or n["active_model_version_id"]
        if not mvid:
            raise VoiceLabError(409, {"code": "NO_MODEL_VERSION", "detail": "narrator has no active model version"})
        v = self.version(mvid, owner_id)
        if v["narrator_id"] != nid:
            raise VoiceLabError(409, {"code": "IDENTITY_MISMATCH",
                                      "detail": "model version belongs to a different narrator; identities are never mixed"})
        return n, v


def config_hash(cfg: dict) -> str:
    return hashlib.sha256(canon(cfg).encode()).hexdigest()
