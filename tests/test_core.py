from __future__ import annotations

import asyncio
import datetime as dt
from pathlib import Path

import pytest
from claude_agent_sdk import AssistantMessage, RateLimitEvent, ResultMessage, TextBlock, ToolUseBlock
from claude_agent_sdk.types import RateLimitInfo

from techno_watch_bot import reports
from techno_watch_bot.claude import FakeBackend, StreamParser, extract_json
from techno_watch_bot.config import Settings
from techno_watch_bot.events import (
    Phase,
    PhaseEvent,
    RateLimitEvt,
    TaskInfo,
    TaskOutcome,
    TaskRun,
    ToolEvent,
    Usage,
    UsageEvent,
)
from techno_watch_bot.runner import RunFinished, Runner, TaskFinished


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(notes_dir=tmp_path / "notes", reports_dir=tmp_path / "reports")


# ------------------------------------------------------------------ config
def test_config_roundtrip(tmp_path: Path) -> None:
    path = tmp_path / "cfg" / "config.toml"
    s = Settings(task_list_name="Watch", notes_dir="~/x", max_reports=3)
    s.save(path)
    loaded = Settings.load(path)
    assert loaded == s
    assert loaded.notes_dir == Path.home() / "x"


def test_config_missing_file_gives_defaults(tmp_path: Path) -> None:
    assert Settings.load(tmp_path / "nope.toml") == Settings()


def test_config_validation() -> None:
    with pytest.raises(ValueError):
        Settings(max_reports=0)
    with pytest.raises(ValueError):
        Settings(task_list_name="  ")
    with pytest.raises(ValueError):
        Settings(tasks_mcp_url="http://evil.example.com")
    assert Settings(tasks_mcp_url="https://x.com/mcp/").tasks_mcp_url == "https://x.com"


# ------------------------------------------------------------------ reports
def _run(title: str, status: Phase, tokens: int, cost: float | None = 0.1) -> TaskRun:
    return TaskRun(
        task=TaskInfo(id=title, title=title),
        status=status,
        usage=Usage(input_tokens=tokens, output_tokens=10),
        cost_usd=cost,
        duration_s=65,
        reason="" if status == Phase.DONE else "too vague",
        result_path="/n/a.md" if status == Phase.DONE else "",
    )


def test_render_report_totals_and_reasons() -> None:
    runs = [_run("A", Phase.DONE, 100), _run("B", Phase.SKIPPED, 50)]
    t = dt.datetime(2026, 1, 2, 3, 4, 5)
    md = reports.render_report(runs, task_list="L", model="", started_at=t,
                               finished_at=t + dt.timedelta(seconds=130))
    assert "n_done: 1" in md and "n_skipped: 1" in md
    assert "total_tokens: 170" in md
    assert "total_cost_usd: 0.2000" in md
    assert "**Why not done:** too vague" in md
    assert "## Totals" in md


def test_render_single_task_has_no_totals() -> None:
    t = dt.datetime.now()
    md = reports.render_report([_run("A", Phase.DONE, 1)], task_list="L", model="m",
                               started_at=t, finished_at=t)
    assert "## Totals" not in md


def test_rotation_keeps_newest(tmp_path: Path) -> None:
    base = dt.datetime(2026, 1, 1)
    for i in range(7):
        reports.write_report(tmp_path, f"---\nn_tasks: {i}\n---\n", base + dt.timedelta(hours=i), keep=5)
    listed = reports.list_reports(tmp_path)
    assert len(listed) == 5
    assert [r.get_int("n_tasks") for r in listed] == [6, 5, 4, 3, 2]


def test_same_second_reports_do_not_collide(tmp_path: Path) -> None:
    t = dt.datetime(2026, 1, 1)
    a = reports.write_report(tmp_path, "x", t, keep=5)
    b = reports.write_report(tmp_path, "y", t, keep=5)
    assert a != b and a.exists() and b.exists()


# ------------------------------------------------------------------ parsing
def test_extract_json_variants() -> None:
    assert extract_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert extract_json('blah {"a": 2} blah') == {"a": 2}
    assert extract_json("nothing") is None


def _result(**kw) -> ResultMessage:
    base = dict(subtype="success", duration_ms=1, duration_api_ms=1, is_error=False,
                num_turns=4, session_id="s", total_cost_usd=0.5,
                usage={"input_tokens": 1000, "output_tokens": 200,
                       "cache_read_input_tokens": 300})
    base.update(kw)
    return ResultMessage(**base)


def test_stream_parser_full_flow() -> None:
    p = StreamParser()
    events = []
    events += p.feed(AssistantMessage(
        content=[TextBlock("PHASE: scouting"), ToolUseBlock("1", "WebSearch", {"query": "uv"})],
        model="m", usage={"input_tokens": 10, "output_tokens": 5}, message_id="m1"))
    # same message id repeated (one message per block) → usage not double-counted
    events += p.feed(AssistantMessage(content=[], model="m",
                                      usage={"input_tokens": 10, "output_tokens": 5},
                                      message_id="m1"))
    events += p.feed(AssistantMessage(
        content=[TextBlock("PHASE: digging\nok"),
                 ToolUseBlock("2", "Write", {"file_path": "/n/x.md"})],
        model="m", usage={"input_tokens": 20, "output_tokens": 5}, message_id="m2"))
    events += p.feed(RateLimitEvent(RateLimitInfo(status="allowed_warning", utilization=0.8),
                                    uuid="u", session_id="s"))
    events += p.feed(_result(structured_output={
        "status": "done", "summary": "S", "reason": "", "result_path": "/n/x.md",
        "sources": ["https://a"]}))

    phases = [e.phase for e in events if isinstance(e, PhaseEvent)]
    assert phases == [Phase.SCOUTING, Phase.DIGGING, Phase.WRITING]
    tools = [(e.name, e.detail) for e in events if isinstance(e, ToolEvent)]
    assert tools == [("WebSearch", "uv"), ("Write", "/n/x.md")]
    usages = [e.usage.total for e in events if isinstance(e, UsageEvent)]
    assert usages[:2] == [15, 40]
    assert any(isinstance(e, RateLimitEvt) and e.utilization == 0.8 for e in events)
    out = events[-1]
    assert isinstance(out, TaskOutcome)
    assert out.status == Phase.DONE and out.result_path == "/n/x.md"
    assert out.usage.total == 1500 and out.cost_usd == 0.5 and out.num_turns == 4


def test_stream_parser_fallback_text_and_errors() -> None:
    out = StreamParser().feed(_result(result='```json\n{"status":"skipped","reason":"vague"}\n```'))[-1]
    assert out.status == Phase.SKIPPED and out.reason == "vague"
    out = StreamParser().feed(_result(is_error=True, subtype="error_max_turns", result=None))[-1]
    assert out.status == Phase.FAILED and "maximum number of turns" in out.reason
    out = StreamParser().feed(_result(result="just prose"))[-1]
    assert out.status == Phase.FAILED


# ------------------------------------------------------------------ runner
async def test_runner_with_fake_backend(settings: Settings) -> None:
    backend = FakeBackend(delay=0)
    tasks = await backend.list_tasks(settings)
    events = [e async for e in Runner(settings, backend, tasks, backend).run()]
    final = events[-1]
    assert isinstance(final, RunFinished) and final.report_path and final.report_path.exists()
    statuses = [r.status for r in final.runs]
    assert statuses == [Phase.DONE, Phase.DONE, Phase.SKIPPED]
    assert all(r.usage.total > 0 for r in final.runs)
    assert len(list(settings.notes_dir.glob("*.md"))) == 2
    assert backend.completed == ["t1", "t2"]  # skipped task stays open


async def test_runner_complete_failure_keeps_task_open(settings: Settings) -> None:
    backend = FakeBackend(delay=0)
    backend.fail_complete = True
    tasks = (await backend.list_tasks(settings))[:1]
    final = [e async for e in Runner(settings, backend, tasks, backend).run()][-1]
    run = final.runs[0]
    assert run.status == Phase.FAILED
    assert "marking the task done failed" in run.reason and run.result_path


class _NoFileBackend(FakeBackend):
    async def run_task(self, settings, task):
        yield TaskOutcome(Phase.DONE, summary="s", result_path="/does/not/exist.md")


async def test_runner_done_without_file_is_failed(settings: Settings) -> None:
    backend = _NoFileBackend(delay=0)
    tasks = (await backend.list_tasks(settings))[:1]
    final = [e async for e in Runner(settings, backend, tasks, backend).run()][-1]
    assert final.runs[0].status == Phase.FAILED
    assert backend.completed == []


def test_research_options_have_no_mcp(settings: Settings) -> None:
    from techno_watch_bot.claude import ClaudeCodeBackend

    opts = ClaudeCodeBackend().options(settings, TaskInfo("t", "x"))
    assert opts.mcp_servers == {} and opts.strict_mcp_config
    assert not any(t.startswith("mcp__") for t in opts.allowed_tools)
    assert opts.permission_mode == "dontAsk"


class _BoomBackend(FakeBackend):
    async def run_task(self, settings, task):
        if task.id == "t1":
            raise RuntimeError("cli missing")
            yield  # pragma: no cover
        async for e in super().run_task(settings, task):
            yield e


async def test_runner_failure_is_isolated(settings: Settings) -> None:
    backend = _BoomBackend(delay=0)
    tasks = await backend.list_tasks(settings)
    final = [e async for e in Runner(settings, backend, tasks, backend).run()][-1]
    assert final.runs[0].status == Phase.FAILED and "cli missing" in final.runs[0].reason
    assert final.runs[1].status == Phase.DONE


async def test_runner_cancel_mid_task(settings: Settings) -> None:
    backend = FakeBackend(delay=0.05)
    tasks = await backend.list_tasks(settings)
    runner = Runner(settings, backend, tasks, backend)
    events = []
    async for e in runner.run():
        events.append(e)
        if len(events) == 3:
            runner.cancel()
    final = events[-1]
    assert isinstance(final, RunFinished) and final.cancelled
    assert [r.status for r in final.runs] == [Phase.CANCELLED] * 3
    assert sum(isinstance(e, TaskFinished) for e in events) == 3
    assert "n_cancelled: 3" in final.report_path.read_text()


def test_tilde_display() -> None:
    from techno_watch_bot.config import tilde

    home = Path.home()
    assert tilde(home / "notes" / "a.md") == str(Path("~") / "notes" / "a.md")
    assert tilde("/elsewhere/x") == "/elsewhere/x"
    assert Settings(notes_dir=tilde(home / "n")).notes_dir == home / "n"  # round-trips on save
