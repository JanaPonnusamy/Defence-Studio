"""
Optional media decoding for previews (Phase 2). Never required; never faked.

  * Video frames / probe: `imageio-ffmpeg` (BSD-2 wrapper; it ships an ffmpeg binary
    whose own licence is LGPL/GPL, or uses the binary named by IMAGEIO_FFMPEG_EXE).
  * PDF pages: `pypdfium2` (Apache-2.0 / BSD-3; bundles PDFium, BSD-3). AGPL PyMuPDF is
    deliberately not used.

If a library or binary is missing, or the file cannot be decoded, the functions raise
PreviewUnavailable with a precise reason; callers show that reason instead of a picture.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from PIL import Image


class PreviewUnavailable(Exception):
    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


@lru_cache(maxsize=1)
def _ffmpeg() -> tuple[object | None, str | None, str]:
    """(imageio_ffmpeg module, exe path, reason-if-unavailable)."""
    try:
        import imageio_ffmpeg  # noqa: PLC0415
    except ImportError:
        return None, None, "imageio-ffmpeg is not installed"
    try:
        exe = imageio_ffmpeg.get_ffmpeg_exe()
    except Exception as exc:  # noqa: BLE001 -- RuntimeError when no binary
        return None, None, f"no ffmpeg binary available ({type(exc).__name__})"
    return imageio_ffmpeg, exe, ""


@lru_cache(maxsize=1)
def _pdfium() -> tuple[object | None, str]:
    try:
        import pypdfium2  # noqa: PLC0415
    except ImportError:
        return None, "pypdfium2 is not installed"
    return pypdfium2, ""


def capabilities() -> dict:
    from app.config import get_settings

    s = get_settings()
    ff, exe, ff_reason = _ffmpeg()
    pdf, pdf_reason = _pdfium()
    if pdf is not None and not getattr(pdf, "__version__", None):
        try:
            from importlib.metadata import version  # noqa: PLC0415

            pdf.__version__ = version("pypdfium2")
        except Exception:  # noqa: BLE001
            pass
    video_ok = bool(ff) and s.video_previews_enabled
    pdf_ok = bool(pdf) and s.pdf_previews_enabled
    tiles = bool(s.basemap_tile_url)
    return {
        "video_frames": {"available": video_ok,
                         "backend": f"imageio-ffmpeg {getattr(ff, '__version__', '')}".strip() if ff else None,
                         "ffmpeg_exe": Path(exe).name if exe else None,
                         "licence": "imageio-ffmpeg BSD-2-Clause; ffmpeg binary LGPL/GPL" if ff else None,
                         "reason": None if video_ok else (ff_reason or "disabled by VIDEO_MODULE_VIDEO_PREVIEWS_ENABLED")},
        "pdf_pages": {"available": pdf_ok,
                      "backend": f"pypdfium2 {getattr(pdf, '__version__', '')}".strip() if pdf else None,
                      "licence": "pypdfium2 Apache-2.0/BSD-3-Clause; PDFium BSD-3-Clause" if pdf else None,
                      "reason": None if pdf_ok else (pdf_reason or "disabled by VIDEO_MODULE_PDF_PREVIEWS_ENABLED")},
        "basemap_tiles": {"configured": tiles, "network_enabled": bool(s.basemap_network_enabled),
                          "attribution": s.basemap_attribution or None,
                          "available": tiles and bool(s.basemap_network_enabled) and bool(s.basemap_attribution),
                          "reason": None if (tiles and s.basemap_network_enabled and s.basemap_attribution) else
                          ("no tile source configured" if not tiles else
                           "tile attribution missing" if not s.basemap_attribution else
                           "network disabled (VIDEO_MODULE_BASEMAP_NETWORK_ENABLED=false)"),
                          "fallback": "schematic map (always available)"},
    }


def probe_video(path: Path) -> dict:
    """{'width','height','fps','duration'} from the container header (no full decode)."""
    from app.config import get_settings

    ff, _exe, reason = _ffmpeg()
    if ff is None or not get_settings().video_previews_enabled:
        raise PreviewUnavailable(reason or "video previews disabled")
    gen = None
    try:
        gen = ff.read_frames(str(path))
        meta = next(gen)
    except StopIteration as exc:
        raise PreviewUnavailable("video has no decodable stream") from exc
    except Exception as exc:  # noqa: BLE001 -- ffmpeg reports undecodable input as RuntimeError/OSError
        raise PreviewUnavailable(f"video could not be decoded ({type(exc).__name__})") from exc
    finally:
        if gen is not None:
            gen.close()
    size = meta.get("size") or (None, None)
    dur = meta.get("duration")
    return {"width": size[0], "height": size[1], "fps": meta.get("fps"),
            "duration": float(dur) if isinstance(dur, (int, float)) and dur > 0 else None}


def video_frame(path: Path, at_seconds: float = 0.0) -> Image.Image:
    """Decode one representative frame at (or just after) `at_seconds`."""
    from app.config import get_settings

    ff, _exe, reason = _ffmpeg()
    if ff is None or not get_settings().video_previews_enabled:
        raise PreviewUnavailable(reason or "video previews disabled")
    gen = None
    try:
        gen = ff.read_frames(str(path), input_params=["-ss", f"{max(0.0, at_seconds):.3f}"], output_params=["-frames:v", "1"])
        meta = next(gen)
        frame = next(gen)
        w, h = meta["size"]
        return Image.frombytes("RGB", (w, h), frame)
    except StopIteration as exc:
        raise PreviewUnavailable(f"no frame at {at_seconds:.2f}s") from exc
    except PreviewUnavailable:
        raise
    except Exception as exc:  # noqa: BLE001
        raise PreviewUnavailable(f"video could not be decoded ({type(exc).__name__})") from exc
    finally:
        if gen is not None:
            gen.close()


def pdf_page_count(path: Path) -> int:
    pdf_mod, reason = _pdfium()
    if pdf_mod is None:
        raise PreviewUnavailable(reason)
    try:
        doc = pdf_mod.PdfDocument(str(path))
    except Exception as exc:  # noqa: BLE001 -- PdfiumError
        raise PreviewUnavailable(f"PDF could not be opened ({type(exc).__name__})") from exc
    try:
        return len(doc)
    finally:
        doc.close()


def pdf_page(path: Path, page: int = 1, max_side: int = 1600) -> Image.Image:
    """Render PDF page `page` (1-based) to an RGB image whose long side is <= max_side."""
    from app.config import get_settings

    pdf_mod, reason = _pdfium()
    if pdf_mod is None or not get_settings().pdf_previews_enabled:
        raise PreviewUnavailable(reason or "PDF previews disabled")
    try:
        doc = pdf_mod.PdfDocument(str(path))
    except Exception as exc:  # noqa: BLE001
        raise PreviewUnavailable(f"PDF could not be opened ({type(exc).__name__})") from exc
    try:
        n = len(doc)
        if not 1 <= page <= n:
            raise PreviewUnavailable(f"page {page} out of range (document has {n} page(s))")
        pg = doc[page - 1]
        try:
            w, h = pg.get_size()
            scale = max(0.1, min(4.0, max_side / max(w, h, 1)))
            bitmap = pg.render(scale=scale)
            img = bitmap.to_pil().convert("RGB")
        finally:
            pg.close()
        return img
    except PreviewUnavailable:
        raise
    except Exception as exc:  # noqa: BLE001
        raise PreviewUnavailable(f"PDF page could not be rendered ({type(exc).__name__})") from exc
    finally:
        doc.close()
