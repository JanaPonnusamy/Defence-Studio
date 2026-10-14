"""RM-7: QC-approved render -> reviewable publishing package.

The package is a versioned manifest (`defence-studio.publish-package.v1`)
binding: the exact approved render + QC report, a branded video (intro + main +
outro, optional ducked music), thumbnail, subtitle exports, a source register
built only from evidence the approved script actually cites, attribution
credits from the timeline, chapters from measured scene spans, and validated
metadata. Nothing is published here; approval of the package only makes it
*eligible* for the (separately approved) publishing stage.
"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import tempfile
from pathlib import Path

from defence_studio.integrations import branding, subtitles as subs
from defence_studio.services.integration import IntegrationStale, IntegrationValidationError
from defence_studio.services.qc import QcService
from defence_studio.services.render import RENDER_KIND
from defence_studio.services.script import _now
from defence_studio.services.script_integrity import overlap_ratio

PACKAGE_KIND = "publish_package"
PACKAGE_SCHEMA = "defence-studio.publish-package.v1"
YT_LANGUAGE = {"ta": "ta", "en": "en"}


def fmt_ts(seconds: float) -> str:
    s = int(seconds)
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    return f"{h}:{m:02d}:{sec:02d}" if h else f"{m}:{sec:02d}"


def build_chapters(spans: list[dict], offset: float, titles: dict[str, str]) -> tuple[list[dict], str]:
    """YouTube chapter rules: starts at 0:00, >=3 chapters, each >=10 s long."""
    chapters, last_start = [], None
    for sp in spans:
        start = offset + sp["start"]
        if last_start is None or start - last_start >= 10:
            chapters.append({"start": round(start if chapters else 0.0, 2),
                             "title": titles.get(sp["scene_id"], f"Part {len(chapters) + 1}")[:80]})
            last_start = start
    if len(chapters) < 3:
        return [], "Fewer than 3 chapters of at least 10 seconds; chapters omitted (YouTube requires 3)."
    return chapters, ""


def validate_metadata(meta: dict) -> list[str]:
    problems = []
    title = (meta.get("title") or "").strip()
    if not title or len(title) > 100:
        problems.append("title must be 1-100 characters")
    if "<" in title or ">" in title:
        problems.append("title must not contain < or >")
    if len(meta.get("description", "").encode("utf-8")) > 5000:
        problems.append("description exceeds 5000 bytes")
    if len("".join(meta.get("tags", []))) + 2 * len(meta.get("tags", [])) > 500:
        problems.append("tags exceed 500 characters in total")
    if len(meta.get("hashtags", [])) > 15:
        problems.append("more than 15 hashtags (YouTube ignores all of them)")
    if not str(meta.get("category_id", "")).isdigit():
        problems.append("category_id must be a numeric YouTube category id")
    return problems


class PackageService(QcService):
    # -- build ----------------------------------------------------------------

    def start_package(self, job_id: str, owner_id: str, language: str, idempotency_key: str, *,
                      overrides: dict | None = None, music_asset: dict | None = None,
                      background: bool = True) -> dict:
        self._check_ownership(job_id, owner_id)  # before any row is created (no cross-owner writes)
        gate = self.approved_render(job_id, owner_id, language)  # raises IntegrationStale without approval
        try:
            template = branding.merge_template((overrides or {}).get("branding"))
        except ValueError as exc:
            raise IntegrationValidationError(str(exc)) from None
        meta_over = (overrides or {}).get("metadata") or {}
        bad = set(meta_over) - {"title", "description_intro", "tags", "hashtags", "category_id"}
        if bad:
            raise IntegrationValidationError(f"Unknown metadata override(s): {sorted(bad)}")
        if music_asset:
            need = {"uri", "licence", "source", "attribution_required"}
            if need - set(music_asset):
                raise IntegrationValidationError(f"music_asset needs {sorted(need)} (licence provenance is required)")
            if str(music_asset.get("licence", "")).strip().upper() in ("", "UNKNOWN"):
                raise IntegrationValidationError("Music with unknown licence is refused")
        binding = {"render_version": gate["render_version"], "qc_version": gate["qc_version"],
                   "video_sha256": gate["video_sha256"], "template": template, "metadata_overrides": meta_over,
                   "music": music_asset, "approval_version": gate["approval"]["version"]}
        item, created = self._create_row(job_id, owner_id, language, PACKAGE_KIND, binding, idempotency_key)
        if created:
            self._launch(lambda: self._run_package(job_id, owner_id, language, item["version"], binding), background)
        return self.get_integration(job_id, owner_id, language, PACKAGE_KIND, item["version"])

    def _run_package(self, job_id, owner_id, language, version, binding) -> None:
        step = "prepare"
        try:
            self._transition(job_id, language, PACKAGE_KIND, version, "RUNNING", detail="Collecting inputs",
                             progress=0.05, allowed_from=("QUEUED",))
            render = self.read_integration_body(job_id, owner_id, language, RENDER_KIND, binding["render_version"])
            main_ref = render.get("video_subtitled") or render["video_plain"]
            main_path = self.store.job_dir(job_id) / main_ref["uri"].split(f"jobs/{job_id}/", 1)[1]
            if self.store.sha256_of(job_id, main_ref["uri"]) != binding["video_sha256"]:
                raise IntegrationStale("The approved video no longer matches its approved hash")
            template = binding["template"]
            profile = render["package"]["output_profile"]
            w, h, fps = profile["width"], profile["height"], profile["fps"]
            script = self.export_approved(job_id, owner_id, language)
            plan_row, plan = self._approved_plan(job_id, owner_id, language)
            evidence = self.export(job_id, owner_id)
            sources_by_id = {s["source_id"]: s for s in evidence["sources"]}
            claims_by_id = {c["claim_id"]: c for c in evidence["claims"]}

            # -- metadata -----------------------------------------------------
            ov = binding["metadata_overrides"]
            title = (ov.get("title") or script["title"]).strip()
            used_claims = sorted({c for s in plan["scenes"] for c in s.get("claim_ids", [])})
            register, seen = [], set()
            for cid in used_claims:
                claim = claims_by_id.get(cid)
                src = sources_by_id.get(claim["source_id"]) if claim else None
                if not src or src["source_id"] in seen:
                    continue
                seen.add(src["source_id"])
                dm = src.get("declared_metadata", {})
                register.append({"source_id": src["source_id"], "title": dm.get("title"),
                                 "publisher": dm.get("publisher"), "published_at": dm.get("published_at"),
                                 "url": src.get("requested_url"), "retrieved_at": src.get("acquisition_timestamp"),
                                 "claim_ids": [c for c in used_claims if claims_by_id.get(c, {}).get("source_id") == src["source_id"]]})
            timeline = self.read_integration_body(job_id, owner_id, language, "visual_timeline",
                                                  render["binding"]["visual_version"])["timeline"]
            credits = [{"asset_id": a["asset_id"], "title": a.get("title"), "creator": a.get("creator"),
                        "licence": a.get("licence"), "attribution": a.get("attribution"), "source_url": a.get("source_url")}
                       for a in timeline.get("attributions", []) if a.get("attribution_required")]
            intro_s, outro_s = float(template["intro_seconds"]), float(template["outro_seconds"])
            if h > w:  # short-form vertical: keep brand cards brief so the Short stays under 60 s
                intro_s, outro_s = min(intro_s, 1.0), min(outro_s, 2.0)
            scene_titles = {s["scene_id"]: (s.get("title") or s["narration"][:60]) for s in plan["scenes"]}
            chapters, chapter_note = build_chapters(render["scene_spans"], intro_s, scene_titles)
            hashtags = list(ov.get("hashtags", template["default_hashtags"]))
            vertical_short = h > w and render["audio_duration_seconds"] + intro_s + outro_s <= 60
            if vertical_short and "#Shorts" not in hashtags:
                hashtags.append("#Shorts")  # vertical and <= 60 s: eligible to surface as a Short
            tags = ov.get("tags", template["default_tags"])
            lines = [ov.get("description_intro") or script.get("opening_hook") or title, ""]
            if chapters:
                lines += [f"{fmt_ts(c['start'])} {c['title']}" for c in chapters] + [""]
            if register:
                lines.append("Sources:")
                lines += [f"- {r['publisher'] or 'Unknown publisher'}: {r['title'] or r['url']} ({r['url']})"
                          for r in register]
                lines.append("")
            if credits:
                lines.append("Credits:")
                lines += [f"- {c['title'] or c['asset_id']} — {c['attribution']} ({c['licence']})" for c in credits]
                lines.append("")
            lines.append("This video summarises the cited reporting; claims are attributed to their sources and "
                         "were reviewed before publication.")
            lines.append(" ".join(hashtags[:15]))
            metadata = {"title": title, "description": "\n".join(lines).strip(), "tags": tags, "hashtags": hashtags,
                        "category_id": ov.get("category_id", template["default_category_id"]),
                        "default_language": YT_LANGUAGE.get(language, language), "chapters": chapters,
                        "chapter_note": chapter_note, "visibility": "private"}
            problems = validate_metadata(metadata)
            if problems:
                raise IntegrationValidationError("Metadata invalid: " + "; ".join(problems))

            # -- media --------------------------------------------------------
            step = "branding"
            work = Path(tempfile.mkdtemp(prefix="ds-pkg-"))
            try:
                self._transition(job_id, language, PACKAGE_KIND, version, "RUNNING", detail="Building branded video",
                                 progress=0.2)
                intro, outro, mixed = work / "intro.mp4", work / "outro.mp4", work / "main_music.mp4"
                branding.make_card_video([template["channel_name"], title, template["tagline"]], intro_s, w, h, fps,
                                         language, template, intro)
                branding.make_card_video([template["channel_name"], *template["end_card_text"]], outro_s, w, h, fps,
                                         language, template, outro)
                main_for_concat = main_path
                music_note = None
                if binding["music"]:
                    music_path = self.store.job_dir(job_id) / binding["music"]["uri"].split(f"jobs/{job_id}/", 1)[1]
                    branding.mix_music(main_path, music_path, mixed, float(template["music"].get("duck_db", -18)))
                    main_for_concat, music_note = mixed, binding["music"]
                branded = work / "branded.mp4"
                branding.concat_videos([intro, main_for_concat, outro], branded, w, h, fps)
                probe = subs.probe(branded)
                errors = subs.full_decode_errors(branded)
                expected = intro_s + render["audio_duration_seconds"] + outro_s
                if errors:
                    raise IntegrationValidationError(f"Branded video has {errors} decode error line(s)")
                if abs(probe["duration"] - expected) > 0.5:
                    raise IntegrationValidationError(
                        f"Branded duration {probe['duration']:.2f}s differs from expected {expected:.2f}s")
                step = "thumbnail"
                self._transition(job_id, language, PACKAGE_KIND, version, "RUNNING", detail="Creating thumbnail",
                                 progress=0.7)
                thumb = work / "thumb.jpg"
                # Frame source is the video WITHOUT burned-in subtitles.
                plain_path = self.store.job_dir(job_id) / render["video_plain"]["uri"].split(f"jobs/{job_id}/", 1)[1]
                tinfo = branding.make_thumbnail(plain_path, title, language, template, thumb,
                                                at_seconds=max(1.0, render["audio_duration_seconds"] * 0.25))
                if not tinfo["within_youtube_limit"]:
                    raise IntegrationValidationError(f"Thumbnail invalid: {tinfo}")
                branded_bytes, thumb_bytes = branded.read_bytes(), thumb.read_bytes()
            finally:
                shutil.rmtree(work, ignore_errors=True)

            def put(name_ext: str, data: bytes, mime: str) -> dict:
                sha = hashlib.sha256(data).hexdigest()
                uri = f"jobs/{job_id}/package/{language}/{sha}.{name_ext}"
                self.store.write_bytes(job_id, uri, data, mime)
                return {"uri": uri, "sha256": sha, "size_bytes": len(data)}

            step = "manifest"
            files = {
                "video_branded": {**put("mp4", branded_bytes, "video/mp4"), "probe": probe, "decode_errors": 0},
                "thumbnail": {**put("jpg", thumb_bytes, "image/jpeg"), **tinfo},
                "description": put("txt", metadata["description"].encode("utf-8"), "text/plain"),
                "source_register": put("json", json.dumps(register, ensure_ascii=False, indent=1).encode("utf-8"),
                                       "application/json"),
            }
            if render.get("subtitles"):
                files["subtitles_srt"] = render["subtitles"]["files"]["srt"]
                files["subtitles_vtt"] = render["subtitles"]["files"]["vtt"]
            history = self._duplicate_check(job_id, owner_id, language, title, plan)
            body = {
                "schema_version": PACKAGE_SCHEMA, "job_id": job_id, "language": language, "version": version,
                "binding": {k: binding[k] for k in ("render_version", "qc_version", "video_sha256",
                                                     "approval_version")},
                "created_at": _now(), "metadata": metadata, "branding": {"template": template,
                                                                       "intro_seconds": intro_s, "outro_seconds": outro_s},
                "music": music_note, "files": files, "source_register_count": len(register), "credits": credits,
                "duplicate_check": history,
                "upstream": {"main_video": {"uri": main_ref["uri"], "sha256": main_ref["sha256"]},
                             "script_revision_sha256": script["approval"]["revision_sha256"],
                             "scene_plan_sha256": plan_row["plan_sha256"]},
                "publishing": {"default_visibility": "private", "published": False,
                               "note": "Building a package never uploads or publishes anything."},
            }
            uri, sha = self._store_json(job_id, f"packages/{language}", body)
            self._transition(job_id, language, PACKAGE_KIND, version, "AWAITING_REVIEW",
                             detail=f"{len(register)} source(s), {len(chapters)} chapter(s)", progress=1.0,
                             uri=uri, sha256=sha)
        except IntegrationStale as exc:
            self._transition(job_id, language, PACKAGE_KIND, version, "FAILED", detail=f"{step}: stale: {exc}")
        except Exception as exc:
            self._transition(job_id, language, PACKAGE_KIND, version, "FAILED",
                             detail=f"{step}: {type(exc).__name__}: {exc}")

    # -- duplicate / history ----------------------------------------------------

    def _duplicate_check(self, job_id, owner_id, language, title, plan) -> dict:
        def toks(t):
            return set(re.findall(r"\w+", t.lower()))
        mine = toks(title)
        hits = []
        for job in self.jobs_for_owner(owner_id):
            if job["job_id"] == job_id:
                continue
            for row in self._integration_rows(job["job_id"], language, PACKAGE_KIND):
                if not row.get("uri"):
                    continue
                try:
                    other = self._read_json_artifact(job["job_id"], row)
                except Exception:
                    continue
                t = toks(other["metadata"]["title"])
                jac = len(mine & t) / max(len(mine | t), 1)
                if jac >= 0.6:
                    hits.append({"job_id": job["job_id"], "package_version": row["version"],
                                 "title": other["metadata"]["title"], "title_similarity": round(jac, 3)})
        return {"checked": True, "similar_previous_titles": hits,
                "flag": "REVIEW_FOR_REPEATED_CONTENT" if hits else None}

    def jobs_for_owner(self, owner_id: str) -> list[dict]:
        from defence_studio.services.jobs import JobService
        return JobService(self.database_file).list_for_owner(owner_id)

    # -- review / reads ----------------------------------------------------------

    def package_status(self, job_id: str, owner_id: str, language: str) -> dict:
        self._check_ownership(job_id, owner_id)
        items = self._integration_rows(job_id, language, PACKAGE_KIND)
        try:
            gate = self.approved_render(job_id, owner_id, language)
            current_qc, current_render = gate["qc_version"], gate["render_version"]
        except IntegrationStale:
            current_qc = current_render = None
        for it in items:
            b = it["input_binding"]
            it["stale"] = current_qc is None or b["qc_version"] != current_qc or b["render_version"] != current_render
        return {"job_id": job_id, "language": language, "items": items}

    def review_package(self, job_id: str, owner_id: str, language: str, version: int, decision: str,
                       note: str) -> dict:
        note = (note or "").strip()
        if not note:
            raise IntegrationValidationError("A note is required")
        if decision not in ("APPROVED", "REJECTED"):
            raise IntegrationValidationError("decision must be APPROVED or REJECTED")
        row = self.get_integration(job_id, owner_id, language, PACKAGE_KIND, version)
        if row["status"] != "AWAITING_REVIEW":
            raise IntegrationStale(f"Package v{version} is {row['status']}, not AWAITING_REVIEW")
        if decision == "APPROVED":
            current = [i for i in self.package_status(job_id, owner_id, language)["items"] if i["version"] == version][0]
            if current["stale"]:
                raise IntegrationStale("The render/QC approval changed after this package was built; rebuild it")
        self._transition(job_id, language, PACKAGE_KIND, version, decision, detail=note[:500], actor=owner_id,
                         allowed_from=("AWAITING_REVIEW",))
        return self.get_integration(job_id, owner_id, language, PACKAGE_KIND, version)

    def package_file(self, job_id: str, owner_id: str, language: str, version: int, name: str) -> tuple[bytes, str]:
        body = self.read_integration_body(job_id, owner_id, language, PACKAGE_KIND, version)
        ref = body["files"].get(name)
        if not ref:
            raise IntegrationStale(f"No file {name!r} in this package")
        data = self.store.read_bytes(job_id, ref["uri"])
        if hashlib.sha256(data).hexdigest() != ref["sha256"]:
            raise IntegrationStale("Stored package file failed its integrity check")
        mime = {"video_branded": "video/mp4", "thumbnail": "image/jpeg", "description": "text/plain; charset=utf-8",
                "source_register": "application/json", "subtitles_srt": "text/plain; charset=utf-8",
                "subtitles_vtt": "text/vtt; charset=utf-8"}.get(name, "application/octet-stream")
        return data, mime
