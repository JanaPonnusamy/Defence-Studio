"""One real local SAPI synthesis. Not a mock."""
import hashlib
import json
from pathlib import Path

import pyttsx3
import soundfile as sf

from app.tts.base import TTSRequest
from app.tts.local_pyttsx3 import LocalPyttsx3Adapter
from app.tts.mock_adapter import MockTTSAdapter

out = Path(__file__).resolve().parent / "tts"
out.mkdir(parents=True, exist_ok=True)

engine = pyttsx3.init()
voices = []
for voice in engine.getProperty("voices"):
    voices.append({"id": voice.id, "name": voice.name})
(out / "voices.json").write_text(json.dumps(voices, indent=2), encoding="utf-8")
print("voices", len(voices), flush=True)
for voice in voices:
    print("-", voice["name"], flush=True)
del engine

adapter = LocalPyttsx3Adapter()
print("describe", adapter.describe(), flush=True)
text = "The Northwind harbour counted four patrol craft during the March drill."
dest = out / "english.wav"
result = adapter.synthesize(
    TTSRequest(text=text, voice_id=None, language="en",
               performance_plan=[{"pace": "moderate", "energy": "controlled-high"}]),
    dest,
)
data, sample_rate = sf.read(str(dest))
info = {
    "provider": result.provider,
    "voice": result.voice,
    "is_mock": result.provider_metadata.get("is_mock"),
    "estimated_cost": result.estimated_cost,
    "bytes": dest.stat().st_size,
    "sample_rate": int(sample_rate),
    "samples": int(len(data)),
    "duration_s": round(len(data) / sample_rate, 3),
    "sha256": hashlib.sha256(dest.read_bytes()).hexdigest(),
    "rate": result.provider_metadata.get("rate"),
    "volume": result.provider_metadata.get("volume"),
}
(out / "english.json").write_text(json.dumps(info, indent=2), encoding="utf-8")
print("english", json.dumps(info), flush=True)
print("mock_labeled", MockTTSAdapter().describe()["is_mock"], flush=True)
