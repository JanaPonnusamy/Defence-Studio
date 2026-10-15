import subprocess, wave, os
import numpy as np

SR = 16000


def tone_phrase(n_syl, f0=200.0, amp=0.3, syl=0.18, gap=0.04, rise=0.0):
    out = []
    for i in range(n_syl):
        t = np.arange(int(syl * SR)) / SR
        f = f0 * (1 + rise * i / max(1, n_syl - 1))
        s = sum(np.sin(2 * np.pi * f * k * t) / k for k in (1, 2, 3)) * np.sin(np.pi * t / syl) ** 2
        out += [amp * 0.5 * s, np.zeros(int(gap * SR))]
    return np.concatenate(out)


def build(phrases, pauses_ms, lead_ms=300, tail_ms=300):
    parts = [np.zeros(int(lead_ms / 1000 * SR))]
    for i, p in enumerate(phrases):
        parts.append(p)
        parts.append(np.zeros(int((pauses_ms[i] if i < len(pauses_ms) else tail_ms) / 1000 * SR)))
    return np.concatenate(parts).astype(np.float32)


def write_wav(path, x, sr=SR):
    with wave.open(path, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(sr)
        w.writeframes((np.clip(x, -1, 1) * 32767).astype("<i2").tobytes())
    return path


def speechlike(path, noise=0.0, seed=0):
    rng = np.random.default_rng(seed)
    ph = [tone_phrase(8, 190), tone_phrase(8, 230, amp=0.5), tone_phrase(12, 200, syl=0.12), tone_phrase(7, 210, rise=0.3)]
    x = build(ph, [200, 700, 400, 300])
    if noise:
        x = x + rng.normal(0, noise, len(x)).astype(np.float32)
    return write_wav(path, x)


def convert(src, dst):
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", src, dst], check=True)
    return dst
