"""Real forced-alignment adapter — gated, honest, never faked.

Selected engine: **torchaudio's CTC forced-alignment API**
(``torchaudio.functional.forced_align`` + ``merge_tokens``) driving a
CPU-capable Wav2Vec2 / MMS-FA acoustic model. Rationale:

- CPU-compatible: forced alignment of a *known* transcript is light enough to
  run on CPU; no GPU is required (GPU stays optional).
- Offline after a one-time, operator-initiated model fetch — nothing is
  downloaded automatically at import or application startup.
- Word/token-level granularity: every word's span comes from the genuine CTC
  frame alignment of its characters, converted to seconds against the *measured*
  audio duration. Timings are **never** produced by evenly dividing duration
  across the transcript.
- Broad language coverage: English is well supported; Tamil is *declared* via
  the multilingual model + romanization but is listed under ``tested_languages``
  only once actually verified on a device — this adapter never claims tested
  Tamil capability it has not demonstrated.

Licensing / provenance (operator must verify): torchaudio's bundled MMS-FA
forced-alignment model is derived from Meta's *Massively Multilingual Speech*
(MMS) project and is distributed under **CC-BY-NC 4.0 (non-commercial)**. That
restriction may be incompatible with a commercial pipeline; the operator is
responsible for selecting and licensing an acceptable model. This adapter loads
whatever local model bundle the operator provisions and asserts nothing about
its license.

torch/torchaudio are imported **lazily** and only when actually needed, so the
package (and the API) import and run with the real backend absent. When the
backend or model is unavailable, ``capabilities().available`` is ``False`` and
``align`` raises ``UnsupportedCapabilityError`` — it NEVER returns fabricated
timings. See README "Enabling real alignment" for installation/configuration
and ``align_engine.tools.fetch_model`` for the explicit operator model fetch.
"""
from __future__ import annotations

import json
import os
import threading

from align_engine.engines.base import (
    AlignmentEngine, EngineAlignResult, EngineCapabilities, EngineTokenInput,
    EngineTokenResult,
)
from align_engine.errors import (
    AlignmentOutputInvalidError, UnsupportedCapabilityError,
)

ENGINE_NAME = "torchaudio-forced-aligner"
ENGINE_VERSION = "mms-fa/ctc-0.1"

# The operator points this at a locally available model bundle. We never fetch
# it; if unset or missing, the adapter reports unavailable. The bundle is the
# model object saved with ``torch.save`` plus a ``<path>.meta.json`` sidecar
# holding the tokenizer dictionary, sample rate and blank id (see fetch_model).
_MODEL_ENV = "DEFENCE_ALIGN_FORCED_MODEL_PATH"

# A word whose averaged per-character alignment probability falls below this is
# flagged ``is_uncertain`` so downstream review can see low-confidence spans.
# This is a label on genuine engine-provided scores, not an invented value.
_UNCERTAIN_BELOW = 0.30


class RealForcedAligner(AlignmentEngine):
    name = ENGINE_NAME
    version = ENGINE_VERSION

    def __init__(self, model_path: str | None = None):
        self._model_path = model_path or os.environ.get(_MODEL_ENV, "")
        self._lock = threading.Lock()
        self._cached: tuple[object, dict] | None = None

    # -- availability (no network, no auto-download) -----------------------

    def _backend_present(self) -> bool:
        try:
            import importlib.util
            return bool(
                importlib.util.find_spec("torch")
                and importlib.util.find_spec("torchaudio")
            )
        except Exception:
            return False

    def _meta_path(self) -> str:
        return self._model_path + ".meta.json"

    def _model_present(self) -> bool:
        return (
            bool(self._model_path)
            and os.path.isfile(self._model_path)
            and os.path.isfile(self._meta_path())
        )

    def _available(self) -> bool:
        # Requires BOTH the libraries AND a locally present model bundle. This
        # is deliberately conservative: a declared code is not a tested one.
        return self._backend_present() and self._model_present()

    def capabilities(self) -> EngineCapabilities:
        available = self._available()
        reasons = []
        if not self._backend_present():
            reasons.append("torch/torchaudio not installed (pip install 'defence-align[real]')")
        if not (bool(self._model_path) and os.path.isfile(self._model_path)):
            reasons.append(f"forced-alignment model not found; set {_MODEL_ENV} to a local model file")
        elif not os.path.isfile(self._meta_path()):
            reasons.append(f"model metadata sidecar not found next to the model ({self._meta_path()})")
        notes = "Real CPU forced aligner." if available else "Unavailable: " + "; ".join(reasons)
        return EngineCapabilities(
            engine_name=ENGINE_NAME, engine_version=ENGINE_VERSION,
            available=available, is_synthetic=False,
            declared_languages=("en", "ta"),
            # Intentionally empty until real-device verification is performed:
            # accepting a language code is not the same as tested capability.
            tested_languages=(),
            audio_formats=("wav",), granularities=("word",),
            requires_model=True, notes=notes,
        )

    # -- model/audio seams (lazily import torch; monkeypatchable in tests) ---

    def _load_model_bundle(self) -> tuple[object, dict]:
        """Load the operator-provisioned model object and its metadata sidecar.

        The sidecar is validated *before* torch is imported, so a malformed or
        missing sidecar fails fast and testably without the backend present.
        """
        meta_path = self._meta_path()
        if not os.path.isfile(meta_path):
            raise UnsupportedCapabilityError(
                f"Model metadata sidecar not found: {meta_path}. "
                "Re-provision with 'python -m align_engine.tools.fetch_model'."
            )
        try:
            with open(meta_path, "r", encoding="utf-8") as handle:
                raw = json.load(handle)
        except (OSError, ValueError) as exc:
            raise UnsupportedCapabilityError(f"Model metadata sidecar is unreadable: {exc}") from exc
        meta = self._validate_meta(raw)

        with self._lock:
            if self._cached is not None:
                return self._cached
            import torch  # lazy: only when a real run is actually requested
            try:
                model = torch.load(self._model_path, map_location="cpu", weights_only=False)
            except Exception as exc:  # malformed / incompatible model file
                raise UnsupportedCapabilityError(
                    f"Forced-alignment model could not be loaded from {self._model_path!r}: {exc}"
                ) from exc
            if hasattr(model, "eval"):
                model.eval()
            self._cached = (model, meta)
            return self._cached

    @staticmethod
    def _validate_meta(raw: object) -> dict:
        if not isinstance(raw, dict):
            raise UnsupportedCapabilityError("Model metadata sidecar is not a JSON object.")
        dictionary = raw.get("dictionary")
        sample_rate = raw.get("sample_rate")
        blank_id = raw.get("blank_id", 0)
        if not isinstance(dictionary, dict) or not dictionary:
            raise UnsupportedCapabilityError("Model metadata sidecar has no non-empty 'dictionary'.")
        if not isinstance(sample_rate, int) or sample_rate <= 0:
            raise UnsupportedCapabilityError("Model metadata sidecar has an invalid 'sample_rate'.")
        try:
            token_map = {str(k): int(v) for k, v in dictionary.items()}
        except (TypeError, ValueError) as exc:
            raise UnsupportedCapabilityError(f"Model metadata 'dictionary' is malformed: {exc}") from exc
        if not isinstance(blank_id, int) or blank_id < 0:
            raise UnsupportedCapabilityError("Model metadata 'blank_id' must be a non-negative int.")
        return {"dictionary": token_map, "sample_rate": sample_rate, "blank_id": int(blank_id)}

    def _decode_audio(self, audio_path, target_sample_rate: int):
        """Decode audio to a mono waveform tensor at the model's sample rate."""
        import torch  # noqa: F401  (ensures torch is present for the tensor ops)
        import torchaudio
        waveform, sample_rate = torchaudio.load(str(audio_path))
        if waveform.shape[0] > 1:  # downmix to mono
            waveform = waveform.mean(dim=0, keepdim=True)
        if sample_rate != target_sample_rate:
            waveform = torchaudio.functional.resample(waveform, sample_rate, target_sample_rate)
        return waveform

    def _emissions(self, model, waveform):
        """Run the acoustic model → frame log-probabilities, shape [1, T, C]."""
        import torch
        with torch.inference_mode():
            out = model(waveform)
        emission = out[0] if isinstance(out, (tuple, list)) else out
        if emission.dim() == 2:  # [T, C] -> [1, T, C]
            emission = emission.unsqueeze(0)
        return emission

    # -- alignment ---------------------------------------------------------

    def align(self, *, audio_path, duration_seconds, language, tokens: list[EngineTokenInput],
              requested_granularity, cancel: threading.Event | None = None) -> EngineAlignResult:
        if not self._available():
            raise UnsupportedCapabilityError(
                "Real forced alignment is not available in this environment. "
                + self.capabilities().notes
            )
        if cancel is not None and cancel.is_set():
            raise UnsupportedCapabilityError("Alignment cancelled before it started.")

        model, meta = self._load_model_bundle()
        waveform = self._decode_audio(audio_path, meta["sample_rate"])

        if cancel is not None and cancel.is_set():
            raise UnsupportedCapabilityError("Alignment cancelled before inference.")

        log_probs = self._emissions(model, waveform)
        num_frames = int(log_probs.shape[1])
        if num_frames <= 0:
            raise AlignmentOutputInvalidError("Acoustic model produced zero frames; cannot align.")
        if duration_seconds <= 0:
            raise AlignmentOutputInvalidError("Measured audio duration is non-positive; cannot align.")
        sec_per_frame = float(duration_seconds) / num_frames

        return self._align_tokens(
            log_probs=log_probs, token_inputs=tokens, dictionary=meta["dictionary"],
            blank_id=meta["blank_id"], sec_per_frame=sec_per_frame, cancel=cancel,
        )

    def _word_token_ids(self, normalized_text: str, dictionary: dict, blank_id: int) -> list[int]:
        """Map a word's characters to in-vocabulary, non-blank token ids.

        Characters absent from the model vocabulary (or mapping to the CTC blank)
        are dropped from the alignment target for that word; a word left with no
        in-vocabulary character is reported unalignable rather than guessed.
        """
        ids: list[int] = []
        for ch in normalized_text:
            tok = dictionary.get(ch)
            if tok is None:
                tok = dictionary.get(ch.lower())
            if tok is None or tok == blank_id:
                continue
            ids.append(int(tok))
        return ids

    def _align_tokens(self, *, log_probs, token_inputs, dictionary, blank_id,
                      sec_per_frame, cancel: threading.Event | None = None) -> EngineAlignResult:
        import torch
        import torchaudio.functional as AF

        num_frames = int(log_probs.shape[1])
        targets: list[int] = []
        word_ranges: list[tuple[EngineTokenInput, int, int]] = []
        skipped: list[tuple[EngineTokenInput, str]] = []
        for ti in token_inputs:
            ids = self._word_token_ids(ti.normalized_text, dictionary, blank_id)
            if not ids:
                skipped.append((ti, "no in-vocabulary characters for the model tokenizer"))
                continue
            start = len(targets)
            targets.extend(ids)
            word_ranges.append((ti, start, len(targets)))

        if not targets:
            raise UnsupportedCapabilityError(
                "No transcript token mapped to the model vocabulary; refusing to "
                "fabricate timings for an all-out-of-vocabulary transcript."
            )
        if len(targets) > num_frames:
            raise UnsupportedCapabilityError(
                f"Audio is too short for the transcript: {len(targets)} alignment tokens "
                f"but only {num_frames} acoustic frames — insufficient evidence to align."
            )
        if cancel is not None and cancel.is_set():
            raise UnsupportedCapabilityError("Alignment cancelled before forced alignment.")

        targets_t = torch.tensor([targets], dtype=torch.int32)
        try:
            aligned, scores = AF.forced_align(log_probs, targets_t, blank=blank_id)
            spans = AF.merge_tokens(aligned[0], scores[0].exp(), blank=blank_id)
        except Exception as exc:
            raise AlignmentOutputInvalidError(
                f"Forced alignment failed inside the acoustic model backend: {exc}"
            ) from exc

        if len(spans) != len(targets):
            raise AlignmentOutputInvalidError(
                f"Backend returned {len(spans)} token spans for {len(targets)} target tokens; "
                "refusing to emit an untrustworthy alignment."
            )

        results: list[EngineTokenResult] = []
        for ti, lo, hi in word_ranges:
            word_spans = spans[lo:hi]
            start_frame = int(word_spans[0].start)
            end_frame = int(word_spans[-1].end)
            conf = sum(float(sp.score) for sp in word_spans) / len(word_spans)
            conf = max(0.0, min(1.0, conf))
            start_s = max(0.0, start_frame * sec_per_frame)
            end_s = min(num_frames * sec_per_frame, end_frame * sec_per_frame)
            results.append(EngineTokenResult(
                token_id=ti.token_id, aligned=True,
                start=round(start_s, 3), end=round(end_s, 3),
                confidence=round(conf, 4), is_uncertain=conf < _UNCERTAIN_BELOW, kind="word",
            ))
        for ti, reason in skipped:
            results.append(EngineTokenResult(
                token_id=ti.token_id, aligned=False, reason=reason, kind="word",
            ))
        return EngineAlignResult(actual_granularity="word", tokens=results)
