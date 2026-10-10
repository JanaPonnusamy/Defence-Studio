import pytest

from defence_studio.acquisition.url_guard import (
    BlockedAddressError,
    UrlGuard,
    UrlValidationError,
    normalize_url,
)


def _resolver(mapping):
    def resolve(host, port):
        if host not in mapping:
            raise OSError("not resolvable")
        return mapping[host]
    return resolve


def test_normalize_lowercases_strips_default_port_and_fragment():
    assert normalize_url("HTTP://Example.COM:80/A?b=1#frag") == "http://example.com/A?b=1"
    assert normalize_url("https://Host:443/x") == "https://host/x"
    assert normalize_url("https://host:8443/x") == "https://host:8443/x"


@pytest.mark.parametrize("url", ["", "   ", "example.com/x", "ftp://example.com"])
def test_invalid_or_disallowed_schemes_rejected(url):
    guard = UrlGuard(resolver=_resolver({"example.com": ["93.184.216.34"]}))
    with pytest.raises(UrlValidationError):
        guard.validate(url)


def test_embedded_credentials_rejected():
    guard = UrlGuard(resolver=_resolver({"example.com": ["93.184.216.34"]}))
    with pytest.raises(UrlValidationError):
        guard.validate("http://user:pass@example.com/")


@pytest.mark.parametrize("url", [
    "http://127.0.0.1/",
    "http://10.0.0.1/",
    "http://192.168.1.10/",
    "http://169.254.169.254/",      # link-local (cloud metadata)
    "http://[::1]/",                # IPv6 loopback
    "http://[::ffff:127.0.0.1]/",   # IPv4-mapped loopback
    "http://0.0.0.0/",              # unspecified
])
def test_literal_blocked_addresses_are_rejected(url):
    guard = UrlGuard(resolver=_resolver({}))
    with pytest.raises(BlockedAddressError):
        guard.validate(url)


def test_public_literal_ip_is_allowed():
    guard = UrlGuard(resolver=_resolver({}))
    validated = guard.validate("http://93.184.216.34/page")
    assert validated.addresses == ("93.184.216.34",)


def test_host_resolving_to_private_is_blocked():
    guard = UrlGuard(resolver=_resolver({"evil.example": ["10.1.2.3"]}))
    with pytest.raises(BlockedAddressError):
        guard.validate("http://evil.example/")


def test_split_horizon_any_private_address_blocks():
    # One public and one private A record -> still blocked.
    guard = UrlGuard(resolver=_resolver({"rebind.example": ["93.184.216.34", "127.0.0.1"]}))
    with pytest.raises(BlockedAddressError):
        guard.validate("http://rebind.example/")


def test_unresolvable_host_is_blocked():
    guard = UrlGuard(resolver=_resolver({}))
    with pytest.raises(BlockedAddressError):
        guard.validate("http://nope.example/")


def test_valid_public_host_passes():
    guard = UrlGuard(resolver=_resolver({"example.com": ["93.184.216.34"]}))
    validated = guard.validate("https://example.com/article?id=5")
    assert validated.scheme == "https"
    assert validated.host == "example.com"
    assert validated.port == 443
    assert validated.addresses == ("93.184.216.34",)
