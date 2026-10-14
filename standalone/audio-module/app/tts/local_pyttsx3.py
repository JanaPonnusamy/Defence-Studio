"""REAL local/offline TTS adapter using pyttsx3 (SAPI5 on Windows, espeak on
Linux). This performs actual local text-to-speech synthesis -- no network
call, no mocked output. Performance-plan hints (pace/energy) are mapped to
pyttsx3's rate/volume engine properties on a best-effort basis since
pyttsx3 does not support SSML-level prosody control.
"""
from __future__ import annotations

import uuid
from pathlib import Path

from app.tts.base import TTSAdapter, TTSRequest, TTSResult

_PACE_TO_RATE = {"slow": 130, "moderate": 170, "fast": 210}
_ENERGY_TO_VOLUME = {"low": 0.6, "controlled-high": 0.85, "high": 1.0}


class LocalPyttsx3Adapter(TTSAdapter):
    provider_name = "local-pyttsx3"
    is_mock = False

    def describe(self) -> dict:
        return {
            "provider": self.provider_name,
            "model": "sapi5-or-espeak (OS default voice)",
            "capabilities": ["basic_rate_control", "basic_volume_control"],
            "output_format": "wav",
            "is_mock": False,
            "notes": "Real local/offline TTS engine. No SSML-level prosody support.",
        }

    def synthesize(self, request: TTSRequest, dest_path: Path) -> TTSResult:
        import pyttsx3  # imported lazily so the mock adapter works even if
        # pyttsx3/its OS speech engine is unavailable in a given environment.

        engine = pyttsx3.init()

        # Best-effort performance-plan mapping: use the first instruction's
        # pace/energy if present, else defaults.
        pace = "moderate"
        energy = "controlled-high"
        if request.performance_plan:
            first = request.performance_plan[0]
            pace = first.get("pace", pace)
            energy = first.get("energy", energy)

        rate = _PACE_TO_RATE.get(pace, 170)
        volume = _ENERGY_TO_VOLUME.get(energy, 0.85)
        engine.setProperty("rate", rate)
        engine.setProperty("volume", volume)

        voice_set = False
        if request.voice_id:
            for v in engine.getProperty("voices"):
                if request.voice_id.lower() in (v.id.lower(), (v.name or "").lower()):
                    engine.setProperty("voice", v.id)
                    voice_set = True
                    break

        dest_path.parent.mkdir(parents=True, exist_ok=True)
        engine.save_to_file(request.text, str(dest_path))
        engine.runAndWait()
        try:
            engine.stop()
        except Exception:  # noqa: BLE001
            pass

        chosen_voice = request.voice_id if voice_set else "os-default"
        return TTSResult(
            provider=self.provider_name,
            model="sapi5-or-espeak",
            voice=chosen_voice,
            language=request.language,
            capabilities=["basic_rate_control", "basic_volume_control"],
            output_format="wav",
            request_id=str(uuid.uuid4()),
            result_path=dest_path,
            estimated_cost=0.0,
            provider_metadata={"rate": rate, "volume": volume, "is_mock": False},
        )
