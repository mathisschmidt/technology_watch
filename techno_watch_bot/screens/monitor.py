from __future__ import annotations

import datetime as dt
import time

from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import Screen
from textual.widgets import DataTable, Footer, Header, RichLog, Static

from ..events import (
    Phase,
    PhaseEvent,
    RateLimitEvt,
    TaskInfo,
    TaskOutcome,
    ToolEvent,
    Usage,
    UsageEvent,
)
from ..config import tilde
from ..reports import fmt_duration, fmt_tokens
from ..runner import RunFinished, Runner, TaskFinished, TaskStarted, TaskUpdate


class MonitorScreen(Screen):
    BINDINGS = [
        Binding("c", "cancel", "Cancel run"),
        Binding("ctrl+c", "cancel", "Cancel run", show=False),
        Binding("escape", "back", "Home"),
        Binding("o", "open_report", "Open report"),
    ]
    DEFAULT_CSS = """
    MonitorScreen #top { height: 1fr; }
    MonitorScreen #queue { width: 3fr; border: round $primary; overflow-x: hidden; }
    MonitorScreen #right { width: 2fr; }
    MonitorScreen #current, MonitorScreen #stats, MonitorScreen #limits {
        border: round $primary; padding: 0 1; height: auto;
    }
    MonitorScreen #log { height: 10; border: round $secondary; }
    MonitorScreen #banner { padding: 0 1; height: auto; }
    """

    def __init__(self, tasks: list[TaskInfo]) -> None:
        super().__init__()
        self.tasks = tasks
        self.runner: Runner | None = None
        self.current: int | None = None
        self.last_tool = ""
        self.task_t0 = 0.0
        self.run_t0 = 0.0
        self.finished_usage = Usage()
        self.finished_cost = 0.0
        self.live_usage = Usage()
        self.live_cost: float | None = None
        self.rate: RateLimitEvt | None = None
        self.result: RunFinished | None = None
        self.run_t0_end: float | None = None

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static("Running…", id="banner")
        with Horizontal(id="top"):
            table = DataTable(id="queue", cursor_type="none")
            table.border_title = "Queue"
            yield table
            with Vertical(id="right"):
                for wid, title in (("current", "Current task"), ("stats", "Usage"),
                                   ("limits", "Claude limits")):
                    s = Static(id=wid)
                    s.border_title = title
                    yield s
        log = RichLog(id="log", markup=True, wrap=True)
        log.border_title = "Activity"
        yield log
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one(DataTable)
        table.add_column("#", key="n")
        table.add_column("Task", key="task")
        table.add_column("Status", key="status", width=14)
        table.add_column("Tokens", key="tokens", width=8)
        table.add_column("Time", key="time", width=9)
        for i, t in enumerate(self.tasks):
            table.add_row(str(i + 1), t.title, f"{Phase.PENDING.icon} pending", "—", "—",
                          key=str(i))
        self.run_t0 = time.monotonic()
        self.set_interval(1, self.refresh_panels)
        self.refresh_panels()
        self.app.settings.ensure_dirs()
        self.runner = Runner(self.app.settings, self.app.backend, self.tasks, self.app.task_source)
        self.run_all()

    # ------------------------------------------------------------ worker
    @work(exclusive=True, group="run")
    async def run_all(self) -> None:
        assert self.runner
        log = self.query_one(RichLog)
        table = self.query_one(DataTable)
        async for ev in self.runner.run():
            if isinstance(ev, TaskStarted):
                self.current = ev.index
                self.task_t0 = time.monotonic()
                self.live_usage, self.live_cost, self.last_tool = Usage(), None, ""
                log.write(f"[b]▶ {self.tasks[ev.index].title}[/b]")
            elif isinstance(ev, TaskUpdate):
                self._on_task_event(ev)
            elif isinstance(ev, TaskFinished):
                run = ev.run
                self.finished_usage = self.finished_usage + run.usage
                self.finished_cost += run.cost_usd or 0.0
                self.live_usage, self.live_cost = Usage(), None
                self.current = None
                key = str(ev.index)
                table.update_cell(key, "status", f"{run.status.icon} {run.status.value}")
                table.update_cell(key, "tokens", fmt_tokens(run.usage.total))
                table.update_cell(key, "time", fmt_duration(run.duration_s))
                msg = f"  {run.status.icon} {run.status.value}"
                if run.result_path:
                    msg += f" → {tilde(run.result_path)}"
                if run.reason and run.status != Phase.DONE:
                    msg += f" [dim]({run.reason})[/dim]"
                log.write(msg)
            elif isinstance(ev, RunFinished):
                self.result = ev
                self._on_finished(ev)
            self.refresh_panels()

    def _on_task_event(self, ev: TaskUpdate) -> None:
        e = ev.event
        table = self.query_one(DataTable)
        if isinstance(e, PhaseEvent):
            table.update_cell(str(ev.index), "status", f"{e.phase.icon} {e.phase.value}")
            self.query_one(RichLog).write(f"  {e.phase.icon} {e.phase.value}")
        elif isinstance(e, ToolEvent):
            self.last_tool = tilde(f"{e.name} {e.detail}".strip())
            self.query_one(RichLog).write(f"  [dim]· {self.last_tool[:200]}[/dim]")
        elif isinstance(e, UsageEvent):
            self.live_usage = e.usage
            if e.cost_usd is not None:
                self.live_cost = e.cost_usd
            table.update_cell(str(ev.index), "tokens", fmt_tokens(e.usage.total))
        elif isinstance(e, RateLimitEvt):
            self.rate = e
        elif isinstance(e, TaskOutcome):
            self.live_usage = e.usage

    def _on_finished(self, ev: RunFinished) -> None:
        counts = {p: sum(1 for r in ev.runs if r.status == p) for p in Phase}
        head = "[b yellow]Run cancelled.[/]" if ev.cancelled else "[b green]Run finished.[/]"
        text = (f"{head}  ✅ {counts[Phase.DONE]}  ⏭️ {counts[Phase.SKIPPED]}  "
                f"❌ {counts[Phase.FAILED]}  🚫 {counts[Phase.CANCELLED]}\n")
        if ev.report_path:
            text += f"Report: {tilde(ev.report_path)}  [dim](o to open, Esc for home)[/dim]"
        else:
            text += f"[red]Report could not be written:[/red] {ev.report_error}"
        self.query_one("#banner", Static).update(text)
        self.run_t0_end = time.monotonic()

    # ------------------------------------------------------------ panels
    def refresh_panels(self) -> None:
        now = time.monotonic()
        finished = self.result is not None
        if self.current is not None:
            t = self.tasks[self.current]
            status = self.query_one(DataTable).get_cell(str(self.current), "status")
            current = (f"[b]{t.title}[/b]\n{status}\n"
                       f"[dim]{(self.last_tool or 'starting…')[:120]}[/dim]\n"
                       f"Elapsed {fmt_duration(now - self.task_t0)}")
        else:
            current = "[dim]idle[/dim]" if finished else "[dim]starting…[/dim]"
        self.query_one("#current", Static).update(current)

        total = self.finished_usage + self.live_usage
        cost = self.finished_cost + (self.live_cost or 0.0)
        elapsed = (self.run_t0_end or now) - self.run_t0
        u = self.live_usage
        self.query_one("#stats", Static).update(
            f"[b]Task[/b]   {fmt_tokens(u.total)} tok "
            f"[dim](in {fmt_tokens(u.input_tokens)} · out {fmt_tokens(u.output_tokens)} · "
            f"cache {fmt_tokens(u.cache_read_tokens + u.cache_creation_tokens)})[/dim]\n"
            f"[b]Total[/b]  {fmt_tokens(total.total)} tok · ${cost:.3f}\n"
            f"[b]Time[/b]   {fmt_duration(elapsed)}"
        )
        if self.rate:
            r = self.rate
            color = {"allowed": "green", "allowed_warning": "yellow"}.get(r.status, "red")
            util = f"{r.utilization * 100:.0f}% used" if r.utilization is not None else ""
            reset = (f"resets {dt.datetime.fromtimestamp(r.resets_at):%a %H:%M}"
                     if r.resets_at else "")
            limits = (f"[{color}]{r.status}[/{color}] {r.limit_type or ''}\n"
                      f"{util}  {reset}".rstrip())
        else:
            limits = "[dim]n/a (no rate-limit info reported yet)[/dim]"
        self.query_one("#limits", Static).update(limits)

    # ------------------------------------------------------------ actions
    def action_cancel(self) -> None:
        if self.runner and not self.result and not self.runner.cancelled:
            self.runner.cancel()
            self.query_one("#banner", Static).update("[yellow]Cancelling…[/yellow]")

    def action_back(self) -> None:
        if self.result is None:
            self.notify("A run is in progress — press c to cancel it first.")
            return
        self.app.pop_screen()

    def action_open_report(self) -> None:
        from .history import ReportScreen

        if self.result and self.result.report_path:
            self.app.push_screen(ReportScreen(self.result.report_path))
