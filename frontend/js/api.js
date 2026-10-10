// Thin fetch wrapper for the Defence Studio F5 review API. No framework, no
// build step: this is a plain ES module loaded directly by the browser.

export class ApiError extends Error {
  constructor(status, detail) {
    super(detail || `Request failed (${status})`);
    this.status = status;
    this.detail = detail;
  }
}

function authHeaders(token, extra) {
  const headers = { "Content-Type": "application/json", ...extra };
  if (token) headers.Authorization = `Bearer ${token}`;
  return headers;
}

async function handle(response) {
  const text = await response.text();
  const body = text ? JSON.parse(text) : null;
  if (!response.ok) {
    throw new ApiError(response.status, body && body.detail ? body.detail : response.statusText);
  }
  return body;
}

export function createApiClient(getToken) {
  const base = "/api";
  return {
    async getJob(jobId) {
      return handle(await fetch(`${base}/jobs/${encodeURIComponent(jobId)}`, {
        headers: authHeaders(getToken()),
      }));
    },
    async listSources(jobId, status) {
      const qs = status ? `?status=${encodeURIComponent(status)}` : "";
      return handle(await fetch(`${base}/jobs/${encodeURIComponent(jobId)}/review/sources${qs}`, {
        headers: authHeaders(getToken()),
      }));
    },
    async getSource(jobId, sourceId) {
      return handle(await fetch(
        `${base}/jobs/${encodeURIComponent(jobId)}/review/sources/${encodeURIComponent(sourceId)}`,
        { headers: authHeaders(getToken()) },
      ));
    },
    async sourceHistory(jobId, sourceId) {
      return handle(await fetch(
        `${base}/jobs/${encodeURIComponent(jobId)}/review/sources/${encodeURIComponent(sourceId)}/history`,
        { headers: authHeaders(getToken()) },
      ));
    },
    async decideSource(jobId, sourceId, body, idempotencyKey) {
      return handle(await fetch(
        `${base}/jobs/${encodeURIComponent(jobId)}/review/sources/${encodeURIComponent(sourceId)}/decisions`,
        {
          method: "POST",
          headers: authHeaders(getToken(), { "Idempotency-Key": idempotencyKey }),
          body: JSON.stringify(body),
        },
      ));
    },
    async listClaims(jobId, { status, conflict, sourceId } = {}) {
      const params = new URLSearchParams();
      if (status) params.set("status", status);
      if (conflict) params.set("conflict", "true");
      if (sourceId) params.set("source_id", sourceId);
      const qs = params.toString() ? `?${params.toString()}` : "";
      return handle(await fetch(`${base}/jobs/${encodeURIComponent(jobId)}/review/claims${qs}`, {
        headers: authHeaders(getToken()),
      }));
    },
    async getClaim(jobId, claimId) {
      return handle(await fetch(
        `${base}/jobs/${encodeURIComponent(jobId)}/review/claims/${encodeURIComponent(claimId)}`,
        { headers: authHeaders(getToken()) },
      ));
    },
    async claimHistory(jobId, claimId) {
      return handle(await fetch(
        `${base}/jobs/${encodeURIComponent(jobId)}/review/claims/${encodeURIComponent(claimId)}/history`,
        { headers: authHeaders(getToken()) },
      ));
    },
    async decideClaim(jobId, claimId, body, idempotencyKey) {
      return handle(await fetch(
        `${base}/jobs/${encodeURIComponent(jobId)}/review/claims/${encodeURIComponent(claimId)}/decisions`,
        {
          method: "POST",
          headers: authHeaders(getToken(), { "Idempotency-Key": idempotencyKey }),
          body: JSON.stringify(body),
        },
      ));
    },
  };
}

export function newIdempotencyKey() {
  if (window.crypto && window.crypto.randomUUID) return window.crypto.randomUUID();
  return `idem-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}
