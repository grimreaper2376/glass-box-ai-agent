"""
GlassBox — security guards for OAuth and MCP discovery.

Implements the mitigations the MCP specification marks **MUST** and **SHOULD**
in its Security Best Practices (2026-07-28), specifically the two that apply to
a client like this one: SSRF during OAuth metadata discovery, and dangerous URL
schemes in authorization URLs.

Why this is not theoretical
---------------------------
GlassBox's `BinanceMCPClient.discover()` deliberately *follows* URLs it is
given rather than hardcoding endpoints — it reads
`/.well-known/oauth-protected-resource`, takes the `authorization_servers` URL
from that response, fetches *that*, and takes `authorization_endpoint` and
`token_endpoint` from the result. That design is correct (it survives Binance
moving their endpoints) but it means a hostile or spoofed MCP endpoint controls
which URLs this process fetches next.

The MCP spec names exactly what an attacker does with that:

* point discovery at `http://169.254.169.254/` — the cloud metadata endpoint —
  to exfiltrate IAM credentials
* point it at `http://127.0.0.1:6379/` or similar to reach services bound to
  loopback that assume nothing external can talk to them
* point it at internal RFC-1918 addresses to map a private network
* return a `javascript:` URL as the authorization endpoint, which becomes code
  execution the moment anything calls `webbrowser.open()` or `window.open()`
  on it

CVE-2025-6514 was this class of bug in `mcp-remote`: a malicious
`authorization_endpoint` used to intercept OAuth tokens before a session was
even established.

Design note
-----------
IP validation is done with the standard library's `ipaddress` module rather
than string matching. The MCP spec explicitly warns against hand-rolling this
("attackers exploit encoding tricks (octal, hex, IPv4-mapped IPv6) that custom
parsers often miss"), and `ipaddress` already handles those forms correctly.
"""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse

# Only these two schemes may ever be opened, fetched, or handed to a browser.
# An allowlist, not a blocklist, because the spec says so and because a
# blocklist of `javascript:`/`data:`/`file:`/`vbscript:` will always be one
# scheme behind the next idea someone has.
ALLOWED_SCHEMES = ("https", "http")


class SecurityViolation(Exception):
    """A URL failed validation and must not be fetched, opened, or trusted."""


def _is_loopback_host(host: str) -> bool:
    if host in ("localhost", "::1"):
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _resolve_all(host: str) -> list[ipaddress.IPv4Address | ipaddress.IPv6Address]:
    """
    Every address a hostname resolves to.

    All of them are checked, not just the first: a DNS-rebinding attacker can
    return one public and one private address and rely on a validator that
    stops at the first.
    """
    out = []
    try:
        for family, _type, _proto, _canon, sockaddr in socket.getaddrinfo(
            host, None, proto=socket.IPPROTO_TCP
        ):
            try:
                out.append(ipaddress.ip_address(sockaddr[0]))
            except ValueError:
                continue
    except socket.gaierror:
        raise SecurityViolation(
            f"Could not resolve '{host}'. Refusing to fetch a URL whose "
            f"destination cannot be verified."
        )
    return out


def _is_blocked_address(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> str | None:
    """Returns a human reason if this address must not be reached, else None."""
    if ip.is_link_local:
        # 169.254.0.0/16 — includes the cloud metadata endpoint at
        # 169.254.169.254 that leaks IAM credentials on AWS/GCP/Azure.
        return "link-local (cloud metadata range)"
    if ip.is_private:
        return "private/internal network"
    if ip.is_loopback:
        return "loopback"
    if ip.is_reserved or ip.is_multicast or ip.is_unspecified:
        return "reserved, multicast or unspecified"
    return None


def validate_url(
    url: str,
    *,
    allow_loopback: bool = False,
    require_https: bool = True,
    purpose: str = "URL",
) -> str:
    """
    Validate a URL before this process fetches or opens it.

    `allow_loopback=True` is used only for the bundled mock MCP server and the
    local OAuth callback, both of which are legitimately on 127.0.0.1 and both
    of which are addresses *this* process chose rather than ones a remote
    server supplied.
    """
    if not url or not isinstance(url, str):
        raise SecurityViolation(f"{purpose} is empty or not a string.")

    parsed = urlparse(url.strip())

    # 1. Scheme allowlist. This is the check that stops `javascript:` and
    #    `data:` URLs reaching webbrowser.open() or window.open().
    if parsed.scheme.lower() not in ALLOWED_SCHEMES:
        raise SecurityViolation(
            f"{purpose} uses the scheme '{parsed.scheme}', which is refused. "
            f"Only https (and http on loopback) are permitted — a "
            f"javascript:, data: or file: URL here would be code execution, "
            f"not navigation."
        )

    host = parsed.hostname
    if not host:
        raise SecurityViolation(f"{purpose} has no host component.")

    loopback = _is_loopback_host(host)

    # 2. HTTPS enforcement, with a loopback exemption per OAuth 2.1 §1.5.
    if parsed.scheme.lower() == "http" and not (loopback and allow_loopback):
        raise SecurityViolation(
            f"{purpose} uses plain http. OAuth URLs must use https so the "
            f"authorization code and token cannot be read in transit."
        )
    if require_https and parsed.scheme.lower() != "https" and not (loopback and allow_loopback):
        raise SecurityViolation(f"{purpose} must use https.")

    # 3. Loopback is only ever acceptable when explicitly allowed for this call.
    if loopback:
        if allow_loopback:
            return url.strip()
        raise SecurityViolation(
            f"{purpose} points at loopback ({host}). A remote server "
            f"supplying a loopback address is trying to reach services on "
            f"this machine that assume nothing external can talk to them."
        )

    # 4. SSRF: block private, link-local, reserved and multicast destinations,
    #    checking *every* address the name resolves to.
    for ip in _resolve_all(host):
        reason = _is_blocked_address(ip)
        if reason:
            raise SecurityViolation(
                f"{purpose} resolves to {ip} ({reason}). Refusing — this is "
                f"the shape of an SSRF attempt against internal "
                f"infrastructure or a cloud metadata endpoint."
            )

    return url.strip()


def validate_discovery_url(url: str, *, allow_loopback: bool = False) -> str:
    """An OAuth metadata URL this process is about to fetch."""
    return validate_url(
        url, allow_loopback=allow_loopback, purpose="OAuth discovery URL"
    )


def validate_authorization_url(url: str) -> str:
    """
    An authorization URL about to be handed to a browser.

    Never loopback-exempt: this URL goes to a real browser, and a remote
    server should never be able to aim that at this machine.
    """
    return validate_url(url, allow_loopback=False, purpose="Authorization URL")


def validate_client_id_url(url: str) -> str:
    """
    A Client ID Metadata Document URL.

    Binance's authorization server will fetch whatever URL is put here, so an
    unvalidated value turns *their* server into the SSRF victim. The MCP spec
    calls this out directly under "SSRF Against Authorization Servers."
    """
    return validate_url(url, allow_loopback=False, purpose="Client ID URL")


def security_headers() -> dict[str, str]:
    """
    Response headers for the local dashboard.

    Defence in depth: the dashboard is loopback-only and already requires a
    per-process session token for every write, but a strict CSP means that
    even if some future change introduced an injection point, injected script
    could not execute, and `frame-ancestors 'none'` prevents another page
    framing the console to trick someone into clicking through it.
    """
    return {
        "Content-Security-Policy": (
            "default-src 'self'; "
            "script-src 'self' 'unsafe-inline'; "  # inline handlers in app.js
            "style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data:; "
            "connect-src 'self'; "
            "frame-ancestors 'none'; "
            "base-uri 'self'; "
            "form-action 'self'"
        ),
        "X-Frame-Options": "DENY",
        "X-Content-Type-Options": "nosniff",
        "Referrer-Policy": "no-referrer",
        "Permissions-Policy": "geolocation=(), microphone=(), camera=()",
        # This console is never a public site and must never be cached by an
        # intermediary that might serve it to someone else.
        "Cache-Control": "no-store",
    }
