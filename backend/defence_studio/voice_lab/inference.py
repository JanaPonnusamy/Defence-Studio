"""Local inference backends. Real model only: there is no synthetic fallback here.

``ParlerBackend`` keeps one warm runner process (isolated venv) per application process,
serialised by a lock, restarted after a crash, shut down after an idle period.
Tests inject their own backend object through ``VoiceLabService(backends=...)``; nothing
in production code registers a fake.
"""
from __future__ import annotations

import json
import os
import subprocess
import threading
import time
from pathlib import Path

from defence_studio.voice_lab.common import VoiceLabError, hf_home, models_root, venv_python
from defence_studio.voice_lab.execution import runner_device

RUNNER = Path(__file__).resolve().parent / "runtime" / "parler_runner.py"


def model_dir(model: dict) -> Path:
    rev = (model.get("weights") or {}).get("revision") or "unpinned"
    return models_root() / f"{model['model_id']}@{rev[:12]}"


def install_status(model: dict) -> dict:
    d = model_dir(model)
    manifest = d / "VOICE_LAB_DOWNLOAD.json"
    weights = (d / "config.json").is_file() and any(d.glob("*.safetensors"))
    info = {"model_dir": str(d), "weights_present": weights, "venv_present": venv_python().is_file(),
            "download_manifest": None}
    if manifest.is_file():
        try:
            info["download_manifest"] = json.loads(manifest.read_text(encoding="utf-8"))
        except ValueError:
            info["download_manifest"] = {"error": "unreadable"}
    pinned = (info["download_manifest"] or {}).get("revision") == (model.get("weights") or {}).get("revision")
    info["installed"] = bool(weights and info["venv_present"] and pinned and model.get("runtime"))
    if weights and not pinned:
        info["problem"] = "weights present but download manifest does not match the pinned revision"
    return info


class ParlerBackend:
    runtime = "parler"

    def __init__(self, idle_seconds: float = 900, timeout_s: float = 1800):
        self._lock = threading.Lock()
        self._proc: subprocess.Popen | None = None
        self._last = 0.0
        self.idle_seconds = idle_seconds
        self.timeout_s = timeout_s
        self._log = None
        threading.Thread(target=self._idle_reaper, daemon=True, name="voice-lab-idle").start()

    def _idle_reaper(self):
        while True:
            time.sleep(30)
            with self._lock:
                if self._proc and time.time() - self._last > self.idle_seconds:
                    self._stop()

    def _stop(self):
        if self._proc:
            try:
                self._proc.stdin.write('{"op":"quit"}\n'); self._proc.stdin.flush()
                self._proc.wait(10)
            except Exception:
                self._proc.kill()
            self._proc = None

    def _start(self):
        py = venv_python()
        if not py.is_file():
            raise VoiceLabError(503, "Voice Lab inference environment is not installed (run scripts/voice_lab_install.ps1)")
        env = {k: v for k, v in os.environ.items() if not k.startswith(("DEFENCE_STUDIO_AUTH", "OPENAI"))}
        env.update(HF_HOME=str(hf_home()), HF_HUB_OFFLINE=env.get("HF_HUB_OFFLINE", "0"), PYTHONIOENCODING="utf-8",
                   TOKENIZERS_PARALLELISM="false", VOICE_LAB_DEVICE=runner_device())
        models_root().mkdir(parents=True, exist_ok=True)
        self._log = open(models_root() / "runner.log", "a", encoding="utf-8")
        self._proc = subprocess.Popen([str(py), "-I", str(RUNNER)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                      stderr=self._log, text=True, encoding="utf-8", env=env, bufsize=1)
        first = self._read(120)
        if not first.get("ready"):
            raise VoiceLabError(503, f"inference runner failed to start: {first}")

    def _read(self, timeout):
        result: dict = {}

        def rd():
            line = self._proc.stdout.readline()
            result["line"] = line
        t = threading.Thread(target=rd, daemon=True)
        t.start()
        t.join(timeout)
        if t.is_alive() or not result.get("line"):
            self._proc.kill()
            self._proc = None
            raise VoiceLabError(504, "inference runner timed out or exited (see models/voice_lab/runner.log)")
        return json.loads(result["line"])

    def _call(self, req, timeout):
        if self._proc is None or self._proc.poll() is not None:
            self._start()
        self._proc.stdin.write(json.dumps(req, ensure_ascii=False) + "\n")
        self._proc.stdin.flush()
        return self._read(timeout)

    def synthesize(self, model: dict, version_cfg: dict, text: str, out_wav: Path, seed: int) -> dict:
        status = install_status(model)
        if not status["installed"]:
            raise VoiceLabError(409, {"code": "MODEL_NOT_INSTALLED", "model_id": model["model_id"], "status": status})
        description = version_cfg.get("description")
        if not description:
            raise VoiceLabError(422, "model version config has no speaker description")
        with self._lock:
            self._last = time.time()
            loaded = self._call({"op": "load", "model_dir": status["model_dir"],
                                 "model_key": f"{model['model_id']}@{model['weights']['revision']}"}, self.timeout_s)
            if not loaded.get("ok"):
                raise VoiceLabError(500, f"model load failed: {loaded.get('error')}")
            res = self._call({"op": "synth", "text": text, "description": description, "seed": seed,
                              "out_wav": str(out_wav)}, self.timeout_s)
            self._last = time.time()
        if not res.get("ok"):
            raise VoiceLabError(500, f"synthesis failed: {res.get('error')}")
        res["load"] = loaded
        device = res.get("device") or loaded.get("device") or "cpu"
        res["execution"] = {"provider": "directml" if device == "directml" else "cpu",
                            "device": device, "fallback_reason": res.get("fallback_reason"),
                            "gpu_workload_verified": device == "directml" and not res.get("fallback_reason")}
        return res

    def shutdown(self):
        with self._lock:
            self._stop()
