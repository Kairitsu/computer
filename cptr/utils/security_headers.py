"""Browser security headers for responses this process produces.

Proxied pages and file downloads keep only ``frame-ancestors`` and
``nosniff``. A full page policy would break those documents.
"""

from __future__ import annotations

import base64
import hashlib
import re
from pathlib import Path

from starlette.requests import Request
from starlette.responses import Response

from cptr.utils.request_origin import public_scheme

_BOOT_HTML = Path(__file__).resolve().parents[1] / "frontend" / "src" / "app.html"
_INLINE_SCRIPT = re.compile(
    r"<script(?![^>]*\bsrc\s*=)[^>]*>(.*?)</script>",
    re.IGNORECASE | re.DOTALL,
)
_MAX_HTML_BYTES = 2_000_000

# Chat replies may show remote pictures. The page policy has to allow the same
# http(s) images the markdown renderer keeps, or those pictures never appear.
_IMG_SRC = "img-src 'self' data: blob: https: http:"


def script_source_hash(source: str) -> str:
    digest = hashlib.sha256(source.encode("utf-8")).digest()
    return "sha256-" + base64.b64encode(digest).decode("ascii")


def boot_script_hash() -> str:
    """Hash of the inline splash script in app.html. Empty if it cannot be read."""
    try:
        text = _BOOT_HTML.read_text(encoding="utf-8")
    except OSError:
        return ""
    match = re.search(r"<script>(.*?)</script>", text, re.DOTALL)
    if not match:
        return ""
    return script_source_hash(match.group(1))


_BOOT_HASH = boot_script_hash()


def site_csp(extra_script_hashes: list[str] | None = None) -> str:
    hashes = []
    if _BOOT_HASH:
        hashes.append(f"'{_BOOT_HASH}'")
    for item in extra_script_hashes or []:
        quoted = item if item.startswith("'") else f"'{item}'"
        if quoted not in hashes:
            hashes.append(quoted)
    script = "script-src 'self'"
    if hashes:
        script += " " + " ".join(hashes)
    return (
        "default-src 'self'; "
        f"{script}; "
        "style-src 'self' 'unsafe-inline'; "
        f"{_IMG_SRC}; "
        "connect-src 'self'; "
        "frame-src 'self'; "
        "frame-ancestors 'self'; "
        "base-uri 'self'; "
        "object-src 'none'; "
        "form-action 'self'"
    )


def uses_site_policy(path: str, content_type: str, disposition: str) -> bool:
    """False for proxied pages and file bytes, which must not inherit the app policy."""
    if path.startswith("/api/browser/"):
        return False
    if path.startswith(
        (
            "/api/workspace/files/view",
            "/api/workspace/files/download",
            "/api/workspace/files/serve",
        )
    ):
        return False
    if path.startswith("/api/files/") and not path.rstrip("/").endswith("/sheets"):
        if "json" not in content_type.lower():
            return False
    if disposition.lower().startswith("attachment"):
        return False
    return True


def header_values(
    *,
    path: str,
    content_type: str,
    disposition: str,
    scheme: str,
    extra_script_hashes: list[str] | None = None,
) -> dict[str, str]:
    headers = {
        "X-Content-Type-Options": "nosniff",
        "X-Frame-Options": "SAMEORIGIN",
        "Referrer-Policy": "no-referrer",
        "Permissions-Policy": (
            "geolocation=(), camera=(self), microphone=(self), display-capture=(self)"
        ),
    }
    if uses_site_policy(path, content_type, disposition):
        headers["Content-Security-Policy"] = site_csp(extra_script_hashes)
    else:
        headers["Content-Security-Policy"] = "frame-ancestors 'self'"
    if scheme == "https":
        headers["Strict-Transport-Security"] = "max-age=31536000"
    return headers


def inline_script_hashes(body: bytes) -> list[str]:
    if len(body) > _MAX_HTML_BYTES:
        return []
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError:
        return []
    hashes = []
    for match in _INLINE_SCRIPT.finditer(text):
        source = match.group(1)
        if not source.strip():
            continue
        hashed = script_source_hash(source)
        if hashed not in hashes:
            hashes.append(hashed)
    return hashes


def _content_type(response: Response) -> str:
    return response.headers.get("content-type", "")


def _disposition(response: Response) -> str:
    return response.headers.get("content-disposition", "")


def apply_headers(request: Request, response: Response, extra_script_hashes: list[str] | None = None) -> None:
    client = request.client.host if request.client else None
    scheme = public_scheme(
        request.url.scheme,
        client,
        request.headers.get("x-forwarded-proto"),
    )
    for name, value in header_values(
        path=request.url.path,
        content_type=_content_type(response),
        disposition=_disposition(response),
        scheme=scheme,
        extra_script_hashes=extra_script_hashes,
    ).items():
        response.headers[name] = value


async def _read_body(response: Response) -> bytes:
    chunks: list[bytes] = []
    async for chunk in response.body_iterator:
        chunks.append(chunk if isinstance(chunk, bytes) else str(chunk).encode())
    return b"".join(chunks)


def _restore_body(response: Response, body: bytes) -> None:
    async def iterator():
        yield body

    response.body_iterator = iterator()
    response.headers["content-length"] = str(len(body))


async def seal_response(request: Request, response: Response) -> Response:
    """Add security headers. HTML responses also allow their own inline scripts."""
    content_type = _content_type(response)
    disposition = _disposition(response)
    extra: list[str] = []
    if (
        content_type.lower().startswith("text/html")
        and uses_site_policy(request.url.path, content_type, disposition)
    ):
        body = await _read_body(response)
        extra = inline_script_hashes(body)
        _restore_body(response, body)
    apply_headers(request, response, extra)
    return response
