"""Run focused emotion-handoff tests. Absolute evidence paths; capture each pytest exit immediately."""
from __future__ import annotations

import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(r"E:\Jana Soft\DefenceStudio")
EVIDENCE = REPO / "AGENT_WORK" / "CODEX_EVIDENCE" / "p1-pipeline-20261015"
COMPONENT = REPO / "components" / "voice-engine"
BACKEND = REPO / "backend"
LOG = EVIDENCE / "focused-emotion-tests.log"
EXIT_FILE = EVIDENCE / "focused-emotion-tests.exit"
DIFF_LOG = EVIDENCE / "diff-check-emotion.log"
DIFF_EXIT = EVIDENCE / "diff-check-emotion.exit"


def run_pytest(cwd: Path, tests: list[str], log) -> int:
    cmd = [sys.executable, "-m", "pytest", "-q", "-ra", "-p", "no:cacheprovider", *tests]
    log.write(f"\n# cwd={cwd}\n# cmd={' '.join(cmd)}\n")
    log.flush()
    proc = subprocess.run(cmd, cwd=str(cwd), stdout=log, stderr=subprocess.STDOUT, text=True)
    log.write(f"# pytest_exit={proc.returncode}\n")
    log.flush()
    return proc.returncode


def main() -> int:
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    with LOG.open("w", encoding="utf-8") as log:
        log.write(f"# started={datetime.now(timezone.utc).isoformat()}\n# python={sys.executable}\n")
        gen_exit = run_pytest(COMPONENT, ["tests/test_generation.py"], log)
        profile_exit = run_pytest(
            BACKEND,
            [
                "tests/test_voice_profile_application.py::test_unsupported_characteristics_are_disclosed_not_applied",
                "tests/test_voice_profile_application.py::test_provider_that_ignores_controls_is_not_reported_as_applied",
            ],
            log,
        )
        combined = gen_exit if gen_exit != 0 else profile_exit
        log.write(f"\n# generation_tests_exit={gen_exit}\n# profile_tests_exit={profile_exit}\n# combined_exit={combined}\n")
    EXIT_FILE.write_text(str(combined), encoding="utf-8")

    with DIFF_LOG.open("w", encoding="utf-8") as log:
        diff = subprocess.run(
            ["git", "-C", str(REPO), "diff", "--check"],
            stdout=log, stderr=subprocess.STDOUT, text=True,
        )
        log.write(f"\n# git_diff_check_exit={diff.returncode}\n")
    DIFF_EXIT.write_text(str(diff.returncode), encoding="utf-8")
    print(f"generation_tests_exit={gen_exit} profile_tests_exit={profile_exit} combined={combined}")
    print(f"git_diff_check_exit={diff.returncode}")
    print(f"log={LOG}")
    return combined


if __name__ == "__main__":
    sys.exit(main())
