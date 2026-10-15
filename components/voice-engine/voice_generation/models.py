"""VoiceModel interface + adapters. Adapters sit UNDER the engine; callers never see them."""
from __future__ import annotations
import abc, os, subprocess, tempfile
import numpy as np
from . import wavutil


class ModelError(Exception):
    pass


class VoiceModel(abc.ABC):
    adapter = "abstract"
    version = "0"

    @abc.abstractmethod
    def synthesize(self, text: str, language: str, controls: dict) -> tuple[np.ndarray, int]:
        """Controls include tempo, energy, pitch_variation, emphasis; adapters advertising emotion also receive
        ``emotion={label, intensity}``. Returns (mono float32, sr)."""

    @abc.abstractmethod
    def capabilities(self) -> dict:
        """{'languages': [...], 'controls': [...], 'identity': 'SYNTHETIC_TEST'|'OS_VOICE'|...}"""

    def applied_controls(self, controls: dict) -> dict:
        """Subset of ``controls`` this adapter actually used. Emotion is never implied: an adapter must
        override this and include ``emotion`` only when it changed synthesis from that field."""
        advertised = set(self.capabilities().get("controls") or ())
        return {key: controls[key] for key in ("tempo", "energy", "pitch_variation", "emphasis")
                if key in advertised and key in controls}

    def validate(self, language: str) -> None:
        if language not in self.capabilities()["languages"]:
            raise ModelError(f"{self.adapter} does not support language {language}")

    @abc.abstractmethod
    def health_check(self) -> dict: ...


class SyntheticModel(VoiceModel):
    """Deterministic tone-burst 'speech' for pipeline testing. NOT natural speech, NOT Tamil phonetics.
    Declares ta-IN only so the contract/pipeline can be exercised; audio is non-linguistic."""
    adapter, version = "synthetic-test", "1.0"

    def __init__(self, f0: float = 210.0, fail_on: str | None = None):
        self.f0, self.fail_on, self.calls = f0, fail_on, 0

    def capabilities(self):
        return {"languages": ["ta-IN", "en-US", "en-GB"], "identity": "SYNTHETIC_TEST",
                "controls": ["tempo", "energy", "pitch_variation", "emphasis"], "natural_speech": False}

    def health_check(self):
        return {"ok": True, "adapter": self.adapter}

    def synthesize(self, text, language, controls):
        self.calls += 1
        if self.fail_on and self.fail_on in text:
            raise ModelError("synthetic failure injected")
        sr = wavutil.OUT_SR
        tempo = controls.get("tempo", 1.0)
        amp = 0.1 + 0.5 * controls.get("energy", 0.7) * (1 + 0.4 * controls.get("emphasis", 0.0))
        out = []
        for wi, word in enumerate(text.split()):
            for si in range(max(1, (len(word) + 2) // 3)):
                dur = 0.17 / tempo
                t = np.arange(int(dur * sr)) / sr
                f = self.f0 * (1 + 0.15 * controls.get("pitch_variation", 0.5) * np.sin(2 * np.pi * 0.7 * (wi + si)))
                tone = sum(np.sin(2 * np.pi * f * k * t) / k for k in (1, 2, 3))
                env = np.sin(np.pi * t / dur) ** 2
                out.append((amp * 0.5 * tone * env).astype(np.float32))
                out.append(np.zeros(int(0.02 / tempo * sr), np.float32))
            out.append(np.zeros(int(0.06 / tempo * sr), np.float32))
        return (np.concatenate(out) if out else np.zeros(1, np.float32)), sr


class SapiModel(VoiceModel):
    """Windows SAPI (System.Speech) adapter — an OS voice, English only here. Tamil is NOT available."""
    adapter, version = "windows-sapi", "1.0"

    def __init__(self, voice_name: str = "Microsoft Zira Desktop", timeout: float = 60.0):
        self.voice_name, self.timeout = voice_name, timeout

    def _ps(self, script: str, **env):
        r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                           capture_output=True, timeout=self.timeout, env={**os.environ, **env})
        if r.returncode != 0:
            raise ModelError("SAPI failed: " + r.stderr.decode("utf-8", "replace")[:300])
        return r.stdout.decode("utf-8", "replace")

    def _voices(self):
        out = self._ps("Add-Type -AssemblyName System.Speech;"
                       "(New-Object System.Speech.Synthesis.SpeechSynthesizer).GetInstalledVoices()|"
                       "%{$_.VoiceInfo.Name+'|'+$_.VoiceInfo.Culture.Name}")
        return dict(l.strip().split("|") for l in out.splitlines() if "|" in l)

    def capabilities(self):
        langs = sorted({c for n, c in self._voices().items() if n == self.voice_name})
        return {"languages": langs, "identity": "OS_VOICE", "controls": ["tempo", "energy"],
                "unsupported_controls": ["pitch_variation", "emphasis(partial: rate/volume only)"],
                "natural_speech": True}

    def health_check(self):
        try:
            return {"ok": self.voice_name in self._voices(), "adapter": self.adapter, "voice": self.voice_name}
        except Exception as e:
            return {"ok": False, "adapter": self.adapter, "error": str(e)[:200]}

    def synthesize(self, text, language, controls):
        self.validate(language)
        rate = int(round(np.clip((controls.get("tempo", 1.0) - 1.0) * 10 / 0.5 * 1.0, -10, 10)))
        vol = int(round(np.clip(40 + 60 * controls.get("energy", 0.7) + 15 * controls.get("emphasis", 0.0), 0, 100)))
        with tempfile.TemporaryDirectory() as td:
            tp, wp = os.path.join(td, "t.txt"), os.path.join(td, "o.wav")
            with open(tp, "w", encoding="utf-8") as f:
                f.write(text)
            self._ps("Add-Type -AssemblyName System.Speech;$s=New-Object System.Speech.Synthesis.SpeechSynthesizer;"
                     "$s.SelectVoice($env:VG_VOICE);$s.Rate=[int]$env:VG_RATE;$s.Volume=[int]$env:VG_VOL;"
                     "$s.SetOutputToWaveFile($env:VG_OUT);$s.Speak([IO.File]::ReadAllText($env:VG_TXT,[Text.Encoding]::UTF8));"
                     "$s.Dispose()", VG_VOICE=self.voice_name, VG_RATE=str(rate), VG_VOL=str(vol), VG_OUT=wp, VG_TXT=tp)
            with open(wp, "rb") as f:
                return wavutil.read_wav(f.read())
