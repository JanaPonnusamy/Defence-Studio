"""Start the API + UI using the module's own configuration (VIDEO_MODULE_HOST / VIDEO_MODULE_PORT).

    .venv\\Scripts\\python.exe scripts\\serve.py            # http://127.0.0.1:8765/ui/
    .venv\\Scripts\\python.exe scripts\\serve.py --port 9000
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)


def main() -> None:
    import uvicorn

    from app.config import get_settings

    s = get_settings()
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default=s.host)
    ap.add_argument("--port", type=int, default=s.port)
    args = ap.parse_args()
    print(f"Video Visual Module -> http://{args.host}:{args.port}/ui/  (API docs: /docs)")
    uvicorn.run("app.main:app", host=args.host, port=args.port, log_level=s.log_level.lower())


if __name__ == "__main__":
    main()
