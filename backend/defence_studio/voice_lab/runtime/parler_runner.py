"""Parler-TTS inference runner. Runs ONLY inside the isolated Voice Lab environment.

Started by ``voice_lab.inference`` as ``<venv python> -I parler_runner.py``. It never
imports Defence Studio code. Protocol: one JSON object per line on stdin, one JSON
reply per line on stdout. Logs go to stderr.

  {"op": "load", "model_dir": "...", "model_key": "..."}
  {"op": "synth", "text": "...", "description": "...", "seed": 1234, "out_wav": "..."}
  {"op": "ping"} / {"op": "quit"}

The model stays loaded between requests (no repeated loading).
"""
import json
import os
import sys
import time
import traceback


def reply(obj):
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def rss_bytes():
    try:
        import psutil
        return psutil.Process().memory_info().rss
    except Exception:
        return None


STATE = {"key": None, "model": None, "tok": None, "dtok": None, "sr": None}


def select_device(torch):
    """Use DirectML only after a matmul on that device succeeds. Otherwise CPU."""
    requested = os.environ.get("VOICE_LAB_DEVICE", "cpu")
    if requested != "directml":
        return "cpu", None
    try:
        import torch_directml
        index = int(os.environ.get("VOICE_LAB_DML_INDEX", "0"))
        device = torch_directml.device(index)
        sample = torch.ones(8, 8, device=device)
        got = float((sample @ sample)[0, 0].cpu())
        if abs(got - 8.0) > 1e-3:
            return "cpu", "DirectML matmul returned an unexpected value"
        return device, None
    except Exception as exc:
        return "cpu", f"{type(exc).__name__}: {exc}"[:300]


def load(model_dir, model_key):
    if STATE["key"] == model_key:
        return {"ok": True, "cached": True, "load_s": 0.0, "rss_bytes": rss_bytes(), "sample_rate": STATE["sr"],
                "device": STATE.get("device_name", "cpu"), "fallback_reason": STATE.get("fallback_reason")}
    import torch
    from parler_tts import ParlerTTSForConditionalGeneration
    from transformers import AutoTokenizer
    torch.set_num_threads(max(1, int(os.environ.get("VOICE_LAB_THREADS", os.cpu_count() or 1))))
    t = time.perf_counter()
    device, fallback = select_device(torch)
    model = ParlerTTSForConditionalGeneration.from_pretrained(model_dir).eval()
    try:
        model = model.to(device)
    except Exception as exc:
        device, fallback = "cpu", f"could not move model: {type(exc).__name__}: {exc}"[:300]
        model = model.to("cpu")
    STATE["device"] = device
    STATE["device_name"] = "directml" if device != "cpu" else "cpu"
    STATE["fallback_reason"] = fallback
    tok = AutoTokenizer.from_pretrained(model_dir)
    dname = model.config.text_encoder._name_or_path
    dtok = AutoTokenizer.from_pretrained(dname)
    STATE.update(key=model_key, model=model, tok=tok, dtok=dtok, sr=int(model.config.sampling_rate))
    return {"ok": True, "cached": False, "load_s": round(time.perf_counter() - t, 3), "rss_bytes": rss_bytes(),
            "sample_rate": STATE["sr"], "description_tokenizer": dname, "device": STATE["device_name"],
            "fallback_reason": STATE.get("fallback_reason"), "torch_threads": torch.get_num_threads()}


def synth(text, description, seed, out_wav):
    import numpy as np
    import soundfile as sf
    import torch
    if STATE["model"] is None:
        return {"ok": False, "error": "model not loaded"}
    torch.manual_seed(int(seed))
    device = STATE.get("device") or "cpu"
    d = STATE["dtok"](description, return_tensors="pt").to(device)
    p = STATE["tok"](text, return_tensors="pt").to(device)
    t = time.perf_counter()
    try:
        with torch.inference_mode():
            gen = STATE["model"].generate(input_ids=d.input_ids, attention_mask=d.attention_mask,
                                          prompt_input_ids=p.input_ids, prompt_attention_mask=p.attention_mask,
                                          do_sample=True)
        used = STATE.get("device_name", "cpu")
        fallback = STATE.get("fallback_reason")
    except Exception as exc:
        if device == "cpu":
            raise
        d = d.to("cpu")
        p = p.to("cpu")
        STATE["model"] = STATE["model"].to("cpu")
        STATE["device"] = "cpu"
        STATE["device_name"] = "cpu"
        fallback = f"DirectML operator failed, CPU used: {type(exc).__name__}: {exc}"[:300]
        STATE["fallback_reason"] = fallback
        with torch.inference_mode():
            gen = STATE["model"].generate(input_ids=d.input_ids, attention_mask=d.attention_mask,
                                          prompt_input_ids=p.input_ids, prompt_attention_mask=p.attention_mask,
                                          do_sample=True)
        used = "cpu"
    gen_s = time.perf_counter() - t
    audio = gen.cpu().numpy().squeeze().astype(np.float32)
    sf.write(out_wav, audio, STATE["sr"], subtype="PCM_16")
    dur = len(audio) / STATE["sr"]
    return {"ok": True, "gen_s": round(gen_s, 3), "audio_s": round(dur, 3),
            "rtf": round(gen_s / dur, 3) if dur > 0 else None, "samples": int(len(audio)),
            "sample_rate": STATE["sr"], "rss_bytes": rss_bytes(), "prompt_tokens": int(p.input_ids.shape[-1]),
            "device": used, "fallback_reason": fallback}


def main():
    reply({"ok": True, "ready": True, "pid": os.getpid(), "python": sys.version.split()[0]})
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
            op = req.get("op")
            if op == "quit":
                reply({"ok": True, "bye": True})
                return
            if op == "ping":
                reply({"ok": True, "loaded": STATE["key"], "rss_bytes": rss_bytes()})
            elif op == "load":
                reply(load(req["model_dir"], req["model_key"]))
            elif op == "synth":
                reply(synth(req["text"], req["description"], req.get("seed", 0), req["out_wav"]))
            else:
                reply({"ok": False, "error": f"unknown op {op!r}"})
        except Exception as exc:  # report, keep serving
            traceback.print_exc(file=sys.stderr)
            reply({"ok": False, "error": f"{type(exc).__name__}: {exc}"[:2000]})


if __name__ == "__main__":
    main()
