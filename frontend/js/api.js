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
  let body;
  try { body = text ? JSON.parse(text) : null; }
  catch { throw new ApiError(response.status, "The server returned an unreadable response. Reload or try again."); }
  if (!response.ok) {
    const detail = body?.detail;
    const message = Array.isArray(detail)
      ? detail.map(item => `${(item.loc || []).join(".")}: ${item.msg || "Invalid value"}`).join("; ")
      : typeof detail === "string" ? detail : response.statusText;
    throw new ApiError(response.status, message);
  }
  return body;
}

export function createApiClient(getToken) {
  const base = "/api";
  return {
    // Canonical Phase 2 surface is /package/* + /timeline (core-design route
    // names). Method names kept stable for the evidence view that calls them.
    async evidencePackage(jobId) {
      return handle(await fetch(`${base}/jobs/${encodeURIComponent(jobId)}/package`, { headers: authHeaders(getToken()) }));
    },
    async evidenceTimeline(jobId, filters = {}) {
      const params = new URLSearchParams(Object.entries(filters).filter(([, value]) => value));
      return handle(await fetch(`${base}/jobs/${encodeURIComponent(jobId)}/timeline?${params}`, { headers: authHeaders(getToken()) }));
    },
    async approveEvidence(jobId, revision, note, key) {
      return handle(await fetch(`${base}/jobs/${encodeURIComponent(jobId)}/package/approve`, {
        method: "POST", headers: authHeaders(getToken(), { "Idempotency-Key": key }), body: JSON.stringify({ revision, note }),
      }));
    },
    async exportEvidence(jobId, draft = false) {
      return handle(await fetch(`${base}/jobs/${encodeURIComponent(jobId)}/package/export?draft=${draft}`, { headers: authHeaders(getToken()) }));
    },
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
    async queueJob(jobId) {
      return handle(await fetch(`${base}/jobs/${encodeURIComponent(jobId)}/queue`, {
        method: "POST", headers: authHeaders(getToken()),
      }));
    },
    async getDiscoveryRequest(jobId) {
      return handle(await fetch(`${base}/jobs/${encodeURIComponent(jobId)}/discovery/requests`, {
        headers: authHeaders(getToken()),
      }));
    },
    async createDiscoveryRequest(jobId, body) {
      return handle(await fetch(`${base}/jobs/${encodeURIComponent(jobId)}/discovery/requests`, {
        method: "POST", headers: authHeaders(getToken()), body: JSON.stringify(body),
      }));
    },
    async listDiscoveryCandidates(jobId, { language, sourceType, provider, minScore, query } = {}) {
      const params = new URLSearchParams();
      if (language) params.set("language", language);
      if (sourceType) params.set("source_type", sourceType);
      if (provider) params.set("provider", provider);
      if (minScore) params.set("min_score", minScore);
      if (query) params.set("query", query);
      const qs = params.toString() ? `?${params.toString()}` : "";
      return handle(await fetch(`${base}/jobs/${encodeURIComponent(jobId)}/discovery/candidates${qs}`, {
        headers: authHeaders(getToken()),
      }));
    },
    async selectDiscoveryCandidates(jobId, candidateIds) {
      return handle(await fetch(`${base}/jobs/${encodeURIComponent(jobId)}/discovery/selection`, {
        method: "POST", headers: authHeaders(getToken()), body: JSON.stringify({ candidate_ids: candidateIds }),
      }));
    },
    async getDiscoverySelection(jobId) {
      return handle(await fetch(`${base}/jobs/${encodeURIComponent(jobId)}/discovery/selection`, {
        headers: authHeaders(getToken()),
      }));
    },
    // -- Phase 3: source-grounded script generation, review, approval --------
    async listScriptRevisions(jobId, language) {
      const qs = language ? `?language=${encodeURIComponent(language)}` : "";
      return handle(await fetch(`${base}/jobs/${encodeURIComponent(jobId)}/script/revisions${qs}`, {
        headers: authHeaders(getToken()),
      }));
    },
    async getScriptRevision(jobId, language, version) {
      return handle(await fetch(
        `${base}/jobs/${encodeURIComponent(jobId)}/script/revisions/${encodeURIComponent(language)}/${version}`,
        { headers: authHeaders(getToken()) },
      ));
    },
    async getScriptRevisionHistory(jobId, language, version) {
      return handle(await fetch(
        `${base}/jobs/${encodeURIComponent(jobId)}/script/revisions/${encodeURIComponent(language)}/${version}/history`,
        { headers: authHeaders(getToken()) },
      ));
    },
    async generateScript(jobId, config, idempotencyKey) {
      return handle(await fetch(`${base}/jobs/${encodeURIComponent(jobId)}/script/generate`, {
        method: "POST", headers: authHeaders(getToken(), { "Idempotency-Key": idempotencyKey }), body: JSON.stringify(config),
      }));
    },
    async requestScriptChanges(jobId, language, version, note, idempotencyKey) {
      return handle(await fetch(
        `${base}/jobs/${encodeURIComponent(jobId)}/script/revisions/${encodeURIComponent(language)}/${version}/request-changes`,
        { method: "POST", headers: authHeaders(getToken(), { "Idempotency-Key": idempotencyKey }), body: JSON.stringify({ note }) },
      ));
    },
    async approveScriptRevision(jobId, language, version, revisionSha256, note, idempotencyKey) {
      return handle(await fetch(
        `${base}/jobs/${encodeURIComponent(jobId)}/script/revisions/${encodeURIComponent(language)}/${version}/approve`,
        {
          method: "POST", headers: authHeaders(getToken(), { "Idempotency-Key": idempotencyKey }),
          body: JSON.stringify({ revision_sha256: revisionSha256, note }),
        },
      ));
    },
    async exportApprovedScript(jobId, language) {
      return handle(await fetch(`${base}/jobs/${encodeURIComponent(jobId)}/script/export/${encodeURIComponent(language)}`, {
        headers: authHeaders(getToken()),
      }));
    },
  };
}

export function newIdempotencyKey() {
  if (window.crypto && window.crypto.randomUUID) return window.crypto.randomUUID();
  return `idem-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}
