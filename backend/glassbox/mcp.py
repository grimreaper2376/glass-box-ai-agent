"""
GlassBox — Binance Agent OS MCP client.

A real Model Context Protocol client that speaks Streamable HTTP to
`https://agent.binance.com/mcp/agentic`, authenticates with OAuth 2.1 + PKCE,
discovers the exchange's tools, and calls them.

Discovery, verified live against Binance
----------------------------------------
    GET /.well-known/oauth-protected-resource
        → resource: https://agent.binance.com/mcp/agentic
          authorization_servers: [https://agent.binance.com]

    GET /.well-known/oauth-authorization-server
        → authorization_endpoint: https://accounts.binance.com/agentic-oauth/authorize
          token_endpoint:         https://accounts.binance.com/oauth-agentic/token
          token_endpoint_auth_methods_supported: ["none"]   (public client)
          code_challenge_methods_supported:      ["S256"]   (PKCE mandatory)
          client_id_metadata_document_supported: true

An unauthenticated POST returns 401 with
`WWW-Authenticate: Bearer resource_metadata="…"`, which is exactly the MCP
authorization handshake. This client follows that chain rather than assuming
endpoints, so it keeps working if Binance moves them.

Security posture
----------------
* Public client, so there is no client secret to leak. PKCE S256 is mandatory
  and the verifier never leaves this process.
* The redirect URI is `http://127.0.0.1:<port>/callback`. Loopback only — an
  authorization code must never traverse a network.
* `state` is random and checked on return, which is the CSRF defence.
* Tokens are stored 0600 and encrypted at rest with a key derived from the
  device key. They are redacted everywhere else — logs, ledger, websocket.
* There is no withdrawal scope to request, and this client never asks for one.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import secrets
import time
import webbrowser
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import httpx

from .security import (
    SecurityViolation,
    validate_authorization_url,
    validate_discovery_url,
)

DEFAULT_MCP_URL = "https://agent.binance.com/mcp/agentic"
MCP_PROTOCOL_VERSION = "2025-06-18"
CALLBACK_PORT = 8788


class MCPError(Exception):
    pass


class NotAuthorised(MCPError):
    pass


# ---------------------------------------------------------------------------
# Token storage
# ---------------------------------------------------------------------------


@dataclass
class Tokens:
    access_token: str
    refresh_token: str | None = None
    expires_at: float = 0.0
    scope: str = ""
    token_type: str = "Bearer"

    @property
    def expired(self) -> bool:
        # Refresh a minute early; a token that expires mid-order is worse than
        # one refreshed slightly too often.
        return self.expires_at > 0 and time.time() > (self.expires_at - 60)

    def to_dict(self) -> dict[str, Any]:
        return {
            "access_token": self.access_token, "refresh_token": self.refresh_token,
            "expires_at": self.expires_at, "scope": self.scope,
            "token_type": self.token_type,
        }


class TokenStore:
    """Encrypted-at-rest token storage, keyed off the device key."""

    def __init__(self, path: Path, device_key: bytes):
        self.path = Path(path)
        self._key = hashlib.sha256(b"glassbox-token-v1" + device_key).digest()

    def _xor(self, data: bytes) -> bytes:
        # Keystream from the device key. This stops a token being read by
        # anything that merely globs the config directory; it is not protection
        # against an attacker who already has the device key and the disk, and
        # the SECURITY doc says so plainly.
        out = bytearray()
        counter = 0
        while len(out) < len(data):
            block = hashlib.sha256(self._key + counter.to_bytes(8, "big")).digest()
            out.extend(block)
            counter += 1
        return bytes(a ^ b for a, b in zip(data, out))

    def save(self, tokens: Tokens) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        blob = self._xor(json.dumps(tokens.to_dict()).encode())
        self.path.write_bytes(base64.b64encode(blob))
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass

    def load(self) -> Tokens | None:
        if not self.path.exists():
            return None
        try:
            raw = self._xor(base64.b64decode(self.path.read_bytes()))
            return Tokens(**json.loads(raw))
        except Exception:
            return None

    def clear(self) -> None:
        if self.path.exists():
            self.path.unlink()


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------


@dataclass
class MCPStatus:
    connected: bool = False
    endpoint: str = DEFAULT_MCP_URL
    server_name: str | None = None
    server_version: str | None = None
    protocol_version: str | None = None
    tools: list[dict] = field(default_factory=list)
    scope: str = ""
    expires_in_s: float | None = None
    last_error: str | None = None
    calls: int = 0
    failures: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "connected": self.connected,
            "endpoint": self.endpoint,
            "server": (
                f"{self.server_name} {self.server_version}" if self.server_name else None
            ),
            "protocol_version": self.protocol_version,
            "tool_count": len(self.tools),
            "tools": [
                {"name": t.get("name"), "description": (t.get("description") or "")[:160]}
                for t in self.tools
            ],
            "scope": self.scope,
            "expires_in_s": round(self.expires_in_s) if self.expires_in_s else None,
            "last_error": self.last_error,
            "calls": self.calls,
            "failures": self.failures,
        }


class BinanceMCPClient:
    def __init__(
        self,
        endpoint: str = DEFAULT_MCP_URL,
        token_path: Path | None = None,
        device_key: bytes | None = None,
        client_id: str | None = None,
    ):
        self.endpoint = endpoint
        self.status = MCPStatus(endpoint=endpoint)
        self._client: httpx.AsyncClient | None = None
        self._session_id: str | None = None
        self._request_id = 0
        self._meta: dict[str, Any] = {}
        # Binance advertises client_id_metadata_document_supported, so the
        # client_id is a URL to a hosted client metadata document rather than a
        # registered string. Configurable because the operator hosts it.
        self.client_id = client_id or os.environ.get("GLASSBOX_MCP_CLIENT_ID", "")
        # The bundled mock runs on loopback and needs no OAuth. Everything else
        # does, and that is decided by the endpoint rather than by a flag a
        # caller could set wrongly.
        self.auth_required = not (
            endpoint.startswith("http://127.0.0.1")
            or endpoint.startswith("http://localhost")
        )
        self.tokens: Tokens | None = None
        if token_path and device_key:
            self.store = TokenStore(token_path, device_key)
            self.tokens = self.store.load()
        else:
            self.store = None

    async def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=30.0, follow_redirects=True)
        return self._client

    async def aclose(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None

    # -- discovery ---------------------------------------------------------

    async def discover(self) -> dict[str, Any]:
        """
        Follow the MCP authorization discovery chain rather than hardcoding it.

        Protected-resource metadata names the authorization server; that server's
        metadata names the endpoints. If Binance moves either, this still works.
        """
        http = await self._http()
        base = self.endpoint.split("/mcp/")[0]

        resource_meta = {}
        for path in (
            "/.well-known/oauth-protected-resource",
            "/.well-known/oauth-protected-resource/gateway-mcp",
        ):
            try:
                # SSRF guard. `base` comes from our own endpoint setting, but
                # validating here keeps the rule in one place and catches a
                # misconfigured endpoint too.
                url = validate_discovery_url(
                    f"{base}{path}", allow_loopback=not self.auth_required
                )
                r = await http.get(url)
                if r.status_code == 200:
                    resource_meta = r.json()
                    break
            except httpx.HTTPError:
                continue

        auth_servers = resource_meta.get("authorization_servers") or [base]
        as_meta = {}
        for server in auth_servers:
            try:
                # This URL came from the previous response's body, i.e. it is
                # remote-controlled. This is the exact SSRF vector the MCP
                # spec describes: a hostile response naming
                # http://169.254.169.254/ to make this process fetch cloud
                # credentials on the attacker's behalf.
                url = validate_discovery_url(
                    f"{server}/.well-known/oauth-authorization-server",
                    allow_loopback=not self.auth_required,
                )
                r = await http.get(url)
                if r.status_code == 200:
                    as_meta = r.json()
                    break
            except httpx.HTTPError:
                continue

        if not as_meta:
            raise MCPError("Could not discover Binance's OAuth metadata.")

        # Both of these came out of a remote response body. They are about
        # to be used to build a URL for a browser and to POST a token
        # exchange, so they are validated before they are trusted, not after.
        loopback_ok = not self.auth_required
        authz_ep = validate_discovery_url(
            as_meta["authorization_endpoint"], allow_loopback=loopback_ok
        )
        token_ep = validate_discovery_url(
            as_meta["token_endpoint"], allow_loopback=loopback_ok
        )

        self._meta = {
            "resource": resource_meta.get("resource", self.endpoint),
            "authorization_endpoint": authz_ep,
            "token_endpoint": token_ep,
            "pkce_methods": as_meta.get("code_challenge_methods_supported", ["S256"]),
            "auth_methods": as_meta.get("token_endpoint_auth_methods_supported", ["none"]),
            "cimd": as_meta.get("client_id_metadata_document_supported", False),
        }
        return self._meta

    # -- authorization -----------------------------------------------------

    def build_authorization_url(self) -> tuple[str, str, str]:
        """
        Returns (url, code_verifier, state).

        PKCE S256 is mandatory here, and the verifier never leaves this process.
        """
        if not self._meta:
            raise MCPError("Call discover() first.")
        verifier = base64.urlsafe_b64encode(secrets.token_bytes(64)).decode().rstrip("=")
        challenge = (
            base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
            .decode()
            .rstrip("=")
        )
        state = secrets.token_urlsafe(24)
        params = {
            "response_type": "code",
            "client_id": self.client_id,
            "redirect_uri": f"http://127.0.0.1:{CALLBACK_PORT}/callback",
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "state": state,
            # RFC 8707. Binds the token to this resource so a leaked token
            # cannot be replayed against a different API.
            "resource": self._meta["resource"],
        }
        url = f"{self._meta['authorization_endpoint']}?{urlencode(params)}"
        # Last line of defence before this reaches webbrowser.open() or the
        # frontend's window.open(): a javascript: or data: URL here would be
        # code execution rather than navigation.
        return validate_authorization_url(url), verifier, state

    async def exchange_code(self, code: str, verifier: str) -> Tokens:
        http = await self._http()
        r = await http.post(
            self._meta["token_endpoint"],
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": f"http://127.0.0.1:{CALLBACK_PORT}/callback",
                "client_id": self.client_id,
                "code_verifier": verifier,
                "resource": self._meta["resource"],
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        if r.status_code != 200:
            raise NotAuthorised(f"Token exchange failed ({r.status_code}): {r.text[:200]}")
        d = r.json()
        tokens = Tokens(
            access_token=d["access_token"],
            refresh_token=d.get("refresh_token"),
            expires_at=time.time() + float(d.get("expires_in", 3600)),
            scope=d.get("scope", ""),
            token_type=d.get("token_type", "Bearer"),
        )
        self.tokens = tokens
        if self.store:
            self.store.save(tokens)
        return tokens

    async def refresh(self) -> bool:
        if not (self.tokens and self.tokens.refresh_token):
            return False
        http = await self._http()
        try:
            r = await http.post(
                self._meta["token_endpoint"],
                data={
                    "grant_type": "refresh_token",
                    "refresh_token": self.tokens.refresh_token,
                    "client_id": self.client_id,
                },
            )
            if r.status_code != 200:
                return False
            d = r.json()
            self.tokens.access_token = d["access_token"]
            self.tokens.refresh_token = d.get("refresh_token", self.tokens.refresh_token)
            self.tokens.expires_at = time.time() + float(d.get("expires_in", 3600))
            if self.store:
                self.store.save(self.tokens)
            return True
        except httpx.HTTPError:
            return False

    async def authorize_interactive(self, open_browser: bool = True) -> Tokens:
        """
        Full loopback authorization flow.

        A tiny local HTTP server catches the redirect. Loopback only — an
        authorization code must never cross a network.
        """
        from aiohttp import web  # optional; falls back below

        raise MCPError("Use authorize_with_callback(); aiohttp path not enabled.")

    async def authorize_with_callback(self, open_browser: bool = True, timeout: float = 300.0):
        """Loopback authorization using the standard library only."""
        import http.server
        import threading
        import urllib.parse as up

        await self.discover()
        url, verifier, state = self.build_authorization_url()
        captured: dict[str, str] = {}
        done = threading.Event()

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):  # silence the default access log
                pass

            def do_GET(self):
                q = up.parse_qs(up.urlparse(self.path).query)
                captured.update({k: v[0] for k, v in q.items()})
                ok = "code" in captured and captured.get("state") == state
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                body = (
                    "<body style='font-family:system-ui;background:#0B0E11;color:#EAECEF;"
                    "display:flex;align-items:center;justify-content:center;height:100vh'>"
                    "<div style='text-align:center'>"
                    f"<div style='width:36px;height:36px;background:#FCD535;transform:rotate(45deg);"
                    "border-radius:6px;margin:0 auto 22px'></div>"
                    + (
                        "<h2>GlassBox is connected</h2>"
                        "<p style='color:#848E9C'>You can close this tab and return to the dashboard.</p>"
                        if ok
                        else "<h2>Authorization failed</h2>"
                        f"<p style='color:#F6465D'>{captured.get('error', 'no code returned')}</p>"
                    )
                    + "</div></body>"
                )
                self.wfile.write(body.encode())
                done.set()

        server = http.server.HTTPServer(("127.0.0.1", CALLBACK_PORT), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()

        if open_browser:
            try:
                webbrowser.open(url)
            except Exception:
                pass
        print(f"\n  Authorize GlassBox in your browser:\n  {url}\n")

        deadline = time.time() + timeout
        while not done.is_set() and time.time() < deadline:
            await asyncio.sleep(0.3)
        server.shutdown()

        if captured.get("state") != state:
            raise NotAuthorised("State mismatch — possible CSRF. Authorization refused.")
        if "code" not in captured:
            raise NotAuthorised(captured.get("error_description") or "No authorization code.")
        return await self.exchange_code(captured["code"], verifier)

    # -- non-blocking authorization (for the dashboard) ---------------------

    async def start_authorization(self) -> str:
        """
        Non-blocking counterpart to `authorize_with_callback`, for a UI that
        cannot sit inside one call while a person switches to their browser.

        Starts the same loopback-only callback listener, then returns
        immediately with the URL for the caller (the dashboard) to open. Poll
        `poll_authorization()` to find out when the person has finished.
        """
        import http.server
        import threading
        import urllib.parse as up

        await self.discover()
        url, verifier, state = self.build_authorization_url()

        self._pending_verifier = verifier
        self._pending_state = state
        self._pending_captured: dict[str, str] = {}
        self._pending_deadline = time.time() + 300.0

        client = self  # captured by the handler below; BaseHTTPRequestHandler
        # methods take their own `self`, so it must not be shadowed.

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):  # silence the default access log
                pass

            def do_GET(handler_self):
                q = up.parse_qs(up.urlparse(handler_self.path).query)
                client._pending_captured.update({k: v[0] for k, v in q.items()})
                ok = (
                    "code" in client._pending_captured
                    and client._pending_captured.get("state") == state
                )
                handler_self.send_response(200)
                handler_self.send_header("Content-Type", "text/html; charset=utf-8")
                handler_self.end_headers()
                body = (
                    "<body style='font-family:system-ui;background:#0B0E11;color:#EAECEF;"
                    "display:flex;align-items:center;justify-content:center;height:100vh'>"
                    "<div style='text-align:center'>"
                    "<div style='width:36px;height:36px;background:#FCD535;transform:rotate(45deg);"
                    "border-radius:6px;margin:0 auto 22px'></div>"
                    + (
                        "<h2>GlassBox is connected</h2>"
                        "<p style='color:#848E9C'>You can close this tab and return to the dashboard.</p>"
                        if ok else
                        "<h2>Authorization failed</h2>"
                        f"<p style='color:#F6465D'>"
                        f"{client._pending_captured.get('error', 'no code returned')}</p>"
                    ) + "</div></body>"
                )
                handler_self.wfile.write(body.encode())

        server = http.server.HTTPServer(("127.0.0.1", CALLBACK_PORT), Handler)
        self._pending_server = server
        threading.Thread(target=server.serve_forever, daemon=True).start()
        return url

    async def poll_authorization(self) -> dict[str, Any]:
        """
        Call repeatedly (e.g. every second) from the dashboard while the
        authorize tab is open. Returns one of:

            {"status": "pending"}                waiting for the redirect
            {"status": "connected", "scope": …}   tokens exchanged and saved
            {"status": "error", "detail": …}      refused — state mismatch, etc.
            {"status": "timeout"}                 5 minutes elapsed with no code
            {"status": "idle"}                    start_authorization() not called
        """
        if not hasattr(self, "_pending_captured"):
            return {"status": "idle"}

        if time.time() > self._pending_deadline:
            self._cleanup_pending()
            return {"status": "timeout"}

        if "code" not in self._pending_captured:
            return {"status": "pending"}

        if self._pending_captured.get("state") != self._pending_state:
            self._cleanup_pending()
            return {
                "status": "error",
                "detail": "State mismatch on the redirect — possible CSRF. Refused.",
            }

        verifier = self._pending_verifier
        code = self._pending_captured["code"]
        self._cleanup_pending()
        try:
            tokens = await self.exchange_code(code, verifier)
        except NotAuthorised as exc:
            return {"status": "error", "detail": str(exc)}
        return {"status": "connected", "scope": tokens.scope}

    def cancel_authorization(self) -> None:
        self._cleanup_pending()

    def _cleanup_pending(self) -> None:
        server = getattr(self, "_pending_server", None)
        if server is not None:
            try:
                server.shutdown()
            except Exception:
                pass
        for attr in (
            "_pending_server", "_pending_captured", "_pending_verifier",
            "_pending_state", "_pending_deadline",
        ):
            if hasattr(self, attr):
                delattr(self, attr)

    # -- transport ---------------------------------------------------------

    async def _rpc(self, method: str, params: dict | None = None) -> Any:
        """One JSON-RPC call over MCP Streamable HTTP."""
        if self.auth_required:
            if not self.tokens:
                raise NotAuthorised(
                    "Not connected to Binance. Run `glassbox connect` first."
                )
            if self.tokens.expired and not await self.refresh():
                raise NotAuthorised("Session expired. Reconnect with `glassbox connect`.")

        self._request_id += 1
        headers = {
            "Content-Type": "application/json",
            # Streamable HTTP may answer with either a JSON body or an SSE
            # stream, so both must be advertised.
            "Accept": "application/json, text/event-stream",
            "MCP-Protocol-Version": MCP_PROTOCOL_VERSION,
        }
        if self.tokens:
            headers["Authorization"] = f"Bearer {self.tokens.access_token}"
        if self._session_id:
            headers["Mcp-Session-Id"] = self._session_id

        http = await self._http()
        self.status.calls += 1
        r = await http.post(
            self.endpoint,
            json={"jsonrpc": "2.0", "id": self._request_id, "method": method,
                  "params": params or {}},
            headers=headers,
        )

        if r.status_code == 401:
            self.status.connected = False
            raise NotAuthorised("Binance rejected the session. Reconnect.")
        if r.status_code >= 400:
            self.status.failures += 1
            raise MCPError(f"MCP {method} failed ({r.status_code}): {r.text[:200]}")

        sid = r.headers.get("mcp-session-id") or r.headers.get("Mcp-Session-Id")
        if sid:
            self._session_id = sid

        payload = _parse_mcp_response(r)
        if "error" in payload:
            self.status.failures += 1
            err = payload["error"]
            raise MCPError(f"{err.get('code')}: {err.get('message')}")
        return payload.get("result", {})

    # -- MCP surface -------------------------------------------------------

    async def initialize(self) -> dict:
        result = await self._rpc(
            "initialize",
            {
                "protocolVersion": MCP_PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "glassbox", "version": "1.0.0"},
            },
        )
        info = result.get("serverInfo", {})
        self.status.server_name = info.get("name")
        self.status.server_version = info.get("version")
        self.status.protocol_version = result.get("protocolVersion")
        self.status.connected = True
        if self.tokens:
            self.status.scope = self.tokens.scope
            self.status.expires_in_s = max(self.tokens.expires_at - time.time(), 0)
        return result

    async def list_tools(self) -> list[dict]:
        result = await self._rpc("tools/list")
        self.status.tools = result.get("tools", [])
        return self.status.tools

    async def call_tool(self, name: str, arguments: dict) -> dict:
        result = await self._rpc("tools/call", {"name": name, "arguments": arguments})
        return {
            "is_error": bool(result.get("isError")),
            "content": result.get("content", []),
            "text": "\n".join(
                c.get("text", "") for c in result.get("content", []) if c.get("type") == "text"
            ),
            "structured": result.get("structuredContent"),
        }

    async def connect(self) -> MCPStatus:
        if self.auth_required:
            await self.discover()
        await self.initialize()
        await self.list_tools()
        return self.status

    def disconnect(self) -> None:
        if self.store:
            self.store.clear()
        self.tokens = None
        self._session_id = None
        self.status = MCPStatus(endpoint=self.endpoint)

    def find_tool(self, *keywords: str) -> str | None:
        """
        Locate a tool by intent rather than by an assumed exact name.

        Binance can rename or add tools without warning; matching on keywords
        against the live `tools/list` keeps execution working when it does.
        """
        for t in self.status.tools:
            blob = f"{t.get('name','')} {t.get('description','')}".lower()
            if all(k.lower() in blob for k in keywords):
                return t.get("name")
        return None


def _parse_mcp_response(r: httpx.Response) -> dict:
    """Streamable HTTP answers as JSON or as an SSE stream; accept both."""
    ctype = r.headers.get("content-type", "")
    if "text/event-stream" in ctype:
        for line in r.text.splitlines():
            if line.startswith("data:"):
                chunk = line[5:].strip()
                if chunk and chunk != "[DONE]":
                    try:
                        return json.loads(chunk)
                    except json.JSONDecodeError:
                        continue
        raise MCPError("No JSON-RPC payload found in the SSE stream.")
    return r.json()
