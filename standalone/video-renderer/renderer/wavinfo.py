"""Minimal RIFF/WAVE header parser (stdlib only).

Handles PCM (1), IEEE float (3) and WAVE_FORMAT_EXTENSIBLE (0xFFFE). Returns the exact
duration from the data-chunk size, which is what the narration timeline is measured in.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class WavInfo:
    format_tag: int
    channels: int
    sample_rate: int
    bits_per_sample: int
    data_bytes: int

    @property
    def frames(self) -> int:
        block = self.channels * self.bits_per_sample // 8
        return self.data_bytes // block if block else 0

    @property
    def duration(self) -> float:
        return self.frames / self.sample_rate if self.sample_rate else 0.0


class WavError(ValueError):
    pass


def read_wav_info(path: Path) -> WavInfo:
    size = path.stat().st_size
    with open(path, "rb") as f:
        head = f.read(12)
        if len(head) < 12 or head[:4] != b"RIFF" or head[8:12] != b"WAVE":
            raise WavError("not a RIFF/WAVE file")
        fmt = None
        while True:
            ch = f.read(8)
            if len(ch) < 8:
                break
            cid, clen = ch[:4], struct.unpack("<I", ch[4:])[0]
            if cid == b"fmt ":
                body = f.read(clen)
                if len(body) < 16:
                    raise WavError("truncated fmt chunk")
                tag, channels, rate, _, _, bits = struct.unpack("<HHIIHH", body[:16])
                if tag == 0xFFFE and len(body) >= 26:
                    tag = struct.unpack("<H", body[24:26])[0]
                fmt = (tag, channels, rate, bits)
            elif cid == b"data":
                if fmt is None:
                    raise WavError("data chunk before fmt chunk")
                # some writers leave 0/0xFFFFFFFF for streamed files: fall back to remaining bytes
                remaining = size - f.tell()
                data_bytes = clen if 0 < clen <= remaining else remaining
                tag, channels, rate, bits = fmt
                if tag not in (1, 3):
                    raise WavError(f"unsupported WAV format tag {tag} (PCM or IEEE float only)")
                if channels < 1 or rate < 8000 or bits not in (8, 16, 24, 32, 64):
                    raise WavError(f"unsupported WAV parameters ch={channels} rate={rate} bits={bits}")
                return WavInfo(tag, channels, rate, bits, data_bytes)
            else:
                f.seek(clen + (clen & 1), 1)
        raise WavError("no fmt/data chunk found")
