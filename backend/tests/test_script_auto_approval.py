"""Automatic approval uses real persistence and validation; provider calls are fake."""
from contextlib import closing

import pytest

from defence_studio.database import connect
from defence_studio.services.script import ScriptService, ScriptStaleRevision
from test_script_phase3 import GOOD_CONFIG, FakeProvider, _default_body, approved, OWNER


def test_valid_draft_is_auto_approved_and_replay_survives_restart(approved):
    s = approved
    service = ScriptService(s['db'], s['root'], provider=s['provider'])
    revision = service.generate(s['job'], OWNER, GOOD_CONFIG, 'auto-1')
    assert revision['status'] == 'APPROVED'
    assert revision['approval']['approval_kind'] == 'AUTOMATED_VALIDATION'
    assert revision['approval']['reviewer_id'] == 'AUTOMATED_VALIDATION'
    assert revision['approval']['policy_version'] == revision['validation']['validator_version']
    history = service.revision_history(s['job'], OWNER, 'ta', 1)
    assert len(history) == 1 and history[0]['reviewer_id'] == 'AUTOMATED_VALIDATION'
    restarted = ScriptService(s['db'], s['root'], provider=s['provider'])
    assert restarted.generate(s['job'], OWNER, GOOD_CONFIG, 'auto-1') == revision
    assert restarted.export_approved(s['job'], OWNER, 'ta') == revision
    assert s['provider'].calls == 1
    assert restarted.revision_history(s['job'], OWNER, 'ta', 1) == history
    record = restarted.list_inspections(s['job'], OWNER, 'ta')['records'][0]
    assert record['outcome'] == 'APPROVED' and record['script_accepted']


def test_invalid_draft_has_no_automatic_approval(approved):
    s = approved
    service = ScriptService(s['db'], s['root'], provider=FakeProvider(
        body_factory=lambda _: _default_body('unknown-claim')))
    revision = service.generate(s['job'], OWNER, GOOD_CONFIG, 'auto-invalid')
    assert revision['status'] == 'VALIDATION_FAILED' and revision['approval'] is None
    assert service.revision_history(s['job'], OWNER, 'ta', 1) == []
    with pytest.raises(ScriptStaleRevision):
        service.export_approved(s['job'], OWNER, 'ta')


def test_auto_approval_is_language_scoped_and_supersedes_old_revision(approved):
    s = approved
    service = ScriptService(s['db'], s['root'], provider=s['provider'])
    ta = service.generate(s['job'], OWNER, GOOD_CONFIG, 'auto-ta-1')
    en = service.generate(s['job'], OWNER, {**GOOD_CONFIG, 'language': 'en'}, 'auto-en-1')
    ta2 = service.generate(s['job'], OWNER, GOOD_CONFIG, 'auto-ta-2')
    assert ta['approval']['version'] == en['approval']['version'] == 1
    assert ta2['approval']['version'] == 2
    assert service.get_revision(s['job'], OWNER, 'ta', 1)['status'] == 'SUPERSEDED'
    assert service.export_approved(s['job'], OWNER, 'en')['version'] == 1
    assert service.export_approved(s['job'], OWNER, 'ta')['version'] == 2


def test_evidence_change_during_generation_cannot_receive_auto_approval(approved):
    s = approved
    def change_evidence(_):
        claim = s['evidence'].draft(s['job'], OWNER)['package']['claims'][0]
        s['evidence'].decide_claim(s['job'], OWNER, claim['claim_id'], 'NEEDS_MORE_EVIDENCE',
            claim['review']['reviewed_artifact_sha256'], 1, 'Changed during generation', 'raced-review')
        return _default_body(s['claim_id'])
    service = ScriptService(s['db'], s['root'], provider=FakeProvider(body_factory=change_evidence))
    with pytest.raises(ScriptStaleRevision):
        service.generate(s['job'], OWNER, GOOD_CONFIG, 'auto-race')
    with closing(connect(s['db'])) as connection:
        assert connection.execute('SELECT COUNT(*) FROM script_revisions').fetchone()[0] == 0
        assert connection.execute('SELECT COUNT(*) FROM script_review_history').fetchone()[0] == 0


@pytest.mark.parametrize('during_generation', [True, False])
def test_new_factcheck_blocks_auto_approval_or_later_export(approved, during_generation):
    s = approved
    from defence_studio.services.factcheck import FactcheckService
    def new_factcheck(_):
        check = FactcheckService(s['db'], s['root'])
        check.enqueue(OWNER, 'fc-claims-race', s['job'], 'claims', {
            'claims': [{'claim_id': s['claim_id'], 'text': 'Officials reported a routine patrol.',
                        'source_id': 'origin', 'category': 'news', 'extraction_confidence': 0.9}],
            'sources': [{'source_id': 'origin', 'excerpt': 'Officials reported a routine patrol.',
                         'publisher': 'origin', 'category': 'news'}],
            'collection': {'attempted': True, 'errors': []}})
        check.run_pending()
        check.enqueue(OWNER, 'fc-race', s['job'], 'package', {'topic': 'New verification'})
        check.run_pending()
        assert check.latest_package(s['job'], OWNER)['package'] is not None
        return _default_body(s['claim_id'])
    if during_generation:
        service = ScriptService(s['db'], s['root'], provider=FakeProvider(body_factory=new_factcheck))
        with pytest.raises(ScriptStaleRevision):
            service.generate(s['job'], OWNER, GOOD_CONFIG, 'auto-fc-race')
        assert service.list_revisions(s['job'], OWNER)['revisions'] == []
    else:
        service = ScriptService(s['db'], s['root'], provider=s['provider'])
        assert service.generate(s['job'], OWNER, GOOD_CONFIG, 'auto-before-fc')['status'] == 'APPROVED'
        new_factcheck(None)
        with pytest.raises(ScriptStaleRevision):
            service.export_approved(s['job'], OWNER, 'ta')


def test_api_generates_and_exports_without_any_human_approval(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from defence_studio.api.app import create_app
    from conftest import SECRET, access_token
    from test_content_flow import build_content_fixture, MockContentProvider
    db, root, job, owner = build_content_fixture(tmp_path)
    provider = MockContentProvider()
    monkeypatch.setattr(ScriptService, '_resolve_provider', lambda self, owner_id=None: provider)
    headers = {'Authorization': 'Bearer ' + access_token(owner), 'Idempotency-Key': 'auto-api'}
    with TestClient(create_app(db, SECRET, root)) as client:
        base = f'/api/jobs/{job}/script'
        response = client.post(base + '/generate', headers=headers,
                               json={**GOOD_CONFIG, 'language': 'en', 'terminology': {}})
        assert response.status_code == 201, response.text
        body = response.json()
        assert body['status'] == 'APPROVED'
        assert body['approval']['approval_kind'] == 'AUTOMATED_VALIDATION'
        assert client.get(base + '/revisions', headers=headers).json()['approval_mode'] == 'AUTOMATIC'
        assert client.get(base + '/export/en', headers=headers).json()['status'] == 'APPROVED'
        assert client.post(base + '/generate', headers=headers,
            json={**GOOD_CONFIG, 'language': 'en', 'terminology': {}}).json() == body
    assert len(provider.prompts) == 1
    with closing(connect(db)) as connection:
        assert connection.execute('SELECT COUNT(*) FROM claim_reviews').fetchone()[0] == 0
        assert connection.execute('SELECT COUNT(*) FROM source_reviews').fetchone()[0] == 0
        actors = [row[0] for row in connection.execute('SELECT reviewer_id FROM script_review_history')]
        assert actors == ['AUTOMATED_VALIDATION']
