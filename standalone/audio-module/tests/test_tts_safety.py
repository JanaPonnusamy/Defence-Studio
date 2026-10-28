"""Safety regressions; fake engines are never real speech acceptance evidence."""
import sys
from types import SimpleNamespace

import pytest

from app.tts.base import TTSRequest
from app.tts.local_pyttsx3 import LocalPyttsx3Adapter


@pytest.mark.parametrize('language,voice,text', [
    ('ta', None, 'தமிழ் செய்தி'),
    ('en', 'missing-voice', 'A short briefing.'),
    ('en', None, '   '),
])
def test_invalid_request_cannot_silently_synthesize(monkeypatch, tmp_path, language, voice, text):
    calls = []
    engine = SimpleNamespace(
        getProperty=lambda key: [SimpleNamespace(id='zira', name='Zira', languages=['en-US'])],
        setProperty=lambda *args: None,
        save_to_file=lambda *args: calls.append(args),
        runAndWait=lambda: None, stop=lambda: None,
    )
    monkeypatch.setitem(sys.modules, 'pyttsx3', SimpleNamespace(init=lambda: engine))
    from app.tts import pyttsx3_worker
    from app.tts.pyttsx3_worker import run
    monkeypatch.setattr(pyttsx3_worker, 'onecore_tokens', lambda: [])   # no OS voices from the real machine
    monkeypatch.setattr(LocalPyttsx3Adapter, '_call', lambda self, payload, folder: run(payload))
    with pytest.raises(ValueError):
        LocalPyttsx3Adapter().synthesize(TTSRequest(text, voice, language), tmp_path / 'bad.wav')
    assert not calls


def test_onecore_voice_is_used_only_for_its_own_language(monkeypatch):
    engine = SimpleNamespace(
        getProperty=lambda key: [SimpleNamespace(id='zira', name='Zira', languages=['en-US'])],
        setProperty=lambda *args: None, save_to_file=lambda *args: None,
        runAndWait=lambda: None, stop=lambda: None,
    )
    monkeypatch.setitem(sys.modules, 'pyttsx3', SimpleNamespace(init=lambda: engine))
    from app.tts import pyttsx3_worker
    tamil = {'id': 'valluvar', 'name': 'Microsoft Valluvar - Tamil (India)', 'languages': ['ta-in'], 'engine': 'onecore'}
    spoken = []
    monkeypatch.setattr(pyttsx3_worker, 'onecore_tokens', lambda: [(tamil, 'token')])
    monkeypatch.setattr(pyttsx3_worker, 'speak_onecore', lambda token, payload: spoken.append(token))
    base = {'voice_id': None, 'rate': 180, 'volume': 1.0, 'output': 'x.wav', 'text': 'செய்தி'}
    assert pyttsx3_worker.run({**base, 'language': 'ta'})['voice']['id'] == 'valluvar' and spoken == ['token']
    with pytest.raises(ValueError):
        pyttsx3_worker.run({**base, 'language': 'hi'})                  # no Hindi voice anywhere: fail closed
    assert pyttsx3_worker.run({**base, 'language': 'en'})['voice']['id'] == 'zira'   # SAPI5 still preferred


@pytest.mark.parametrize('controls', [{'pitch_semitones': 4}, {'rate_wpm': 0},
    {'volume': float('nan')}, {'emotion': 'angry'}, {'preset': 'clone'}, {'rate_wpm': True}])
def test_invalid_controls_rejected(controls):
    from app.tts.controls import resolve_controls
    with pytest.raises(ValueError):
        resolve_controls(controls)


@pytest.mark.parametrize('kind', ['timeout', 'missing', 'corrupt', 'metadata', 'empty_metadata'])
def test_provider_failure_has_no_published_output(monkeypatch, tmp_path, kind):
    import subprocess
    from pathlib import Path
    def call(self, payload, folder):
        if kind == 'timeout':
            raise subprocess.TimeoutExpired('speech', 1)
        if kind == 'missing':
            raise FileNotFoundError('missing executable')
        if kind == 'metadata':
            raise ValueError('missing metadata')
        Path(payload['output']).write_bytes(b'not audio')
        if kind == 'empty_metadata':
            return {}
        return {'voice': {'id': 'test'}}
    monkeypatch.setattr(LocalPyttsx3Adapter, '_call', call)
    with pytest.raises((ValueError, FileNotFoundError, subprocess.TimeoutExpired)):
        LocalPyttsx3Adapter().synthesize(TTSRequest('Briefing.', None, 'en'), tmp_path / 'output.wav')
    assert list(tmp_path.iterdir()) == []


def test_lcid_and_unicode_language_codes():
    from app.tts.pyttsx3_worker import languages
    assert languages(SimpleNamespace(languages=['409', b'\x05ta', 'en_GB'])) == ['en-us', 'ta', 'en-gb']


def test_existing_output_is_never_overwritten(tmp_path):
    path = tmp_path / 'output.wav'
    path.write_bytes(b'original')
    with pytest.raises(ValueError, match='already exists'):
        LocalPyttsx3Adapter().synthesize(TTSRequest('Briefing.', None, 'en'), path)
    assert path.read_bytes() == b'original'


def test_edge_neural_fails_closed_on_language_mismatch(monkeypatch, tmp_path):
    from app.tts.edge_neural import EdgeNeuralAdapter, select_voice
    calls = []
    monkeypatch.setattr(EdgeNeuralAdapter, '_fetch', lambda self, *args: calls.append(args))
    assert select_voice(None, 'ta') == 'ta-IN-ValluvarNeural'
    for voice, language in [('ta-IN-PallaviNeural', 'en'), ('missing-voice', 'ta'), (None, 'fr')]:
        with pytest.raises(ValueError):
            EdgeNeuralAdapter().synthesize(TTSRequest('தமிழ் செய்தி', voice, language), tmp_path / 'bad.wav')
    assert not calls


def test_edge_neural_returns_service_sentence_boundaries(monkeypatch, tmp_path):
    import numpy as np
    import soundfile as sf
    from app.tts.edge_neural import EdgeNeuralAdapter
    marks = [{'text': 'முதல் வாக்கியம்.', 'start': 0.1, 'end': 0.9}, {'text': 'இரண்டாவது.', 'start': 1.0, 'end': 1.8}]

    def fake_fetch(self, text, voice, params, mp3_path):
        tone = 0.3 * np.sin(np.linspace(0, 2000 * np.pi, 48000)).astype('float32')
        sf.write(mp3_path, tone, 24000, format='WAV')  # soundfile reads it back by content
        return marks
    monkeypatch.setattr(EdgeNeuralAdapter, '_fetch', fake_fetch)
    result = EdgeNeuralAdapter().synthesize(TTSRequest('முதல் வாக்கியம். இரண்டாவது.', None, 'ta'), tmp_path / 'ok.wav')
    assert result.voice == 'ta-IN-ValluvarNeural'
    assert result.provider_metadata['sentence_boundaries'] == marks


def test_edge_neural_maps_controls_to_native_prosody():
    from app.tts.edge_neural import prosody
    assert prosody({'rate_wpm': 176, 'volume': 1.0, 'pitch_semitones': 0}) == \
        {'rate': '+10%', 'volume': '+0%', 'pitch': '+0Hz'}
