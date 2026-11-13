from __future__ import annotations

from collections.abc import Callable

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.screen import Screen
from textual.widgets import Footer, Header, OptionList, Static
from textual.widgets.option_list import Option

from ..reports import list_reports


def connect_google_tasks(app: App, on_done: Callable[[], None] | None = None) -> None:
    """Run the browser sign-in in a thread and report back with a notification."""

    def _login() -> None:
        try:
            status = app.task_source.login(app.settings)
        except Exception as exc:  # noqa: BLE001
            app.call_from_thread(app.notify, f"Google Tasks sign-in failed: {exc}",
                                 severity="error", timeout=10)
            return
        app.call_from_thread(app.notify, f"Google Tasks {status}.")
        if on_done:
            app.call_from_thread(on_done)

    app.notify("Opening your browser to sign in to Google Tasks…", timeout=8)
    app.run_worker(_login, thread=True, exclusive=True, group="login")


class HomeScreen(Screen):
    BINDINGS = [
        Binding("r", "go('run')", "Run research"),
        Binding("h", "go('history')", "History"),
        Binding("s", "go('settings')", "Settings"),
        Binding("c", "go('connect')", "Connect Google Tasks"),
        Binding("q", "app.quit", "Quit"),
    ]

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static("techno-watch-bot", classes="title")
        yield Static("Claude Code researches your Google Tasks and writes the results.",
                     classes="muted")
        yield OptionList(
            Option("▶  Run research", id="run"),
            Option("🗂  Reports history", id="history"),
            Option("⚙  Settings", id="settings"),
            Option("🔑  Connect Google Tasks", id="connect"),
            Option("⏻  Quit", id="quit"),
            id="menu",
        )
        yield Static(id="summary", classes="panel")
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#menu").styles.height = "auto"
        self.refresh_summary()

    def on_screen_resume(self) -> None:
        self.refresh_summary()

    def refresh_summary(self) -> None:
        s = self.app.settings
        n = len(list_reports(s.reports_dir))
        self.query_one("#summary", Static).update(
            f"[b]Task list[/b]   {s.task_list_name}\n"
            f"[b]Notes dir[/b]   {s.notes_dir}\n"
            f"[b]Reports dir[/b] {s.reports_dir}  [dim]({n}/{s.max_reports} kept)[/dim]\n"
            f"[b]Tasks MCP[/b]   {s.tasks_mcp_url or '—'}  "
            f"[dim]({self.app.task_source.status(s)})[/dim]\n"
            f"[b]Model[/b]       {s.model or 'Claude Code default'}"
        )

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        self.action_go(event.option.id or "")

    def action_go(self, target: str) -> None:
        from .history import HistoryScreen
        from .picker import PickerScreen
        from .settings import SettingsScreen

        if target == "run":
            self.app.push_screen(PickerScreen())
        elif target == "history":
            self.app.push_screen(HistoryScreen())
        elif target == "settings":
            self.app.push_screen(SettingsScreen())
        elif target == "connect":
            connect_google_tasks(self.app, on_done=self.refresh_summary)
        elif target == "quit":
            self.app.exit()
