// ESIC Compliance Audit UI. Plain JS, no build step. All data comes from the local token-protected API.
(() => {
  'use strict';
  const $ = (id) => document.getElementById(id);
  const LIVE = ['READY', 'WAITING_FOR_LOGIN', 'AUTHENTICATED', 'INSPECTING', 'EXTRACTING', 'RECONCILING', 'GENERATING_REPORTS'];
  let token = '';
  try { token = sessionStorage.getItem('esicAuditToken') || ''; } catch (e) { /* storage blocked */ }
  let liveUuid = null, pollTimer = null, currentDetail = null;

  const esc = (v) => String(v ?? '').replace(/[&<>"']/g, (c) => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
  const inr = (v) => {
    if (v === null || v === undefined || v === '') return 'NOT AVAILABLE';
    const n = String(v); const neg = n.startsWith('-'); const [w, f = '00'] = n.replace('-', '').split('.');
    const last3 = w.slice(-3), rest = w.slice(0, -3);
    const grouped = rest ? rest.replace(/\B(?=(\d{2})+(?!\d))/g, ',') + ',' + last3 : last3;
    return (neg ? '-' : '') + '₹' + grouped + '.' + (f + '00').slice(0, 2);
  };
  const tag = (t) => `<span class="tag ${esc(String(t).split(' ')[0].replace(/\s/g, '-'))}">${esc(t)}</span>`;
  const showError = (msg) => { const e = $('error'); e.textContent = msg; e.hidden = !msg; };

  async function api(path, opts = {}) {
    const res = await fetch(path, {...opts, headers: {...(opts.headers || {}), 'X-ESIC-Audit-Token': token,
      ...(opts.body ? {'Content-Type': 'application/json'} : {})}});
    if (res.status === 401) { askToken(); throw new Error('Access token required'); }
    if (!res.ok) { let d = ''; try { d = (await res.json()).detail; } catch (e) {} throw new Error(d || res.statusText); }
    return res;
  }
  const json = async (path, opts) => (await api(path, opts)).json();

  function askToken() {
    const d = $('token-dialog');
    if (!d.open) d.showModal();
  }
  $('token-form').addEventListener('submit', (ev) => {
    ev.preventDefault();
    token = $('token-input').value.trim();
    try { sessionStorage.setItem('esicAuditToken', token); } catch (e) {}
    $('token-dialog').close();
    route();
  });

  // ---------- navigation ----------
  function show(view) {
    document.querySelectorAll('.view').forEach((v) => { v.hidden = v.id !== 'view-' + view; });
    document.querySelectorAll('.sidebar nav a').forEach((a) => a.classList.toggle('active', a.dataset.view === view));
  }
  function route() {
    const [view, arg] = (location.hash.slice(1) || 'dashboard').split('/');
    showError('');
    if (view === 'details' && arg) { show('details'); loadDetails(arg); }
    else if (view === 'history') { show('history'); loadHistory(); }
    else if (view === 'compare') { show('compare'); loadCompare(); }
    else if (view === 'rules') { show('rules'); loadRules(); }
    else { show('dashboard'); loadDashboard(); }
  }
  window.addEventListener('hashchange', route);

  // ---------- dashboard ----------
  function cards(totals, el) {
    if (!totals) { el.innerHTML = '<p class="muted">No reconciled audit yet.</p>'; return; }
    const items = [
      ['Confirmed Damages', totals.confirmed_damages_outstanding, 'CONFIRMED BY ESIC', ''],
      ['Confirmed Interest', totals.confirmed_interest_outstanding, 'CONFIRMED BY ESIC', ''],
      ['Confirmed Recovery Demand', totals.confirmed_recovery_outstanding, 'CONFIRMED BY ESIC', ''],
      ['Estimated Potential Damages (max)', totals.estimated_potential_damages_max, 'ESTIMATED', 'est'],
      ['Total Confirmed Outstanding Liability', totals.total_confirmed_outstanding,
        totals.total_is_complete ? 'OUTSTANDING' : 'OUTSTANDING (INCOMPLETE)', 'total'],
    ];
    el.innerHTML = items.map(([label, v, status, cls]) => `<div class="card ${cls}"><div class="label">${esc(label)}</div>
      <div class="value">${esc(inr(v))}</div>${tag(v === null || v === undefined ? 'NOT AVAILABLE' : status)}</div>`).join('');
    if (totals.included_in_recovery_not_added_again && totals.included_in_recovery_not_added_again !== '0.00') {
      el.insertAdjacentHTML('beforeend', `<p class="muted wide">${esc(inr(totals.included_in_recovery_not_added_again))} of interest/damages is already inside recovery demands and is not added to the total again.</p>`);
    }
    if (totals.categories_not_available && totals.categories_not_available.length) {
      el.insertAdjacentHTML('beforeend', `<p class="muted wide">Not available in this audit: ${esc(totals.categories_not_available.join(', '))}. Missing data is never counted as zero.</p>`);
    }
  }

  const QUESTIONS = {
    outstanding_damages: 'Are there any outstanding ESIC damages?',
    esic_assessed_damages: 'Has ESIC officially assessed damages?',
    damages_demands_pending: 'Are any damages demands pending?',
    delayed_contributions: 'Are there delayed monthly contributions?',
    recovery_or_defaulter_records: 'Are there recovery or defaulter records?',
    possible_unassessed_damages: 'Are there possible unassessed damages requiring clarification?',
  };
  function answers(a) {
    if (!a) return '';
    return '<table><tr><th>Question</th><th>Answer</th></tr>' + Object.keys(QUESTIONS).filter((k) => k in a).map((k) => [k, a[k]]).map(([k, v]) =>
      `<tr><td>${esc(QUESTIONS[k] || k)}</td><td>${tag(v)}</td></tr>`).join('') + '</table>';
  }

  async function loadDashboard() {
    try {
      const health = await json('/api/health');
      $('synthetic-banner').hidden = !health.synthetic_portal;
      const audits = await json('/api/audits');
      const done = audits.filter((a) => a.totals);
      const last = audits[0], latest = done[0];
      const live = audits.find((a) => a.running);
      const ov = [
        ['Last audit date', last ? new Date(last.started_at).toLocaleString() : '—'],
        ['Latest audit status', last ? last.status : '—'],
        ['Contribution periods inspected', latest ? latest.counts.periods_inspected : '—'],
        ['Delayed payments identified', latest ? latest.counts.delayed_payments : '—'],
        ['Confirmed outstanding damages', latest ? inr(latest.totals.confirmed_damages_outstanding) : '—'],
        ['Confirmed outstanding interest', latest ? inr(latest.totals.confirmed_interest_outstanding) : '—'],
        ['Recovery demands', latest ? latest.counts.recovery_records : '—'],
        ['Unassessed potential liabilities', latest ? inr(latest.totals.estimated_potential_damages_max) : '—'],
        ['Data extraction warnings', latest ? latest.counts.discrepancies : '—'],
      ];
      $('overview').innerHTML = ov.map(([k, v]) => `<div><b>${esc(v)}</b><span>${esc(k)}</span></div>`).join('');
      cards(latest && latest.totals, $('latest-cards'));
      $('latest-answers').innerHTML = latest ? answers(latest.answers) + `<p><a href="#details/${esc(latest.audit_uuid)}">Open full audit details</a></p>` : '';
      $('btn-start').disabled = !!live;
      if (live) startPolling(live.audit_uuid); else $('live').hidden = true;
    } catch (e) { showError(e.message); }
  }

  $('btn-start').addEventListener('click', async () => {
    $('btn-start').disabled = true;
    try { const run = await json('/api/audits', {method: 'POST'}); startPolling(run.audit_uuid); }
    catch (e) { showError(e.message); $('btn-start').disabled = false; }
  });

  function startPolling(uuid) {
    liveUuid = uuid; $('live').hidden = false;
    clearInterval(pollTimer); pollTimer = setInterval(poll, 1500); poll();
  }

  async function poll() {
    if (!liveUuid) return;
    try {
      const d = await json('/api/audits/' + liveUuid);
      $('live-status').textContent = d.status; $('live-status').className = 'badge ' + d.status;
      $('live-activity').textContent = d.current_activity || '';
      $('live-stages').innerHTML = d.stages.filter((s) => s.outcome !== 'ENTERED' || LIVE.includes(s.stage)).slice(-25).map((s) =>
        `<li class="${/FAIL|ERROR|NOT_FOUND|EXPIRED|INTERRUPTED|REFUSED/.test(s.outcome) ? 'bad' : (/OK|CAPTURED|DONE|ACTIVE|CLOSED/.test(s.outcome) ? 'ok' : '')}">
          ${esc(s.stage)} — ${esc(s.outcome)}${s.detail ? ': ' + esc(s.detail) : ''}</li>`).join('');
      const live = LIVE.includes(d.status) && d.running;
      $('btn-login').hidden = d.status !== 'WAITING_FOR_LOGIN';
      $('capture-box').hidden = d.status !== 'INSPECTING';
      $('btn-cancel').hidden = !live;
      if (!live) {
        clearInterval(pollTimer); liveUuid = null; $('btn-start').disabled = false;
        loadDashboardSummaryOnly();
      }
    } catch (e) { showError(e.message); }
  }
  async function loadDashboardSummaryOnly() { try { await loadDashboard(); } catch (e) {} }

  const post = (suffix, body) => json(`/api/audits/${liveUuid}/${suffix}`, {method: 'POST', body: body ? JSON.stringify(body) : undefined});
  $('btn-login').addEventListener('click', () => post('confirm-login').catch((e) => showError(e.message)));
  $('btn-capture').addEventListener('click', () => post('capture', {category: $('capture-category').value,
    page_name: $('capture-name').value || null}).then(() => { $('capture-name').value = ''; }).catch((e) => showError(e.message)));
  $('btn-finish').addEventListener('click', () => post('finish').catch((e) => showError(e.message)));
  $('btn-cancel').addEventListener('click', () => {
    if (confirm('Cancel this audit? Captured pages are kept but no report is produced.')) post('cancel').catch((e) => showError(e.message));
  });
  $('btn-live-logs').addEventListener('click', () => liveUuid && showLogs(liveUuid));

  // ---------- history ----------
  async function loadHistory() {
    try {
      const audits = await json('/api/audits');
      $('history-table').innerHTML = '<tr><th>Audit ID</th><th>Date &amp; time</th><th>Employer</th><th>Status</th><th>Periods</th>' +
        '<th>Confirmed damages</th><th>Confirmed interest</th><th>Reports</th><th></th></tr>' +
        (audits.length ? audits.map((a) => `<tr><td><code>${esc(a.audit_uuid.slice(0, 8))}</code>${a.synthetic ? ' ' + tag('SYNTHETIC') : ''}</td>
          <td>${esc(new Date(a.started_at).toLocaleString())}</td><td>${esc(a.employer_identifier_masked || '—')}</td>
          <td><span class="badge ${esc(a.status)}">${esc(a.status)}</span></td><td class="num">${a.counts ? a.counts.periods_inspected : '—'}</td>
          <td class="num">${a.totals ? esc(inr(a.totals.confirmed_damages_outstanding)) : '—'}</td>
          <td class="num">${a.totals ? esc(inr(a.totals.confirmed_interest_outstanding)) : '—'}</td>
          <td>${esc(a.reports.join(', ') || '—')}</td><td><a href="#details/${esc(a.audit_uuid)}">View details</a></td></tr>`).join('')
          : '<tr><td colspan="9" class="muted">No audits yet.</td></tr>');
    } catch (e) { showError(e.message); }
  }

  // ---------- details ----------
  function table(headers, rows, numeric = []) {
    if (!rows.length) return '<p class="muted">No records.</p>';
    return '<div class="table-wrap"><table><tr>' + headers.map((h) => `<th>${esc(h)}</th>`).join('') + '</tr>' +
      rows.map((r) => '<tr>' + r.map((c, i) => `<td class="${numeric.includes(i) ? 'num' : ''}">${numeric.includes(i) ? esc(inr(c)) : esc(c ?? '—')}</td>`).join('') + '</tr>').join('') +
      '</table></div>';
  }
  const section = (title, html, open = false) => `<details ${open ? 'open' : ''}><summary>${esc(title)}</summary>${html}</details>`;

  async function loadDetails(uuid) {
    try {
      const d = await json('/api/audits/' + uuid);
      currentDetail = d;
      $('d-status').textContent = d.status; $('d-status').className = 'badge ' + d.status;
      $('d-head').innerHTML = `<div class="kv-grid">
        <div><b><code>${esc(d.audit_uuid)}</code></b><span>Audit ID</span></div>
        <div><b>${esc(new Date(d.started_at).toLocaleString())}</b><span>Started</span></div>
        <div><b>${esc(d.employer_identifier_masked || 'NOT AVAILABLE')}</b><span>Employer (masked)</span></div>
        <div><b>${esc(d.records_inspected)}</b><span>Records extracted</span></div>
        <div><b>${esc(d.rules_version)}</b><span>Rules version</span></div>
        <div><b>${esc(d.evidence_count)}</b><span>Evidence files</span></div></div>
        ${d.synthetic ? '<p class="banner warn">Synthetic fixture data — not real ESIC records.</p>' : ''}
        ${d.error_summary ? `<p class="muted">${esc(d.error_summary)}</p>` : ''}`;
      const types = d.reports.map((r) => r.report_type);
      $('d-pdf').disabled = !types.includes('pdf'); $('d-xlsx').disabled = !types.includes('xlsx');
      const r = d.result;
      cards(r && r.totals, $('d-cards'));
      if (!r) { $('d-body').innerHTML = '<p class="muted">This audit has no reconciliation (it was cancelled, failed, or is still running).</p>'; return; }
      const periods = r.periods.map((p) => [p.wage_month, p.due_date, p.status, p.total_amount, p.paid_amount, p.unpaid_amount, p.max_delay_days]);
      const delayed = [];
      r.periods.forEach((p) => p.payments.forEach((x) => { if (x.delay_days) delayed.push([p.wage_month, p.due_date, x.payment_date || 'not paid', x.delay_days,
        x.amount, x.interest_estimate && x.interest_estimate.amount, x.damages_estimate && x.damages_estimate.amount, x.interest_estimate ? x.interest_estimate.label : '—']); }));
      const dem = (items) => items.map((i) => [i.period, i.demand_reference, i.state, i.assessed, i.paid, i.outstanding, i.covered_by_rc || '', i.outstanding_basis]);
      $('d-body').innerHTML =
        section('Key questions', answers(r.answers), true) +
        section('Monthly contribution reconciliation', table(['Wage month', 'Due date', 'Status', 'Total', 'Paid', 'Not shown as paid', 'Max delay days'], periods, [3, 4, 5])) +
        section('Delayed payments (estimates)', table(['Wage month', 'Due date', 'Paid on', 'Delay days', 'Amount', 'Indicative interest', 'Max damages', 'Label'], delayed, [4, 5, 6])) +
        section('Interest demands', table(['Period', 'Reference', 'State', 'Assessed', 'Paid', 'Outstanding', 'Inside RC', 'Basis'], dem(r.interest), [3, 4, 5])) +
        section('Damages demands', table(['Period', 'Reference', 'State', 'Assessed', 'Paid', 'Outstanding', 'Inside RC', 'Basis'], dem(r.damages), [3, 4, 5])) +
        section('Recovery records', table(['RC', 'Demand date', 'From', 'To', 'Demand', 'Paid', 'Outstanding', 'Status'],
          r.recovery.map((x) => [x.recovery_reference, x.demand_date, x.period_from, x.period_to, x.demand, x.paid, x.outstanding, x.status]), [4, 5, 6])) +
        section('Potential (unassessed) damages — ESTIMATED', table(['Period', 'Category', 'Max damages', 'Basis', 'Explanation'],
          r.potential_damages.map((x) => [x.period, x.category, x.max_damages, x.basis, x.explanation]), [2])) +
        section(`Discrepancies (${r.discrepancies.length})`, table(['Severity', 'Type', 'Period', 'Message'],
          r.discrepancies.map((x) => [x.severity, x.type, x.period, x.message]))) +
        section('Inspection stages', table(['Stage', 'Outcome', 'Detail', 'Recorded'], d.stages.map((s) => [s.stage, s.outcome, s.detail, s.recorded_at])));
    } catch (e) { showError(e.message); }
  }

  async function download(type) {
    if (!currentDetail) return;
    try {
      const res = await api(`/api/audits/${currentDetail.audit_uuid}/reports/${type}`);
      const name = (res.headers.get('content-disposition') || '').match(/filename="?([^";]+)/);
      const url = URL.createObjectURL(await res.blob());
      const a = document.createElement('a'); a.href = url; a.download = name ? name[1] : `ESIC_Compliance_Audit.${type}`;
      document.body.appendChild(a); a.click(); a.remove(); setTimeout(() => URL.revokeObjectURL(url), 5000);
    } catch (e) { showError(e.message); }
  }
  $('d-pdf').addEventListener('click', () => download('pdf'));
  $('d-xlsx').addEventListener('click', () => download('xlsx'));
  $('d-logs').addEventListener('click', () => currentDetail && showLogs(currentDetail.audit_uuid));
  $('d-evidence').addEventListener('click', async () => {
    if (!currentDetail) return;
    try {
      const ev = await json(`/api/audits/${currentDetail.audit_uuid}/evidence`);
      modal('Evidence', table(['Type', 'Page', 'Captured (UTC)', 'SHA-256', 'Status', ''], ev.map((e) => [e.evidence_type, e.page_name, e.captured_at,
        e.content_hash.slice(0, 16) + '…', e.extraction_status, ''])) +
        '<p class="muted">Evidence files are stored locally under storage/evidence and are listed with SHA-256 hashes in the reports.</p>');
      document.querySelectorAll('#modal-body tr').forEach((tr, i) => {
        if (i === 0) return;
        const e = ev[i - 1]; const td = tr.lastElementChild; const b = document.createElement('button');
        b.textContent = 'Open'; b.onclick = async () => { const res = await api(`/api/audits/${currentDetail.audit_uuid}/evidence/${e.id}`);
          window.open(URL.createObjectURL(await res.blob()), '_blank', 'noopener'); };
        td.textContent = ''; td.appendChild(b);
      });
    } catch (e) { showError(e.message); }
  });

  async function showLogs(uuid) {
    try {
      const l = await json(`/api/audits/${uuid}/logs`);
      modal('Audit log', table(['Level', 'Message', 'Time (UTC)'], l.events.map((e) => [e.level, e.message, e.recorded_at])) +
        '<h3>Stages</h3>' + table(['Stage', 'Outcome', 'Detail', 'Time (UTC)'], l.stages.map((s) => [s.stage, s.outcome, s.detail, s.recorded_at])));
    } catch (e) { showError(e.message); }
  }
  function modal(title, html) { $('modal-body').innerHTML = `<h2>${esc(title)}</h2>` + html; $('modal').showModal(); }

  // ---------- compare ----------
  async function loadCompare() {
    try {
      const audits = (await json('/api/audits')).filter((a) => a.totals);
      const opts = audits.map((a) => `<option value="${esc(a.audit_uuid)}">${esc(new Date(a.started_at).toLocaleString())} — ${esc(a.status)} — ${esc(a.audit_uuid.slice(0, 8))}</option>`).join('');
      $('cmp-a').innerHTML = opts; $('cmp-b').innerHTML = opts;
      if (audits.length > 1) $('cmp-a').selectedIndex = 1;
      $('cmp-out').innerHTML = audits.length < 2 ? '<p class="muted">At least two reconciled audits are needed.</p>' : '';
    } catch (e) { showError(e.message); }
  }
  $('cmp-go').addEventListener('click', async () => {
    try {
      const c = await json(`/api/compare?older=${encodeURIComponent($('cmp-a').value)}&newer=${encodeURIComponent($('cmp-b').value)}`);
      const list = (title, rows) => section(`${title} (${rows.length})`, rows.length ? table(Object.keys(rows[0]), rows.map((r) => Object.values(r))) : '<p class="muted">None.</p>', rows.length > 0);
      $('cmp-out').innerHTML = section('Totals', table(['Item', 'Before', 'After'], Object.entries(c.totals).map(([k, v]) => [k.replace(/_/g, ' '), inr(v.before), inr(v.after)])), true) +
        list('New demands', c.new_demands) + list('Payments recorded', c.payments_recorded) + list('Changed outstanding amounts', c.changed_outstanding) +
        list('New recovery records', c.new_recovery_records) + list('Newly available contribution periods', c.new_contribution_periods) +
        list('Corrected or missing data', c.corrected_or_missing);
    } catch (e) { showError(e.message); }
  });

  // ---------- rules ----------
  async function loadRules() {
    try {
      const rules = await json('/api/rules');
      $('rules-table').innerHTML = '<tr><th>Category</th><th>Rule</th><th>Provision</th><th>Applies to</th><th>Method</th><th>Status</th><th>Source</th></tr>' +
        rules.map((r) => `<tr><td>${esc(r.category)}</td><td>${esc(r.rule_id)}<br><span class="muted">${esc(r.rule_name)}</span></td><td>${esc(r.provision)}</td>
          <td>${esc(r.applies)}</td><td>${esc(r.method)}</td><td>${tag(r.status)}<br><span class="muted">${esc(r.verification)}</span></td>
          <td><a href="${esc(r.source_url)}" target="_blank" rel="noopener noreferrer">${esc(r.source_title)}</a></td></tr>`).join('');
    } catch (e) { showError(e.message); }
  }

  if (!token) askToken(); else route();
})();
