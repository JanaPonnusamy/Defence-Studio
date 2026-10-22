import test from 'node:test';
import assert from 'node:assert/strict';
import { renderAssessmentHtml } from '../js/views/assessment-panel.js';
import { renderClaimDetail, renderClaimsList } from '../js/views/claims-view.js';
import { renderSourceDetail } from '../js/views/sources-view.js';

test('automated assessment reasons and references are visible and escaped', () => {
  const value = {outcome:'NEEDS_MORE_SOURCES', reasons:['<script>bad</script>'], assessor_version:'v2', analysis_sha256:'a'.repeat(64), created_at:'2026-10-16'};
  const html = renderAssessmentHtml(value);
  assert(html.includes('NEEDS_MORE_SOURCES') && html.includes('a'.repeat(64)));
  assert(html.includes('&lt;script&gt;') && !html.includes('<script>'));
  const claim = {claim_id:'c', claim_text:'A claim',source_id:'s',assessment:value,review_status:'PENDING',passage:{}};
  assert(renderClaimsList([claim]).includes('NEEDS_MORE_SOURCES'));
  assert(renderClaimDetail(claim,[]).includes('Human exception review'));
  assert(renderSourceDetail({source_id:'s',assessment:value}).includes('Human exception review'));
});

test('assessment outcomes never describe extraction as factual truth', () => {
  for (const outcome of ['SUPPORTED','CONTRADICTED','INSUFFICIENT_EVIDENCE','NEEDS_MORE_SOURCES','ASSESSMENT_FAILED']) {
    const html = renderAssessmentHtml({outcome,reasons:['Policy basis']});
    assert(html.includes(outcome));
    assert(html.includes('not certification of factual truth'));
  }
  assert(renderAssessmentHtml(null).includes('Automated assessment pending'));
});
