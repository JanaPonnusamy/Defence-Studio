"""One permitted live PIB article, real guarded HTTP; no DB or AI provider."""
import dataclasses
import json
import sys
import threading
from pathlib import Path
repo=Path(__file__).resolve().parents[3]
sys.path.insert(0,str(repo/'backend'))
from defence_studio.acquisition.source_policy import SourcePolicy
from defence_studio.acquisition.url_guard import UrlGuard
from defence_studio.contracts.discovery import DiscoveryFilters, DiscoveryLimits
from defence_studio.discovery.providers import DiscoveryContext, ManualUrlProvider
url='https://www.pib.gov.in/PressReleaseIframePage.aspx?PRID=2323814&lang=2&reg=48'
context=DiscoveryContext(job_id='isolated-live-content-probe',topic='GSAT-7C and Varunastra',
 search_terms=(),language='en',filters=DiscoveryFilters(),limits=DiscoveryLimits(timeout_seconds=15),
 guard=UrlGuard(policy=SourcePolicy.load()),cancel=threading.Event(),seed_urls=(url,))
outcome,candidates=ManualUrlProvider().discover(context)
result={'scope':'real guarded HTTP discovery only; no DB, review, AI call or draft',
        'outcome':dataclasses.asdict(outcome),'candidates':[dataclasses.asdict(c) for c in candidates]}
Path(__file__).with_name('live-source.json').write_text(json.dumps(result,indent=2,default=str),encoding='utf-8')
print(json.dumps({'status':outcome.status.value,'candidates':len(candidates),'message':outcome.message}))
raise SystemExit(0 if candidates else 1)
