"""HTTP adapter for the standalone Video Visual Module (public API only).

Uses only the documented endpoints in
``standalone/video-visual-module/docs/API_CONTRACTS.md`` (projects, scenes,
visual-plans, jobs, timeline). It never imports the module's code or opens its
database. Failures are structured (`VisualModuleError`) and never become an
empty success.
"""
from __future__ import annotations

import os
import time

import httpx

TIMELINE_SCHEMA = "video-visual-timeline"
SUPPORTED_TIMELINE_VERSIONS = ("1.1",)
SCENE_ID_MAX = 64


class VisualModuleError(Exception):
    def __init__(self, code: str, message: str, retryable: bool = False):
        super().__init__(message)
        self.code, self.message, self.retryable = code, message, retryable


def configured_base_url() -> str:
    return os.environ.get("DEFENCE_STUDIO_VISUAL_MODULE_URL", "http://127.0.0.1:8765").rstrip("/")


class VisualModuleClient:
    def __init__(self, base_url: str | None = None, api_key: str | None = None, *, client_factory=None,
                 poll_interval: float = 0.5, timeout_seconds: float = 180.0, request_timeout: float = 30.0):
        self.base_url = (base_url or configured_base_url()).rstrip("/")
        self.api_key = api_key if api_key is not None else os.environ.get("DEFENCE_STUDIO_VISUAL_MODULE_API_KEY", "")
        self._factory = client_factory or (lambda **kw: httpx.Client(**kw))
        self.poll_interval, self.timeout_seconds, self.request_timeout = poll_interval, timeout_seconds, request_timeout

    # -- transport -----------------------------------------------------------

    def _request(self, client, method: str, path: str, **kw):
        headers = {"X-API-Key": self.api_key} if self.api_key else {}
        headers.update(kw.pop("headers", {}))
        try:
            response = client.request(method, f"{self.base_url}{path}", headers=headers, **kw)
        except httpx.TimeoutException as exc:
            raise VisualModuleError("timeout", f"Visual module timed out on {path}", retryable=True) from exc
        except httpx.HTTPError as exc:
            raise VisualModuleError("unreachable", f"Visual module is unreachable at {self.base_url}: {exc}",
                                    retryable=True) from exc
        if response.status_code >= 400:
            try:
                body = response.json()
            except ValueError:
                body = {}
            code = body.get("error_code") or f"http_{response.status_code}"
            raise VisualModuleError(code, f"{code}: {body.get('error_message') or response.text[:300]}",
                                    retryable=response.status_code >= 500)
        try:
            return response.json()
        except ValueError as exc:
            raise VisualModuleError("malformed_response", f"Non-JSON response from {path}") from exc

    # -- operations -----------------------------------------------------------

    def health(self) -> dict:
        with self._factory(timeout=self.request_timeout) as client:
            return self._request(client, "GET", "/health")

    def build_timeline(self, project_name: str, scenes: list[dict], options: dict | None = None,
                       idempotency_key: str | None = None, on_progress=None,
                       assets: list[dict] | None = None) -> dict:
        """``scenes``: ``[{scene_id, text, start, end}]`` in order. Returns
        ``{project_id, visual_plan_id, job_id, module_version, scenes, timeline}``."""
        if not scenes:
            raise VisualModuleError("no_scenes", "At least one scene is required")
        for s in scenes:
            if len(s["scene_id"]) > SCENE_ID_MAX:
                raise VisualModuleError("scene_id_too_long", f"scene_id {s['scene_id']!r} exceeds {SCENE_ID_MAX} chars")
        with self._factory(timeout=self.request_timeout) as client:
            health = self._request(client, "GET", "/health")
            project = self._request(client, "POST", "/projects", json={"name": project_name[:200]})
            pid = project["project_id"]
            imported: dict[str, str] = {}  # module asset_id -> core asset_id
            for a in assets or []:
                resp = self._import_asset(client, pid, a)
                imported[resp["asset_id"]] = a["core_asset_id"]
            segments = [{"text": s["text"], "start": round(s["start"], 3), "end": round(s["end"], 3),
                         "segment_id": s["scene_id"], "scene": s["scene_id"]} for s in scenes]
            scene_resp = self._request(client, "POST", "/scenes", json={"project_id": pid, "segments": segments})
            headers = {"Idempotency-Key": idempotency_key} if idempotency_key else {}
            job = self._request(client, "POST", "/visual-plans/build", headers=headers,
                                json={"project_id": pid, "options": options or {}})
            deadline = time.monotonic() + self.timeout_seconds
            while job.get("status") not in ("SUCCEEDED", "FAILED", "CANCELLED"):
                if time.monotonic() > deadline:
                    raise VisualModuleError("poll_timeout", "Visual plan build did not finish in time", retryable=True)
                time.sleep(self.poll_interval)
                job = self._request(client, "GET", f"/jobs/{job['job_id']}")
                if on_progress:
                    on_progress(float(job.get("progress") or 0.0), str(job.get("stage") or ""))
            if job["status"] != "SUCCEEDED":
                raise VisualModuleError(job.get("error_code") or "build_failed",
                                        job.get("error_message") or f"Visual plan job {job['status']}")
            plan_id = job["result_id"]
            timeline = self._request(client, "GET", f"/visual-plans/{plan_id}/timeline")
        if timeline.get("schema") != TIMELINE_SCHEMA or str(timeline.get("version")) not in SUPPORTED_TIMELINE_VERSIONS:
            raise VisualModuleError("unsupported_timeline",
                                    f"Unsupported timeline {timeline.get('schema')!r} v{timeline.get('version')!r}")
        return {"project_id": pid, "visual_plan_id": plan_id, "job_id": job["job_id"],
                "module_version": health.get("version"), "scenes": scene_resp.get("scenes", []),
                "timeline": timeline, "imported_assets": imported}

    def _import_asset(self, client, project_id: str, a: dict) -> dict:
        """Multipart ``POST /assets/import`` with the rights fields the module requires."""
        import json as _json
        headers = {"X-API-Key": self.api_key} if self.api_key else {}
        fields = {"project_id": project_id, "asset_type": "SOURCE_PHOTO",
                  "source_type": "EXTERNAL_SOURCE" if a.get("source_url") else "USER_PROVIDED",
                  "rights_status": a["rights_status"], "title": a["title"][:200],
                  "description": (a.get("description") or "")[:1000], "creator": a.get("creator") or "",
                  "licence": a.get("licence") or "", "attribution": a.get("attribution") or "",
                  "source_url": a.get("source_url") or "",
                  "attribution_required": "true" if a.get("attribution_required") else "false",
                  "editorial_category": a.get("editorial_category", "ILLUSTRATIVE_VISUAL"),
                  "extra_metadata": _json.dumps({"keywords": a.get("keywords", []),
                                                 "core_asset_id": a["core_asset_id"]})}
        fields = {k: v for k, v in fields.items() if v != ""}
        try:
            response = client.post(f"{self.base_url}/assets/import", data=fields, headers=headers,
                                   files={"file": (a["filename"], a["data"], a["mime_type"])})
        except httpx.HTTPError as exc:
            raise VisualModuleError("unreachable", f"Asset import failed: {exc}", retryable=True) from exc
        if response.status_code not in (200, 201):
            try:
                body = response.json()
            except ValueError:
                body = {}
            raise VisualModuleError(body.get("error_code") or f"http_{response.status_code}",
                                    f"Asset import rejected: {body.get('error_message') or response.text[:200]}")
        return response.json()

    def fetch_asset(self, asset_id: str) -> bytes:
        """Stored bytes of one module asset (public ``GET /assets/{id}/file``)."""
        with self._factory(timeout=self.request_timeout) as client:
            headers = {"X-API-Key": self.api_key} if self.api_key else {}
            try:
                response = client.get(f"{self.base_url}/assets/{asset_id}/file", headers=headers)
            except httpx.HTTPError as exc:
                raise VisualModuleError("unreachable", f"Cannot fetch asset {asset_id}: {exc}", retryable=True) from exc
            if response.status_code != 200:
                raise VisualModuleError(f"http_{response.status_code}", f"Asset {asset_id} unavailable")
            return response.content
