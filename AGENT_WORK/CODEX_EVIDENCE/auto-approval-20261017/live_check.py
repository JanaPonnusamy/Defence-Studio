"""Snapshot production rows, then verify the deployed automatic mode without mutation."""
import hashlib
import json
from pathlib import Path
import sqlite3
import sys

import httpx

ROOT = Path(__file__).resolve().parents[3]
OUT = Path(__file__).parent
JOB = 'ff16816c-7af1-477a-b5f7-888ebd42f21a'


def fingerprint():
    with sqlite3.connect((ROOT / 'data/defence_studio.sqlite3').as_uri() + '?mode=ro', uri=True) as db:
        tables = [r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
        result = {}
        for table in tables:
            rows = sorted(repr(row) for row in db.execute('SELECT * FROM "' + table.replace('"', '""') + '"'))
            result[table] = {'count': len(rows), 'sha256': hashlib.sha256('\n'.join(rows).encode()).hexdigest()}
        active = db.execute("SELECT COUNT(*) FROM worker_claims WHERE status='ACTIVE'").fetchone()[0]
        statuses = dict(db.execute('SELECT status, COUNT(*) FROM script_revisions GROUP BY status'))
    return result, active, statuses


rows, active, statuses = fingerprint()
if '--snapshot' in sys.argv:
    (OUT / 'database-before.json').write_text(json.dumps(rows, indent=2), encoding='utf-8')
    print(json.dumps({'active_claims': active, 'existing_script_statuses': statuses}))
    raise SystemExit(0 if active == 0 else 1)

checks = {}
with httpx.Client(base_url='http://127.0.0.1:8011', timeout=30) as client:
    session = client.get('/api/dev/session')
    session.raise_for_status()
    client.headers['Authorization'] = 'Bearer ' + session.json()['token']
    result = client.get(f'/api/jobs/{JOB}/script/revisions')
    result.raise_for_status()
    checks['automatic_mode_live'] = result.json().get('approval_mode') == 'AUTOMATIC'
    checks['no_unrequested_script_generation'] = not result.json()['revisions']
    policy = client.get(f'/api/jobs/{JOB}/evidence/policy')
    policy.raise_for_status()
    checks['evidence_readiness_still_enforced'] = not policy.json()['ready_for_generation']
    script_js = client.get('/js/views/script-view.js')
    evidence_js = client.get('/js/views/evidence-view.js')
    inspector_js = client.get('/js/views/inspector-view.js')
    for response in (script_js, evidence_js, inspector_js):
        response.raise_for_status()
    checks['script_has_no_manual_approval_button'] = 'approve-revision-btn' not in script_js.text
    checks['evidence_has_no_manual_approval_button'] = 'approve-package' not in evidence_js.text
    checks['ui_describes_automatic_approval'] = 'automatically approved' in script_js.text
    checks['inspector_reports_auto_approval'] = 'Automatically approved after validation' in inspector_js.text
    checks['no_human_approval_instruction'] = 'must be human-approved' not in script_js.text
before = json.loads((OUT / 'database-before.json').read_text(encoding='utf-8'))
checks['all_production_database_rows_preserved'] = before == rows
result = {'checks': checks, 'passed': sum(checks.values()), 'total': len(checks),
          'script_statuses': statuses, 'current_job': JOB,
          'remaining_blockers': policy.json().get('policy_criteria_failed', [])}
(OUT / 'live-final.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
print(json.dumps(result, indent=2))
raise SystemExit(0 if all(checks.values()) else 1)
