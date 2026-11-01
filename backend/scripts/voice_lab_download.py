"""Download one Voice Lab model at its PINNED revision into the managed model directory.

Run with the isolated environment's Python (it needs huggingface_hub):

    .venv-voicelab\\Scripts\\python.exe -I backend\\scripts\\voice_lab_download.py <model_id> [--config PATH] [--dest DIR]

* The revision comes from backend/config/voice_lab_models.json; it is never "main".
* A gated model needs the owner to have accepted its terms and to provide HF_TOKEN in
  the environment. The token is never printed, logged or written anywhere.
* On success it writes VOICE_LAB_DOWNLOAD.json (repo, revision, file hashes, licence files).
"""
import argparse
import hashlib
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def sha256(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("model_id")
    ap.add_argument("--config", default=str(ROOT / "backend" / "config" / "voice_lab_models.json"))
    ap.add_argument("--dest", default=os.environ.get("DEFENCE_STUDIO_VOICE_LAB_MODELS") or str(ROOT / "models" / "voice_lab"))
    a = ap.parse_args()
    models = {m["model_id"]: m for m in json.loads(Path(a.config).read_text(encoding="utf-8"))["models"]}
    m = models.get(a.model_id)
    if not m:
        sys.exit(f"unknown model_id {a.model_id}")
    w = m["weights"]
    if not w.get("revision") or "/" not in (w.get("repo") or ""):
        sys.exit(f"{a.model_id} has no pinned Hugging Face revision; refusing to download")
    if m["commercial_status"] == "NOT_ELIGIBLE":
        print(f"NOTE: {a.model_id} is NOT_ELIGIBLE for commercial use; download is for evaluation only.")
    token = os.environ.get("HF_TOKEN") or os.environ.get("HUGGING_FACE_HUB_TOKEN")
    if w.get("gated") and not token:
        sys.exit(json.dumps({"status": "BLOCKED", "code": "HF_TOKEN_MISSING",
                             "detail": f"{w['repo']} is gated: accept its terms on huggingface.co with your account, "
                                       "then set HF_TOKEN in your environment (never paste it into chat or code)."}))
    os.environ.setdefault("HF_HOME", str(Path(a.dest) / "hf"))
    from huggingface_hub import snapshot_download
    from huggingface_hub.utils import GatedRepoError, RepositoryNotFoundError
    dest = Path(a.dest) / f"{a.model_id}@{w['revision'][:12]}"
    t = time.time()
    try:
        snapshot_download(repo_id=w["repo"], revision=w["revision"], local_dir=str(dest), token=token or False,
                          allow_patterns=["*.json", "*.safetensors", "*.model", "*.txt", "*.md", "LICENSE*", "*.py"])
    except GatedRepoError:
        sys.exit(json.dumps({"status": "BLOCKED", "code": "GATED_ACCESS_DENIED",
                             "detail": "the token's account has not been granted access; accept the model terms first"}))
    except RepositoryNotFoundError:
        sys.exit(json.dumps({"status": "BLOCKED", "code": "NOT_FOUND_OR_NO_ACCESS"}))
    files = sorted(p for p in dest.rglob("*") if p.is_file() and ".cache" not in p.parts and p.name != "VOICE_LAB_DOWNLOAD.json")
    manifest = {"model_id": a.model_id, "repo": w["repo"], "revision": w["revision"], "weights_licence": w["licence"],
                "code_commit": m["code"]["commit"], "downloaded_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "seconds": round(time.time() - t, 1),
                "files": [{"path": p.relative_to(dest).as_posix(), "bytes": p.stat().st_size, "sha256": sha256(p)} for p in files],
                "licence_files": [p.relative_to(dest).as_posix() for p in files if p.name.upper().startswith(("LICENSE", "README"))]}
    manifest["total_bytes"] = sum(f["bytes"] for f in manifest["files"])
    (dest / "VOICE_LAB_DOWNLOAD.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    print(json.dumps({"status": "OK", "model_dir": str(dest), "files": len(files), "total_bytes": manifest["total_bytes"],
                      "seconds": manifest["seconds"]}))


if __name__ == "__main__":
    main()
