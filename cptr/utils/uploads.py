"""How an uploaded blob is sent back to the browser.

The stored content type is whatever the uploader declared. Responses ignore
it and sniff a small allowlist of image formats. Everything else is an
opaque attachment so a browser will not execute it as a page.
"""

from __future__ import annotations

from urllib.parse import quote

_PNG = b"\x89PNG\r\n\x1a\n"
_JPEG = b"\xff\xd8\xff"
_GIF = (b"GIF87a", b"GIF89a")
_ICO = b"\x00\x00\x01\x00"
_BMP = b"BM"

# ISO-BMFF brands that are still pictures, not documents.
_AVIF = {b"avif", b"avis"}
_HEIC = {b"heic", b"heix", b"hevc", b"hevx", b"heim", b"heis", b"mif1"}
_HEIF = {b"heif", b"msf1"}


def image_media_type(data: bytes) -> str | None:
    """Return an image media type when the bytes are a known picture, else None."""
    if data.startswith(_PNG):
        return "image/png"
    if data.startswith(_JPEG):
        return "image/jpeg"
    if data.startswith(_GIF):
        return "image/gif"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data.startswith(_BMP):
        return "image/bmp"
    if data.startswith(_ICO):
        return "image/x-icon"
    if len(data) >= 12 and data[4:8] == b"ftyp":
        brand = data[8:12]
        if brand in _AVIF:
            return "image/avif"
        if brand in _HEIC:
            return "image/heic"
        if brand in _HEIF:
            return "image/heif"
    return None


def content_disposition(filename: str, *, inline: bool) -> str:
    """One header value. Quotes, breaks, and NULs in the name cannot split it."""
    cleaned = "".join(ch for ch in filename if ch not in "\r\n\"\\\x00")
    cleaned = cleaned.strip() or "file"
    ascii_name = "".join(ch for ch in cleaned if 32 <= ord(ch) < 127) or "file"
    kind = "inline" if inline else "attachment"
    encoded = quote(cleaned, safe="")
    return f"{kind}; filename=\"{ascii_name}\"; filename*=UTF-8''{encoded}"


def upload_headers(data: bytes, filename: str) -> tuple[str, dict[str, str]]:
    """Media type and headers for a stored upload. Never trusts the client type."""
    image = image_media_type(data)
    media = image or "application/octet-stream"
    return media, {
        "Cache-Control": "private, no-store",
        "Content-Disposition": content_disposition(filename, inline=image is not None),
        "X-Content-Type-Options": "nosniff",
    }
