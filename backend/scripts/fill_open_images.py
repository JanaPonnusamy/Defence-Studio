"""Fill scene visuals with openly licensed photos from Wikimedia Commons.

Operator tool (owner policy 2026-10-28): search Commons for each query, keep
only public-domain / CC0 / CC BY / CC BY-SA files, upload them through the
running app's public API, record the licence + attribution the file page
declares, approve them, and assign them so every scene of each approved plan
has at least one image. The app's rights gate still re-derives every verdict;
nothing with unknown, non-commercial or expired rights can be approved.

    python backend/scripts/fill_open_images.py JOB_ID --lang en --lang ta \
        --query "GSAT-7 satellite" --query "Varunastra torpedo" ...

Uses the local dev-auth bypass (loopback only); pass --token otherwise.
"""
import argparse
import hashlib
import html
import re
import sys

import httpx

COMMONS = "https://commons.wikimedia.org/w/api.php"
# Wikimedia's User-Agent policy requires a reachable contact URL.
USER_AGENT = "DefenceStudio/0.1 (https://github.com/JanaPonnusamy/Defence-Studio) httpx"
MIN_WIDTH = 1000


def licence_of(meta: dict) -> dict | None:
    """Map Commons extmetadata to a rights record, or None if not open enough."""
    short = (meta.get("LicenseShortName", {}).get("value") or "").strip()
    url = (meta.get("LicenseUrl", {}).get("value") or "").strip() or None
    low = short.lower()
    if low.startswith("cc by-sa"):
        basis = "cc_by_sa"
    elif low.startswith("cc by") and "nc" not in low and "nd" not in low:
        basis = "cc_by"
    elif low in ("cc0", "public domain", "pd") or low.startswith("pd-") or low.startswith("cc0"):
        basis = "public_domain"
    else:
        return None
    artist = re.sub(r"<[^>]+>", "", html.unescape(meta.get("Artist", {}).get("value") or "")).strip()
    record = {"schema_version": "defence-studio.asset-rights.v1", "rights_basis": basis,
              "licence_id": short[:200], "licence_name": short[:400], "commercial_use": "allowed",
              "attribution_required": basis != "public_domain"}
    if url:
        record["licence_url"] = url[:2000]
    if basis != "public_domain":
        record["attribution_text"] = f"{artist or 'Unknown author'} / Wikimedia Commons / {short}"[:2000]
    return {"rights": record, "artist": artist[:400]}


def search(client: httpx.Client, query: str, limit: int) -> list[dict]:
    params = {"action": "query", "format": "json", "generator": "search", "gsrnamespace": 6,
              "gsrsearch": f"{query} filetype:bitmap", "gsrlimit": limit * 4, "prop": "imageinfo",
              "iiprop": "url|extmetadata|mime|size", "iiurlwidth": 1920}
    pages = client.get(COMMONS, params=params).raise_for_status().json().get("query", {}).get("pages", {})
    wanted = words(query)
    out = []
    for page in sorted(pages.values(), key=lambda p: p.get("index", 0)):
        # Full-text search also matches descriptions (e.g. a nickname); require the
        # file title itself to name one query word.
        if not wanted & words(page["title"]):
            continue
        info = (page.get("imageinfo") or [{}])[0]
        if info.get("mime") not in ("image/jpeg", "image/png") or info.get("width", 0) < MIN_WIDTH:
            continue
        lic = licence_of(info.get("extmetadata") or {})
        if lic:
            out.append({"title": page["title"].removeprefix("File:"), "page": info["descriptionurl"],
                        "url": info.get("thumburl") or info["url"], "query": query, **lic})
        if len(out) >= limit:
            break
    return out


def words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]{3,}", text.lower())}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("job_id")
    ap.add_argument("--lang", action="append", required=True, choices=["en", "ta"])
    ap.add_argument("--query", action="append", required=True)
    ap.add_argument("--exclude", action="append", default=[], help="skip files whose title contains this text")
    ap.add_argument("--per-query", type=int, default=2)
    ap.add_argument("--base", default="http://127.0.0.1:8011/api")
    ap.add_argument("--token")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--by-order", action="store_true",
                    help="scene N gets the images of query N (no English plan needed)")
    args = ap.parse_args()
    api = httpx.Client(base_url=args.base, timeout=120,
                       headers={"Authorization": f"Bearer {args.token}"} if args.token else {})
    web = httpx.Client(timeout=120, headers={"User-Agent": USER_AGENT}, follow_redirects=True)
    job = args.job_id

    found, seen = [], set()
    for q in args.query:
        for item in search(web, q, args.per_query):
            if item["page"] not in seen and not any(x.lower() in item["title"].lower() for x in args.exclude):
                seen.add(item["page"])
                found.append(item)
    print(f"{len(found)} open-licence image(s) found")
    for f in found:
        print(f"  [{f['rights']['licence_name']}] {f['title']}  <- {f['query']}")
    if args.dry_run or not found:
        return 0 if found else 1

    assets = []
    for f in found:
        data = web.get(f["url"]).raise_for_status().content
        key = "commons-" + hashlib.sha256(f["page"].encode()).hexdigest()[:32]
        up = api.post(f"/jobs/{job}/assets/upload", headers={"Idempotency-Key": key},
                      files={"file": (f["title"][-80:], data)},
                      data={"title": f["title"][:400], "description": f"Wikimedia Commons; search: {f['query']}",
                            "creator": f["artist"], "source_url": f["page"],
                            "documented_origin": "Wikimedia Commons file page (licence from file metadata)"})
        up.raise_for_status()
        aid = up.json()["asset_id"]
        # Re-posting rights (even identical) invalidates an approval, so set them only once.
        if not api.get(f"/jobs/{job}/assets/{aid}").raise_for_status().json().get("rights_sha256"):
            api.post(f"/jobs/{job}/assets/{aid}/rights", headers={"Idempotency-Key": key + "-rights"},
                     json=f["rights"]).raise_for_status()
        cur = api.get(f"/jobs/{job}/assets/{aid}").raise_for_status().json()
        if cur.get("review_status") != "APPROVED":
            revision = len(api.get(f"/jobs/{job}/assets/{aid}/history").raise_for_status().json())
            api.post(f"/jobs/{job}/assets/{aid}/review", headers={"Idempotency-Key": f"{key}-review-{revision}"}, json={
                "decision": "APPROVED", "reviewed_asset_sha256": cur["asset_sha256"],
                "reviewed_rights_sha256": cur["rights_sha256"], "expected_revision": revision,
                "note": f"Approved under owner open-licence policy (2026-10-28): {f['rights']['licence_name']} "
                        f"per Wikimedia Commons metadata, {f['page']}"}).raise_for_status()
        assets.append({**f, "asset_id": aid, "words": words(f["query"] + " " + f["title"])})
        print(f"  approved {aid[:12]} {f['title']}")

    # Score scenes on the English plan (Tamil scenes are matched by order), or follow query order.
    plans = {lang: api.get(f"/jobs/{job}/scene/plans", params={"language": lang}).raise_for_status().json()
             for lang in set(args.lang) | (set() if args.by_order else {"en"})}
    def approved(lang):
        rows = [p for p in plans[lang]["plans"] if p["status"] == "APPROVED"]
        return api.get(f"/jobs/{job}/scene/plans/{lang}/{rows[-1]['version']}").raise_for_status().json()
    picks = []
    if args.by_order:
        picks = [[a["asset_id"] for a in assets if a["query"] == q][:2] for q in args.query]
        picks = [p for p in picks if p]
    for i, scene in enumerate([] if args.by_order else sorted(approved("en")["scenes"], key=lambda s: s["order"])):
        sw = words(scene["narration"])
        ranked = sorted(assets, key=lambda a: (-len(a["words"] & sw), (assets.index(a) - i) % len(assets)))
        picks.append([ranked[0]["asset_id"]] + ([ranked[1]["asset_id"]] if len(ranked) > 1 else []))
    for lang in args.lang:
        plan = approved(lang)   # scene ids repeat across plan versions, so the key carries the version
        scenes = sorted(plan["scenes"], key=lambda s: s["order"])
        for i, scene in enumerate(scenes):
            for aid in picks[min(i, len(picks) - 1)]:
                api.post(f"/jobs/{job}/scenes/{scene['scene_id']}/assets",
                         headers={"Idempotency-Key": f"assign-{lang}-v{plan['version']}-{scene['scene_id']}-{aid[:16]}"},
                         json={"language": lang, "asset_id": aid}).raise_for_status()
        print(f"{lang}: assigned images to {len(scenes)} scene(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
