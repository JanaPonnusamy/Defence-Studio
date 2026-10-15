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
    from app.tts.pyttsx3_worker import run
    monkeypatch.setattr(LocalPyttsx3Adapter, '_call', lambda self, payload, folder: run(payload))
    with pytest.raises(ValueError):
        LocalPyttsx3Adapter().synthesize(TTSRequest(text, voice, language), tmp_path / 'bad.wav')
    assert not calls


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
