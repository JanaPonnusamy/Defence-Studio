"""URL normalization and SSRF protection for outbound acquisition.

Collected URLs are untrusted input. Before any outbound request this guard:

* restricts the scheme to http/https;
* rejects embedded credentials (``user:pass@host``) so nothing leaks;
* resolves the host to every A/AAAA address and blocks the request if *any*
  resolved address is loopback, private, link-local, reserved, multicast,
  unspecified, or an IPv4-mapped/compat form of those (blocking on *any* bad
  address defeats split-horizon DNS that returns one public and one internal
  record);
* re-validates every redirect hop with the same rules.

Residual risk, stated honestly: DNS can change between this check and the TCP
connect (rebinding/TOCTOU). The guard re-resolves immediately before each hop,
which closes the common redirect-to-internal vector, but a determined attacker
flipping a record inside the connect window is only fully mitigated by network
egress controls around the worker host. This is documented, not claimed solved.
"""

from __future__ import annotations

import ipaddress
import socket
from dataclasses import dataclass
from urllib.parse import urlsplit, urlunsplit


DEFAULT_ALLOWED_SCHEMES = ("http", "https")


class UrlValidationError(ValueError):
    """The URL is malformed or uses a disallowed scheme/shape."""


class BlockedAddressError(UrlValidationError):
    """The URL resolves to a destination acquisition must never reach."""


@dataclass(frozen=True)
class ValidatedUrl:
    url: str
    scheme: str
    host: str
    port: int
    addresses: tuple[str, ...]


def normalize_url(url: str) -> str:
    """Canonical form for comparison/dedup and as the request URL.

    Lowercases scheme and host, strips the fragment and any default port, and
    leaves the path/query intact. Does not perform network access.
    """
    if not isinstance(url, str) or not url.strip():
        raise UrlValidationError("URL is required")
    parts = urlsplit(url.strip())
    scheme = parts.scheme.lower()
    if not scheme:
        raise UrlValidationError("URL must be absolute with an explicit scheme")
    if parts.username or parts.password:
        raise UrlValidationError("Embedded credentials are not allowed in acquisition URLs")
    host = (parts.hostname or "").lower()
    if not host:
        raise UrlValidationError("URL must include a host")
    port = parts.port
    # IPv6 literals must keep their brackets in the netloc, or a re-split of the
    # normalized URL mis-reads the trailing groups as a port.
    host_part = f"[{host}]" if ":" in host else host
    netloc = host_part
    if port is not None and not _is_default_port(scheme, port):
        netloc = f"{host_part}:{port}"
    return urlunsplit((scheme, netloc, parts.path or "", parts.query, ""))


def _is_default_port(scheme: str, port: int) -> bool:
    return (scheme == "http" and port == 80) or (scheme == "https" and port == 443)


def _forbidden(ip: ipaddress._BaseAddress) -> bool:
    # Normalize IPv4-mapped / 6to4 / Teredo IPv6 down to the embedded IPv4 so a
    # mapped loopback/private address cannot sneak past the v6 checks.
    if isinstance(ip, ipaddress.IPv6Address):
        if ip.ipv4_mapped is not None:
            ip = ip.ipv4_mapped
        elif ip.sixtofour is not None:
            ip = ip.sixtofour
        elif getattr(ip, "teredo", None):
            ip = ip.teredo[1]
    return (
        ip.is_loopback
        or ip.is_private
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )


Resolver = "typing.Callable[[str, int], list[str]]"


def _default_resolver(host: str, port: int) -> list[str]:
    infos = socket.getaddrinfo(host, port or None, proto=socket.IPPROTO_TCP)
    return [info[4][0] for info in infos]


class UrlGuard:
    def __init__(self, allowed_schemes=DEFAULT_ALLOWED_SCHEMES, resolver=None):
        self.allowed_schemes = tuple(s.lower() for s in allowed_schemes)
        self._resolve = resolver or _default_resolver

    def validate(self, url: str) -> ValidatedUrl:
        normalized = normalize_url(url)
        parts = urlsplit(normalized)
        scheme = parts.scheme.lower()
        if scheme not in self.allowed_schemes:
            raise UrlValidationError(f"Scheme '{scheme}' is not allowed for acquisition")
        host = (parts.hostname or "").lower()
        port = parts.port or (443 if scheme == "https" else 80)

        # A literal IP in the URL must be validated directly (never resolved).
        literal = _as_ip(host)
        if literal is not None:
            if _forbidden(literal):
                raise BlockedAddressError(f"Destination {host} is in a blocked range")
            return ValidatedUrl(normalized, scheme, host, port, (str(literal),))

        try:
            resolved = self._resolve(host, port)
        except OSError as exc:
            raise BlockedAddressError(f"Could not resolve host {host!r}") from exc
        if not resolved:
            raise BlockedAddressError(f"Host {host!r} did not resolve to any address")
        addresses: list[str] = []
        for raw in resolved:
            ip = _as_ip(raw)
            if ip is None:
                raise BlockedAddressError(f"Host {host!r} resolved to a non-IP address")
            if _forbidden(ip):
                raise BlockedAddressError(f"Host {host!r} resolves to blocked address {raw}")
            addresses.append(str(ip))
        return ValidatedUrl(normalized, scheme, host, port, tuple(dict.fromkeys(addresses)))


def _as_ip(value: str):
    try:
        return ipaddress.ip_address(value.strip("[]"))
    except ValueError:
        return None
