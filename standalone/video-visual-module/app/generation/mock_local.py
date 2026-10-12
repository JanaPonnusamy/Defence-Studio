"""
Explicitly-labelled MOCK provider for tests -- generation_kind = TEST_FIXTURE.
Writes a tiny real PNG so downstream existence/size checks behave, but
`is_mock = True` / `generation_kind = TEST_FIXTURE` is propagated into the
GenerationRecord, CostRecord, Asset and every API response. It stands in for
an AI provider in tests (so the AI code path is exercised) but is never
counted or reported as a real AI image. It is never the default provider.
"""
from __future__ import annotations

import time
import uuid
from pathlib import Path

from PIL import Image, ImageDraw

from app.generation.base import (
    COST_NOT_APPLICABLE, GEN_FIXTURE, AIImageProvider, CostInfo, ImageGenerationRequest, ImageGenerationResult,
)


class MockLocalProvider(AIImageProvider):
    name = "mock-local"
    model = "mock-model-v0"
    is_mock = True
    generation_kind = GEN_FIXTURE
    requires_credentials = False
    billable = False
    description = "MOCK / TEST FIXTURE provider for automated tests. Produces a 64x36 grey image labelled MOCK. No cost."

    def generate(self, request: ImageGenerationRequest, out_dir: Path) -> ImageGenerationResult:
        start = time.monotonic()
        out_dir.mkdir(parents=True, exist_ok=True)
        request_id = str(uuid.uuid4())
        file_path = out_dir / f"{request_id}.mock.png"
        img = Image.new("RGB", (64, 36), (128, 128, 128))
        ImageDraw.Draw(img).text((4, 12), "MOCK", fill=(255, 0, 0))
        img.save(file_path, format="PNG")
        return ImageGenerationResult(
            provider=self.name,
            is_mock=True,
            model=self.model,
            request_id=request_id,
            file_path=file_path,
            width=64,
            height=36,
            generation_time_seconds=round(time.monotonic() - start, 4),
            estimated_cost=None,
            metadata={"mock": True},
            generation_kind=GEN_FIXTURE,
            cost=CostInfo(status=COST_NOT_APPLICABLE, basis="test fixture; no billable provider"),
        )
