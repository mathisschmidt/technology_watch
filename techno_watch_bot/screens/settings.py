from __future__ import annotations

from pydantic import ValidationError
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, VerticalScroll
from textual.screen import Screen
from textual.widgets import Button, Footer, Header, Input, Label, Static

from ..config import CONFIG_FILE, Settings

FIELDS: list[tuple[str, str, str]] = [
    ("task_list_name", "Google Tasks list name", "Title of the list to read tasks from"),
    ("notes_dir", "Notes directory", "Where research results are written (e.g. Obsidian vault)"),
    ("reports_dir", "Reports directory", "Where research-reports are stored"),
    ("max_reports", "Reports to keep", "Older reports are deleted beyond this number"),
    ("tasks_mcp_url", "Google Tasks MCP server URL",
     "Your google-tasks-mcp server (the app calls it directly; reconnect after changing it)"),
    ("model", "Model", "Empty = Claude Code default (e.g. claude-opus-5-5)"),
    ("max_turns_per_task", "Max turns per task", "Hard stop for one task's agent loop"),
    ("scout_max_searches", "Scouting searches", "Quick searches before deciding to dig"),
    ("claude_cli_path", "Claude CLI path", "Empty = auto-detect / bundled"),
]


class SettingsScreen(Screen):
    BINDINGS = [
        Binding("escape", "app.pop_screen", "Back"),
        Binding("ctrl+s", "save", "Save"),
    ]
    DEFAULT_CSS = """
    SettingsScreen .field-label { padding: 1 0 0 0; text-style: bold; }
    SettingsScreen .field-help { color: $text-muted; }
    SettingsScreen #buttons { height: auto; padding: 1 0; }
    SettingsScreen #errors { color: $error; height: auto; }
    """

    def compose(self) -> ComposeResult:
        s = self.app.settings
        yield Header()
        with VerticalScroll():
            yield Static(f"Settings — {CONFIG_FILE}", classes="title")
            for key, label, help_ in FIELDS:
                yield Label(label, classes="field-label")
                yield Static(help_, classes="field-help")
                yield Input(str(getattr(s, key)), id=f"f_{key}")
            yield Static(id="errors")
            with Horizontal(id="buttons"):
                yield Button("Save", variant="primary", id="save")
                yield Button("Cancel", id="cancel")
        yield Footer()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "save":
            self.action_save()
        else:
            self.app.pop_screen()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.action_save()

    def action_save(self) -> None:
        data = {key: self.query_one(f"#f_{key}", Input).value for key, _, _ in FIELDS}
        errors = self.query_one("#errors", Static)
        try:
            settings = Settings.model_validate(data)
            settings.save()
            settings.ensure_dirs()
        except ValidationError as exc:
            errors.update("\n".join(
                f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors()
            ))
            return
        except OSError as exc:
            errors.update(f"Could not save: {exc}")
            return
        self.app.settings = settings
        self.app.first_run = False
        self.app.notify("Settings saved.")
        self.app.pop_screen()
