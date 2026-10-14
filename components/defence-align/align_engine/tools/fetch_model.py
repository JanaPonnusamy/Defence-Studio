"""Explicit, operator-run forced-alignment model acquisition.

This is the **only** place in the component that downloads a model, and it runs
**only** when an operator invokes it directly:

    python -m align_engine.tools.fetch_model --out /abs/path/forced_align_model.pt

Nothing here executes at package import or at API startup — importing this
module is side-effect-free and does not require torch. The download happens
inside ``main()`` when the operator runs the command, satisfying the rule that
model acquisition is an explicit operator action, never automatic.

It writes two files the real adapter consumes:

* ``<out>``            — the acoustic model object (``torch.save``).
* ``<out>.meta.json``  — ``{"dictionary", "sample_rate", "blank_id",
                           "engine_source", "license"}`` used by the adapter's
                           tokenizer and frame→second conversion.

LICENSE / PROVENANCE: the default source is torchaudio's bundled MMS-FA
forced-aligner, derived from Meta's *Massively Multilingual Speech* (MMS) and
distributed under **CC-BY-NC 4.0 (non-commercial)**. The command prints this
and requires ``--i-accept-model-license`` so acquisition is a conscious,
documented operator decision. For commercial use the operator must supply a
differently-licensed model and its own metadata sidecar.
"""
from __future__ import annotations

import argparse
import json
import sys

_MMS_FA_LICENSE = "CC-BY-NC 4.0 (non-commercial) - Meta MMS / torchaudio MMS_FA"


def _build_mms_fa():
    """Fetch torchaudio's MMS-FA bundle (network) and return (model, meta).

    Imported lazily so this module imports without torch present.
    """
    import torchaudio.pipelines as pipelines

    bundle = pipelines.MMS_FA
    model = bundle.get_model()               # <-- network download happens here
    model.eval()
    token_dict = bundle.get_dict()           # local: char -> index mapping
    dictionary = {str(k): int(v) for k, v in token_dict.items()}
    sample_rate = int(getattr(bundle, "sample_rate", 16000))
    meta = {
        "dictionary": dictionary,
        "sample_rate": sample_rate,
        "blank_id": 0,
        "engine_source": "torchaudio.pipelines.MMS_FA",
        "license": _MMS_FA_LICENSE,
    }
    return model, meta


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="align_engine.tools.fetch_model",
        description="Operator-run, one-time forced-alignment model acquisition. "
                    "Downloads the acoustic model and writes it plus a metadata "
                    "sidecar for the real alignment adapter.",
    )
    parser.add_argument("--out", required=True, help="Destination model file path (e.g. forced_align_model.pt).")
    parser.add_argument("--source", default="mms_fa", choices=("mms_fa",),
                        help="Model source to fetch (default: mms_fa).")
    parser.add_argument("--i-accept-model-license", action="store_true",
                        help=f"Required acknowledgement that you accept the model license ({_MMS_FA_LICENSE}).")
    args = parser.parse_args(argv)

    print(f"[fetch_model] source={args.source} license={_MMS_FA_LICENSE}", file=sys.stderr)
    if not args.i_accept_model_license:
        print(
            "[fetch_model] Refusing to download: re-run with --i-accept-model-license "
            "to acknowledge the model license above. Model acquisition is an explicit "
            "operator decision; nothing was downloaded.",
            file=sys.stderr,
        )
        return 2

    try:
        import torch
    except Exception as exc:  # pragma: no cover - environment dependent
        print(f"[fetch_model] torch/torchaudio not installed: {exc}. "
              "Install with: pip install -e '.[real]'", file=sys.stderr)
        return 3

    print("[fetch_model] fetching model (this performs a network download)...", file=sys.stderr)
    model, meta = _build_mms_fa()

    torch.save(model, args.out)
    meta_path = args.out + ".meta.json"
    with open(meta_path, "w", encoding="utf-8") as handle:
        json.dump(meta, handle, ensure_ascii=False, indent=2)

    print(f"[fetch_model] wrote model     -> {args.out}", file=sys.stderr)
    print(f"[fetch_model] wrote metadata  -> {meta_path}", file=sys.stderr)
    print(f"[fetch_model] set DEFENCE_ALIGN_FORCED_MODEL_PATH={args.out} to enable the real engine.",
          file=sys.stderr)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
