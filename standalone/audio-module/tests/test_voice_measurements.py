import numpy as np
import pytest
import soundfile as sf


def test_measurement_silence_has_no_confident_pitch(tmp_path):
    from app.pipeline.measure import measure
    path = tmp_path / 'silence.wav'
    sf.write(path, np.zeros(16000), 16000)
    result = measure(path)
    assert result['pitch_hz'] is None
    assert result['voiced_ratio'] == 0
    assert result['duration_seconds'] == 1
    assert result['warnings']


def test_known_frequency_calibrates_estimator(tmp_path):
    # Calibration signal ONLY, never counted as synthesized speech.
    from app.pipeline.measure import measure
    path = tmp_path / 'calibration.wav'
    t = np.arange(32000) / 16000
    sf.write(path, .2 * np.sin(2 * np.pi * 200 * t), 16000)
    result = measure(path, 'two words')
    assert abs(result['pitch_hz']['median'] - 200) < 4
    assert result['speaking_rate_wpm'] == 60


@pytest.mark.parametrize('values,sr', [(np.array([]),16000), (np.array([np.nan]),16000),
                                      (np.ones(10),0)])
def test_invalid_analysis_rejected(values, sr):
    from app.pipeline.analyze import analyze_audio
    with pytest.raises(ValueError):
        analyze_audio(values, sr, [])


def test_nonfinite_audio_fails_validation(tmp_path):
    from app.pipeline.validate import validate_generated_audio
    path = tmp_path / 'nan.wav'
    sf.write(path, np.full(16000, np.nan), 16000, subtype='FLOAT')
    assert validate_generated_audio(path)['status'] == 'FAILED'


@pytest.mark.parametrize('body', [{'text':'   '}, {'text':'hello','controls':{'pitch_semitones':12}},
    {'text':'hello','controls':{'emotion':'angry'}}, {'text':'hello','output_format':'mp3'}])
def test_api_invalid_inputs(body):
    from fastapi.testclient import TestClient
    from app.main import app
    with TestClient(app) as client:
        assert client.post('/tts/synthesize', json=body).status_code == 422


def test_rate_includes_pauses_but_articulation_excludes_them(monkeypatch):
    from app.pipeline import analyze
    from app.pipeline.align import AlignedSegment, WordTiming
    monkeypatch.setattr(analyze, 'detect_speech_regions', lambda *a: [(0., 1.)])
    monkeypatch.setattr(analyze.librosa, 'pyin', lambda *a, **k: (np.array([np.nan]), None, None))
    seg = AlignedSegment('one two', 0, 1, .4,
                         [WordTiming('one',0,.5,.4), WordTiming('two',.5,1,.4)])
    result = analyze.analyze_audio(np.zeros(32000),16000,[seg])
    assert result.speaking_rate_wpm == 60
    assert result.articulation_rate_wps == 2


def test_invalid_output_cannot_become_succeeded(monkeypatch, tmp_path):
    from app.db.init_db import init_db
    from app.db.base import SessionLocal
    from app.db.models import Job, GeneratedAudio
    from app.jobs import handlers
    from app.jobs.queue import get_job_queue
    from app.tts.mock_adapter import MockTTSAdapter
    init_db()
    monkeypatch.setattr(handlers, 'get_adapter', lambda provider: MockTTSAdapter())
    monkeypatch.setattr(handlers, 'validate_generated_audio', lambda *a, **k:
                        {'status':'FAILED','reason':'invalid-test-audio','details':{}})
    with SessionLocal() as db:
        job=Job(job_type='tts_synthesize',status='QUEUED',payload={'text':'Test only','provider':'mock-tts'})
        db.add(job)
        db.commit()
        job_id=job.job_id
    get_job_queue()._run(job_id,handlers.tts_synthesize_job,{})
    with SessionLocal() as db:
        assert db.get(Job,job_id).status == 'FAILED'
        assert db.query(GeneratedAudio).filter_by(job_id=job_id).count() == 0
