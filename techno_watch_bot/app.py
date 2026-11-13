"""Textual application: holds settings + backend, routes screens."""
from __future__ import annotations

from pydantic import ValidationError
from textual.app import App

from .claude import Backend, TaskSource, default_backends
from .config import Settings


class TechnoWatchApp(App):
    TITLE = "techno-watch-bot"
    SUB_TITLE = "Claude Code tech-watch"
    CSS = """
    Screen { padding: 0 1; }
    .title { text-style: bold; color: $accent; padding: 1 0 0 0; }
    .muted { color: $text-muted; }
    .panel { border: round $primary; padding: 0 1; height: auto; }
    .error { color: $error; }
    """

    def __init__(self, backend: Backend | None = None, settings: Settings | None = None,
                 task_source: TaskSource | None = None) -> None:
        super().__init__()
        if backend is None or task_source is None:
            default_backend, default_source = default_backends()
            backend = backend or default_backend
            task_source = task_source or default_source
        self.backend = backend
        self.task_source = task_source
        self.config_error = ""
        if settings is None:
            try:
                settings = Settings.load()
            except (ValidationError, OSError, ValueError) as exc:
                self.config_error = str(exc)
                settings = Settings()
        self.settings = settings
        self.first_run = not Settings.exists()

    def on_mount(self) -> None:
        from .screens.home import HomeScreen
        from .screens.settings import SettingsScreen

        self.push_screen(HomeScreen())
        if self.first_run or self.config_error:
            self.push_screen(SettingsScreen())
            if self.config_error:
                self.notify(f"Invalid config, defaults loaded:\n{self.config_error}",
                            severity="error", timeout=10)
