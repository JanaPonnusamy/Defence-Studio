"""One explicitly requested Tamil speech sample. No retries or job-state changes."""
import array
import hashlib
import io
import json
import math
import os
from pathlib import Path
import sys
import time
import wave

import httpx

ROOT = Path(__file__).resolve().parents[3]
OUT = Path(__file__).parent
WAV = OUT / 'tamil-openai-marin-sample.wav'
RESULT = OUT / 'tamil-speech-result.json'
if WAV.exists() or RESULT.exists():
    raise SystemExit('A result already exists; refusing an accidental duplicate paid call.')
config = {}
for line in (ROOT / 'backend/.env.local').read_text(encoding='utf-8-sig').splitlines():
    if line.strip() and not line.lstrip().startswith('#') and '=' in line:
        k, v = line.split('=', 1)
        config[k.strip()] = v.strip().strip('\"\'')
key = config.get('DEFENCE_STUDIO_OPENAI_API_KEY') or os.getenv('DEFENCE_STUDIO_OPENAI_API_KEY')
if not key:
    raise SystemExit('No configured OpenAI key; no request sent.')
request = json.loads((OUT / 'tamil-speech-request.json').read_text(encoding='utf-8'))
assert request['model'] == 'gpt-4o-mini-tts' and request['voice'] == 'marin'
assert 0 < len(request['input']) < 1000
endpoint = 'https://api.openai.com/v1/audio/speech'
result = {'endpoint': endpoint, 'model': request['model'], 'voice': request['voice'],
          'input_chars': len(request['input']), 'provider_calls': 1,
          'synthetic_voice': True, 'custom_voice_created': False,
          'human_listening_review': 'PENDING', 'production_voice_registered': False,
          'measured_cost': None}
start = time.monotonic()
try:
    with httpx.Client(timeout=httpx.Timeout(120, connect=20), follow_redirects=False) as client:
        with client.stream('POST', endpoint,
                           headers={'Authorization': 'Bearer ' + key}, json=request) as response:
            result.update(http_status=response.status_code,
                          provider_request_id=response.headers.get('x-request-id'),
                          content_type=response.headers.get('content-type'))
            if response.status_code != 200:
                body = response.read()
                try:
                    error = json.loads(body).get('error', {})
                    result.update(error_type=error.get('type'), error_code=error.get('code'))
                except (ValueError, AttributeError):
                    result['error_type'] = 'non_json_error'
                raise RuntimeError('Speech provider rejected the request; see sanitized result.')
            chunks, total = [], 0
            for chunk in response.iter_bytes():
                total += len(chunk)
                if total > 10 * 1024 * 1024:
                    raise RuntimeError('Sample exceeds the bounded 10 MiB output limit.')
                chunks.append(chunk)
            audio = b''.join(chunks)
    with wave.open(io.BytesIO(audio), 'rb') as wav:
        channels, width, rate = wav.getnchannels(), wav.getsampwidth(), wav.getframerate()
        frames = wav.readframes(wav.getnframes())
    assert channels == 1 and width == 2 and rate > 0 and len(frames) > 0
    samples = array.array('h', frames)
    if sys.byteorder != 'little':
        samples.byteswap()
    rms = math.sqrt(sum(x*x for x in samples) / len(samples)) / 32768
    peak = max(abs(x) for x in samples) / 32768
    duration = len(frames) / (channels * width * rate)
    assert 1 <= duration <= 120 and rms > 0.0001, 'Invalid or silent sample'
    WAV.write_bytes(audio)
    result.update(status='SUCCEEDED', output_path=str(WAV), bytes=len(audio),
                  sha256=hashlib.sha256(audio).hexdigest(), sample_rate=rate,
                  channels=channels, duration_seconds=round(duration, 3),
                  rms=round(rms, 6), peak=round(peak, 6),
                  clipped_samples=sum(abs(x) >= 32767 for x in samples))
except Exception as exc:
    result.update(status='FAILED', failure_class=type(exc).__name__)
finally:
    result['elapsed_seconds'] = round(time.monotonic() - start, 3)
    RESULT.write_text(json.dumps(result, indent=2), encoding='utf-8')
print(json.dumps(result, indent=2))
raise SystemExit(0 if result['status'] == 'SUCCEEDED' else 1)
