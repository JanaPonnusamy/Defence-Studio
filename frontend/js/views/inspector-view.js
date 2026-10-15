// Inspector: (1) research pipeline trace -- raw -> clean -> structured -> claims
// -> passages with defect-origin hints; (2) script request/response inspector
// (instructions vs source material vs evidence references; raw / parsed /
// clean / validation views). Read-only; all data is already sanitised by the
// server (no credentials, no local paths).
import { escapeHtml as esc } from "../components/status-badge.js";

const pre = (v) => `<pre class="inspect-pre">${esc(typeof v === "string" ? v : JSON.stringify(v, null, 2))}</pre>`;
const dl = (rows) => `<dl class="inspect-dl">${rows.filter(([, v]) => v !== undefined && v !== null && v !== "")
  .map(([k, v]) => `<dt>${esc(k)}</dt><dd>${esc(v)}</dd>`).join("")}</dl>`;
const list = (items) => `<ul>${items.map((x) => `<li>${esc(x)}</li>`).join("")}</ul>`;

export function renderTraceHtml(trace) {
  if (!trace || !trace.available) {
    return `<p class="inline-blocked" role="status">${esc((trace && trace.reason) || "No pipeline trace available.")}</p>`;
  }
  const sources = (trace.sources || []).map((s) => {
    const findings = (s.findings || []).length ? list(s.findings) : "";
    const origin = s.defect_origin
      ? `<p class="inline-blocked" role="alert"><strong>Defect origin: ${esc(s.defect_origin)}</strong></p>${findings}`
      : `<p class="inline-ok" role="status">No defect detected.</p>${findings}`;
    const claims = (s.claims || []).map((c, i) => {
      const chk = (s.passage_checks || [])[i] || {};
      return `<li><strong>${esc(c.claim_id)}</strong>: ${esc(c.claim_text)}
        <br><small>passage [${esc(chk.start)}-${esc(chk.end)}] ${esc(chk.kind)}; matches normalized text: ${esc(chk.matches_normalized_text)}</small>
        <blockquote>${esc(c.passage && c.passage.text)}</blockquote></li>`;
    }).join("") || "<li>No claims extracted.</li>";
    const acq = s.acquisition || {};
    const raw = s.raw || {};
    const clean = s.clean || {};
    const dropped = clean.numeric_tokens_dropped_vs_raw_visible_text || [];
    return `<details class="inspect-source" open><summary>Source ${esc(s.source_id)} - ${esc(s.status)}</summary>
      ${origin}
      <h4>1. Acquisition</h4>${dl([["status", acq.status], ["method", acq.method], ["requested", acq.requested_url],
        ["final", acq.final_url], ["acquired", acq.acquired_at], ["raw sha256", acq.artifact && acq.artifact.sha256],
        ["raw hash verified", raw.sha256_verified], ["error", acq.error]])}
      <h4>2. Raw extracted content</h4>${raw.note ? `<p>${esc(raw.note)}</p>` : ""}${pre(raw.text || "(unavailable)")}
      <h4>3. Clean normalized content</h4>${dl([["version", clean.normalization_version], ["language", clean.language],
        ["chars", clean.chars], ["sha256", clean.sha256], ["numeric tokens in raw but not in clean", dropped.join(", ")]])}${pre(clean.text || "(empty)")}
      <h4>4. Structured representation</h4>${pre(s.structured || {})}
      ${(s.duplicate_groups || []).length ? `<h4>Duplicate groups</h4>${pre(s.duplicate_groups)}` : ""}
      <h4>5. Claims and supporting passages</h4><ul>${claims}</ul></details>`;
  }).join("");
  const h = trace.hashes || {};
  return `<h2>Research pipeline trace</h2>${dl([["claims total", trace.claims_total],
    ["analysis sha256", h.analysis_result_sha256], ["acquisition sha256", h.acquisition_manifest_sha256],
    ["analysis input acquisition sha256", h.analysis_input_acquisition_sha256]])}${sources}`;
}

function group(title, items) {
  if (!items || !items.length) return "";
  return `<h5>${esc(title)}</h5><ul>${items.map((x) => `<li>${esc(x.section_id)} <em>${esc(x.statement_kind)}</em>: ${esc(x.narration)}
    <br><small>${(x.evidence || []).map((e) => `${esc(e.claim_id)} ${e.resolved ? "resolved" : "UNRESOLVED"}${e.source_id ? " (source " + esc(e.source_id) + ")" : ""}`).join("; ")}</small>
    ${x.qualification ? `<br><small>${esc(x.qualification)}</small>` : ""}</li>`).join("")}</ul>`;
}

export function renderInspectionHtml(rec) {
  if (!rec) return "<p>Select a generation attempt.</p>";
  const rq = rec.request || {};
  const st = rq.structure || {};
  const raw = rec.raw_response || {};
  const parsed = rec.parsed_response || {};
  const clean = rec.clean_output;
  const val = rec.validation_report;
  const usage = raw.usage;
  const http = raw.http;
  const verdict = rec.script_accepted
    ? '<p class="inline-ok" role="status">Accepted for human review (not approved).</p>'
    : `<p class="inline-blocked" role="alert"><strong>Not an accepted script:</strong> ${esc(rec.outcome)}${rec.outcome_detail ? " - " + esc(rec.outcome_detail) : ""}</p>`;
  const rawBlock = raw.available
    ? dl([["http status", http && http.http_status], ["provider request id", http && http.provider_request_id],
        ["duration ms", http && http.duration_ms], ["attempts", (raw.attempts || []).length],
        ["input tokens", usage && usage.input_tokens], ["output tokens", usage && usage.output_tokens],
        ["measured cost", usage ? (usage.cost_is_measured ? `${usage.measured_text_cost_inr} ${usage.currency}` : "unavailable") : null],
        ["pricing source", usage && usage.pricing_source]]) + pre(http ? http.body : (raw.returned_text || ""))
    : "<p>No response: the provider was not called.</p>";
  const refChecks = (parsed.evidence_reference_checks || []).map((r) =>
    `${r.section_id} -> ${r.claim_id}: ${r.in_eligible_set ? "in eligible set" : "NOT in eligible set"}`);
  const issues = ((val && val.issues) || []).map((i) => `${i.severity} ${i.code} ${i.section_id || ""}: ${i.detail}`);
  return `<h2>Generation attempt ${esc(rec.record_id)}</h2>${verdict}
    ${dl([["provider", rq.provider], ["model", rq.model], ["endpoint", rq.endpoint], ["provider calls", rec.provider_calls],
      ["max output tokens", rq.max_output_tokens], ["request bytes", rq.wire_body_bytes],
      ["request sha256", rec.request_sha256], ["package sha256", rec.package_sha256],
      ["budget (INR)", rq.budget_policy && rq.budget_policy.max_text_cost_inr],
      ["timeout s / retries", rq.timeout_s != null ? `${rq.timeout_s} / ${rq.max_retries}` : null]])}
    <h3>Request</h3>
    <h4>Instructions - system</h4>${pre((st.instructions || {}).system || "")}
    <h4>Instructions - task</h4>${pre((st.instructions || {}).task || "")}
    <h4>Normalized source material (${(st.normalized_source_material || []).length} claims)</h4>${pre(st.normalized_source_material || [])}
    <h4>Evidence references</h4>${pre(st.evidence_references || {})}
    <h4>Exact serialized request body</h4>${rq.wire_body
      ? `<p>Wire messages match client input: ${esc(st.wire_messages_match_client_input)}</p>${pre(rq.wire_body)}`
      : "<p>No provider call was made, or this provider does not expose its wire body.</p>"}
    <button type="button" class="btn" data-copy="request">Copy sanitized request JSON</button>
    <h3>A. Raw response</h3>${rawBlock}
    <h3>B. Parsed response</h3>
    ${dl([["parsed", parsed.parsed], ["error", parsed.error]])}
    ${(parsed.field_errors || []).length ? "<h5>Field errors</h5>" + list(parsed.field_errors) : ""}
    ${(parsed.schema_errors || []).length ? "<h5>Schema errors</h5>" + list(parsed.schema_errors) : ""}
    ${refChecks.length ? "<h5>Evidence reference checks</h5>" + list(refChecks) : ""}
    ${parsed.object ? pre(parsed.object) : ""}
    <h3>C. Clean output${clean ? " - " + esc(clean.status_label) : ""}</h3>
    ${clean ? `<h4>${esc(clean.title)}</h4><p>${esc(clean.opening_hook)}</p>
      ${group("Verified facts", clean.verified_facts)}${group("Attributed claims", clean.attributed_claims)}
      ${group("Uncertain / analysis", clean.uncertain_or_analysis)}${group("Other", clean.other)}<p>${esc(clean.closing)}</p>`
      : "<p>No parseable output.</p>"}
    <h3>D. Validation report</h3>
    ${val ? dl([["passed", val.passed], ["validator", val.validator_version], ["checked", val.checked_at]]) +
        (issues.length ? list(issues) : "<p>No issues.</p>")
      : "<p>Validation did not run for this outcome.</p>"}`;
}

export async function mountInspector({ api, jobId, list: listEl, detail, filters, isCurrent }) {
  listEl.innerHTML = '<p class="loading-state">Loading inspector...</p>';
  detail.innerHTML = "";
  detail.classList.remove("detail-overlay");
  filters.innerHTML = "<p>Read-only diagnostics. Credentials and local paths are redacted server-side.</p>";
  const language = "ta";
  try {
    const [trace, attempts] = await Promise.all([
      api.getPipelineTrace(jobId).catch((e) => ({ available: false, reason: e.message })),
      api.listScriptInspections(jobId, language).catch(() => ({ records: [] })),
    ]);
    if (!isCurrent()) return;
    const rows = (attempts.records || []).map((r) => `<li><button type="button" class="link-btn" data-record="${esc(r.record_id)}">
      ${esc(r.recorded_at)} - ${esc(r.outcome)} (calls: ${esc(r.provider_calls)})</button></li>`).join("")
      || "<li>No generation attempts recorded.</li>";
    listEl.innerHTML = `${renderTraceHtml(trace)}<h2>Script generation attempts (${esc(language)})</h2><ul>${rows}</ul>`;
    listEl.querySelectorAll("[data-record]").forEach((btn) => btn.addEventListener("click", async () => {
      detail.classList.add("detail-overlay");
      detail.innerHTML = '<p class="loading-state">Loading...</p>';
      try {
        const rec = await api.getScriptInspection(jobId, language, btn.dataset.record);
        if (!isCurrent()) return;
        detail.innerHTML = renderInspectionHtml(rec);
        const copy = detail.querySelector('[data-copy="request"]');
        if (copy) {
          copy.addEventListener("click", () => {
            const body = rec.request && (rec.request.wire_body || rec.request.client_input);
            if (navigator.clipboard) navigator.clipboard.writeText(JSON.stringify(body, null, 2));
          });
        }
      } catch (error) {
        detail.innerHTML = `<p role="alert" class="error-state">${esc(error.message)}</p>`;
      }
    }));
  } catch (error) {
    if (isCurrent()) listEl.innerHTML = `<p role="alert" class="error-state">${esc(error.message)}</p>`;
  }
}
