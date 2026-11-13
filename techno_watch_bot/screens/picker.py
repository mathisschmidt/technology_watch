from __future__ import annotations

from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.screen import Screen
from textual.widgets import DataTable, Footer, Header, LoadingIndicator, Static

from ..events import TaskInfo
from ..tasks_api import AuthRequired

ALL_KEY = "__all__"


class PickerScreen(Screen):
    BINDINGS = [
        Binding("escape", "app.pop_screen", "Back"),
        Binding("r", "reload", "Refresh"),
        Binding("a", "run_all", "Run all"),
        Binding("c", "connect", "Connect Google Tasks"),
    ]

    def __init__(self) -> None:
        super().__init__()
        self.tasks: list[TaskInfo] = []

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static(id="heading", classes="title")
        yield Static("Loading tasks…", id="status", classes="muted")
        yield LoadingIndicator(id="loading")
        table = DataTable(id="tasks", cursor_type="row")
        table.display = False
        yield table
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#heading", Static).update(
            f"Open tasks — {self.app.settings.task_list_name}"
        )
        table = self.query_one(DataTable)
        table.add_columns("#", "Task", "Description", "Due")
        self.action_reload()

    def action_reload(self) -> None:
        self.query_one("#loading").display = True
        self.query_one(DataTable).display = False
        self.query_one("#status", Static).update("Loading tasks…")
        self.load_tasks()

    @work(exclusive=True, group="load")
    async def load_tasks(self) -> None:
        status = self.query_one("#status", Static)
        try:
            self.tasks = await self.app.task_source.list_tasks(self.app.settings)
        except AuthRequired as exc:
            self.query_one("#loading").display = False
            status.update(f"[yellow]{exc}[/yellow]\n"
                          "Press [b]c[/b] to connect Google Tasks (opens your browser).")
            return
        except Exception as exc:  # noqa: BLE001
            self.query_one("#loading").display = False
            status.update(f"[red]Could not load tasks:[/red] {exc}\n[dim]r to retry[/dim]")
            return
        self.query_one("#loading").display = False
        table = self.query_one(DataTable)
        table.clear()
        if not self.tasks:
            status.update("No open task in this list. [dim]r to refresh[/dim]")
            return
        table.add_row("★", f"[b]All tasks ({len(self.tasks)})[/b]", "", "", key=ALL_KEY)
        for i, t in enumerate(self.tasks, 1):
            desc = t.notes.replace("\n", " ")
            table.add_row(str(i), t.title, desc[:60] + ("…" if len(desc) > 60 else ""),
                          t.due[:10], key=t.id)
        status.update("↑/↓ to choose, Enter to launch.")
        table.display = True
        table.focus()

    def action_connect(self) -> None:
        from .home import connect_google_tasks

        connect_google_tasks(self.app, on_done=self.action_reload)

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        key = event.row_key.value
        chosen = self.tasks if key == ALL_KEY else [t for t in self.tasks if t.id == key]
        self._launch(chosen)

    def action_run_all(self) -> None:
        if self.tasks:
            self._launch(self.tasks)

    def _launch(self, tasks: list[TaskInfo]) -> None:
        from .monitor import MonitorScreen

        if tasks:
            self.app.switch_screen(MonitorScreen(tasks))
