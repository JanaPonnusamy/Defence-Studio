"""Explainable image duplicate grouping.

Same URL, same file checksum, or a close perceptual hash groups candidates.
Two photographs are not grouped only because they share a source page or a topic.
"""
from __future__ import annotations

from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from defence_studio.acquisition.url_guard import UrlValidationError, normalize_url
from defence_studio.image_research.images import hamming_hex


_TRACKING = {
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "fbclid", "gclid", "mc_cid", "mc_eid", "igshid",
}
_NEAR_DUPLICATE_DISTANCE = 6


def canonical_url(url: str | None) -> str | None:
    if not url or not str(url).strip():
        return None
    try:
        normalized = normalize_url(str(url).strip())
    except UrlValidationError:
        return None
    parts = urlsplit(normalized)
    query = [(key, value) for key, value in parse_qsl(parts.query, keep_blank_values=True)
             if key.lower() not in _TRACKING]
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), ""))


def host_of(url: str | None) -> str | None:
    if not url:
        return None
    try:
        return urlsplit(url).hostname
    except ValueError:
        return None


def assign_duplicates(records: list[dict]) -> None:
    """Set similarity_group_id, duplicate_role and duplicate_reason in place."""
    count = len(records)
    parent = list(range(count))
    group_reason: dict[int, str] = {}

    def find(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    def link(left: int, right: int, reason: str) -> None:
        if left == right:
            return
        root_left, root_right = find(left), find(right)
        if root_left == root_right:
            return
        parent[root_right] = root_left
        group_reason[root_left] = group_reason.get(root_left) or group_reason.get(root_right) or reason

    by_image_url: dict[str, int] = {}
    by_sha: dict[str, int] = {}
    hashed: list[tuple[int, str]] = []
    for index, record in enumerate(records):
        image_url = record.get("canonical_image_url")
        if image_url:
            prior = by_image_url.get(image_url)
            if prior is None:
                by_image_url[image_url] = index
            else:
                link(prior, index, "Exact canonical image URL match after tracking-parameter removal.")
        digest = record.get("content_sha256")
        if digest:
            prior = by_sha.get(digest)
            if prior is None:
                by_sha[digest] = index
            else:
                websites = {records[prior].get("source_website"), record.get("source_website")}
                if len(websites) > 1:
                    reason = "Identical content hash on different source websites."
                else:
                    reason = "Identical content hash."
                link(prior, index, reason)
        perceptual, flat, mean = _hash_facts(record)
        if perceptual:
            for other_index, other_hash, other_flat, other_mean in hashed:
                distance = hamming_hex(perceptual, other_hash)
                if distance is None:
                    continue
                if flat or other_flat:
                    if flat and other_flat and mean is not None and other_mean is not None and abs(mean - other_mean) <= 2:
                        link(other_index, index, "Flat image with the same average tone (recompressed variant).")
                        break
                    continue
                if distance == 0:
                    link(other_index, index, "Identical perceptual hash (resized or recompressed variant).")
                    break
                if distance <= _NEAR_DUPLICATE_DISTANCE:
                    link(other_index, index, f"Near-duplicate perceptual hash (hamming distance {distance}).")
                    break
            hashed.append((index, perceptual, flat, mean))

    groups: dict[int, list[int]] = {}
    for index in range(count):
        groups.setdefault(find(index), []).append(index)
    for members in groups.values():
        preferred = _preferred(records, members)
        group_id = records[preferred].get("image_id") or f"group-{preferred}"
        for index in members:
            record = records[index]
            record["similarity_group_id"] = group_id
            if index == preferred:
                record["duplicate_role"] = "UNIQUE" if len(members) == 1 else "PREFERRED"
                record["duplicate_reason"] = None if len(members) == 1 else "Preferred original in this duplicate group (highest resolution)."
            else:
                record["duplicate_role"] = "DUPLICATE"
                record["duplicate_reason"] = group_reason.get(find(index)) or "Grouped with a preferred original."


def _hash_facts(record: dict) -> tuple[str | None, bool, int | None]:
    status = record.get("perceptual_hash_status") or ""
    mean = None
    if "mean=" in status:
        try:
            mean = int(status.split("mean=", 1)[1].split(";", 1)[0])
        except ValueError:
            mean = None
    return record.get("perceptual_hash"), "flat=1" in status, mean


def _preferred(records: list[dict], members: list[int]) -> int:
    def rank(index: int) -> tuple:
        record = records[index]
        width = record.get("width") or 0
        height = record.get("height") or 0
        downloaded = 1 if record.get("download_status") == "OK" else 0
        return (downloaded, width * height, -index)
    return max(members, key=rank)
