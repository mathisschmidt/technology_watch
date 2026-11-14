"""Claude Code research backend (via claude-agent-sdk) + a fake backend.

- A *research backend* exposes ``run_task(settings, task)``: an async iterator of
  TaskEvent ending with a TaskOutcome. Claude only researches and writes files.
- A *task source* (see tasks_api.McpTaskSource) lists and completes tasks.
FakeBackend implements both, for ``--fake`` and tests.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import json
import os
import re
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any, Protocol

from slugify import slugify

from . import prompts
from .config import Settings
from .events import (
    Phase,
    PhaseEvent,
    RateLimitEvt,
    TaskEvent,
    TaskInfo,
    TaskOutcome,
    ToolEvent,
    Usage,
    UsageEvent,
)


class Backend(Protocol):
    def run_task(self, settings: Settings, task: TaskInfo) -> AsyncIterator[TaskEvent]: ...


class TaskSource(Protocol):
    async def list_tasks(self, settings: Settings) -> list[TaskInfo]: ...

    async def complete_task(self, settings: Settings, task: TaskInfo) -> None: ...

    def status(self, settings: Settings) -> str: ...

    def login(self, settings: Settings) -> str: ...


# ==========================================================================
# Stream parsing (pure, testable)
# ==========================================================================
_PHASE_RE = re.compile(r"^\s*PHASE:\s*(scouting|digging|writing)\b", re.I | re.M)
_JSON_BLOCK_RE = re.compile(r"\{.*\}", re.S)


def _tool_detail(name: str, data: dict[str, Any]) -> str:
    for key in ("query", "url", "file_path", "path", "pattern", "task", "title"):
        if key in data and isinstance(data[key], str):
            return data[key]
    return ""


def short_tool_name(name: str) -> str:
    if name.startswith("mcp__"):
        return name.split("__", 2)[-1]
    return name


def extract_json(text: str | None) -> dict[str, Any] | None:
    if not text:
        return None
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    for candidate in filter(None, [fenced.group(1) if fenced else None, text]):
        m = _JSON_BLOCK_RE.search(candidate)
        if not m:
            continue
        try:
            data = json.loads(m.group(0))
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            return data
    return None


class StreamParser:
    """Turns claude-agent-sdk messages into TaskEvents for one task."""

    def __init__(self) -> None:
        self.phase = Phase.PENDING
        self.usage = Usage()
        self._seen_messages: set[str] = set()
        self.outcome: TaskOutcome | None = None

    def _set_phase(self, phase: Phase, out: list[TaskEvent]) -> None:
        order = [Phase.PENDING, Phase.SCOUTING, Phase.DIGGING, Phase.WRITING, Phase.COMPLETING]
        if phase in order and order.index(phase) > order.index(self.phase):
            self.phase = phase
            out.append(PhaseEvent(phase))

    def feed(self, msg: Any) -> list[TaskEvent]:
        out: list[TaskEvent] = []
        kind = type(msg).__name__

        if kind == "AssistantMessage":
            for block in msg.content:
                bkind = type(block).__name__
                if bkind == "TextBlock":
                    for m in _PHASE_RE.finditer(block.text):
                        self._set_phase(Phase(m.group(1).lower()), out)
                elif bkind in ("ToolUseBlock", "ServerToolUseBlock"):
                    name = block.name
                    short = short_tool_name(name)
                    if self.phase == Phase.PENDING:
                        self._set_phase(Phase.SCOUTING, out)
                    if short in ("Write", "Edit"):
                        self._set_phase(Phase.WRITING, out)
                    if short != "StructuredOutput":
                        out.append(ToolEvent(short, _tool_detail(name, block.input or {})))
            msg_id = getattr(msg, "message_id", None)
            if msg.usage and (msg_id is None or msg_id not in self._seen_messages):
                if msg_id:
                    self._seen_messages.add(msg_id)
                self.usage = self.usage + Usage.from_api(msg.usage)
                out.append(UsageEvent(self.usage))

        elif kind == "RateLimitEvent":
            info = msg.rate_limit_info
            out.append(
                RateLimitEvt(
                    status=info.status,
                    limit_type=info.rate_limit_type,
                    utilization=info.utilization,
                    resets_at=info.resets_at,
                )
            )

        elif kind == "ResultMessage":
            if msg.usage:
                self.usage = Usage.from_api(msg.usage)
            out.append(UsageEvent(self.usage, msg.total_cost_usd))
            out.append(self._outcome_from_result(msg))

        return out

    def _outcome_from_result(self, msg: Any) -> TaskOutcome:
        data = msg.structured_output if isinstance(msg.structured_output, dict) else None
        data = data or extract_json(msg.result)
        common = dict(usage=self.usage, cost_usd=msg.total_cost_usd, num_turns=msg.num_turns)
        if msg.is_error and not data:
            reason = "; ".join(msg.errors or []) or (msg.result or msg.subtype or "unknown error")
            if getattr(msg, "terminal_reason", None) == "max_turns" or msg.subtype == "error_max_turns":
                reason = "Reached the maximum number of turns."
            self.outcome = TaskOutcome(Phase.FAILED, reason=reason, **common)
        elif not data:
            self.outcome = TaskOutcome(
                Phase.FAILED,
                summary=(msg.result or "")[:500],
                reason="Claude did not return a structured result.",
                **common,
            )
        else:
            status = {"done": Phase.DONE, "skipped": Phase.SKIPPED}.get(
                str(data.get("status", "")).lower(), Phase.FAILED
            )
            self.outcome = TaskOutcome(
                status,
                summary=str(data.get("summary") or ""),
                reason=str(data.get("reason") or ""),
                result_path=str(data.get("result_path") or ""),
                sources=[str(s) for s in data.get("sources") or []],
                **common,
            )
        return self.outcome


# ==========================================================================
# Real backend
# ==========================================================================
class ClaudeCodeBackend:
    """Runs one headless Claude Code session per task: web research + writing only."""

    TOOLS = ["WebSearch", "WebFetch", "Read", "Write", "Edit", "Glob"]

    def options(self, settings: Settings, task: TaskInfo):
        from claude_agent_sdk import ClaudeAgentOptions

        settings.notes_dir.mkdir(parents=True, exist_ok=True)
        return ClaudeAgentOptions(
            cwd=str(settings.notes_dir),
            cli_path=settings.claude_cli_path or None,
            model=settings.model or None,
            system_prompt={
                "type": "preset",
                "preset": "claude_code",
                "append": prompts.research_system_append(settings, task),
            },
            # No MCP servers at all: task management is done by Python.
            mcp_servers={},
            strict_mcp_config=True,
            tools=self.TOOLS,
            allowed_tools=[*self.TOOLS, "StructuredOutput"],
            disallowed_tools=["Bash"],
            permission_mode="dontAsk",  # anything not pre-approved is denied
            max_turns=settings.max_turns_per_task,
            output_format={"type": "json_schema", "schema": prompts.RESEARCH_SCHEMA},
        )

    async def run_task(self, settings: Settings, task: TaskInfo) -> AsyncIterator[TaskEvent]:
        from claude_agent_sdk import query

        parser = StreamParser()
        options = self.options(settings, task)
        async for msg in query(prompt=prompts.research_user_prompt(task), options=options):
            for event in parser.feed(msg):
                yield event
        if parser.outcome is None:
            yield TaskOutcome(Phase.FAILED, reason="Claude Code stopped without a result.",
                              usage=parser.usage)


# ==========================================================================
# Fake backend — TECHNO_WATCH_FAKE=1, no Claude calls
# ==========================================================================
class FakeBackend:
    def __init__(self, delay: float | None = None) -> None:
        self.delay = float(os.environ.get("TECHNO_WATCH_FAKE_DELAY", 0.6)) if delay is None else delay
        self.completed: list[str] = []
        self.fail_complete = False

    def status(self, settings: Settings) -> str:
        return "simulated"

    def login(self, settings: Settings) -> str:
        return self.status(settings)

    async def complete_task(self, settings: Settings, task: TaskInfo) -> None:
        await asyncio.sleep(self.delay)
        if self.fail_complete:
            raise RuntimeError("server unreachable")
        self.completed.append(task.id)

    async def list_tasks(self, settings: Settings) -> list[TaskInfo]:
        await asyncio.sleep(self.delay)
        return [
            TaskInfo("t1", "Textual 8 — what's new",
                     "Focus on the new widgets and whether we should upgrade.", list_id="L"),
            TaskInfo("t2", "uv workspaces", "Can it replace our monorepo tooling?", list_id="L"),
            TaskInfo("t3", "that thing from the meeting", "", list_id="L"),
        ]

    # Scripted research so --fake (and the README screenshots) look like a real run.
    _SCRIPT = {
        "t1": (["Textual 8 release notes", "Textual 8 new widgets"],
               ["https://textual.textualize.io/blog/", "https://github.com/Textualize/textual/releases",
                "https://textual.textualize.io/widget_gallery/"],
               "Summarised the Textual 8 release: new widgets, CSS changes and breaking "
               "changes, with an upgrade checklist."),
        "t2": (["uv workspaces monorepo", "uv workspace vs pants vs hatch"],
               ["https://docs.astral.sh/uv/concepts/projects/workspaces/",
                "https://github.com/astral-sh/uv/issues?q=workspace",
                "https://docs.astral.sh/uv/concepts/projects/dependencies/"],
               "Compared uv workspaces with the current monorepo setup; recommends a pilot "
               "on two packages first."),
    }

    async def run_task(self, settings: Settings, task: TaskInfo) -> AsyncIterator[TaskEvent]:
        usage = Usage()

        async def step(ev: TaskEvent, tokens: int = 0) -> AsyncIterator[TaskEvent]:
            nonlocal usage
            await asyncio.sleep(self.delay)
            yield ev
            if tokens:
                usage = usage + Usage(input_tokens=tokens, output_tokens=tokens // 4,
                                      cache_read_tokens=tokens // 2)
                yield UsageEvent(usage)

        searches, pages, summary = self._SCRIPT.get(
            task.id, ([task.title], [], f"Researched {task.title} and wrote a note."))
        async for e in step(PhaseEvent(Phase.SCOUTING)):
            yield e
        async for e in step(ToolEvent("WebSearch", searches[0]), 1800):
            yield e
        yield RateLimitEvt("allowed", "five_hour", 0.42, int(dt.datetime.now().timestamp()) + 9000)
        if not pages:
            yield TaskOutcome(Phase.SKIPPED, reason="Title too vague to identify a subject.",
                              usage=usage, cost_usd=0.01, num_turns=2)
            return
        async for e in step(PhaseEvent(Phase.DIGGING)):
            yield e
        for q in searches[1:]:
            async for e in step(ToolEvent("WebSearch", q), 2200):
                yield e
        for url in pages:
            async for e in step(ToolEvent("WebFetch", url), 4000):
                yield e
        async for e in step(PhaseEvent(Phase.WRITING)):
            yield e
        settings.notes_dir.mkdir(parents=True, exist_ok=True)
        path = Path(settings.notes_dir) / f"{dt.date.today()} - {slugify(task.title)}.md"
        path.write_text(f"# {task.title}\n\n(fake note)\n", encoding="utf-8")
        async for e in step(ToolEvent("Write", str(path)), 3000):
            yield e
        yield TaskOutcome(Phase.DONE, summary=summary, result_path=str(path), sources=pages,
                          usage=usage, cost_usd=0.12, num_turns=9)


def default_backends() -> tuple[Backend, TaskSource]:
    """(research backend, task source) — both fake with TECHNO_WATCH_FAKE=1."""
    if os.environ.get("TECHNO_WATCH_FAKE"):
        fake = FakeBackend()
        return fake, fake
    from .tasks_api import McpTaskSource

    return ClaudeCodeBackend(), McpTaskSource()
