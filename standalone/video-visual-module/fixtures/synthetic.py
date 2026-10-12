"""
Synthetic, locally-generated test fixtures (no downloads, no real-world claims).

The 10-minute episode uses FICTIONAL places, forces and systems so the
benchmark narration never asserts real-world facts.
"""
from __future__ import annotations

import io
import random

from PIL import Image, ImageDraw


def png_bytes(w: int = 640, h: int = 360, seed: int = 1, fmt: str = "PNG", text: str | None = None) -> bytes:
    rnd = random.Random(seed)
    img = Image.new("RGB", (w, h), (rnd.randint(0, 255), rnd.randint(0, 255), rnd.randint(0, 255)))
    d = ImageDraw.Draw(img)
    for _ in range(6):
        x0, y0 = rnd.randint(0, w - 2), rnd.randint(0, h - 2)
        d.rectangle([x0, y0, min(w - 1, x0 + rnd.randint(1, max(2, w // 3))), min(h - 1, y0 + rnd.randint(1, max(2, h // 3)))],
                    fill=(rnd.randint(0, 255), rnd.randint(0, 255), rnd.randint(0, 255)))
    if text:
        d.text((10, 10), text[:60], fill=(255, 255, 255))
    buf = io.BytesIO()
    img.save(buf, format=fmt)
    return buf.getvalue()


def truncated_png(seed: int = 2) -> bytes:
    data = png_bytes(640, 360, seed)
    return data[: len(data) // 2]


def bitflipped_png(seed: int = 3) -> bytes:
    data = bytearray(png_bytes(320, 180, seed))
    mid = len(data) // 2
    for i in range(mid, mid + 64):
        data[i] ^= 0xFF
    return bytes(data)


def fake_png_header_garbage() -> bytes:
    return b"\x89PNG\r\n\x1a\n" + b"not really a png" * 20


def pdf_bytes(pages: int = 2) -> bytes:
    objs = [b"1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj"]
    kids = " ".join(f"{3 + i} 0 R" for i in range(pages))
    objs.append(f"2 0 obj << /Type /Pages /Kids [{kids}] /Count {pages} >> endobj".encode())
    for i in range(pages):
        objs.append(f"{3 + i} 0 obj << /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] >> endobj".encode())
    return b"%PDF-1.4\n" + b"\n".join(objs) + b"\ntrailer << /Root 1 0 R >>\n%%EOF\n"


def mp4_bytes() -> bytes:
    """Container header only -- NOT decodable (used to test 'preview unavailable')."""
    return b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00mp42isom" + b"\x00" * 256


def real_mp4_bytes(seconds: float = 2.0, fps: int = 10, size=(160, 90)) -> bytes | None:
    """A genuine, decodable H.264 MP4 encoded locally with imageio-ffmpeg (frame colour changes over time
    so a frame at t=0 differs from one at t=1s). Returns None if imageio-ffmpeg / ffmpeg is unavailable."""
    import os
    import tempfile

    try:
        import imageio_ffmpeg
        imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:  # noqa: BLE001
        return None
    fd, path = tempfile.mkstemp(suffix=".mp4")
    os.close(fd)
    try:
        w = imageio_ffmpeg.write_frames(path, size, fps=fps, codec="libx264", pix_fmt_out="yuv420p",
                                        macro_block_size=1, quality=7)
        w.send(None)
        n = int(seconds * fps)
        for i in range(n):
            v = int(255 * i / max(1, n - 1))
            w.send(bytes([v, 40, 255 - v]) * (size[0] * size[1]))
        w.close()
        with open(path, "rb") as fh:
            return fh.read()
    finally:
        os.remove(path)


def real_pdf_bytes(pages: int = 2) -> bytes:
    """A genuine renderable PDF (Pillow), each page drawn with visible content and its page number."""
    from PIL import Image, ImageDraw

    imgs = []
    for i in range(pages):
        im = Image.new("RGB", (612, 792), (255, 255, 255))
        d = ImageDraw.Draw(im)
        d.rectangle([40, 40, 572, 200], outline=(0, 0, 0), width=4, fill=(200 - 60 * (i % 3), 220, 240))
        d.text((60, 100), f"SYNTHETIC TEST DOCUMENT - PAGE {i + 1}", fill=(0, 0, 0))
        imgs.append(im)
    buf = io.BytesIO()
    imgs[0].save(buf, format="PDF", save_all=True, append_images=imgs[1:])
    return buf.getvalue()


SAMPLE_NARRATION = (
    "The fleet is concentrated near the Kessan Strait this week. Officials say 40 warships have been moved into the area.\n\n"
    "According to a released document, the deployment order was signed in 2025. The Talon-9 missile system is "
    "a long range launcher known for its mobile radar. In conclusion, analysts expect tensions to remain high."
)

# --------------------------------------------------------------------- 10-minute episode
_SECTIONS = [
    # (force, location, hardware, number, pct)
    ("Northern Fleet of Valdoria", "Kessan Strait", "Talon-9 missile system", 40, 18),
    ("Arvel Coastal Brigade", "Port Arvel", "Harrier-class frigate", 12, 25),
    ("Ostrava Air Wing", "Lunet Airfield", "Kestrel fighter jets", 36, 30),
    ("Valdorian Marine Division", "Corran Peninsula", "Brakk amphibious tanks", 85, 12),
    ("Eastern Drone Squadron", "Mirel Valley", "Wasp reconnaissance drones", 150, 40),
    ("Southern Naval Group", "Gulf of Tessar", "Orca diesel submarines", 6, 50),
    ("Kessan Air Defence Command", "Teral Plateau", "Shield-4 radar network", 22, 15),
    ("Arvel Logistics Corps", "Dunmore Rail Hub", "Atlas heavy transport trucks", 300, 20),
    ("Ostrava Artillery Regiment", "Belvan Ridge", "Hammer self-propelled howitzers", 48, 35),
    ("Joint Rapid Response Force", "Saren Border Region", "Falcon armoured vehicles", 64, 22),
    ("Valdorian Coast Guard", "Isle of Merrow", "Sentinel patrol boats", 18, 10),
    ("Combined Strike Command", "Varos Capital District", "Lance cruise missiles", 24, 28),
]


def ten_minute_episode() -> tuple[str, list[dict]]:
    """Returns (narration_text, asset_specs). Each asset spec has kind in
    {photo, map, stat, chart, document, satellite} plus import fields."""
    paragraphs: list[str] = []
    assets: list[dict] = []
    for i, (force, loc, hw, num, pct) in enumerate(_SECTIONS):
        year = 2015 + i
        s = [
            f"The {force} is concentrated near {loc} according to regional monitors this week.",
            f"Officials say {num} units have been moved into the area since the start of the month.",
            "That is a notable shift.",
            f"The {hw} is the most capable system currently assigned to the {force}.",
            f"According to a released document, the deployment order for {loc} was signed in {year}.",
            f"Compared to last year, the force around {loc} is roughly {pct} percent larger.",
            f"Commanders call it routine, not an offensive." if i % 3 == 0 else "Few details were given.",
            f"This matters because the routes near {loc} carry much of the regional trade and fuel." if i % 2 == 0 else "",
            "Few expected it.",
            f"Analysts expect the situation around {loc} to stay tense for several more months.",
        ]
        if i % 2 == 1:
            s.insert(6, f"Satellite imagery reviewed by analysts shows new construction at {loc} in recent weeks.")
        if i == len(_SECTIONS) - 1:
            s.append("In conclusion, the coming weeks will show whether these deployments are a warning or a plan.")
        paragraphs.append(" ".join(x for x in s if x))
        tag_force = force.lower().replace(" of ", " ").split()
        tag_loc = loc.lower().split()
        tag_hw = hw.lower().replace("-", " ").split()
        assets.append({"kind": "photo", "title": f"{force} {hw}", "tags": tag_force + tag_hw + ["crews", "operations"],
                       "seed": 100 + i, "rights_status": "LICENSED", "licence": "Synthetic test licence"})
        assets.append({"kind": "map", "title": f"{loc} deployment area", "tags": tag_loc + ["routes", "regional", "trade"],
                       "lat": 40 + i * 0.7, "lon": 20 + i * 0.9, "label": loc})
        assets.append({"kind": "stat", "title": f"{force} units moved", "value": num, "unit": "units",
                       "tags": ["units", "moved", "area"] + tag_force})
        if i % 2 == 0:
            assets.append({"kind": "document", "title": f"Deployment order for {loc}", "tags": ["document", "deployment", "order", "released"] + tag_loc,
                           "seed": 300 + i, "rights_status": "PERMITTED", "licence": "Official release (synthetic)"})
        else:
            assets.append({"kind": "satellite", "title": f"Satellite imagery of {loc}", "tags": ["satellite", "imagery", "construction"] + tag_loc,
                           "seed": 400 + i, "rights_status": "LICENSED", "licence": "Synthetic satellite licence"})
            assets.append({"kind": "chart", "title": f"Force size near {loc} compared to last year", "value": pct,
                           "tags": ["force", "larger", "compared"] + tag_loc})
    return "\n\n".join(paragraphs), assets
