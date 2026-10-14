"""Safe extraction of an uploaded package .zip into storage/packages/<id>/ (zip-slip, size and count limits)."""
from __future__ import annotations

import uuid
import zipfile
from pathlib import Path

from renderer.errors import RendererError

MAX_ZIP_FILES = 6000
MAX_UNCOMPRESSED_BYTES = 4 * 1024 ** 3


def extract_package_zip(zip_path: Path, packages_dir: Path) -> Path:
    """Extract to a new folder and return the package root (zip root, or its single top-level folder)."""
    try:
        zf = zipfile.ZipFile(zip_path)
    except zipfile.BadZipFile:
        raise RendererError("INVALID_PACKAGE", "upload is not a valid zip file") from None
    with zf:
        infos = zf.infolist()
        if len(infos) > MAX_ZIP_FILES or sum(i.file_size for i in infos) > MAX_UNCOMPRESSED_BYTES:
            raise RendererError("INVALID_PACKAGE", "zip has too many files or is too large when extracted")
        dest = packages_dir / ("up_" + uuid.uuid4().hex[:12])
        dest.mkdir(parents=True)
        base = dest.resolve()
        for i in infos:
            name = i.filename
            if name.startswith(("/", "\\")) or ".." in Path(name.replace("\\", "/")).parts or ":" in name:
                raise RendererError("INVALID_PACKAGE", f"zip entry '{name}' is unsafe (path traversal)")
            target = (base / name).resolve()
            if not target.is_relative_to(base):
                raise RendererError("INVALID_PACKAGE", f"zip entry '{name}' is unsafe (path traversal)")
            if i.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(i) as src, open(target, "wb") as out:
                while chunk := src.read(1 << 20):
                    out.write(chunk)
    if (dest / "package.json").is_file():
        return dest
    subs = [p for p in dest.iterdir() if p.is_dir()]
    if len(subs) == 1 and (subs[0] / "package.json").is_file():
        return subs[0]
    raise RendererError("INVALID_PACKAGE", "zip does not contain package.json at its root")
