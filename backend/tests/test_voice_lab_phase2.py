"""Studio Phase 2: Voice Lab capabilities, previews, approved profiles and their use in narration.

Contract tests use a fake of the Audio Module's documented API describing the
edge-neural provider. They prove wiring and honesty rules, not real speech;
the real Tamil preview is exercised separately against the running module.
"""
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from defence_studio.api.app import create_app
from defence_studio.integrations.audio_module import AudioModuleClient
from defence_studio.services.audio import AudioService
from defence_studio.services.integration import IntegrationStale, IntegrationValidationError
from defence_studio.services.studio import build_studio_state
from defence_studio.services.voice_lab import UNSUPPORTED, config_sha256
from defence_studio.services.youtube import YouTubeService
from test_audio_rm4 import wav_bytes
from test_scene_service_m1 import OWNER, SCENE_CONFIG, _plan_sha, approved_script  # noqa: F401
from conftest import SECRET, access_token

CONTROLS = {"rate_wpm": {"range": [80, 260], "kind": "native"}, "volume": {"range": [0, 1], "kind": "native"},
            "pitch_semitones": {"range": [-3, 3], "kind": "approximation"}}
EDGE = {"provider": "edge-neural", "model": "edge-read-aloud-neural", "available": True, "network": True,
        "capabilities": ["native_rate_control"], "controls": {**CONTROLS, "emotion": {"range": [0, 1]}},
        "voices": [{"id": "ta-IN-ValluvarNeural", "languages": ["ta-in"]},
                   {"id": "ta-IN-PallaviNeural", "languages": ["ta-in"]},
                   {"id": "en-IN-NeerjaNeural", "languages": ["en-in"]}]}
UNDECLARED = {"provider": "other-tts", "available": True, "controls": CONTROLS,
              "voices": [{"id": "x", "languages": ["ta"]}]}


class FakeEdgeModule:
    def __init__(self, fail=False, wrong_provider=False):
        self.fail, self.wrong_provider = fail, wrong_provider
        self.calls = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/tts/providers":
            return httpx.Response(200, json={"providers": [EDGE, UNDECLARED]})
        if path == "/tts/synthesize":
            if self.fail:
                return httpx.Response(503, json={"error": {"code": "service_unavailable", "message": "edge down"}})
            self.calls.append(json.loads(request.content))
            n = len(self.calls)
            return httpx.Response(200, json={"job_id": f"j{n}", "status": "SUCCEEDED", "result_id": f"gen{n}"})
        if path.startswith("/generated/") and path.endswith("/file"):
            return httpx.Response(200, content=wav_bytes(1.0))
        if path.startswith("/generated/"):
            body = self.calls[int(path.split("gen")[-1]) - 1]
            applied = {"rate_wpm": 170, "volume": 0.85, "pitch_semitones": 0, **body.get("controls", {})}
            return httpx.Response(200, json={
                "generated_id": path.rsplit("/", 1)[-1], "duration": 1.0, "sample_rate": 8000, "channels": 1,
                "provider": "local-pyttsx3" if self.wrong_provider else "edge-neural",
                "model": "edge-read-aloud-neural", "validation_status": "PASSED", "validation_details": {},
                "provider_metadata": {"is_mock": False, "voice": body["voice_id"], "controls": applied,
                                      "prosody": {"rate": "+0%"}}})
        if path in ("/references", "/references/ref1/process", "/references/ref1", "/alignment/al1"):
            return httpx.Response(200, json={"reference_id": "ref1", "job_id": "pj", "status": "SUCCEEDED",
                                             "alignment_id": "al1", "segments": []})
        return httpx.Response(404, json={"error": {"code": "not_found", "message": path}})


@pytest.fixture(autouse=True)
def declared(monkeypatch):
    monkeypatch.setenv("DEFENCE_STUDIO_TTS_PROVIDER_LANGUAGES", "edge-neural:ta|en,local-pyttsx3:en")


def _svc(s, fake):
    client = AudioModuleClient(base_url="http://audio.test", poll_interval=0,
                               client_factory=lambda **kw: httpx.Client(transport=httpx.MockTransport(fake.handler), **kw))
    return AudioService(s["db"], s["root"], provider=s["provider"], audio_client=client)


def _approve_plan(s):
    plan = s["scene"].generate(s["job"], OWNER, SCENE_CONFIG, "scene-ta")
    s["scene"].approve(s["job"], OWNER, "ta", plan["version"], _plan_sha(s["scene"], s["job"], plan["version"]),
                       "ok", "plan-ta")
    return plan


VALLUVAR = {"provider": "edge-neural", "voice_id": "ta-IN-ValluvarNeural", "controls": {"rate_wpm": 150, "volume": 0.9}}


def test_capabilities_list_only_declared_providers_voices_and_controls(approved_script):
    s = approved_script
    state = _svc(s, FakeEdgeModule()).voice_lab_state(s["job"], OWNER, "ta")
    assert [p["provider"] for p in state["providers"]] == ["edge-neural"]
    edge = state["providers"][0]
    assert [v["id"] for v in edge["voices"]] == ["ta-IN-ValluvarNeural", "ta-IN-PallaviNeural"]
    assert set(edge["controls"]) == {"rate_wpm", "volume", "pitch_semitones"}  # "emotion" is not a real control here
    assert state["unsupported"] == UNSUPPORTED and "voice_cloning" in state["unsupported"]
    assert state["approved_profile"] is None and state["provider_error"] is None


def test_preview_rejects_unoffered_voice_unknown_and_out_of_range_controls(approved_script):
    s = approved_script
    fake = FakeEdgeModule()
    svc = _svc(s, fake)
    bad = [({**VALLUVAR, "voice_id": "en-IN-NeerjaNeural"}, "not offered"),
           ({**VALLUVAR, "provider": "other-tts"}, "Unknown TTS provider"),
           ({**VALLUVAR, "controls": {"emotion": 0.5}}, "Unsupported controls"),
           ({**VALLUVAR, "controls": {"rate_wpm": 400}}, "between 80 and 260"),
           ({**VALLUVAR, "controls": {"volume": True}}, "between 0 and 1")]
    for i, (config, message) in enumerate(bad):
        with pytest.raises(IntegrationValidationError, match=message):
            svc.create_voice_preview(s["job"], OWNER, "ta", f"bad-{i}", text="வணக்கம்", **config)
    with pytest.raises(IntegrationValidationError, match="1 to 400"):
        svc.create_voice_preview(s["job"], OWNER, "ta", "long", text="அ" * 401, **VALLUVAR)
    assert fake.calls == []


def test_preview_records_actual_voice_and_confirmed_controls_and_replays_identical_config(approved_script):
    s = approved_script
    fake = FakeEdgeModule()
    svc = _svc(s, fake)
    out = svc.create_voice_preview(s["job"], OWNER, "ta", "p-1", text="ஏவுகணை 40% வேகமாக 300 km பறந்தது.", **VALLUVAR)
    assert out["status"] == "SUCCEEDED" and not out["replayed"]
    r = out["result"]
    assert r["provider"] == "edge-neural" and r["model"] == "edge-read-aloud-neural"
    assert r["voice"] == "ta-IN-ValluvarNeural" and r["is_mock"] is False
    assert r["applied_controls"] == {"rate_wpm": 150, "volume": 0.9} and r["unconfirmed_controls"] == []
    assert r["text"] == "ஏவுகணை 40% வேகமாக 300 km பறந்தது." and r["tts_text"] != r["text"]  # prepared like full narration
    assert fake.calls[0]["text"] == r["tts_text"] and fake.calls[0]["controls"] == VALLUVAR["controls"]
    assert svc.voice_preview_audio(s["job"], OWNER, "ta", out["version"]) == wav_bytes(1.0)
    again = svc.create_voice_preview(s["job"], OWNER, "ta", "p-2", text="ஏவுகணை 40% வேகமாக 300 km பறந்தது.", **VALLUVAR)
    assert again["replayed"] and again["version"] == out["version"] and len(fake.calls) == 1


def test_provider_failure_or_substitution_fails_the_preview_without_fallback(approved_script):
    s = approved_script
    down = _svc(s, FakeEdgeModule(fail=True)).create_voice_preview(s["job"], OWNER, "ta", "f-1", text="சோதனை", **VALLUVAR)
    assert down["status"] == "FAILED" and "edge down" in down["detail"] and down["result"] is None
    swapped = _svc(s, FakeEdgeModule(wrong_provider=True)).create_voice_preview(
        s["job"], OWNER, "ta", "f-2", text="சோதனை இரண்டு", **VALLUVAR)
    assert swapped["status"] == "FAILED" and "provider_mismatch" in swapped["detail"]


def test_profile_approval_needs_a_preview_of_the_exact_config_and_supersedes(approved_script):
    s = approved_script
    svc = _svc(s, FakeEdgeModule())
    v1 = svc.create_voice_profile(s["job"], OWNER, "ta", "prof-1", **VALLUVAR)
    assert v1["status"] == "AWAITING_REVIEW" and v1["input_binding"]["config_sha256"] == config_sha256(
        "edge-neural", "ta-IN-ValluvarNeural", {k: float(v) for k, v in VALLUVAR["controls"].items()})
    with pytest.raises(IntegrationStale, match="preview"):
        svc.review_voice_profile(s["job"], OWNER, "ta", v1["version"], "APPROVED", "sounds good")
    # a preview of a *different* configuration does not count
    svc.create_voice_preview(s["job"], OWNER, "ta", "p-x", text="சோதனை", **{**VALLUVAR, "controls": {"rate_wpm": 151}})
    with pytest.raises(IntegrationStale, match="preview"):
        svc.review_voice_profile(s["job"], OWNER, "ta", v1["version"], "APPROVED", "sounds good")
    svc.create_voice_preview(s["job"], OWNER, "ta", "p-y", text="சோதனை", **VALLUVAR)
    with pytest.raises(IntegrationValidationError, match="note"):
        svc.review_voice_profile(s["job"], OWNER, "ta", v1["version"], "APPROVED", " ")
    assert svc.review_voice_profile(s["job"], OWNER, "ta", v1["version"], "APPROVED", "clear")["status"] == "APPROVED"
    pallavi = {**VALLUVAR, "voice_id": "ta-IN-PallaviNeural"}
    v2 = svc.create_voice_profile(s["job"], OWNER, "ta", "prof-2", **pallavi)
    svc.create_voice_preview(s["job"], OWNER, "ta", "p-z", text="சோதனை", **pallavi)
    svc.review_voice_profile(s["job"], OWNER, "ta", v2["version"], "APPROVED", "warmer")
    state = svc.voice_lab_state(s["job"], OWNER, "ta")
    assert {p["version"]: p["status"] for p in state["profiles"]} == {v1["version"]: "SUPERSEDED", v2["version"]: "APPROVED"}
    assert state["approved_profile"]["version"] == v2["version"]
    with pytest.raises(IntegrationStale, match="AWAITING_REVIEW"):
        svc.review_voice_profile(s["job"], OWNER, "ta", v1["version"], "APPROVED", "again")


def test_narration_uses_approved_profile_and_a_voice_change_marks_audio_stale(approved_script):
    s = approved_script
    plan = _approve_plan(s)
    fake = FakeEdgeModule()
    svc = _svc(s, fake)
    v1 = svc.create_voice_profile(s["job"], OWNER, "ta", "prof-1", **VALLUVAR)
    svc.create_voice_preview(s["job"], OWNER, "ta", "p-1", text="சோதனை", **VALLUVAR)
    svc.review_voice_profile(s["job"], OWNER, "ta", v1["version"], "APPROVED", "ok")
    before = len(fake.calls)
    item = svc.start_audio(s["job"], OWNER, "ta", "audio-1", align=False, background=False)
    assert item["status"] == "SUCCEEDED", item["detail"]
    binding = item["input_binding"]
    assert binding["provider"] == "edge-neural" and binding["voice_id"] == "ta-IN-ValluvarNeural"
    assert binding["voice_profile"] == {"version": v1["version"], "config_sha256": v1["input_binding"]["config_sha256"]}
    narration = fake.calls[before:]
    assert len(narration) == len(plan["scenes"])
    assert all(c["voice_id"] == "ta-IN-ValluvarNeural" and c["controls"].get("volume") == 0.9 for c in narration)
    assert all(abs(c["controls"]["rate_wpm"] - 150) <= 150 * 0.5 for c in narration)  # scene direction scales the base
    assert not svc.audio_status(s["job"], OWNER, "ta")["items"][-1]["stale"]

    pallavi = {**VALLUVAR, "voice_id": "ta-IN-PallaviNeural"}
    v2 = svc.create_voice_profile(s["job"], OWNER, "ta", "prof-2", **pallavi)
    svc.create_voice_preview(s["job"], OWNER, "ta", "p-2", text="சோதனை", **pallavi)
    svc.review_voice_profile(s["job"], OWNER, "ta", v2["version"], "APPROVED", "switch")
    assert svc.audio_status(s["job"], OWNER, "ta")["items"][-1]["stale"] is True
    with pytest.raises(IntegrationStale):
        svc.current_audio_set(s["job"], OWNER, "ta")
    overview = YouTubeService(s["db"], s["root"]).pipeline_overview(s["job"], OWNER, "ta")
    audio = next(x for x in build_studio_state(overview, None)["stages"] if x["key"] == "audio")
    assert audio["state"] == "STALE"


def test_explicit_provider_in_request_bypasses_the_profile(approved_script):
    s = approved_script
    _approve_plan(s)
    fake = FakeEdgeModule()
    item = _svc(s, fake).start_audio(s["job"], OWNER, "ta", "audio-x", provider="edge-neural",
                                     voice_id="ta-IN-PallaviNeural", align=False, background=False)
    assert "voice_profile" not in item["input_binding"] and "base_controls" not in item["input_binding"]
    assert all("volume" not in c.get("controls", {}) for c in fake.calls)


def test_http_voice_lab_routes_auth_validation_and_audio(approved_script, monkeypatch):
    s = approved_script
    fake = FakeEdgeModule()
    real_client = AudioService._client
    monkeypatch.setattr(AudioService, "_client", lambda self: AudioModuleClient(
        base_url="http://audio.test", poll_interval=0,
        client_factory=lambda **kw: httpx.Client(transport=httpx.MockTransport(fake.handler), **kw)))
    app = create_app(s["db"], SECRET, s["root"])
    mine = {"Authorization": f"Bearer {access_token(OWNER)}"}
    other = {"Authorization": f"Bearer {access_token('intruder')}"}
    base = f"/api/jobs/{s['job']}/voice-lab/ta"
    with TestClient(app) as client:
        assert client.get(base).status_code == 401
        assert client.get(base, headers=other).status_code == 404
        assert client.get(base, headers=mine).json()["providers"][0]["provider"] == "edge-neural"
        body = {**VALLUVAR, "text": "வணக்கம்"}
        assert client.post(f"{base}/previews", json=body, headers=mine).status_code == 422  # no Idempotency-Key
        assert client.post(f"{base}/previews", json={**body, "controls": {"tone": 1}},
                           headers={**mine, "Idempotency-Key": "h0"}).status_code == 422
        r = client.post(f"{base}/previews", json=body, headers={**mine, "Idempotency-Key": "h1"})
        assert r.status_code == 200 and r.json()["status"] == "SUCCEEDED"
        audio = client.get(f"{base}/previews/{r.json()['version']}/file", headers=mine)
        assert audio.status_code == 200 and audio.headers["content-type"] == "audio/wav"
        assert client.get(f"{base}/previews/{r.json()['version']}/file", headers=other).status_code == 404
        prof = client.post(f"{base}/profiles", json=VALLUVAR, headers={**mine, "Idempotency-Key": "h2"})
        assert prof.status_code == 201 and prof.json()["status"] == "AWAITING_REVIEW"
        review = f"{base}/profiles/{prof.json()['version']}/review"
        assert client.post(review, json={"decision": "MAYBE", "note": "x"}, headers=mine).status_code == 422
        ok = client.post(review, json={"decision": "APPROVED", "note": "clear voice"}, headers=mine)
        assert ok.status_code == 200 and ok.json()["status"] == "APPROVED"
    assert real_client is not None
