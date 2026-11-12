"""Settings — a single TOML file, validated with Pydantic, editable from the UI."""
from __future__ import annotations

import os
import tomllib
from pathlib import Path

import tomli_w
from pydantic import BaseModel, Field, field_validator

CONFIG_DIR = Path(
    os.environ.get("TECHNO_WATCH_CONFIG_DIR", Path.home() / ".config" / "techno-watch-bot")
)
CONFIG_FILE = CONFIG_DIR / "config.toml"

DEFAULT_NOTES_DIR = Path.home() / "techno-watch" / "notes"
DEFAULT_REPORTS_DIR = Path.home() / ".local" / "share" / "techno-watch-bot" / "reports"


class Settings(BaseModel):
    task_list_name: str = "Veille tech"
    notes_dir: Path = DEFAULT_NOTES_DIR
    reports_dir: Path = DEFAULT_REPORTS_DIR
    max_reports: int = Field(default=5, ge=1, le=100)
    google_task_mcp_name: str = "google-task"
    model: str = ""
    max_turns_per_task: int = Field(default=40, ge=5, le=500)
    scout_max_searches: int = Field(default=3, ge=1, le=10)
    claude_cli_path: str = ""

    @field_validator("notes_dir", "reports_dir", mode="before")
    @classmethod
    def _expand(cls, v: str | Path) -> Path:
        return Path(v).expanduser()

    @field_validator("task_list_name", "google_task_mcp_name")
    @classmethod
    def _non_empty(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("must not be empty")
        return v

    # ------------------------------------------------------------------ io
    @classmethod
    def exists(cls, path: Path | None = None) -> bool:
        return (path or CONFIG_FILE).exists()

    @classmethod
    def load(cls, path: Path | None = None) -> "Settings":
        path = path or CONFIG_FILE
        if not path.exists():
            return cls()
        with path.open("rb") as fh:
            return cls.model_validate(tomllib.load(fh))

    def save(self, path: Path | None = None) -> Path:
        path = path or CONFIG_FILE
        path.parent.mkdir(parents=True, exist_ok=True)
        data = self.model_dump(mode="json")
        with path.open("wb") as fh:
            tomli_w.dump(data, fh)
        return path

    def ensure_dirs(self) -> None:
        self.notes_dir.mkdir(parents=True, exist_ok=True)
        self.reports_dir.mkdir(parents=True, exist_ok=True)

    # -------------------------------------------------------------- helpers
    def mcp_tool(self, name: str) -> str:
        return f"mcp__{self.google_task_mcp_name}__{name}"
