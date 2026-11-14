from __future__ import annotations

from pathlib import Path

from textual.app import ComposeResult
from textual.binding import Binding
from textual.screen import Screen
from textual.widgets import DataTable, Footer, Header, MarkdownViewer, Static

from ..config import tilde
from ..reports import fmt_tokens, list_reports


class HistoryScreen(Screen):
    BINDINGS = [Binding("escape", "app.pop_screen", "Back")]

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static("Reports history", classes="title")
        yield Static(id="info", classes="muted")
        yield DataTable(id="reports", cursor_type="row")
        yield Footer()

    def on_mount(self) -> None:
        s = self.app.settings
        reports = list_reports(s.reports_dir)
        self.query_one("#info", Static).update(
            f"{tilde(s.reports_dir)} — {len(reports)} report(s), the {s.max_reports} newest are kept."
            + ("" if reports else "\nNo report yet: run some research first.")
        )
        table = self.query_one(DataTable)
        table.add_columns("Date", "Tasks", "✅", "⏭️", "❌", "Tokens", "Cost")
        for r in reports:
            cost = r.meta.get("total_cost_usd", "null")
            table.add_row(
                r.date, str(r.get_int("n_tasks")), str(r.get_int("n_done")),
                str(r.get_int("n_skipped")), str(r.get_int("n_failed")),
                fmt_tokens(r.get_int("total_tokens")),
                "—" if cost == "null" else f"${float(cost):.3f}",
                key=str(r.path),
            )
        table.focus()

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        self.app.push_screen(ReportScreen(Path(str(event.row_key.value))))


class ReportScreen(Screen):
    BINDINGS = [Binding("escape", "app.pop_screen", "Back")]

    def __init__(self, path: Path) -> None:
        super().__init__()
        self.path = path

    def compose(self) -> ComposeResult:
        yield Header()
        try:
            text = self.path.read_text(encoding="utf-8")
        except OSError as exc:
            text = f"Could not read `{self.path}`: {exc}"
        # Hide the frontmatter in the viewer.
        if text.startswith("---\n"):
            end = text.find("\n---", 4)
            if end != -1:
                text = text[end + 4:].lstrip()
        yield MarkdownViewer(text, show_table_of_contents=False)
        yield Footer()
