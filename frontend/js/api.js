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
    authHeader() { return authHeaders(getToken()); },
    // Generic authenticated call for stage routes (scene/visual/audio/render/...).
    // `path` is relative to /api. Throws ApiError with the server's message.
    async request(method, path, { body, idempotencyKey } = {}) {
      const extra = idempotencyKey ? { "Idempotency-Key": idempotencyKey } : undefined;
      return handle(await fetch(`${base}${path}`, {
        method, headers: authHeaders(getToken(), extra),
        body: body === undefined ? undefined : JSON.stringify(body),
      }));
    },
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
    // Owner-scoped job list for the dashboard (real persisted jobs, no mocks).
    async listJobs() {
      return handle(await fetch(`${base}/jobs`, { headers: authHeaders(getToken()) }));
    },
    // Development entry: ask the server to mint a short-lived token. Only
    // succeeds under the safe loopback dev-bypass config; 404 otherwise. The
    // token is never fabricated client-side.
    async devSession() {
      return handle(await fetch(`${base}/dev/session`, { headers: { "Content-Type": "application/json" } }));
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
    async createJob(topic, language, idempotencyKey) {
      return handle(await fetch(`${base}/jobs`, {
        method: "POST",
        headers: authHeaders(getToken(), { "Idempotency-Key": idempotencyKey }),
        body: JSON.stringify({ topic, language }),
      }));
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
    async listDiscoveryCandidates(jobId, opts = {}) {
      const map = {
        language: "language", sourceType: "source_type", provider: "provider", minScore: "min_score",
        query: "query", category: "category", region: "region", sourceClass: "source_class",
        dateFrom: "date_from", dateTo: "date_to", publisher: "publisher", cursor: "cursor", pageSize: "page_size",
      };
      const params = new URLSearchParams();
      for (const [key, name] of Object.entries(map)) {
        if (opts[key]) params.set(name, opts[key]);
      }
      const qs = params.toString() ? `?${params.toString()}` : "";
      return handle(await fetch(`${base}/jobs/${encodeURIComponent(jobId)}/discovery/candidates${qs}`, {
        headers: authHeaders(getToken()),
      }));
    },
    async getDiscoveryProviders() {
      return handle(await fetch(`${base}/discovery/providers`, { headers: authHeaders(getToken()) }));
    },
    async getDiscoveryTaxonomy() {
      return handle(await fetch(`${base}/discovery/taxonomy`, { headers: authHeaders(getToken()) }));
    },
    async listDiscoverySearches() {
      return handle(await fetch(`${base}/discovery/searches`, { headers: authHeaders(getToken()) }));
    },
    async rerunDiscovery(jobId) {
      return handle(await fetch(`${base}/jobs/${encodeURIComponent(jobId)}/discovery/rerun`, {
        method: "POST", headers: authHeaders(getToken()),
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
    async getPipelineTrace(jobId) {
      return handle(await fetch(`${base}/jobs/${encodeURIComponent(jobId)}/research/pipeline-trace`, {
        headers: authHeaders(getToken()),
      }));
    },
    async listScriptInspections(jobId, language) {
      return handle(await fetch(
        `${base}/jobs/${encodeURIComponent(jobId)}/script/inspections/${encodeURIComponent(language)}`,
        { headers: authHeaders(getToken()) }));
    },
    async getScriptInspection(jobId, language, recordId) {
      return handle(await fetch(
        `${base}/jobs/${encodeURIComponent(jobId)}/script/inspections/${encodeURIComponent(language)}/${encodeURIComponent(recordId)}`,
        { headers: authHeaders(getToken()) }));
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
    async getScriptStaleness(jobId, language) {
      const qs = language ? `?language=${encodeURIComponent(language)}` : "";
      return handle(await fetch(`${base}/jobs/${encodeURIComponent(jobId)}/script/staleness${qs}`, {
        headers: authHeaders(getToken()),
      }));
    },
    async compareScriptRevisions(jobId, language, oldVersion, newVersion) {
      return handle(await fetch(
        `${base}/jobs/${encodeURIComponent(jobId)}/script/compare/${encodeURIComponent(language)}?old=${oldVersion}&new=${newVersion}`,
        { headers: authHeaders(getToken()) }));
    },
    async getScriptRepetition(jobId, language, version) {
      return handle(await fetch(
        `${base}/jobs/${encodeURIComponent(jobId)}/script/revisions/${encodeURIComponent(language)}/${version}/repetition`,
        { headers: authHeaders(getToken()) }));
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
    // -- Phase 4 M1: scene planning, persistence, editing, approval ----------
    async listScenePlans(jobId, language) {
      const qs = language ? `?language=${encodeURIComponent(language)}` : "";
      return handle(await fetch(`${base}/jobs/${encodeURIComponent(jobId)}/scene/plans${qs}`, {
        headers: authHeaders(getToken()),
      }));
    },
    async getScenePlan(jobId, language, version) {
      return handle(await fetch(
        `${base}/jobs/${encodeURIComponent(jobId)}/scene/plans/${encodeURIComponent(language)}/${version}`,
        { headers: authHeaders(getToken()) },
      ));
    },
    async getScenePlanHistory(jobId, language, version) {
      return handle(await fetch(
        `${base}/jobs/${encodeURIComponent(jobId)}/scene/plans/${encodeURIComponent(language)}/${version}/history`,
        { headers: authHeaders(getToken()) },
      ));
    },
    async generateScenePlan(jobId, config, idempotencyKey) {
      return handle(await fetch(`${base}/jobs/${encodeURIComponent(jobId)}/scene/generate`, {
        method: "POST", headers: authHeaders(getToken(), { "Idempotency-Key": idempotencyKey }), body: JSON.stringify(config),
      }));
    },
    async reviseScenePlan(jobId, language, baseVersion, edits, idempotencyKey) {
      return handle(await fetch(`${base}/jobs/${encodeURIComponent(jobId)}/scene/plans/${encodeURIComponent(language)}/revise`, {
        method: "POST", headers: authHeaders(getToken(), { "Idempotency-Key": idempotencyKey }),
        body: JSON.stringify({ base_version: baseVersion, edits }),
      }));
    },
    async requestScenePlanChanges(jobId, language, version, note, idempotencyKey) {
      return handle(await fetch(
        `${base}/jobs/${encodeURIComponent(jobId)}/scene/plans/${encodeURIComponent(language)}/${version}/request-changes`,
        { method: "POST", headers: authHeaders(getToken(), { "Idempotency-Key": idempotencyKey }), body: JSON.stringify({ note }) },
      ));
    },
    async approveScenePlan(jobId, language, version, planSha256, note, idempotencyKey) {
      return handle(await fetch(
        `${base}/jobs/${encodeURIComponent(jobId)}/scene/plans/${encodeURIComponent(language)}/${version}/approve`,
        {
          method: "POST", headers: authHeaders(getToken(), { "Idempotency-Key": idempotencyKey }),
          body: JSON.stringify({ plan_sha256: planSha256, note }),
        },
      ));
    },
    async factcheckProviders() {
      return handle(await fetch(`${base}/factcheck/providers`, { headers: authHeaders(getToken()) }));
    },
    async factcheckPackage(jobId) {
      return handle(await fetch(`${base}/jobs/${encodeURIComponent(jobId)}/factcheck/package`, { headers: authHeaders(getToken()) }));
    },
    async factcheckClaims(jobId, body, idempotencyKey) {
      return handle(await fetch(`${base}/jobs/${encodeURIComponent(jobId)}/factcheck/claims`, {
        method: "POST", headers: authHeaders(getToken(), { "Idempotency-Key": idempotencyKey }), body: JSON.stringify(body),
      }));
    },
    async factcheckBuildPackage(jobId, topic, idempotencyKey) {
      return handle(await fetch(`${base}/jobs/${encodeURIComponent(jobId)}/factcheck/package`, {
        method: "POST", headers: authHeaders(getToken(), { "Idempotency-Key": idempotencyKey }), body: JSON.stringify({ topic }),
      }));
    },
    async factcheckMedia(jobId, form, idempotencyKey) {
      const headers = {};
      const token = getToken();
      if (token) headers.Authorization = `Bearer ${token}`;
      headers["Idempotency-Key"] = idempotencyKey;
      return handle(await fetch(`${base}/jobs/${encodeURIComponent(jobId)}/factcheck/media`, { method: "POST", headers, body: form }));
    },
    async factcheckApprove(jobId, packageId, note) {
      return handle(await fetch(`${base}/jobs/${encodeURIComponent(jobId)}/factcheck/approve`, {
        method: "POST", headers: authHeaders(getToken()), body: JSON.stringify({ package_id: packageId, note }),
      }));
    },
    async factcheckOverride(jobId, body) {
      return handle(await fetch(`${base}/jobs/${encodeURIComponent(jobId)}/factcheck/override`, {
        method: "POST", headers: authHeaders(getToken()), body: JSON.stringify(body),
      }));
    },
    async factcheckHistory(jobId) {
      return handle(await fetch(`${base}/jobs/${encodeURIComponent(jobId)}/factcheck/history`, { headers: authHeaders(getToken()) }));
    },
  };
}

export function newIdempotencyKey() {
  if (window.crypto && window.crypto.randomUUID) return window.crypto.randomUUID();
  return `idem-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}
