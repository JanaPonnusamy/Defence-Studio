"""Image byte inspection without trusting provider metadata.

Perceptual hashes use Pillow when it is installed. PNG bytes also have a
stdlib decoder so tests and PNG candidates still get a hash when Pillow is
absent. Other formats then record PERCEPTUAL_HASH_UNAVAILABLE rather than a
made-up hash.
"""
from __future__ import annotations

import hashlib
import io
import struct
import zlib


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sniff_format(data: bytes) -> str | None:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if data.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "gif"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    return None


def image_size(data: bytes) -> tuple[int | None, int | None]:
    kind = sniff_format(data)
    if kind == "png" and len(data) >= 24:
        width, height = struct.unpack(">II", data[16:24])
        return int(width), int(height)
    if kind == "gif" and len(data) >= 10:
        width, height = struct.unpack("<HH", data[6:10])
        return int(width), int(height)
    if kind == "jpeg":
        return _jpeg_size(data)
    if kind == "webp":
        return _webp_size(data)
    return None, None


def perceptual_hash(data: bytes) -> tuple[str | None, str]:
    hashed = _pillow_ahash(data)
    if hashed:
        return hashed
    hashed = _png_ahash(data)
    if hashed:
        return hashed
    if sniff_format(data):
        return None, "PERCEPTUAL_HASH_UNAVAILABLE"
    return None, "NOT_AN_IMAGE"


def hamming_hex(left: str | None, right: str | None) -> int | None:
    if not left or not right or len(left) != len(right):
        return None
    try:
        return (int(left, 16) ^ int(right, 16)).bit_count()
    except ValueError:
        return None


def _ahash_from_samples(samples: list[int]) -> tuple[str, str]:
    average = sum(samples) / len(samples)
    bits = "".join("1" if sample >= average else "0" for sample in samples)
    flat = max(samples) - min(samples) == 0
    return f"{int(bits, 2):016x}", f"AHASH_8X8;mean={int(average)};flat={1 if flat else 0}"


def _pillow_ahash(data: bytes) -> tuple[str, str] | None:
    try:
        from PIL import Image
    except ImportError:
        return None
    try:
        image = Image.open(io.BytesIO(data)).convert("L").resize((8, 8))
    except Exception:
        return None
    pixels = list(image.getdata())
    if len(pixels) != 64:
        return None
    return _ahash_from_samples(pixels)


def _png_ahash(data: bytes) -> str | None:
    decoded = _decode_png(data)
    if decoded is None:
        return None
    width, height, rows, channels = decoded
    samples = []
    for gy in range(8):
        for gx in range(8):
            x = min(width - 1, int((gx + 0.5) * width / 8))
            y = min(height - 1, int((gy + 0.5) * height / 8))
            pixel = rows[y][x * channels:(x + 1) * channels]
            if channels >= 3:
                luma = int(0.299 * pixel[0] + 0.587 * pixel[1] + 0.114 * pixel[2])
            else:
                luma = pixel[0]
            samples.append(luma)
    return _ahash_from_samples(samples)


def _decode_png(data: bytes):
    if not data.startswith(b"\x89PNG\r\n\x1a\n"):
        return None
    position = 8
    width = height = bit_depth = color_type = interlace = None
    idat = []
    while position + 8 <= len(data):
        length, kind = struct.unpack(">I4s", data[position:position + 8])
        position += 8
        chunk = data[position:position + length]
        position += length + 4
        if kind == b"IHDR":
            width, height, bit_depth, color_type, _comp, _filt, interlace = struct.unpack(">IIBBBBB", chunk)
        elif kind == b"IDAT":
            idat.append(chunk)
        elif kind == b"IEND":
            break
    if interlace or bit_depth != 8 or color_type not in (2, 6) or not width or not height:
        return None
    channels = 3 if color_type == 2 else 4
    try:
        raw = zlib.decompress(b"".join(idat))
    except zlib.error:
        return None
    stride = width * channels
    rows = []
    cursor = 0
    previous = bytearray(stride)
    for _ in range(height):
        if cursor + 1 + stride > len(raw):
            return None
        filter_type = raw[cursor]
        cursor += 1
        scanline = bytearray(raw[cursor:cursor + stride])
        cursor += stride
        try:
            previous = _unfilter(filter_type, scanline, previous, channels)
        except ValueError:
            return None
        rows.append(previous)
    return width, height, rows, channels


def _unfilter(filter_type: int, scanline: bytearray, previous: bytearray, channels: int) -> bytearray:
    out = bytearray(len(scanline))
    for index, value in enumerate(scanline):
        left = out[index - channels] if index >= channels else 0
        up = previous[index]
        if filter_type == 0:
            mixed = 0
        elif filter_type == 1:
            mixed = left
        elif filter_type == 2:
            mixed = up
        elif filter_type == 3:
            mixed = (left + up) // 2
        elif filter_type == 4:
            upper_left = previous[index - channels] if index >= channels else 0
            mixed = _paeth(left, up, upper_left)
        else:
            raise ValueError("unsupported png filter")
        out[index] = (value + mixed) & 255
    return out


def _paeth(left: int, up: int, upper_left: int) -> int:
    estimate = left + up - upper_left
    pa, pb, pc = abs(estimate - left), abs(estimate - up), abs(estimate - upper_left)
    if pa <= pb and pa <= pc:
        return left
    if pb <= pc:
        return up
    return upper_left


def _jpeg_size(data: bytes) -> tuple[int | None, int | None]:
    position = 2
    while position + 9 < len(data):
        if data[position] != 0xFF:
            position += 1
            continue
        marker = data[position + 1]
        position += 2
        if marker in {0xD8, 0xD9}:
            continue
        if position + 2 > len(data):
            break
        length = struct.unpack(">H", data[position:position + 2])[0]
        if marker in {0xC0, 0xC1, 0xC2} and position + 7 <= len(data):
            height, width = struct.unpack(">HH", data[position + 3:position + 7])
            return int(width), int(height)
        position += length
    return None, None


def _webp_size(data: bytes) -> tuple[int | None, int | None]:
    if len(data) >= 30 and data[12:16] == b"VP8X":
        width = 1 + int.from_bytes(data[24:27], "little")
        height = 1 + int.from_bytes(data[27:30], "little")
        return width, height
    if len(data) >= 30 and data[12:16] == b"VP8 " and data[23:26] == b"\x9d\x01\x2a":
        width = struct.unpack("<H", data[26:28])[0] & 0x3FFF
        height = struct.unpack("<H", data[28:30])[0] & 0x3FFF
        return int(width), int(height)
    return None, None
