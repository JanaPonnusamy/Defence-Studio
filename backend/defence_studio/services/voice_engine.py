"""Thin adapter between Defence Studio and the standalone components/voice-engine.

The engines never import application code; this module is the only place the
backend reaches into them. It also builds the *honest* voice/provider catalog:
a voice is only reported ``ready`` when its adapter reports healthy and the
requested language is actually declared by the adapter.
"""
from __future__ import annotations

import os
import re
import sys
import time
from pathlib import Path

from defence_studio.config import PROJECT_ROOT


def ensure_path() -> None:
    root = os.environ.get("DEFENCE_STUDIO_VOICE_ENGINE_PATH")
    path = str(Path(root) if root else PROJECT_ROOT / "components" / "voice-engine")
    if path not in sys.path:
        sys.path.insert(0, path)


def engine_schemas() -> list[str]:
    ensure_path()
    from voice_analyzer.store import SCHEMA as analyzer_schema
    from voice_generation.store import SCHEMA as generation_schema
    return [analyzer_schema, generation_schema]


def test_tone_enabled() -> bool:
    return os.environ.get("DEFENCE_STUDIO_VOICE_TEST_TONE", "") == "1"


_CATALOG_TTL = 60.0
_catalog_cache: dict = {"at": 0.0, "voices": None, "models": {}}

TEST_TONE_ID = "TEST_TONE_SYNTHETIC"
NARRATOR_ID = "DEFENCE_ERA_FEMALE_001"
_LIC_OS = {
    "speaker_license_ref": "Windows OS-bundled voice; redistribution/commercial/YouTube terms NOT verified",
    "training_allowed": False, "commercial_use_allowed": False, "youtube_use_allowed": False,
    "valid_from": "2020-01-01", "valid_until": None, "terms_hash": None,
}


def _sapi_models():
    from voice_generation import SapiModel
    probe = SapiModel()
    try:
        voices = probe._voices()           # name -> culture
    except Exception:
        return []
    out = []
    for name, culture in sorted(voices.items()):
        m = SapiModel(name)
        vid = "OS_SAPI_" + re.sub(r"[^A-Z0-9]+", "_", name.upper()).strip("_")
        out.append((vid, name, culture, m))
    return out


def catalog(refresh: bool = False) -> list[dict]:
    """Voice catalog with real readiness. Never claims Tamil unless an adapter declares it."""
    now = time.time()
    if not refresh and _catalog_cache["voices"] is not None and now - _catalog_cache["at"] < _CATALOG_TTL:
        return _catalog_cache["voices"]
    ensure_path()
    from voice_generation import SyntheticModel
    voices, models = [], {}
    voices.append({
        "voice_id": NARRATOR_ID, "display_name": "Defence Era female narrator (owned/licensed)",
        "kind": "NOT_PROVISIONED", "adapter": None, "ready": False, "languages": [],
        "natural_speech": False, "tamil_verified": False, "license": None,
        "reason": "No owned/licensed Tamil narrator model has been provisioned or verified.",
    })
    for vid, name, culture, m in _sapi_models():
        ok = m.health_check().get("ok")
        caps = m.capabilities() if ok else {"languages": [], "controls": [], "unsupported_controls": []}
        voices.append({
            "voice_id": vid, "display_name": f"{name} (Windows OS voice)", "kind": "OS_VOICE",
            "adapter": m.adapter, "ready": bool(ok and caps["languages"]), "languages": caps["languages"],
            "natural_speech": True, "tamil_verified": False,
            "supported_controls": caps.get("controls", []), "unsupported_controls": caps.get("unsupported_controls", []),
            "license": {"license_id": "OS-SAPI-" + vid, **_LIC_OS},
            "reason": "" if ok else "SAPI voice not healthy",
        })
        if ok:
            models[vid] = (m, {"license_id": "OS-SAPI-" + vid, **_LIC_OS}, name)
    if test_tone_enabled():
        t = SyntheticModel()
        lic = {"license_id": "TEST-TONE", "speaker_license_ref": "Synthetic tones generated in-process; no speaker",
               "training_allowed": False, "commercial_use_allowed": False, "youtube_use_allowed": False,
               "valid_from": "2020-01-01", "valid_until": None, "terms_hash": None}
        caps = t.capabilities()
        voices.append({
            "voice_id": TEST_TONE_ID, "display_name": "Synthetic test tone (NOT speech)", "kind": "TEST_TONE",
            "adapter": t.adapter, "ready": True, "languages": caps["languages"], "natural_speech": False,
            "tamil_verified": False, "supported_controls": caps["controls"], "unsupported_controls": [],
            "license": lic, "reason": "Pipeline testing only. Output is non-linguistic tones, not Tamil narration.",
        })
        models[TEST_TONE_ID] = (t, lic, "Synthetic test tone")
    _catalog_cache.update(at=now, voices=voices, models=models)
    return voices


def build_engine(database_file, storage_dir):
    ensure_path()
    from voice_generation import GenStore, VoiceGenerationEngine
    catalog()
    engine = VoiceGenerationEngine(GenStore(str(database_file)), str(storage_dir))
    for vid, (model, lic, display) in _catalog_cache["models"].items():
        engine.register_voice(vid, model, display_name=display, license=lic)
    return engine


def build_analyzer(database_file, max_seconds: float | None = None):
    ensure_path()
    from voice_analyzer import Store, VoiceAnalyzerService
    return VoiceAnalyzerService(Store(str(database_file)),
                                max_seconds=max_seconds or float(os.environ.get("DEFENCE_STUDIO_VOICE_MAX_SECONDS", 3600)))


def aligner_status() -> dict:
    """Report the real forced-aligner availability (components/defence-align)."""
    base = PROJECT_ROOT / "components" / "defence-align"
    status = {"name": "defence-align (CTC forced aligner)", "available": False, "languages_verified": [],
              "license_note": "MMS-FA bundled model is CC-BY-NC 4.0 (non-commercial); not approved for production.",
              "reason": ""}
    try:
        import torchaudio  # noqa: F401
    except Exception:
        status["reason"] = "torch/torchaudio not installed; no forced-alignment model present"
        return status
    status["reason"] = "torchaudio present but model bundle and Tamil accuracy not verified"
    return status
