"""Pillow frame composition: letterboxed shot frames, overlays, GAP cards, crossfades.

Latin script only in R1: overlay text containing letters outside the Latin script is NOT rendered
(replaced by an explicit notice) and reported as a QC warning; no font is bundled or downloaded.
"""
from __future__ import annotations

import os
import unicodedata
from collections import OrderedDict
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps

from renderer.layout import ShotPlan, WarnBag
from renderer.motion import letterbox, window_at
from renderer.package import AssetFile

BLACK = (0, 0, 0)
FONT_CANDIDATES = [
    r"C:\Windows\Fonts\arial.ttf", r"C:\Windows\Fonts\segoeui.ttf", r"C:\Windows\Fonts\calibri.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "/Library/Fonts/Arial.ttf",
]
OVERLAY_STYLE = {  # fill RGBA, text colour
    "editorial_label": ((176, 0, 32, 225), (255, 255, 255)),
    "source_caption": ((0, 0, 0, 175), (240, 240, 240)),
    "rights_warning": ((196, 120, 0, 225), (255, 255, 255)),
}
UNSUPPORTED_NOTICE = "[text not rendered: non-Latin script unsupported in R1]"


def find_font(configured: str | None) -> str | None:
    for p in ([configured] if configured else []) + FONT_CANDIDATES:
        if p and os.path.isfile(p):
            return p
    return None


def is_latin_text(text: str) -> bool:
    """True when every letter is Latin script (digits, punctuation, symbols and spaces are allowed)."""
    for ch in text:
        if ch.isalpha() and not unicodedata.name(ch, "").startswith("LATIN"):
            return False
    return True


class Fonts:
    def __init__(self, path: str | None):
        self.path = find_font(path)
        self._cache: dict[int, ImageFont.FreeTypeFont | ImageFont.ImageFont] = {}

    def get(self, size: int):
        size = max(8, int(size))
        if size not in self._cache:
            try:
                self._cache[size] = ImageFont.truetype(self.path, size) if self.path else ImageFont.load_default(size)
            except OSError:
                self._cache[size] = ImageFont.load_default(size)
        return self._cache[size]


def wrap_text(draw: ImageDraw.ImageDraw, text: str, font, max_w: int, max_lines: int) -> list[str]:
    words, lines, cur = text.split(), [], ""
    for w in words:
        trial = f"{cur} {w}".strip()
        if draw.textlength(trial, font=font) <= max_w or not cur:
            cur = trial
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        last = lines[-1]
        while last and draw.textlength(last + "...", font=font) > max_w:
            last = last[:-1]
        lines[-1] = last.rstrip() + "..."
    # hard-cut a single over-long word
    out = []
    for ln in lines:
        while len(ln) > 1 and draw.textlength(ln, font=font) > max_w:
            ln = ln[:-1]
        out.append(ln)
    return out or [""]


class Overlay:
    """A pre-rendered overlay box: RGBA tile + position, pasted with its own alpha."""
    __slots__ = ("tile", "xy")

    def __init__(self, tile: Image.Image, xy: tuple[int, int]):
        self.tile, self.xy = tile, xy


def build_overlays(shot: dict, out_w: int, out_h: int, fonts: Fonts, bag: WarnBag, order: int) -> list[Overlay]:
    """Text boxes for editorial_label (top-left), source_caption (bottom-left) and rights_warning (bottom, above)."""
    margin = max(12, out_h // 54)
    fsize = max(14, out_h // 36)
    font = fonts.get(fsize)
    pad = max(6, fsize // 3)
    max_w = int(out_w * 0.62)
    probe = ImageDraw.Draw(Image.new("RGB", (4, 4)))
    line_h = int(fsize * 1.25)
    tiles: dict[str, Image.Image] = {}
    for ov in shot.get("overlays") or []:
        kind = ov.get("type")
        if kind not in OVERLAY_STYLE or kind in tiles:
            if kind not in OVERLAY_STYLE:
                bag.add("OVERLAY_UNSUPPORTED", f"overlay type '{kind}' not rendered in R1", order, key=str(kind))
            continue
        text = str(ov.get("text") or "").strip()
        if not text:
            continue
        if not is_latin_text(text):
            bag.add("OVERLAY_NON_LATIN", "overlay text in a non-Latin script was not rendered (R1 supports Latin only)",
                    order, key="nonlatin")
            text = UNSUPPORTED_NOTICE
        lines = wrap_text(probe, text, font, max_w - 2 * pad, 3)
        w = int(max(probe.textlength(ln, font=font) for ln in lines)) + 2 * pad
        h = line_h * len(lines) + 2 * pad - (line_h - fsize) // 2
        fill, fg = OVERLAY_STYLE[kind]
        tile = Image.new("RGBA", (w, h), fill)
        d = ImageDraw.Draw(tile)
        for k, ln in enumerate(lines):
            d.text((pad, pad + k * line_h), ln, font=font, fill=fg)
        tiles[kind] = tile
    out: list[Overlay] = []
    y_bottom = out_h - margin
    if "source_caption" in tiles:
        t = tiles["source_caption"]
        y_bottom -= t.height
        out.append(Overlay(t, (margin, y_bottom)))
        y_bottom -= margin // 2
    if "rights_warning" in tiles:
        t = tiles["rights_warning"]
        y_bottom -= t.height
        out.append(Overlay(t, (margin, y_bottom)))
    if "editorial_label" in tiles:
        out.append(Overlay(tiles["editorial_label"], (margin, margin)))
    return out


def paste_overlays(frame: Image.Image, overlays: list[Overlay]) -> None:
    for ov in overlays:
        frame.paste(ov.tile, ov.xy, ov.tile)


def gap_card(shot: dict, out_w: int, out_h: int, fonts: Fonts) -> Image.Image:
    """Explicit missing-material card. Never an image; never AI/placeholder imagery."""
    img = Image.new("RGB", (out_w, out_h), (28, 28, 34))
    d = ImageDraw.Draw(img)
    d.rectangle((0, 0, out_w - 1, out_h - 1), outline=(200, 40, 40), width=max(4, out_h // 180))
    big, mid, small = fonts.get(out_h // 8), fonts.get(out_h // 22), fonts.get(out_h // 32)
    cy = out_h // 2

    def centred(text: str, y: int, font, fill) -> None:
        w = d.textlength(text, font=font)
        d.text(((out_w - w) / 2, y), text, font=font, fill=fill)

    centred("VISUAL GAP", cy - out_h // 6, big, (235, 235, 235))
    code = str(shot.get("gap_code") or "UNSPECIFIED")
    centred(f"No approved visual material  -  {code}", cy + out_h // 24, mid, (230, 160, 160))
    centred(f"shot {shot.get('order')}  |  {float(shot['start']):.2f}s - {float(shot['end']):.2f}s",
            cy + out_h // 24 + int(out_h / 14), small, (170, 170, 178))
    return img


class SourceCache:
    """Small LRU of decoded RGB sources, pre-reduced so crops/resizes stay cheap."""

    def __init__(self, assets: dict[str, AssetFile], out_w: int, out_h: int, size: int = 3):
        self.assets, self.size = assets, size
        self.limit = max(out_w, out_h) * 2
        self._lru: OrderedDict[str, Image.Image] = OrderedDict()

    def get(self, asset_id: str) -> Image.Image:
        if asset_id in self._lru:
            self._lru.move_to_end(asset_id)
            return self._lru[asset_id]
        with Image.open(self.assets[asset_id].path) as im:
            im = ImageOps.exif_transpose(im)
            im = im.convert("RGB") if im.mode != "RGB" else im.copy()
        if max(im.size) > self.limit:  # reduce very large sources once (never upscaled)
            s = self.limit / max(im.size)
            im = im.resize((max(1, round(im.width * s)), max(1, round(im.height * s))), Image.LANCZOS)
        self._lru[asset_id] = im
        while len(self._lru) > self.size:
            self._lru.popitem(last=False)
        return im


class Composer:
    def __init__(self, assets: dict[str, AssetFile], out_w: int, out_h: int, font_path: str | None, bag: WarnBag):
        self.w, self.h, self.bag = out_w, out_h, bag
        self.fonts = Fonts(font_path)
        self.sources = SourceCache(assets, out_w, out_h)
        self._static: dict[int, Image.Image] = {}
        self._overlays: dict[int, list[Overlay]] = {}
        self._black = Image.new("RGB", (out_w, out_h), BLACK)
        if not self.fonts.path:
            bag.add("FONT_FALLBACK", "no TrueType font found; Pillow's built-in font used for overlay text",
                    key="font")

    def _overlays_for(self, sp: ShotPlan) -> list[Overlay]:
        ov = self._overlays.get(sp.index)
        if ov is None:
            ov = build_overlays(sp.shot, self.w, self.h, self.fonts, self.bag, sp.index)
            self._overlays[sp.index] = ov
        return ov

    def is_static(self, sp: ShotPlan) -> bool:
        return sp.status == "GAP" or sp.motion.is_static

    def drop_static(self, keep: set[int]) -> None:
        for k in [k for k in self._static if k not in keep]:
            del self._static[k]
        for k in [k for k in self._overlays if k not in keep]:
            del self._overlays[k]

    def shot_image(self, sp: ShotPlan, p: float) -> Image.Image:
        """Composed frame (RGB, output size) for a shot at progress p. Static shots are cached."""
        static = self.is_static(sp)
        if static and sp.index in self._static:
            return self._static[sp.index]
        if sp.status == "GAP":
            frame = gap_card(sp.shot, self.w, self.h, self.fonts)
        else:
            src = self.sources.get(sp.asset_id)
            win = window_at(sp.motion, 0.0 if static else p)
            sw, sh = src.size
            box = (win.x * sw, win.y * sh, (win.x + win.w) * sw, (win.y + win.h) * sh)
            dx, dy, dw, dh = letterbox(box[2] - box[0], box[3] - box[1], self.w, self.h)
            frame = self._black.copy()
            frame.paste(src.resize((dw, dh), Image.BICUBIC, box=box), (dx, dy))
        paste_overlays(frame, self._overlays_for(sp))
        if static:
            self._static[sp.index] = frame
        return frame

    @staticmethod
    def crossfade(a: Image.Image, b: Image.Image, alpha: float) -> Image.Image:
        return Image.blend(a, b, alpha)
