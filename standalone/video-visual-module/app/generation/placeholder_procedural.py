"""
PLACEHOLDER image provider (no network, no model, no cost) -- generation_kind = PLACEHOLDER.

This is NOT an AI model. It is a deterministic procedural renderer: given a
prompt it draws a new PNG on this machine (gradient + horizon + geometric
motif seeded from the prompt hash) with a permanent
"PLACEHOLDER - NOT AI - NOT EVIDENCE" band. It exists so the pipeline can be
exercised end-to-end, and so editors see where an illustration slot is,
without any credentials or network.

Cost: NOT_APPLICABLE (local CPU only, no billable provider). Earlier builds
reported an invented "synthetic" USD 0.01/image rate; that was removed in
Phase 2 because it was not a real price.

Formerly registered as "local-procedural"; that name remains an alias.
"""
from __future__ import annotations

import hashlib
import time
import uuid
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from app.generation.base import (
    COST_NOT_APPLICABLE, GEN_PLACEHOLDER, AIImageProvider, CostInfo, ImageGenerationRequest,
    ImageGenerationResult, ProviderError, parse_resolution,
)

PLACEHOLDER_BAND_TEXT = "PLACEHOLDER - NOT AI - NOT EVIDENCE"


class PlaceholderProceduralProvider(AIImageProvider):
    name = "placeholder-procedural"
    aliases = ("local-procedural",)
    model = "procedural-composition-v1"
    is_mock = False
    generation_kind = GEN_PLACEHOLDER
    requires_credentials = False
    billable = False
    description = ("Local deterministic procedural PLACEHOLDER renderer. Not an AI model and never evidence. "
                   "Output carries a permanent 'PLACEHOLDER - NOT AI - NOT EVIDENCE' band. Cost not applicable.")

    def generate(self, request: ImageGenerationRequest, out_dir: Path) -> ImageGenerationResult:
        start = time.monotonic()
        try:
            width, height = parse_resolution(request.resolution)
        except ValueError as exc:
            raise ProviderError("GENERATION_FAILED", f"Invalid resolution: {exc}") from exc
        seed = hashlib.sha256(f"{request.prompt}|{request.purpose or ''}".encode("utf-8")).digest()
        c1 = (seed[0] // 2, seed[1] // 2, seed[2] // 2 + 40)       # darker sky
        c2 = (min(255, seed[3] + 60), min(255, seed[4] + 40), seed[5])

        strip = Image.new("RGB", (1, 256))
        for y in range(256):
            t = y / 255
            strip.putpixel((0, y), tuple(int(c1[i] * (1 - t) + c2[i] * t) for i in range(3)))
        img = strip.resize((width, height), Image.BILINEAR)
        draw = ImageDraw.Draw(img)

        horizon = int(height * (0.55 + (seed[6] % 20) / 100))
        pts = [(0, height)]
        steps = 24
        for i in range(steps + 1):
            x = int(i * width / steps)
            y = horizon - int((seed[(7 + i) % 32] / 255) * height * 0.12)
            pts.append((x, y))
        pts.append((width, height))
        draw.polygon(pts, fill=(c1[0] // 3, c1[1] // 3, c1[2] // 3))

        n_shapes = 3 + (seed[8] % 4)
        outline = (255 - c1[0], 255 - c1[1], 255 - c1[2])
        for i in range(n_shapes):
            cx = int((seed[(i * 2 + 9) % 32] / 255) * width)
            cy = int((seed[(i * 2 + 10) % 32] / 255) * horizon)
            r = max(6, int((20 + seed[(i + 3) % 32] % 60) * width / 1280))
            draw.ellipse([cx - r, cy - r, cx + r, cy + r], outline=outline, width=max(1, width // 400))

        font = ImageFont.load_default()
        band = max(24, height // 18)
        draw.rectangle([0, height - band, width, height], fill=(0, 0, 0))
        caption = " ".join((request.prompt or "").split())[:80]
        draw.text((10, height - band + 6), f"{PLACEHOLDER_BAND_TEXT} | {caption}", fill=(255, 255, 255), font=font)

        out_dir.mkdir(parents=True, exist_ok=True)
        request_id = str(uuid.uuid4())
        file_path = out_dir / f"{request_id}.placeholder.png"
        img.save(file_path, format="PNG")

        return ImageGenerationResult(
            provider=self.name,
            is_mock=False,
            model=self.model,
            request_id=request_id,
            file_path=file_path,
            width=width,
            height=height,
            generation_time_seconds=round(time.monotonic() - start, 4),
            estimated_cost=None,
            metadata={"motif_shapes": n_shapes, "renderer": "procedural", "placeholder": True,
                      "band_text": PLACEHOLDER_BAND_TEXT},
            generation_kind=GEN_PLACEHOLDER,
            cost=CostInfo(status=COST_NOT_APPLICABLE, basis="local procedural placeholder; no billable provider"),
        )
