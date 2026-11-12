"""Sequential orchestration of tasks, with graceful cancellation and reporting."""
from __future__ import annotations

import asyncio
import datetime as dt
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from typing import TypeVar

from . import reports
from .claude import Backend
from .config import Settings
from .events import Phase, PhaseEvent, TaskEvent, TaskInfo, TaskOutcome, TaskRun, UsageEvent

T = TypeVar("T")


@dataclass
class TaskStarted:
    index: int


@dataclass
class TaskUpdate:
    index: int
    event: TaskEvent


@dataclass
class TaskFinished:
    index: int
    run: TaskRun


@dataclass
class RunFinished:
    runs: list[TaskRun]
    report_path: Path | None
    cancelled: bool
    report_error: str = ""


RunnerEvent = TaskStarted | TaskUpdate | TaskFinished | RunFinished


class _Stopped(Exception):
    pass


async def _until(stream: AsyncIterator[T], stop: asyncio.Event) -> AsyncIterator[T]:
    """Iterate ``stream`` but raise _Stopped as soon as ``stop`` is set."""
    it = stream.__aiter__()
    stopper = asyncio.ensure_future(stop.wait())
    try:
        while True:
            nxt = asyncio.ensure_future(it.__anext__())
            done, _ = await asyncio.wait({nxt, stopper}, return_when=asyncio.FIRST_COMPLETED)
            if nxt not in done:
                nxt.cancel()
                await asyncio.gather(nxt, return_exceptions=True)
                raise _Stopped
            try:
                yield nxt.result()
            except StopAsyncIteration:
                return
    finally:
        stopper.cancel()
        aclose = getattr(it, "aclose", None)
        if aclose:
            try:
                await aclose()
            except (Exception, asyncio.CancelledError):  # noqa: BLE001
                pass


class Runner:
    def __init__(self, settings: Settings, backend: Backend, tasks: list[TaskInfo]) -> None:
        self.settings = settings
        self.backend = backend
        self.runs = [TaskRun(task=t) for t in tasks]
        self._stop = asyncio.Event()

    def cancel(self) -> None:
        self._stop.set()

    @property
    def cancelled(self) -> bool:
        return self._stop.is_set()

    async def run(self) -> AsyncIterator[RunnerEvent]:
        started_at = dt.datetime.now()
        for i, run in enumerate(self.runs):
            if self._stop.is_set():
                run.status = Phase.CANCELLED
                run.reason = "Run cancelled before this task started."
                yield TaskFinished(i, run)
                continue

            yield TaskStarted(i)
            t0 = time.monotonic()
            try:
                async for ev in _until(self.backend.run_task(self.settings, run.task), self._stop):
                    self._apply(run, ev)
                    yield TaskUpdate(i, ev)
                if not run.status.finished:
                    run.status = Phase.FAILED
                    run.reason = run.reason or "No result received."
            except _Stopped:
                run.status = Phase.CANCELLED
                run.reason = "Run cancelled by the user while this task was in progress."
            except Exception as exc:  # noqa: BLE001
                run.status = Phase.FAILED
                run.reason = f"{type(exc).__name__}: {exc}"
            run.duration_s = time.monotonic() - t0
            yield TaskFinished(i, run)

        report_path, err = None, ""
        try:
            content = reports.render_report(
                self.runs,
                task_list=self.settings.task_list_name,
                model=self.settings.model,
                started_at=started_at,
                finished_at=dt.datetime.now(),
            )
            report_path = reports.write_report(
                self.settings.reports_dir, content, started_at, self.settings.max_reports
            )
        except OSError as exc:
            err = str(exc)
        yield RunFinished(self.runs, report_path, self.cancelled, err)

    @staticmethod
    def _apply(run: TaskRun, ev: TaskEvent) -> None:
        if isinstance(ev, PhaseEvent):
            run.status = ev.phase
        elif isinstance(ev, UsageEvent):
            run.usage = ev.usage
            if ev.cost_usd is not None:
                run.cost_usd = ev.cost_usd
        elif isinstance(ev, TaskOutcome):
            run.status = ev.status
            run.summary = ev.summary
            run.reason = ev.reason
            run.result_path = ev.result_path
            run.sources = ev.sources
            run.usage = ev.usage
            run.num_turns = ev.num_turns
            if ev.cost_usd is not None:
                run.cost_usd = ev.cost_usd
