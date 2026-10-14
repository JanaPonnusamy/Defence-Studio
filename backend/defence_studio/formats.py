"""Output format presets (RM-10).

A preset only sets the *output profile* and the QC duration limits; every
pipeline stage, contract and artifact is the same as for the primary
16:9 defence-news video, so new formats reuse the persistent job system,
evidence, assets, audio, renderer, QC and export framework unchanged.
"""
from __future__ import annotations

FORMATS = {
    "landscape_1080p": {"label": "YouTube video, 16:9 1080p", "width": 1920, "height": 1080, "fps": 30,
                        "qc": {"min_seconds": 5.0, "max_seconds": 3600.0}, "short_form": False},
    "landscape_720p": {"label": "YouTube video, 16:9 720p", "width": 1280, "height": 720, "fps": 25,
                       "qc": {"min_seconds": 5.0, "max_seconds": 3600.0}, "short_form": False},
    "vertical_short": {"label": "Shorts / Reels, 9:16", "width": 1080, "height": 1920, "fps": 30,
                       "qc": {"min_seconds": 3.0, "max_seconds": 60.0, "max_cue_chars": 70}, "short_form": True},
    "vertical_short_720": {"label": "Shorts / Reels, 9:16 (720p)", "width": 720, "height": 1280, "fps": 25,
                           "qc": {"min_seconds": 3.0, "max_seconds": 60.0, "max_cue_chars": 70}, "short_form": True},
    "square": {"label": "Square 1:1", "width": 1080, "height": 1080, "fps": 30,
               "qc": {"min_seconds": 3.0, "max_seconds": 600.0}, "short_form": False},
}
DEFAULT_FORMAT = "landscape_1080p"


class UnknownFormat(ValueError):
    pass


def get(name: str | None) -> dict:
    key = name or DEFAULT_FORMAT
    if key not in FORMATS:
        raise UnknownFormat(f"Unknown format {key!r}; available: {sorted(FORMATS)}")
    return {"name": key, **FORMATS[key]}


def visual_options(name: str | None) -> dict:
    f = get(name)
    return {"output_width": f["width"], "output_height": f["height"], "frame_rate": f["fps"]}


def qc_requirements(name: str | None) -> dict:
    return dict(get(name)["qc"])


def listing() -> list[dict]:
    return [{"name": k, **v} for k, v in FORMATS.items()]
