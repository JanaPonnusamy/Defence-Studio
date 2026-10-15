import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent / "baseline-source"))
from voice_generation import VoiceGenerationEngine, GenStore, SyntheticModel

class EmotionProbe(SyntheticModel):
    seen = None
    def capabilities(self):
        c = super().capabilities(); c["controls"] = [*c["controls"], "emotion"]; return c
    def synthesize(self, text, language, controls):
        self.seen = controls.get("emotion"); return super().synthesize(text, language, controls)

model = EmotionProbe()
engine = VoiceGenerationEngine(GenStore(), str(Path(__file__).parent / "baseline-source" / "audio"))
lic = {"license_id":"L", "speaker_license_ref":"test", "training_allowed":False,
       "commercial_use_allowed":False, "youtube_use_allowed":False, "valid_from":"2020-01-01", "valid_until":None}
engine.register_voice("probe", model, display_name="probe", license=lic)
direction = {"voice_id":"probe", "language":"ta-IN", "emotion":{"label":"SERIOUS", "intensity":0.72}}
engine.generate("one sentence.", direction)
print("advertised_emotion=True; baseline_received=", model.seen)
assert model.seen is None, "baseline unexpectedly forwarded emotion"
