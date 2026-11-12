"""Decide whether a browser request is from this site.

Cookie checks alone accept a request from any page that the browser will
attach the cookie to. SameSite covers other registrable domains, not a
sibling subdomain. These helpers compare the browser-set Origin with the
origin the visitor actually used.
"""

from __future__ import annotations

_LOOPBACK = frozenset({"127.0.0.1", "::1"})
_UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


def public_scheme(scheme: str, client_host: str | None, forwarded_proto: str | None) -> str:
    """Scheme the visitor used.

    X-Forwarded-Proto is honored only from a loopback peer, which is the
    reverse proxy on this machine. A browser talking to the process directly
    cannot pick the scheme.
    """
    if client_host in _LOOPBACK:
        proto = (forwarded_proto or "").split(",")[0].strip().lower()
        if proto in {"https", "http"}:
            return proto
    return scheme or "http"


def public_origin(
    scheme: str,
    host: str | None,
    client_host: str | None,
    forwarded_proto: str | None,
) -> str | None:
    if not host:
        return None
    return f"{public_scheme(scheme, client_host, forwarded_proto)}://{host}"


def origin_is_trusted(
    *,
    origin: str | None,
    host: str | None,
    scheme: str,
    client_host: str | None,
    forwarded_proto: str | None,
    sec_fetch_site: str | None,
    allowed_origins: list[str] | None = None,
) -> bool:
    """True when this Origin may send a cookie-authenticated command.

    The site's own origin must also claim Sec-Fetch-Site same-origin when the
    browser sent that header. An explicitly configured origin is trusted as
    given; the default list is empty.
    """
    if not origin:
        return False
    if origin in (allowed_origins or []):
        return True
    expected = public_origin(scheme, host, client_host, forwarded_proto)
    if not expected or origin != expected:
        return False
    site = (sec_fetch_site or "").strip().lower()
    return not site or site == "same-origin"


def write_is_trusted(
    *,
    method: str,
    origin: str | None,
    host: str | None,
    scheme: str,
    client_host: str | None,
    forwarded_proto: str | None,
    sec_fetch_site: str | None,
    allowed_origins: list[str] | None = None,
) -> bool:
    """GET and other safe methods pass. Writes must come from a trusted origin."""
    if method.upper() not in _UNSAFE_METHODS:
        return True
    return origin_is_trusted(
        origin=origin,
        host=host,
        scheme=scheme,
        client_host=client_host,
        forwarded_proto=forwarded_proto,
        sec_fetch_site=sec_fetch_site,
        allowed_origins=allowed_origins,
    )
