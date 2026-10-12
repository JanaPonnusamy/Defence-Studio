"""
Optional, attributed raster base map for MAP assets (Phase 2). OFF by default.

Used only when ALL of these hold:
  * VIDEO_MODULE_BASEMAP_TILE_URL is set to an XYZ template ({z}/{x}/{y}) of a tile
    service the operator is licensed to use,
  * VIDEO_MODULE_BASEMAP_ATTRIBUTION is set (it is drawn on the map and stored on the asset),
  * VIDEO_MODULE_BASEMAP_NETWORK_ENABLED=true (network is off by default),
  * the map request asks for it (`spec.basemap: true`).
Otherwise -- or if any tile fails to download/decode -- the caller falls back to the
schematic map (no basemap) and records why. Tiles are cached under storage/maps/tiles/.
No tile provider is bundled or defaulted (each has its own usage policy).
"""
from __future__ import annotations

import hashlib
import io
import math

import httpx
from PIL import Image

TILE = 256
MAX_TILES = 36
MAX_ZOOM = 12
MERCATOR_MAX_LAT = 85.0511


class BasemapUnavailable(Exception):
    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


# tests inject httpx.MockTransport here
_transport: httpx.BaseTransport | None = None


def merc_x(lon: float, z: int) -> float:
    return (lon + 180.0) / 360.0 * (2 ** z) * TILE


def merc_y(lat: float, z: int) -> float:
    lat = max(-MERCATOR_MAX_LAT, min(MERCATOR_MAX_LAT, lat))
    r = math.radians(lat)
    return (1.0 - math.log(math.tan(r) + 1.0 / math.cos(r)) / math.pi) / 2.0 * (2 ** z) * TILE


def status() -> dict:
    from app.config import get_settings

    s = get_settings()
    if not s.basemap_tile_url:
        return {"usable": False, "reason": "no tile source configured"}
    if not s.basemap_attribution:
        return {"usable": False, "reason": "tile attribution not configured"}
    if not s.basemap_network_enabled:
        return {"usable": False, "reason": "network disabled (VIDEO_MODULE_BASEMAP_NETWORK_ENABLED=false)"}
    return {"usable": True, "reason": None}


def _pick_zoom(bb: dict, target_w: int) -> int:
    for z in range(MAX_ZOOM, -1, -1):
        w = merc_x(bb["max_lon"], z) - merc_x(bb["min_lon"], z)
        tx = int(merc_x(bb["max_lon"], z) // TILE) - int(merc_x(bb["min_lon"], z) // TILE) + 1
        ty = int(merc_y(bb["min_lat"], z) // TILE) - int(merc_y(bb["max_lat"], z) // TILE) + 1
        if w <= target_w * 1.5 and tx * ty <= MAX_TILES:
            return z
    return 0


def _fetch_tile(client: httpx.Client, url_t: str, z: int, x: int, y: int) -> Image.Image:
    from app.config import get_settings

    cache_dir = get_settings().maps_dir / "tiles" / hashlib.sha256(url_t.encode()).hexdigest()[:12] / str(z)
    cache = cache_dir / f"{x}_{y}.png"
    if cache.exists():
        with Image.open(cache) as im:
            return im.convert("RGB")
    url = url_t.replace("{z}", str(z)).replace("{x}", str(x)).replace("{y}", str(y))
    try:
        r = client.get(url)
    except httpx.HTTPError as exc:
        raise BasemapUnavailable(f"tile download failed ({type(exc).__name__})") from exc
    if r.status_code != 200:
        raise BasemapUnavailable(f"tile server returned HTTP {r.status_code}")
    try:
        with Image.open(io.BytesIO(r.content)) as im:
            img = im.convert("RGB")
    except Exception as exc:  # noqa: BLE001
        raise BasemapUnavailable("tile is not a decodable image") from exc
    cache_dir.mkdir(parents=True, exist_ok=True)
    img.save(cache, format="PNG")
    return img


def render_basemap(bb: dict, size: tuple[int, int]) -> tuple[Image.Image, dict]:
    """Return (image of exactly `size` covering bbox in Web Mercator, info). Raises BasemapUnavailable."""
    from app.config import get_settings

    st = status()
    if not st["usable"]:
        raise BasemapUnavailable(st["reason"])
    if bb["max_lat"] > MERCATOR_MAX_LAT or bb["min_lat"] < -MERCATOR_MAX_LAT:
        raise BasemapUnavailable("bbox outside Web Mercator latitude range")
    s = get_settings()
    z = _pick_zoom(bb, size[0])
    x0, x1 = merc_x(bb["min_lon"], z), merc_x(bb["max_lon"], z)
    y0, y1 = merc_y(bb["max_lat"], z), merc_y(bb["min_lat"], z)
    tx0, tx1, ty0, ty1 = int(x0 // TILE), int(x1 // TILE), int(y0 // TILE), int(y1 // TILE)
    mosaic = Image.new("RGB", ((tx1 - tx0 + 1) * TILE, (ty1 - ty0 + 1) * TILE))
    n = 2 ** z
    with httpx.Client(timeout=10.0, transport=_transport, headers={"User-Agent": s.basemap_user_agent},
                      follow_redirects=False) as client:
        for tx in range(tx0, tx1 + 1):
            for ty in range(ty0, ty1 + 1):
                if not (0 <= ty < n):
                    continue
                tile = _fetch_tile(client, s.basemap_tile_url, z, tx % n, ty)
                mosaic.paste(tile.resize((TILE, TILE)), ((tx - tx0) * TILE, (ty - ty0) * TILE))
    crop = mosaic.crop((int(x0 - tx0 * TILE), int(y0 - ty0 * TILE), int(x1 - tx0 * TILE), int(y1 - ty0 * TILE)))
    return crop.resize(size, Image.BILINEAR), {"used": True, "zoom": z, "tiles": (tx1 - tx0 + 1) * (ty1 - ty0 + 1),
                                               "projection": "web_mercator", "attribution": s.basemap_attribution,
                                               "tile_source": s.basemap_tile_url.split("/{")[0]}
