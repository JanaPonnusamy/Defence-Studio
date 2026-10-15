"""Replaceable alignment adapter interface.

Accuracy classes (never mix them):
  MEASURED_RENDER_BOUNDARY  sentence span measured from the rendered waveform
  MODEL_DERIVED             produced by a forced-alignment model run on the audio
  ESTIMATED_PROPORTIONAL    words spread by character count (an estimate)
  UNAVAILABLE               nothing could be produced
"""
from __future__ import annotations
import abc, os, sys


class AlignerUnavailable(Exception):
    pass


class Aligner(abc.ABC):
    name = "abstract"
    version = "0"

    @abc.abstractmethod
    def status(self) -> dict:
        """{'available': bool, 'languages': [...], 'reason': str, 'license_note': str}"""

    @abc.abstractmethod
    def align(self, audio_path: str, sentences: list[str], language: str) -> dict:
        """Return {'segments': [...], 'words': [...]} with an 'accuracy' on every item,
        or raise AlignerUnavailable. Must never return requested timings as measured."""


class DefenceAlignAdapter(Aligner):
    """Adapter slot for components/defence-align (CTC forced aligner).

    Only availability is probed here. The run path is intentionally not wired until a
    licensed model is installed and Tamil accuracy is verified on real narration."""
    name, version = "defence-align", "slot-1"

    def __init__(self, component_path: str | None = None):
        self.component_path = component_path

    def status(self) -> dict:
        base = {"available": False, "languages": [], "license_note":
                "MMS-FA bundled weights are CC-BY-NC 4.0 (non-commercial); needs an approved licence."}
        try:
            import torchaudio  # noqa: F401
        except Exception:
            return {**base, "reason": "torch/torchaudio not installed"}
        return {**base, "reason": "torchaudio present but no verified licensed model bundle or Tamil accuracy evidence"}

    def align(self, audio_path, sentences, language):
        raise AlignerUnavailable(self.status()["reason"])
