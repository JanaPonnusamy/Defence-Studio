"""Runtime configuration. Everything comes from environment variables with safe defaults.

No credential of any kind is configured here: the employer logs in by hand in the
browser window the module opens. The only secret is the local API access token that
protects audit data, reports and evidence on this machine.
"""
from __future__ import annotations

import os
import secrets
from dataclasses import dataclass
from pathlib import Path

MODULE_ROOT = Path(__file__).resolve().parent.parent

# Verified 2026-11-03: https://portal.esic.gov.in/ redirects to https://esic.gov.in/, whose
# "Employer Login" button links to this page (username, password, CAPTCHA fields).
OFFICIAL_LOGIN_URL = "https://portal.esic.gov.in/EmployerPortal/ESICInsurancePortal/Portal_Loginnew.aspx"
OFFICIAL_HOST_SUFFIXES = ("esic.gov.in", "esic.in")


@dataclass(frozen=True)
class Settings:
    storage_dir: Path
    db_path: Path
    login_url: str
    allowed_host_suffixes: tuple[str, ...]
    browser_channel: str | None  # "chrome", "msedge" or None for Playwright's bundled Chromium
    headless: bool
    synthetic: bool  # True only when pointed at the synthetic fixture portal (tests/simulation)
    access_token: str

    @property
    def reports_dir(self) -> Path:
        return self.storage_dir / "reports"

    @property
    def evidence_dir(self) -> Path:
        return self.storage_dir / "evidence"


def _token(storage_dir: Path) -> str:
    env = os.environ.get("ESIC_AUDIT_ACCESS_TOKEN", "").strip()
    if env:
        return env
    path = storage_dir / "access_token.txt"
    if path.exists():
        value = path.read_text(encoding="utf-8").strip()
        if value:
            return value
    value = secrets.token_urlsafe(32)
    path.write_text(value, encoding="utf-8")
    return value


def load_settings(**overrides) -> Settings:
    storage = Path(overrides.pop("storage_dir", None) or os.environ.get("ESIC_AUDIT_STORAGE_DIR") or MODULE_ROOT / "storage")
    storage.mkdir(parents=True, exist_ok=True)
    login_url = overrides.pop("login_url", None) or os.environ.get("ESIC_AUDIT_LOGIN_URL") or OFFICIAL_LOGIN_URL
    synthetic = login_url != OFFICIAL_LOGIN_URL
    hosts = OFFICIAL_HOST_SUFFIXES if not synthetic else ("127.0.0.1", "localhost")
    channel = overrides.pop("browser_channel", os.environ.get("ESIC_AUDIT_BROWSER_CHANNEL", "chrome")) or None
    headless = overrides.pop("headless", os.environ.get("ESIC_AUDIT_HEADLESS", "0") == "1")
    settings = Settings(
        storage_dir=storage,
        db_path=Path(overrides.pop("db_path", None) or storage / "esic_audit.sqlite3"),
        login_url=login_url,
        allowed_host_suffixes=tuple(overrides.pop("allowed_host_suffixes", hosts)),
        browser_channel=channel,
        headless=bool(headless),
        synthetic=synthetic,
        access_token=overrides.pop("access_token", None) or _token(storage),
    )
    if overrides:
        raise TypeError(f"unknown settings: {sorted(overrides)}")
    settings.reports_dir.mkdir(parents=True, exist_ok=True)
    settings.evidence_dir.mkdir(parents=True, exist_ok=True)
    return settings
