"""Read-only live status; never print or persist credentials or session tokens."""
import hashlib
import json
from pathlib import Path
import wave

import httpx

ROOT = Path(__file__).resolve().parents[3]
OUT = Path(__file__).parent
JOB = 'ff16816c-7af1-477a-b5f7-888ebd42f21a'
result = {}
with httpx.Client(base_url='http://127.0.0.1:8011/api', timeout=45) as client:
    session = client.get('/dev/session')
    session.raise_for_status()
    client.headers['Authorization'] = 'Bearer ' + session.json()['token']
    for name, path in {
        'voices': '/voice/providers',
        'voice_jobs': '/voice/jobs',
        'voice_profiles': '/voice/profiles',
        'credentials': '/settings/credentials',
        'scripts': f'/jobs/{JOB}/script/revisions',
        'openai_inspections_en': f'/jobs/{JOB}/script/inspections/en',
        'openai_inspections_ta': f'/jobs/{JOB}/script/inspections/ta',
    }.items():
        response = client.get(path)
        response.raise_for_status()
        value = response.json()
        if name == 'credentials':
            def clean(obj):
                if isinstance(obj, dict):
                    return {k: clean(v) for k, v in obj.items() if k not in {'last4', 'value', 'encrypted_value'}}
                return [clean(x) for x in obj] if isinstance(obj, list) else obj
            value = clean(value)
        result[name] = value

result['existing_tamil_samples'] = []
for path in sorted((ROOT / 'AGENT_WORK/indic-parler-trial/output').glob('*.wav')):
    with wave.open(str(path), 'rb') as wav:
        result['existing_tamil_samples'].append({
            'path': str(path), 'bytes': path.stat().st_size,
            'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
            'duration_seconds': round(wav.getnframes() / wav.getframerate(), 3),
            'sample_rate': wav.getframerate(), 'channels': wav.getnchannels(),
        })
(OUT / 'status.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
print(json.dumps(result, ensure_ascii=True, indent=2))
