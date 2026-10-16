"""Summarize retained fresh execution evidence without substituting historic counts."""
import json,re
from pathlib import Path
out=Path(__file__).resolve().parent
names=['backend-fresh','backend-clean-final','affected-clean-final','export-final-tests','publishing-final-tests','publisher-final-lifecycle','publisher-cancel-final','audio-fresh','voice-fresh','visual-fresh','renderer-fresh','frontend-fresh']
results=[]
for name in names:
    log=out/(name+'.log'); marker=out/(name+'.exit')
    if not marker.exists():
        results.append({'suite':name,'status':'RUNNING_OR_NOT_RUN'}); continue
    text=log.read_text(encoding='utf-8',errors='replace')
    summaries=[line for line in text.splitlines() if re.search(r'\d+ (passed|failed|skipped).* in \d',line)]
    result={'suite':name,'exit':int(marker.read_text(encoding='utf-8-sig').strip()),'summary':summaries[-1:]}
    if name=='frontend-fresh':
        result['summary']=[line for line in text.splitlines() if re.match(r'[#ℹ] (tests|pass|fail|skipped|duration_ms) ',line)]
    result['skip_reasons']=[line for line in text.splitlines() if line.startswith('SKIPPED ')]
    results.append(result)
(out/'verification-summary.json').write_text(json.dumps(results,indent=2))
print(json.dumps(results,indent=2))
