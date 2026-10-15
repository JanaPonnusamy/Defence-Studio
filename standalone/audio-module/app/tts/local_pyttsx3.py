"""Real offline speech, isolated per request; fail closed on voice/language mismatch."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unicodedata
import uuid

from app.tts.base import TTSAdapter, TTSRequest, TTSResult
from app.tts.controls import PROFILE, resolve_controls


class LocalPyttsx3Adapter(TTSAdapter):
    provider_name = 'local-pyttsx3'
    is_mock = False

    def __init__(self, timeout=60):
        self.timeout = timeout

    def _call(self, payload, folder):
        metadata = Path(folder) / 'worker.json'
        payload = {**payload, 'metadata': str(metadata)}
        worker = Path(__file__).with_name('pyttsx3_worker.py')
        subprocess.run([sys.executable, str(worker)], input=json.dumps(payload), text=True,
                       capture_output=True, check=True, timeout=self.timeout)
        result = json.loads(metadata.read_text(encoding='utf-8'))
        if result.get('error'):
            raise ValueError(result['error'])
        return result

    def describe(self):
        result = {'provider': self.provider_name, 'model': 'sapi5-or-espeak', 'is_mock': False,
                  'capabilities': ['basic_rate_control', 'basic_volume_control', 'postprocess_pitch'],
                  'output_format': 'wav', 'controls': PROFILE['controls'],
                  'notes': PROFILE['emotion'], 'available': False, 'voices': []}
        try:
            with tempfile.TemporaryDirectory() as folder:
                result.update(self._call({'inventory': True}, folder))
            result['available'] = bool(result['voices'])
        except Exception as exc:
            result['error'] = type(exc).__name__ + ': ' + str(exc)
        return result

    def synthesize(self, request: TTSRequest, dest_path: Path):
        if not isinstance(request.text, str) or not request.text.strip() or len(request.text) > 20000:
            raise ValueError('Text must contain 1 to 20000 characters')
        if request.output_format != 'wav':
            raise ValueError('Only WAV output is supported')
        controls = resolve_controls(request.controls, request.performance_plan)
        dest_path = Path(dest_path)
        if dest_path.exists():
            raise ValueError('Output already exists; refusing to overwrite an artifact')
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=dest_path.parent, prefix='.tts-') as folder:
            raw = Path(folder) / 'speech.wav'
            metadata = self._call({'text': unicodedata.normalize('NFC', request.text),
                                   'voice_id': request.voice_id, 'language': request.language,
                                   'rate': round(controls['rate_wpm']), 'volume': controls['volume'],
                                   'output': str(raw)}, folder)
            if not isinstance(metadata.get('voice'), dict) or not metadata['voice'].get('id'):
                raise ValueError('Provider did not return selected voice metadata')
            if controls['pitch_semitones']:
                import librosa
                import numpy as np
                import soundfile as sf
                data, sr = sf.read(raw, dtype='float32')
                if data.ndim != 1:
                    raise ValueError('Pitch processing requires mono audio')
                data = librosa.effects.pitch_shift(data, sr=sr, n_steps=controls['pitch_semitones'])
                peak = float(np.max(np.abs(data)))
                attenuation = min(1., .98 / peak) if peak else 1.
                data *= attenuation
                metadata['pitch_gain'] = attenuation
                sf.write(raw, data, sr, subtype='PCM_16')
            from app.pipeline.validate import validate_generated_audio
            validation = validate_generated_audio(raw)
            if validation['status'] == 'FAILED':
                raise ValueError('Invalid synthesized audio: ' + validation['reason'])
            # Atomic, exclusive publication on this filesystem: no partial or overwritten artifact.
            os.link(raw, dest_path)
        return TTSResult(provider=self.provider_name, model='sapi5-or-espeak',
                         voice=metadata['voice']['id'], language=request.language,
                         capabilities=['basic_rate_control', 'basic_volume_control', 'postprocess_pitch'],
                         output_format='wav', request_id=str(uuid.uuid4()), result_path=dest_path,
                         estimated_cost=0.0, provider_metadata={**metadata, 'is_mock': False,
                         'controls': controls, 'profile_sha256': PROFILE['sha256'],
                         'cost_kind': 'unmetered_local_estimate', 'emotion_kind': 'rate_volume_approximation'})
