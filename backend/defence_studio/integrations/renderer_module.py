"""HTTP adapter for the standalone Video Renderer (public API only).

Uses ``GET /health``, ``POST /renders``, ``GET /renders/{id}``,
``POST /renders/{id}/cancel``, ``GET /renders/{id}/output`` and
``GET /renders/{id}/qc``. The package is a folder the core builds on a path the
renderer can read (same machine); the renderer validates it independently.
"""
from __future__ import annotations

import os
import time

import httpx


class RendererError(Exception):
    def __init__(self, code: str, message: str, retryable: bool = False):
        super().__init__(message)
        self.code, self.message, self.retryable = code, message, retryable


def configured_base_url() -> str:
    return os.environ.get("DEFENCE_STUDIO_RENDERER_URL", "http://127.0.0.1:8780").rstrip("/")


class RendererClient:
    def __init__(self, base_url: str | None = None, *, client_factory=None, poll_interval: float = 1.0,
                 timeout_seconds: float = 3600.0, request_timeout: float = 60.0):
        self.base_url = (base_url or configured_base_url()).rstrip("/")
        self._factory = client_factory or (lambda **kw: httpx.Client(**kw))
        self.poll_interval, self.timeout_seconds, self.request_timeout = poll_interval, timeout_seconds, request_timeout

    def _call(self, client, method, path, *, raw=False, **kw):
        try:
            response = client.request(method, f"{self.base_url}{path}", **kw)
        except httpx.TimeoutException as exc:
            raise RendererError("timeout", f"Renderer timed out on {path}", retryable=True) from exc
        except httpx.HTTPError as exc:
            raise RendererError("unreachable", f"Renderer unreachable at {self.base_url}: {exc}", retryable=True) from exc
        if response.status_code >= 400:
            try:
                err = response.json().get("error") or {}
            except ValueError:
                err = {}
            code = err.get("code") or f"http_{response.status_code}"
            raise RendererError(code, f"{code}: {err.get('message') or response.text[:300]}",
                                retryable=response.status_code >= 500)
        return response.content if raw else response.json()

    def health(self) -> dict:
        with self._factory(timeout=self.request_timeout) as client:
            return self._call(client, "GET", "/health")

    def render(self, package_path: str, idempotency_key: str, options: dict | None = None,
               on_progress=None, should_cancel=None) -> dict:
        """Submit, poll to a terminal state, and return ``{job, qc, output_bytes}``
        only for SUCCEEDED. Any other terminal state raises with the renderer's code."""
        with self._factory(timeout=self.request_timeout) as client:
            health = self._call(client, "GET", "/health")
            if (health.get("ffmpeg") or {}).get("status") != "AVAILABLE":
                raise RendererError("FFMPEG_NOT_AVAILABLE", "Renderer reports ffmpeg is not available")
            job = self._call(client, "POST", "/renders", json={
                "package_path": package_path, "idempotency_key": idempotency_key, "options": options or {}})
            deadline = time.monotonic() + self.timeout_seconds
            while job.get("status") not in ("SUCCEEDED", "FAILED", "CANCELLED"):
                if should_cancel and should_cancel():
                    try:
                        self._call(client, "POST", f"/renders/{job['job_id']}/cancel")
                    except RendererError:
                        pass
                if time.monotonic() > deadline:
                    raise RendererError("poll_timeout", "Render did not finish in time", retryable=True)
                time.sleep(self.poll_interval)
                job = self._call(client, "GET", f"/renders/{job['job_id']}")
                if on_progress:
                    on_progress(float(job.get("progress") or 0.0), int(job.get("frames_done") or 0),
                                int(job.get("frames_total") or 0))
            if job["status"] != "SUCCEEDED":
                raise RendererError(job.get("error_code") or job["status"],
                                    job.get("error_message") or f"Render {job['status']}")
            qc = self._call(client, "GET", f"/renders/{job['job_id']}/qc")
            data = self._call(client, "GET", f"/renders/{job['job_id']}/output", raw=True)
        return {"job": job, "qc": qc, "output_bytes": data, "health": health}
