"""No-network source-policy diagnostic; DNS fixtures are explicitly simulated."""
import json
import sys
from pathlib import Path
repo=Path(__file__).resolve().parents[3]
sys.path.insert(0,str(repo/'backend'))
from defence_studio.acquisition.source_policy import SourcePolicy
from defence_studio.acquisition.url_guard import UrlGuard
policy=SourcePolicy.load(repo/'backend/config/source_policy.json')
feeds=json.loads((repo/'backend/config/discovery_feeds.json').read_text())['feeds']
results=[]
for feed in feeds:
    try:
        # Guard applies policy BEFORE DNS; fail if a blocked feed tries resolution.
        def no_dns(*args): raise AssertionError('unexpected DNS call for blocked feed')
        UrlGuard(policy=policy,resolver=no_dns).validate(feed['url'])
        allowed=True; reason='allowed'
    except ValueError as exc:
        allowed=False; reason=str(exc)
    results.append({'url':feed['url'],'allowed':allowed,'reason':reason})
checks=[]
cases=[
 ('https://missilethreat.csis.org/defsys/s-400/',True),
 ('HTTPS://MISSILETHREAT.CSIS.ORG:443/defsys/s-400/#fragment',True),
 ('https://missilethreat.csis.org.evil.example/defsys/s-400/',False),
 ('https://missilethreat.csis.org/search',False),
 ('https://user:pass@missilethreat.csis.org/defsys/s-400/',False),
 ('file:///etc/passwd',False),('ftp://missilethreat.csis.org/defsys/s-400/',False),
 ('https://[invalid',False),
]
for url,expected in cases:
    try: UrlGuard(policy=policy,resolver=lambda *_:['93.184.216.34']).validate(url); allowed=True
    except ValueError: allowed=False
    checks.append({'url':url,'expected_allowed':expected,'actual_allowed':allowed})
for addresses in [['127.0.0.1'],['169.254.169.254'],['93.184.216.34','10.0.0.1']]:
    try: UrlGuard(policy=policy,resolver=lambda *_,a=addresses:a).validate(cases[0][0]); blocked=False
    except ValueError: blocked=True
    checks.append({'simulated_dns':addresses,'blocked':blocked})
result={'scope':'no-network production-policy boundary check; no live feed/candidate claim',
        'feeds':results,'guard_checks':checks,'http_requests':0}
Path(__file__).with_name('policy-diagnostic.json').write_text(json.dumps(result,indent=2))
assert not any(r['allowed'] for r in results)
assert all(c.get('blocked',c.get('expected_allowed')==c.get('actual_allowed')) for c in checks)
print('4 configured feeds blocked before DNS; 11 guard checks passed; zero HTTP requests')
