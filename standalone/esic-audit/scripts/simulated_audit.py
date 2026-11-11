"""Run one complete audit against the SYNTHETIC fixture portal and print where the outputs are.

    .venv\\Scripts\\python scripts\\simulated_audit.py [storage_dir]

Uses headless Chrome, a throw-away storage folder (default storage/simulation, git-ignored)
and invented data. Nothing touches the real ESIC portal. The test stand-in for the
employer types the fake login; the module itself never does.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.config import load_settings  # noqa: E402
from app.orchestrator import AuditOrchestrator  # noqa: E402
from app.repository import Repository  # noqa: E402
from tests.fixtures.portal_server import FixturePortal  # noqa: E402
from tests.test_esic_browser_e2e import CHANNEL, HumanSession  # noqa: E402


def main() -> int:
    storage = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "storage" / "simulation"
    with FixturePortal() as portal:
        settings = load_settings(storage_dir=storage, login_url=portal.login_url, headless=True,
                                 browser_channel=None if CHANNEL == "bundled" else CHANNEL)
        repo = Repository(settings.db_path)

        def factory(on_event):
            s = HumanSession(settings.login_url, settings.allowed_host_suffixes, settings.browser_channel, settings.headless, on_event)
            s.base = portal.base
            return s

        orch = AuditOrchestrator(settings, repo, session_factory=factory)
        run = orch.start()
        uuid = run["audit_uuid"]

        def wait(pred, timeout=90):
            end = time.monotonic() + timeout
            while time.monotonic() < end:
                r = repo.get_run(uuid)
                if pred(r):
                    return r
                time.sleep(0.3)
            raise SystemExit(f"timeout at {repo.get_run(uuid)['status']}")

        wait(lambda r: r["status"] == "WAITING_FOR_LOGIN" and "Log in" in (r["current_activity"] or ""))
        orch.command(uuid, "confirm_login")
        wait(lambda r: any(s["stage"] == "ASSISTED_CAPTURE" and s["outcome"] == "WAITING" for s in repo.stages(r["id"])))
        orch.command(uuid, "capture", category="damages", page_name="Damages Orders")
        wait(lambda r: any(s["stage"] == "CAPTURE:damages" for s in repo.stages(r["id"])))
        orch.command(uuid, "finish")
        orch.wait(120)
        final = repo.get_run(uuid)
        print(f"audit {uuid}: {final['status']}  (forbidden portal hits: {len(portal.state.forbidden_hits)})")
        for rep in repo.reports(final["id"]):
            print(f"  {rep['report_type']}: {rep['file_path']}  sha256={rep['checksum'][:16]}...")
        print(f"  access token for the UI: {settings.storage_dir / 'access_token.txt'}")
        return 0 if final["status"] in ("COMPLETED", "PARTIAL") and not portal.state.forbidden_hits else 1


if __name__ == "__main__":
    raise SystemExit(main())
