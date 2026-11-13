"""Google Tasks through the user's own MCP server, called directly from Python.

The server (github.com/mathisschmidt/google-tasks-mcp) speaks MCP over HTTP:
each JSON-RPC message is one stateless POST to ``/mcp`` with a Bearer token,
and the answer comes back as a one-event SSE stream.

Auth is the server's OAuth 2.1 flow: dynamic client registration, PKCE (S256),
a browser sign-in, and a loopback redirect to this app. The resulting access
token lasts 30 days (no refresh token) and is cached in the config folder.
"""
from __future__ import annotations

import base64
import hashlib
import http.server
import json
import os
import secrets
import threading
import time
import urllib.parse
import webbrowser
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from . import config as config_mod
from .config import Settings
from .events import TaskInfo


class TasksApiError(RuntimeError):
    pass


class NotConfigured(TasksApiError):
    """The MCP server URL is not set in Settings."""


class AuthRequired(TasksApiError):
    """No valid token: the user must connect Google Tasks (Home → Connect)."""


# ==========================================================================
# Token cache
# ==========================================================================
@dataclass
class Token:
    server_url: str
    access_token: str
    expires_at: float

    @property
    def valid(self) -> bool:
        return bool(self.access_token) and time.time() < self.expires_at - 60

    @property
    def days_left(self) -> int:
        return max(0, int((self.expires_at - time.time()) // 86400))


def token_path() -> Path:
    return config_mod.CONFIG_DIR / "mcp_token.json"


def load_token(server_url: str) -> Token | None:
    try:
        data = json.loads(token_path().read_text(encoding="utf-8"))
        tok = Token(data["server_url"], data["access_token"], float(data["expires_at"]))
    except (OSError, ValueError, KeyError, TypeError):
        return None
    # A token is only valid for the server that issued it.
    if tok.server_url.rstrip("/") != server_url.rstrip("/"):
        return None
    return tok


def save_token(tok: Token) -> None:
    path = token_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(tok.__dict__, fh)
    tmp.replace(path)


def clear_token() -> None:
    token_path().unlink(missing_ok=True)


# ==========================================================================
# OAuth login (browser + loopback redirect)
# ==========================================================================
def pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)[:96]
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=")
    return verifier, challenge.decode()


_DONE_PAGE = (
    "<html><body style='font-family:sans-serif;padding:3em'>"
    "<h2>{title}</h2><p>You can close this tab and go back to techno-watch-bot.</p>"
    "</body></html>"
)


class _CallbackHandler(http.server.BaseHTTPRequestHandler):
    result: dict[str, str] = {}
    done = threading.Event()

    def do_GET(self) -> None:  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path != "/callback":
            self.send_response(404)
            self.end_headers()
            return
        params = dict(urllib.parse.parse_qsl(parsed.query))
        type(self).result = params
        ok = "code" in params
        self.send_response(200 if ok else 400)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        title = "Google Tasks connected ✔" if ok else f"Sign-in failed: {params.get('error', '?')}"
        self.wfile.write(_DONE_PAGE.format(title=title).encode())
        type(self).done.set()

    def log_message(self, *args: Any) -> None:  # keep the TUI clean
        pass


def login(
    server_url: str,
    *,
    open_browser: Callable[[str], Any] = webbrowser.open,
    timeout: float = 300,
) -> Token:
    """Run the full OAuth flow. Blocking: call it from a thread."""
    base = server_url.rstrip("/")
    with httpx.Client(timeout=30) as web:
        meta = web.get(f"{base}/.well-known/oauth-authorization-server")
        meta.raise_for_status()
        meta = meta.json()

        handler = type("Handler", (_CallbackHandler,), {"result": {}, "done": threading.Event()})
        srv = _start_loopback(handler)
        try:
            redirect_uri = f"http://127.0.0.1:{srv.server_address[1]}/callback"
            reg = web.post(meta["registration_endpoint"], json={
                "client_name": "techno-watch-bot",
                "redirect_uris": [redirect_uri],
                "grant_types": ["authorization_code"],
                "response_types": ["code"],
                "token_endpoint_auth_method": "none",
            })
            if reg.status_code >= 400:
                raise TasksApiError(f"Client registration failed: {reg.text[:200]}")
            client_id = reg.json()["client_id"]

            verifier, challenge = pkce_pair()
            state = secrets.token_urlsafe(24)
            auth_url = meta["authorization_endpoint"] + "?" + urllib.parse.urlencode({
                "response_type": "code",
                "client_id": client_id,
                "redirect_uri": redirect_uri,
                "state": state,
                "code_challenge": challenge,
                "code_challenge_method": "S256",
            })
            open_browser(auth_url)
            if not handler.done.wait(timeout):
                raise TasksApiError("Timed out waiting for the browser sign-in.")
            params = handler.result
        finally:
            srv.shutdown()
            srv.server_close()

        if params.get("state") != state:
            raise TasksApiError("Sign-in rejected: state mismatch.")
        if "code" not in params:
            raise TasksApiError(f"Sign-in failed: {params.get('error_description') or params.get('error')}")

        tok = web.post(meta["token_endpoint"], data={
            "grant_type": "authorization_code",
            "code": params["code"],
            "code_verifier": verifier,
            "redirect_uri": redirect_uri,
            "client_id": client_id,
        })
        if tok.status_code >= 400:
            raise TasksApiError(f"Token exchange failed: {tok.text[:200]}")
        data = tok.json()
    token = Token(base, data["access_token"], time.time() + int(data.get("expires_in", 0)))
    save_token(token)
    return token


def _start_loopback(handler: type[http.server.BaseHTTPRequestHandler]) -> http.server.HTTPServer:
    srv = http.server.HTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


# ==========================================================================
# MCP tool calls
# ==========================================================================
def _parse_mcp_response(resp: httpx.Response) -> dict[str, Any]:
    """Return the JSON-RPC message from a JSON or SSE response."""
    ctype = resp.headers.get("content-type", "")
    if "text/event-stream" in ctype:
        for line in resp.text.splitlines():
            if line.startswith("data:"):
                payload = line[5:].strip()
                if payload:
                    return json.loads(payload)
        raise TasksApiError("Empty response from the MCP server.")
    return resp.json()


def _tool_json(text: str) -> Any:
    """Tool results are JSON text, sometimes followed by warning paragraphs."""
    try:
        value, _ = json.JSONDecoder().raw_decode(text.lstrip())
        return value
    except json.JSONDecodeError as exc:
        raise TasksApiError(f"Unexpected tool output: {text[:200]}") from exc


class McpTasksClient:
    """Thin async client for the google-tasks-mcp server."""

    def __init__(self, server_url: str, *, transport: httpx.AsyncBaseTransport | None = None,
                 token: Token | None = None) -> None:
        self.server_url = server_url.rstrip("/")
        self._transport = transport
        self._token = token
        self._ids = 0

    @property
    def token(self) -> Token | None:
        if self._token is None or self._token.server_url != self.server_url:
            self._token = load_token(self.server_url)
        return self._token

    def forget_token(self) -> None:
        self._token = None

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        tok = self.token
        if tok is None or not tok.valid:
            raise AuthRequired("Google Tasks is not connected (or the 30-day sign-in expired).")
        self._ids += 1
        body = {"jsonrpc": "2.0", "id": self._ids, "method": "tools/call",
                "params": {"name": name, "arguments": arguments}}
        headers = {
            "Authorization": f"Bearer {tok.access_token}",
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
        }
        async with httpx.AsyncClient(transport=self._transport, timeout=60) as web:
            try:
                resp = await web.post(f"{self.server_url}/mcp", json=body, headers=headers)
            except httpx.HTTPError as exc:
                raise TasksApiError(f"Cannot reach {self.server_url}: {exc}") from exc
        if resp.status_code == 401:
            self.forget_token()
            raise AuthRequired("The Google Tasks server rejected the token: sign in again.")
        if resp.status_code >= 400:
            raise TasksApiError(f"MCP server error {resp.status_code}: {resp.text[:200]}")
        msg = _parse_mcp_response(resp)
        if "error" in msg:
            raise TasksApiError(f"{name}: {msg['error'].get('message', msg['error'])}")
        result = msg.get("result") or {}
        text = "".join(c.get("text", "") for c in result.get("content", []) if c.get("type") == "text")
        if result.get("isError"):
            raise TasksApiError(f"{name}: {text[:300]}")
        return _tool_json(text)

    # ----------------------------------------------------------- high level
    async def find_list_id(self, list_name: str) -> str:
        page_token = None
        names = []
        while True:
            args = {"pageToken": page_token} if page_token else {}
            data = await self.call_tool("list_task_lists", args)
            for item in data.get("items", []):
                names.append(item.get("title", ""))
                if item.get("title", "").strip().lower() == list_name.strip().lower():
                    return item["id"]
            page_token = data.get("nextPageToken")
            if not page_token:
                break
        raise TasksApiError(
            f'Task list "{list_name}" not found. Available: {", ".join(names) or "none"}'
        )

    async def list_open_tasks(self, list_name: str) -> list[TaskInfo]:
        list_id = await self.find_list_id(list_name)
        tasks: list[TaskInfo] = []
        page_token = None
        while True:
            args: dict[str, Any] = {"taskListId": list_id, "showCompleted": False,
                                    "showHidden": False, "maxResults": 100}
            if page_token:
                args["pageToken"] = page_token
            data = await self.call_tool("list_tasks", args)
            for t in data.get("items", []):
                if t.get("status") == "completed" or t.get("deleted") or not t.get("id"):
                    continue
                if not (t.get("title") or "").strip():
                    continue  # Google keeps empty placeholder tasks
                tasks.append(TaskInfo(
                    id=t["id"], title=t["title"].strip(), notes=t.get("notes") or "",
                    due=t.get("due") or "", list_id=list_id,
                ))
            page_token = data.get("nextPageToken")
            if not page_token:
                return tasks

    async def complete_task(self, task: TaskInfo) -> None:
        await self.call_tool("patch_task", {
            "taskListId": task.list_id, "taskId": task.id, "status": "completed",
        })


class McpTaskSource:
    """TaskSource backed by the MCP server configured in Settings."""

    def __init__(self) -> None:
        self._client: McpTasksClient | None = None

    def client(self, settings: Settings) -> McpTasksClient:
        if not settings.tasks_mcp_url:
            raise NotConfigured(
                "No Google Tasks MCP server URL yet: set it in Settings (s on Home)."
            )
        if self._client is None or self._client.server_url != settings.tasks_mcp_url.rstrip("/"):
            self._client = McpTasksClient(settings.tasks_mcp_url)
        return self._client

    def status(self, settings: Settings) -> str:
        if not settings.tasks_mcp_url:
            return "not configured: set the URL in Settings"
        tok = self.client(settings).token
        if tok and tok.valid:
            return f"connected ({tok.days_left} days left)"
        return "not connected"

    async def list_tasks(self, settings: Settings) -> list[TaskInfo]:
        return await self.client(settings).list_open_tasks(settings.task_list_name)

    async def complete_task(self, settings: Settings, task: TaskInfo) -> None:
        await self.client(settings).complete_task(task)

    def login(self, settings: Settings) -> str:
        """Blocking — run in a thread."""
        self.client(settings)  # raises NotConfigured when no URL is set
        tok = login(settings.tasks_mcp_url)
        self.client(settings).forget_token()
        return f"connected ({tok.days_left} days left)"
