"""Thin wrapper around the Google Tasks API."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Optional

from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

if TYPE_CHECKING:
    from .config import Config

log = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/tasks"]

_CLIENT_CONFIG = {
    "installed": {
        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
        "token_uri": "https://oauth2.googleapis.com/token",
        "redirect_uris": ["http://localhost"],
    }
}


@dataclass
class GTask:
    id: str
    title: str
    notes: str
    list_id: str
    status: str  # "needsAction" | "completed"


def _credentials(config: "Config") -> Credentials:
    cache = config.google_token
    creds: Optional[Credentials] = None

    if cache.token or cache.refresh_token:
        log.debug("Found cached token (refresh_token present: %s)", bool(cache.refresh_token))
        from datetime import datetime
        creds = Credentials(
            token=cache.token,
            refresh_token=cache.refresh_token,
            token_uri=cache.token_uri,
            client_id=config.google_client_id,
            client_secret=config.google_client_secret,
            scopes=cache.scopes,
            # Force expiry so the refresh path always runs on startup — this
            # catches revoked/invalid_grant errors in ensure_authenticated()
            # rather than silently failing mid-request.
            expiry=datetime(2000, 1, 1),
        )

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            log.debug("Token expired — refreshing…")
            try:
                creds.refresh(Request())
                log.debug("Token refreshed successfully")
            except RefreshError as exc:
                log.warning("Token refresh failed (%s) — re-authenticating", exc)
                creds = None
        if not creds or not creds.valid:
            log.debug("No valid credentials — starting OAuth flow")
            client_config = {
                "installed": {
                    **_CLIENT_CONFIG["installed"],
                    "client_id": config.google_client_id,
                    "client_secret": config.google_client_secret,
                }
            }
            flow = InstalledAppFlow.from_client_config(client_config, SCOPES)
            auth_url, _ = flow.authorization_url(prompt="consent")
            print(
                f"\nGoogle OAuth required. Open this URL in your browser:\n\n  {auth_url}\n",
                flush=True,
            )
            creds = flow.run_local_server(port=0, open_browser=True)
            log.debug("OAuth flow completed")
        config.save_token(creds)
        log.debug("Token saved to config")
    else:
        log.debug("Using valid cached credentials")

    return creds


def ensure_authenticated(config: "Config") -> None:
    """Pre-flight auth check — run this before starting the TUI."""
    _credentials(config)


def _service(config: "Config"):
    return build("tasks", "v1", credentials=_credentials(config), cache_discovery=False)


def find_list_id(config: "Config", list_name: str) -> str:
    log.debug("Fetching task lists…")
    svc = _service(config)
    results = svc.tasklists().list(maxResults=100).execute()
    lists = results.get("items", [])
    log.debug("Found %d task list(s): %s", len(lists), [l["title"] for l in lists])
    for item in lists:
        if item["title"].lower() == list_name.lower():
            log.debug("Matched list '%s' → id=%s", list_name, item["id"])
            return item["id"]
    raise ValueError(f"No task list named {list_name!r} found.")


def fetch_open_tasks(config: "Config", list_id: str) -> list[GTask]:
    log.debug("Fetching open tasks for list_id=%s…", list_id)
    svc = _service(config)
    items = svc.tasks().list(
        tasklist=list_id,
        showCompleted=False,
        showHidden=False,
        maxResults=100,
    ).execute().get("items", [])
    log.debug("Received %d task(s) from API", len(items))
    tasks = [
        GTask(
            id=t["id"],
            title=t.get("title", "(untitled)"),
            notes=t.get("notes", ""),
            list_id=list_id,
            status=t.get("status", "needsAction"),
        )
        for t in items
    ]
    for t in tasks:
        log.debug("  task id=%s  status=%s  title=%r", t.id, t.status, t.title)
    return tasks


def complete_task(config: "Config", task: GTask) -> None:
    log.debug("Marking task %s as completed in Google Tasks", task.id)
    svc = _service(config)
    svc.tasks().patch(
        tasklist=task.list_id,
        task=task.id,
        body={"status": "completed"},
    ).execute()
    log.debug("Task %s marked completed", task.id)
