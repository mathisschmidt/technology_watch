"""Tests for the direct google-tasks-mcp client (mocked HTTP)."""
from __future__ import annotations

import json
import time
import urllib.parse
from pathlib import Path

import httpx
import pytest

from techno_watch_bot import config as config_mod
from techno_watch_bot import tasks_api
from techno_watch_bot.events import TaskInfo
from techno_watch_bot.tasks_api import (
    AuthRequired,
    McpTasksClient,
    TasksApiError,
    Token,
    load_token,
    save_token,
)

URL = "https://tasks.example.com"


@pytest.fixture(autouse=True)
def cfg_dir(tmp_path: Path, monkeypatch) -> Path:
    monkeypatch.setattr(config_mod, "CONFIG_DIR", tmp_path)
    return tmp_path


def _sse(result: dict, *, is_error: bool = False, text: str | None = None) -> httpx.Response:
    body = {"jsonrpc": "2.0", "id": 1, "result": {
        "content": [{"type": "text", "text": text if text is not None else json.dumps(result)}],
        **({"isError": True} if is_error else {}),
    }}
    return httpx.Response(200, headers={"content-type": "text/event-stream"},
                          text=f"event: message\ndata: {json.dumps(body)}\n\n")


class FakeServer:
    """Mimics google-tasks-mcp: list_task_lists, paginated list_tasks, patch_task."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.token = "tok"

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.headers.get("authorization") != f"Bearer {self.token}":
            return httpx.Response(401, json={"error": "unauthorized"})
        assert request.url.path == "/mcp"
        assert "text/event-stream" in request.headers["accept"]
        msg = json.loads(request.content)
        name, args = msg["params"]["name"], msg["params"]["arguments"]
        self.calls.append((name, args))
        if name == "list_task_lists":
            return _sse({"items": [{"id": "L1", "title": "Other"},
                                   {"id": "L2", "title": "Veille tech"}]})
        if name == "list_tasks":
            if args.get("pageToken") != "p2":
                # Real server appends warnings after the JSON.
                data = {"items": [{"id": "a", "title": "Alpha", "notes": "n", "due": "2026-12-01T00:00:00Z"},
                                  {"id": "x", "title": "", "status": "needsAction"}],
                        "nextPageToken": "p2"}
                return _sse({}, text=json.dumps(data) + "\n\n⚠️ RECURRING TASKS: blah")
            return _sse({"items": [{"id": "b", "title": "Beta", "status": "needsAction"},
                                   {"id": "c", "title": "Done", "status": "completed"}]})
        if name == "patch_task":
            if args["taskId"] == "bad":
                return _sse({}, is_error=True, text="Error: Not Found")
            return _sse({"id": args["taskId"], "status": "completed"})
        raise AssertionError(name)


def _client(server: FakeServer, token: str = "tok") -> McpTasksClient:
    return McpTasksClient(URL, transport=httpx.MockTransport(server),
                          token=Token(URL, token, time.time() + 3600))


async def test_list_open_tasks_paginates_and_filters() -> None:
    server = FakeServer()
    tasks = await _client(server).list_open_tasks("veille TECH")
    assert [(t.id, t.title, t.list_id) for t in tasks] == [("a", "Alpha", "L2"), ("b", "Beta", "L2")]
    assert tasks[0].notes == "n" and tasks[0].due.startswith("2026-12-01")
    list_calls = [a for n, a in server.calls if n == "list_tasks"]
    assert list_calls[0]["showCompleted"] is False and list_calls[1]["pageToken"] == "p2"


async def test_list_missing_names_available_lists() -> None:
    with pytest.raises(TasksApiError, match="Available: Other, Veille tech"):
        await _client(FakeServer()).list_open_tasks("Nope")


async def test_complete_task_and_tool_error() -> None:
    server = FakeServer()
    client = _client(server)
    await client.complete_task(TaskInfo("a", "Alpha", list_id="L2"))
    assert server.calls[-1] == ("patch_task", {"taskListId": "L2", "taskId": "a", "status": "completed"})
    with pytest.raises(TasksApiError, match="Not Found"):
        await client.complete_task(TaskInfo("bad", "x", list_id="L2"))


async def test_auth_required_without_token_and_on_401() -> None:
    with pytest.raises(AuthRequired):
        await McpTasksClient(URL, transport=httpx.MockTransport(FakeServer())).call_tool("list_task_lists", {})
    with pytest.raises(AuthRequired):
        await _client(FakeServer(), token="wrong").call_tool("list_task_lists", {})
    expired = McpTasksClient(URL, transport=httpx.MockTransport(FakeServer()),
                             token=Token(URL, "tok", time.time() - 10))
    with pytest.raises(AuthRequired):
        await expired.call_tool("list_task_lists", {})


def test_token_store_roundtrip_and_permissions(cfg_dir: Path) -> None:
    save_token(Token(URL, "abc", time.time() + 86400 * 30))
    tok = load_token(URL + "/")
    assert tok and tok.access_token == "abc" and tok.days_left in (29, 30)
    assert (cfg_dir / "mcp_token.json").stat().st_mode & 0o777 == 0o600
    assert load_token("https://other.example.com") is None


def test_pkce_pair() -> None:
    import base64
    import hashlib

    verifier, challenge = tasks_api.pkce_pair()
    assert 43 <= len(verifier) <= 128
    expected = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=")
    assert challenge == expected.decode()


def test_login_full_flow(monkeypatch, cfg_dir: Path) -> None:
    """DCR + PKCE + loopback redirect + token exchange against a fake OAuth server."""
    state: dict = {}

    def oauth(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/.well-known/oauth-authorization-server":
            return httpx.Response(200, json={
                "authorization_endpoint": f"{URL}/authorize",
                "token_endpoint": f"{URL}/token",
                "registration_endpoint": f"{URL}/register",
            })
        if path == "/register":
            body = json.loads(request.content)
            state["redirect_uris"] = body["redirect_uris"]
            return httpx.Response(201, json={"client_id": "cid"})
        if path == "/token":
            form = dict(urllib.parse.parse_qsl(request.content.decode()))
            import base64
            import hashlib
            challenge = base64.urlsafe_b64encode(
                hashlib.sha256(form["code_verifier"].encode()).digest()).rstrip(b"=").decode()
            assert challenge == state["challenge"]
            assert form["code"] == "the-code" and form["client_id"] == "cid"
            assert form["redirect_uri"] == state["redirect_uris"][0]
            return httpx.Response(200, json={"access_token": "mcp-tok", "token_type": "Bearer",
                                             "expires_in": 30 * 86400})
        return httpx.Response(404)

    real_client = httpx.Client
    monkeypatch.setattr(tasks_api.httpx, "Client",
                        lambda **kw: real_client(transport=httpx.MockTransport(oauth)))

    def browser(auth_url: str) -> None:
        q = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(auth_url).query))
        assert q["code_challenge_method"] == "S256" and q["client_id"] == "cid"
        state["challenge"] = q["code_challenge"]
        # The "user" signs in; the server redirects to our loopback listener.
        redirect = q["redirect_uri"] + "?" + urllib.parse.urlencode(
            {"code": "the-code", "state": q["state"]})
        assert redirect.startswith("http://127.0.0.1:")
        real_client().get(redirect)

    tok = tasks_api.login(URL, open_browser=browser, timeout=10)
    assert tok.access_token == "mcp-tok" and tok.days_left >= 29
    assert load_token(URL).access_token == "mcp-tok"


def test_login_rejects_bad_state(monkeypatch) -> None:
    def oauth(request: httpx.Request) -> httpx.Response:
        if request.url.path.startswith("/.well-known"):
            return httpx.Response(200, json={"authorization_endpoint": f"{URL}/a",
                                             "token_endpoint": f"{URL}/t",
                                             "registration_endpoint": f"{URL}/r"})
        return httpx.Response(201, json={"client_id": "cid"})

    real_client = httpx.Client
    monkeypatch.setattr(tasks_api.httpx, "Client",
                        lambda **kw: real_client(transport=httpx.MockTransport(oauth)))

    def browser(auth_url: str) -> None:
        q = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(auth_url).query))
        real_client().get(q["redirect_uri"] + "?code=c&state=forged")

    with pytest.raises(TasksApiError, match="state mismatch"):
        tasks_api.login(URL, open_browser=browser, timeout=10)


async def test_task_source_requires_configured_url() -> None:
    from techno_watch_bot.config import Settings
    from techno_watch_bot.tasks_api import McpTaskSource, NotConfigured

    src, s = McpTaskSource(), Settings()
    assert s.tasks_mcp_url == ""
    assert "not configured" in src.status(s)
    with pytest.raises(NotConfigured):
        await src.list_tasks(s)
    with pytest.raises(NotConfigured):
        src.login(s)
