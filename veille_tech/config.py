"""Config loading — single YAML file, validated with Pydantic."""
from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from typing import Optional

import yaml
from pydantic import BaseModel, field_validator

CONFIG_DIR = Path(
    os.environ.get("VEILLE_TECH_CONFIG_DIR", Path.home() / ".config" / "veille-tech")
)
CONFIG_FILE = CONFIG_DIR / "config.yaml"


class GoogleTokenCache(BaseModel):
    token: Optional[str] = None
    refresh_token: Optional[str] = None
    token_uri: str = "https://oauth2.googleapis.com/token"
    scopes: list[str] = ["https://www.googleapis.com/auth/tasks"]
    expiry: Optional[datetime] = None


class Config(BaseModel):
    vault_folder: Path
    task_list_name: str = "Veille tech"
    complete_on_success: bool = True
    model: str = "claude-opus-4-7"
    max_web_searches: int = 8
    anthropic_api_key: str
    google_client_id: str
    google_client_secret: str
    google_token: GoogleTokenCache = GoogleTokenCache()

    @field_validator("vault_folder", mode="before")
    @classmethod
    def expand_path(cls, v: str | Path) -> Path:
        return Path(v).expanduser()

    @classmethod
    def load(cls) -> "Config":
        if not CONFIG_FILE.exists():
            raise FileNotFoundError(
                f"No config at {CONFIG_FILE}. See README for the template."
            )
        with CONFIG_FILE.open() as fh:
            data = yaml.safe_load(fh)
        config = cls.model_validate(data)
        if not config.vault_folder.exists():
            raise FileNotFoundError(
                f"vault_folder {config.vault_folder} does not exist. "
                "Create it or fix the path in config.yaml."
            )
        return config

    def save_token(self, creds) -> None:
        """Write the refreshed OAuth token back into config.yaml."""
        with CONFIG_FILE.open() as fh:
            data = yaml.safe_load(fh) or {}
        data["google_token"] = {
            "token": creds.token,
            "refresh_token": creds.refresh_token,
            "token_uri": creds.token_uri,
            "scopes": list(creds.scopes or self.google_token.scopes),
            "expiry": creds.expiry.isoformat() if creds.expiry else None,
        }
        with CONFIG_FILE.open("w") as fh:
            yaml.safe_dump(data, fh, default_flow_style=False, allow_unicode=True)
        self.google_token = GoogleTokenCache.model_validate(data["google_token"])
