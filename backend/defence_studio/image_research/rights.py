"""License classification from provider metadata.

An unclear or unrecognized license is never marked VERIFIED. Non-commercial
and all-rights-reserved terms are RESTRICTED, not cleared for reuse.
"""
from __future__ import annotations

import re


_TAG = re.compile(r"<[^>]+>")
_OPENVERSE = {
    "cc0": ("CC0", "ALLOWED", "ALLOWED"),
    "pdm": ("Public Domain Mark", "ALLOWED", "ALLOWED"),
    "by": ("CC BY", "ALLOWED", "ALLOWED"),
    "by-sa": ("CC BY-SA", "ALLOWED", "ALLOWED"),
    "by-nd": ("CC BY-ND", "ALLOWED", "DISALLOWED"),
    "by-nc": ("CC BY-NC", "DISALLOWED", "ALLOWED"),
    "by-nc-sa": ("CC BY-NC-SA", "DISALLOWED", "ALLOWED"),
    "by-nc-nd": ("CC BY-NC-ND", "DISALLOWED", "DISALLOWED"),
}


def strip_html(value: str) -> str:
    text = _TAG.sub(" ", value or "")
    return re.sub(r"\s+", " ", text).strip()


def openverse_license(code: str | None, version: str | None, license_url: str | None) -> dict:
    key = (code or "").strip().lower()
    mapped = _OPENVERSE.get(key)
    if mapped is None:
        return classify_rights(license_name=code or "", license_url=license_url or "")
    name, commercial, modification = mapped
    if version:
        name = f"{name} {version}".strip()
    classified = classify_rights(
        license_name=name,
        license_url=license_url or "",
        commercial_use=commercial,
        modification=modification,
    )
    classified["license_name"] = name
    return classified


def classify_rights(*, license_name: str, license_url: str,
                    commercial_use: str | None = None, modification: str | None = None) -> dict:
    name = strip_html(license_name)
    url = (license_url or "").strip()
    lowered = name.lower()
    if not name or lowered in {"unknown", "none", "unspecified", "copyrighted"}:
        return _result("UNCLEAR", "UNKNOWN", "UNKNOWN", "Provider did not state a reusable license. Not rights-cleared.")

    commercial = commercial_use or _commercial_from_name(lowered)
    modification_value = modification or _modification_from_name(lowered)
    if not url:
        return _result(
            "UNCLEAR", commercial if commercial != "UNKNOWN" else "UNKNOWN", modification_value,
            "A license name was present without a license URL. Not marked verified.",
        )
    if commercial == "DISALLOWED" or "all rights reserved" in lowered:
        return _result("RESTRICTED", "DISALLOWED", modification_value,
                       "License metadata states that reuse is restricted. Not rights-cleared.")
    recognized = any(token in lowered for token in (
        "public domain", "cc0", "creative commons", "cc by", "cc-by", "pdm",
    )) or lowered.startswith("cc ")
    if not recognized:
        return _result("UNCLEAR", commercial, modification_value,
                       "License URL is present but the terms were not recognized. Not marked verified.")
    note = "License name and URL came from provider metadata."
    if modification_value == "DISALLOWED":
        note += " Modification is not permitted."
    if "by" in lowered or "attribution" in lowered:
        note += " Attribution is required."
    return _result("VERIFIED", commercial, modification_value, note)


def _commercial_from_name(lowered: str) -> str:
    if any(token in lowered for token in ("noncommercial", "non-commercial", "by-nc", "cc by-nc")):
        return "DISALLOWED"
    if "all rights reserved" in lowered:
        return "DISALLOWED"
    if any(token in lowered for token in ("public domain", "cc0", "cc by", "creative commons", "pdm")):
        return "ALLOWED"
    return "UNKNOWN"


def _modification_from_name(lowered: str) -> str:
    if any(token in lowered for token in ("noderiv", "no deriv", "by-nd", "-nd")):
        return "DISALLOWED"
    if any(token in lowered for token in ("public domain", "cc0", "cc by", "creative commons")):
        return "ALLOWED"
    return "UNKNOWN"


def _result(status: str, commercial: str, modification: str, notes: str) -> dict:
    return {
        "license_name": None,
        "rights_status": status,
        "commercial_use": commercial,
        "modification": modification,
        "rights_notes": notes,
    }
