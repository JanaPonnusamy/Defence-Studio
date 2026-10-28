"""Free Microsoft Edge neural voices (edge-tts); online, no key. Fail closed on voice/language mismatch.

The narration text is sent to Microsoft's public read-aloud endpoint, so this
provider must only be enabled where that is acceptable. Output is decoded from
MP3 and published as mono PCM-16 WAV like the local provider.
"""
from __future__ import annotations

import asyncio
import os
from pathlib import Path
import tempfile
import unicodedata
import uuid

from app.tts.base import TTSAdapter, TTSRequest, TTSResult
from app.tts.controls import PROFILE, resolve_controls

# Language -> default voice. Valluvar (neural, male) chosen 2026-10-28 by an ASR round-trip of the same
# Tamil sample: both ta-IN voices read plain Tamil and numbers well, but Pallavi mispronounced
# English acronyms (DRDO, IAF) in every written form tried; Valluvar read them correctly.
DEFAULT_VOICES = {'ta': 'ta-IN-ValluvarNeural', 'en': 'en-IN-NeerjaNeural'}
VOICES = {
    'ta-IN-PallaviNeural': 'ta-in', 'ta-LK-SaranyaNeural': 'ta-lk', 'ta-SG-VenbaNeural': 'ta-sg',
    'ta-MY-KaniNeural': 'ta-my', 'ta-IN-ValluvarNeural': 'ta-in',
    'en-IN-NeerjaNeural': 'en-in', 'en-US-AriaNeural': 'en-us',
}
BASE_WPM = 160  # rate_wpm that maps to the voice's native (0%) speaking rate
CAPABILITIES = ['native_rate_control', 'native_volume_control', 'native_pitch_approximation']


def select_voice(voice_id, language):
    requested = language.lower().replace('_', '-')
    voice = voice_id or DEFAULT_VOICES.get(requested.split('-')[0])
    locale = VOICES.get(voice)
    if not locale or not (locale == requested or ('-' not in requested and locale.split('-')[0] == requested)):
        raise ValueError('No edge neural voice matches the requested voice and language')
    return voice


def prosody(controls):
    return {'rate': f"{round((controls['rate_wpm'] / BASE_WPM - 1) * 100):+d}%",
            'volume': f"{round((controls['volume'] - 1) * 100):+d}%",
            # ~6% of a ~220 Hz voice per semitone; an approximation, not exact semitones.
            'pitch': f"{round(controls['pitch_semitones'] * 13):+d}Hz"}


class EdgeNeuralAdapter(TTSAdapter):
    provider_name = 'edge-neural'
    is_mock = False

    def describe(self):
        result = {'provider': self.provider_name, 'model': 'edge-read-aloud-neural', 'is_mock': False,
                  'capabilities': CAPABILITIES, 'output_format': 'wav', 'controls': PROFILE['controls'],
                  'network': True, 'notes': 'Online: text is sent to Microsoft read-aloud service.',
                  'voices': [{'id': v, 'languages': [l]} for v, l in VOICES.items()]}
        try:
            import edge_tts  # noqa: F401
            result['available'] = True
        except ImportError as exc:
            result.update(available=False, error='ImportError: ' + str(exc))
        return result

    def _fetch(self, text, voice, params, mp3_path):
        """Write the MP3 and return the service's own sentence boundaries (seconds)."""
        import edge_tts

        async def run():
            marks = []
            with open(mp3_path, 'wb') as fh:
                async for chunk in edge_tts.Communicate(text, voice, boundary='SentenceBoundary', **params).stream():
                    if chunk['type'] == 'audio':
                        fh.write(chunk['data'])
                    elif chunk['type'] == 'SentenceBoundary':
                        marks.append({'text': chunk['text'], 'start': chunk['offset'] / 1e7,
                                      'end': (chunk['offset'] + chunk['duration']) / 1e7})
            return marks
        return asyncio.run(run())

    def synthesize(self, request: TTSRequest, dest_path: Path):
        if not isinstance(request.text, str) or not request.text.strip() or len(request.text) > 20000:
            raise ValueError('Text must contain 1 to 20000 characters')
        if request.output_format != 'wav':
            raise ValueError('Only WAV output is supported')
        voice = select_voice(request.voice_id, request.language)
        controls = resolve_controls(request.controls, request.performance_plan)
        params = prosody(controls)
        dest_path = Path(dest_path)
        if dest_path.exists():
            raise ValueError('Output already exists; refusing to overwrite an artifact')
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=dest_path.parent, prefix='.tts-') as folder:
            mp3, raw = Path(folder) / 'speech.mp3', Path(folder) / 'speech.wav'
            boundaries = self._fetch(unicodedata.normalize('NFC', request.text), voice, params, mp3) or []
            import soundfile as sf
            data, sr = sf.read(mp3, dtype='float32')
            if data.ndim != 1:
                data = data.mean(axis=1)
            sf.write(raw, data, sr, subtype='PCM_16')
            from app.pipeline.validate import validate_generated_audio
            validation = validate_generated_audio(raw)
            if validation['status'] == 'FAILED':
                raise ValueError('Invalid synthesized audio: ' + validation['reason'])
            os.link(raw, dest_path)
        return TTSResult(provider=self.provider_name, model='edge-read-aloud-neural', voice=voice,
                         language=request.language, capabilities=CAPABILITIES, output_format='wav',
                         request_id=str(uuid.uuid4()), result_path=dest_path, estimated_cost=0.0,
                         provider_metadata={'voice': {'id': voice, 'languages': [VOICES[voice]]},
                                            'prosody': params, 'controls': controls, 'is_mock': False,
                                            'network': True, 'cost_kind': 'free_online_service',
                                            'profile_sha256': PROFILE['sha256'],
                                            # Real timing reported by the speech service, not an estimate.
                                            'sentence_boundaries': boundaries})
