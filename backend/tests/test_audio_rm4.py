"""RM-4: approved scene plan -> real audio + alignment through the Audio Module.

Contract tests use a faithful fake of the module's documented API. The
INTEGRATED test drives the real standalone module (local OS voice, English)
and skips with a reason when it is not running at
DEFENCE_STUDIO_AUDIO_MODULE_URL (default http://127.0.0.1:8001).
"""
import io
import json
import struct
import time
import wave

import httpx
import pytest
from fastapi.testclient import TestClient

from defence_studio.api.app import create_app
from defence_studio.integrations.audio_module import AudioModuleClient, AudioModuleError
from defence_studio.services.audio import AudioService
from defence_studio.services.integration import IntegrationStale, IntegrationValidationError
from test_scene_service_m1 import (  # noqa: F401
    SCENE_CONFIG, OWNER, _generate_plan, _plan_sha, _rev_sha, approved_script,
)
from test_script_phase3 import GOOD_CONFIG
from conftest import SECRET, access_token

EN_SCENE_CONFIG = {**SCENE_CONFIG, "language": "en"}


def wav_bytes(seconds=1.0, rate=8000) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(rate)
        w.writeframes(b"".join(struct.pack("<h", int(8000 * ((i // 20) % 2 * 2 - 1))) for i in range(int(rate * seconds))))
    return buf.getvalue()


class FakeAudioModule:
    def __init__(self, fail_after=None, invalid=False, corrupt_download=False):
        self.fail_after, self.invalid = fail_after, invalid
        self.synth_calls, self.texts, self.controls, self.alignments, self.jobs = 0, [], [], 0, {}

    def handler(self, request: httpx.Request) -> httpx.Response:
        path, method = request.url.path, request.method
        if path == "/health":
            return httpx.Response(200, json={"status": "ok", "service": "audio-module"})
        if path == "/tts/synthesize":
            self.synth_calls += 1
            if self.fail_after is not None and self.synth_calls > self.fail_after:
                return httpx.Response(500, json={"error": {"code": "internal_error", "message": "engine crashed"}})
            body = json.loads(request.content)
            self.texts.append((body["text"], body["provider"], body["language"]))
            self.controls.append(body.get("controls", {}))
            jid = f"job{self.synth_calls}"
            self.jobs[jid] = f"gen{self.synth_calls}"
            return httpx.Response(200, json={"job_id": jid, "status": "SUCCEEDED", "result_id": f"gen{self.synth_calls}"})
        if path.startswith("/generated/") and path.endswith("/file"):
            return httpx.Response(200, content=wav_bytes(1.0 + int(path.split("gen")[-1].split("/")[0])))
        if path.startswith("/generated/"):
            n = int(path.split("gen")[-1])
            return httpx.Response(200, json={
                "generated_id": f"gen{n}", "provider": "local-pyttsx3", "model": "sapi5", "duration": 1.0 + n,
                "sample_rate": 8000, "channels": 1, "validation_status": "FAILED" if self.invalid else "PASSED",
                "validation_details": {}, "provider_metadata": {"is_mock": False, "voice": "os-default",
                                                                     "controls": {"rate_wpm": 170, "volume": 0.85,
                                                                                  "pitch_semitones": 0,
                                                                                  **self.controls[n - 1]}}})
        if path == "/references" and method == "POST":
            return httpx.Response(200, json={"reference_id": "ref1"})
        if path == "/references/ref1/process":
            return httpx.Response(200, json={"job_id": "pj", "status": "SUCCEEDED"})
        if path == "/references/ref1":
            return httpx.Response(200, json={"reference_id": "ref1", "alignment_id": "al1"})
        if path == "/alignment/al1":
            self.alignments += 1
            return httpx.Response(200, json={"alignment_id": "al1", "provider": "vad_proportional",
                                             "quality_label": "approximate_proportional_vad", "mean_confidence": 0.4,
                                             "segments": [{"text": "x", "start": 0.1, "end": 0.9, "words": []}]})
        return httpx.Response(404, json={"error": {"code": "not_found", "message": path}})


def _client(fake):
    return AudioModuleClient(base_url="http://audio.test", poll_interval=0,
                             client_factory=lambda **kw: httpx.Client(transport=httpx.MockTransport(fake.handler), **kw))


def _english_plan(s):
    """An approved English script -> approved English scene plan on the shared evidence."""
    claim = s["claim_id"]
    body = {"title": "Jets update", "opening_hook": "Here is what officials said.",
            "sections": [{"section_id": "e1", "heading": "", "narration": "The army ordered new jets last week.",
                          "requires_evidence": True, "statement_kind": "ATTRIBUTED_CLAIM",
                          "evidence": {"claim_ids": [claim], "source_ids": [], "qualification": "Attributed"},
                          "presentation_note": ""}],
            "closing": "That is the latest update."}
    s["provider"].body_factory = lambda _p: body
    rev = s["script"].generate(s["job"], OWNER, {**GOOD_CONFIG, "language": "en"}, "gen-en")
    s["script"].approve(s["job"], OWNER, "en", rev["version"], _rev_sha(s["script"], s["job"], rev["version"], "en"),
                        "approved", "script-en-approve")
    plan = s["scene"].generate(s["job"], OWNER, EN_SCENE_CONFIG, "scene-en")
    s["scene"].approve(s["job"], OWNER, "en", plan["version"], _plan_sha(s["scene"], s["job"], plan["version"], "en"),
                       "plan approved", "plan-en-approve")
    return plan


def _service(s, fake):
    return AudioService(s["db"], s["root"], provider=s["provider"], audio_client=_client(fake))


def test_tamil_on_english_only_voice_fails_closed_before_any_call(approved_script):
    s = approved_script
    plan = s["scene"].generate(s["job"], OWNER, SCENE_CONFIG, "scene-ta")
    s["scene"].approve(s["job"], OWNER, "ta", plan["version"], _plan_sha(s["scene"], s["job"], plan["version"]), "ok", "p-ta")
    fake = FakeAudioModule()
    with pytest.raises(IntegrationValidationError, match="LANGUAGE_UNSUPPORTED"):
        _service(s, fake).start_audio(s["job"], OWNER, "ta", "a-1", background=False)
    assert fake.synth_calls == 0


def test_mock_provider_requires_double_opt_in(approved_script, monkeypatch):
    s = approved_script
    _english_plan(s)
    svc = _service(s, FakeAudioModule())
    with pytest.raises(IntegrationValidationError, match="mock"):
        svc.start_audio(s["job"], OWNER, "en", "m-1", provider="mock-tts", background=False)
    with pytest.raises(IntegrationValidationError, match="mock"):
        svc.start_audio(s["job"], OWNER, "en", "m-2", provider="mock-tts", allow_mock=True, background=False)


def test_audio_set_is_bound_hashed_measured_and_verifiable(approved_script):
    s = approved_script
    plan = _english_plan(s)
    fake = FakeAudioModule()
    svc = _service(s, fake)
    item = svc.start_audio(s["job"], OWNER, "en", "a-1", background=False)
    assert item["status"] == "SUCCEEDED", item["detail"]
    body = svc.read_integration_body(s["job"], OWNER, "en", "audio_set", item["version"])
    assert body["schema_version"] == "defence-studio.audio-set.v1"
    assert [x["scene_id"] for x in body["scenes"]] == [sc["scene_id"] for sc in plan["scenes"]]
    assert [t[0] for t in fake.texts] == [sc["narration"] for sc in plan["scenes"]]  # approved text, verbatim
    assert all(t[2] == "en" for t in fake.texts)
    first = body["scenes"][0]
    assert first["duration_seconds"] == 2.0 and len(first["audio_sha256"]) == 64
    assert first["alignment"]["is_approximate"] is True and first["alignment"]["quality_label"].startswith("approximate")
    assert first["voice_direction"]["applied"] is False
    assert body["summary"]["any_mock"] is False
    assert body["summary"]["total_duration_seconds"] == round(sum(x["duration_seconds"] for x in body["scenes"]), 3)
    assert svc.verify_audio_set(s["job"], OWNER, "en", item["version"])["ok"] is True
    # tamper with a stored file -> verification catches it
    uri = first["audio_uri"]
    path = svc.store._resolve(s["job"], uri)[1]
    path.write_bytes(b"tampered")
    v = svc.verify_audio_set(s["job"], OWNER, "en", item["version"])
    assert v["ok"] is False and "checksum mismatch" in v["problems"][0]


def test_scene_voice_direction_controls_reach_provider_and_are_reported(approved_script, monkeypatch):
    s = approved_script
    _english_plan(s)
    fake = FakeAudioModule()
    svc = _service(s, fake)
    row, plan = svc._approved_plan(s["job"], OWNER, "en")
    plan["scenes"][0]["voice_direction"] = {
        "tone": "urgent", "rate": 1.2, "pitch": 1.1, "pause_after_ms": 0,
    }
    monkeypatch.setattr(svc, "_approved_plan", lambda *_args: (row, plan))

    item = svc.start_audio(s["job"], OWNER, "en", "voice-controls", background=False)

    assert item["status"] == "SUCCEEDED"
    assert fake.controls[0] == {"rate_wpm": 204, "pitch_semitones": 1.65}
    body = svc.read_integration_body(s["job"], OWNER, "en", "audio_set", item["version"])
    result = body["scenes"][0]["voice_direction"]
    assert result["applied"] is True
    assert result["applied_controls"] == {"rate_wpm": 204, "pitch_semitones": 1.65}
    assert result["unsupported_controls"] == ["tone"]


def test_failure_mid_run_is_recorded_and_retry_creates_new_version(approved_script):
    s = approved_script
    _english_plan(s)
    broken = _service(s, FakeAudioModule(fail_after=1))
    item = broken.start_audio(s["job"], OWNER, "en", "f-1", background=False)
    assert item["status"] == "FAILED" and "internal_error" in item["detail"] and item["uri"] is None
    with pytest.raises(IntegrationStale):
        broken.current_audio_set(s["job"], OWNER, "en")
    fixed = _service(s, FakeAudioModule())
    retry = fixed.retry_audio(s["job"], OWNER, "en", item["version"], "f-retry", background=False)
    assert retry["status"] == "SUCCEEDED" and retry["version"] == 2
    assert fixed.current_audio_set(s["job"], OWNER, "en")["summary"]["scene_count"] >= 1


def test_validation_failed_audio_is_never_accepted(approved_script):
    s = approved_script
    _english_plan(s)
    item = _service(s, FakeAudioModule(invalid=True)).start_audio(s["job"], OWNER, "en", "i-1", background=False)
    assert item["status"] == "FAILED" and "audio_invalid" in item["detail"]


def test_replay_and_same_binding_do_not_resynthesize(approved_script):
    s = approved_script
    _english_plan(s)
    fake = FakeAudioModule()
    svc = _service(s, fake)
    svc.start_audio(s["job"], OWNER, "en", "r-1", background=False)
    calls = fake.synth_calls
    svc.start_audio(s["job"], OWNER, "en", "r-1", background=False)
    svc.start_audio(s["job"], OWNER, "en", "r-2", background=False)
    assert fake.synth_calls == calls


def test_http_routes_background_run_stream_and_ownership(approved_script):
    s = approved_script
    _english_plan(s)
    fake = FakeAudioModule()
    app = create_app(s["db"], SECRET, s["root"], integration_clients={"audio": _client(fake)})
    mine = {"Authorization": f"Bearer {access_token(OWNER)}", "Idempotency-Key": "h-1"}
    other = {"Authorization": f"Bearer {access_token('intruder')}", "Idempotency-Key": "h-2"}
    base = f"/api/jobs/{s['job']}/audio/en"
    with TestClient(app) as client:
        assert client.post(f"{base}/generate", json={}).status_code == 401
        assert client.post(f"{base}/generate", json={}, headers=other).status_code == 404
        assert client.post(f"{base}/generate", json={"provider": "elevenlabs"}, headers=mine).status_code == 422
        assert client.post(f"{base}/generate", json={"surprise": 1}, headers=mine).status_code == 422
        assert client.post(f"{base}/generate", json={}, headers=mine).status_code == 202
        for _ in range(100):
            status = client.get(base, headers=mine).json()
            if status["items"] and status["items"][0]["status"] in ("SUCCEEDED", "FAILED"):
                break
            time.sleep(0.1)
        assert status["items"][0]["status"] == "SUCCEEDED" and status["provider_languages"]["local-pyttsx3"] == ["en"]
        v = status["items"][0]["version"]
        body = client.get(f"{base}/{v}", headers=mine).json()
        sid = body["scenes"][0]["scene_id"]
        audio = client.get(f"{base}/{v}/scene/{sid}/file", headers=mine)
        assert audio.status_code == 200 and audio.headers["content-type"] == "audio/wav" and audio.content[:4] == b"RIFF"
        assert client.get(f"{base}/{v}/scene/{sid}/file", headers=other).status_code == 404
        assert client.get(f"{base}/{v}/verify", headers=mine).json()["ok"] is True


def test_unreachable_audio_module_is_structured_error():
    with pytest.raises(AudioModuleError) as err:
        AudioModuleClient(base_url="http://127.0.0.1:9", request_timeout=1).health()
    assert err.value.code in ("unreachable", "timeout") and err.value.retryable


# -- INTEGRATED against the real standalone module ----------------------------


def _real_audio_up() -> bool:
    try:
        return AudioModuleClient(request_timeout=2).health().get("service") == "audio-module"
    except Exception:
        return False


@pytest.mark.skipif(not _real_audio_up(), reason="real Audio Module not running at DEFENCE_STUDIO_AUDIO_MODULE_URL")
def test_real_audio_module_integration_english(approved_script):
    s = approved_script
    plan = _english_plan(s)
    svc = AudioService(s["db"], s["root"], provider=s["provider"],
                       audio_client=AudioModuleClient(poll_interval=0.3, job_timeout_seconds=240))
    item = svc.start_audio(s["job"], OWNER, "en", "real-1", align=True, background=False)
    assert item["status"] == "SUCCEEDED", item["detail"]
    body = svc.read_integration_body(s["job"], OWNER, "en", "audio_set", item["version"])
    assert body["summary"]["scene_count"] == len(plan["scenes"]) and body["summary"]["any_mock"] is False
    for sc in body["scenes"]:
        assert sc["duration_seconds"] > 0.3 and sc["validation_status"] == "PASSED"
        assert sc["alignment"]["quality_label"]
    assert svc.verify_audio_set(s["job"], OWNER, "en", item["version"])["ok"]
