"""Claude Code backend (via claude-agent-sdk) + a fake backend for demos/tests.

A backend exposes two coroutines:
- ``list_tasks(settings)`` -> list[TaskInfo]
- ``run_task(settings, task)`` -> async iterator of TaskEvent, ending with TaskOutcome
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


class BackendError(RuntimeError):
    pass


class Backend(Protocol):
    async def list_tasks(self, settings: Settings) -> list[TaskInfo]: ...

    def run_task(self, settings: Settings, task: TaskInfo) -> AsyncIterator[TaskEvent]: ...


# ==========================================================================
# Stream parsing (pure, testable)
# ==========================================================================
_PHASE_RE = re.compile(r"^\s*PHASE:\s*(scouting|digging|writing|completing)\b", re.I | re.M)
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
                    elif short in ("patch_task", "update_task"):
                        self._set_phase(Phase.COMPLETING, out)
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
    def _options(self, settings: Settings, **kw: Any):
        from claude_agent_sdk import ClaudeAgentOptions

        settings.notes_dir.mkdir(parents=True, exist_ok=True)
        return ClaudeAgentOptions(
            cwd=str(settings.notes_dir),
            cli_path=settings.claude_cli_path or None,
            model=settings.model or None,
            setting_sources=["user", "project", "local"],  # loads the user's MCP servers
            permission_mode="dontAsk",  # anything not pre-approved is denied
            disallowed_tools=[
                "Bash",
                settings.mcp_tool("delete_task"),
                settings.mcp_tool("delete_task_list"),
                settings.mcp_tool("clear_completed_tasks"),
            ],
            **kw,
        )

    async def list_tasks(self, settings: Settings) -> list[TaskInfo]:
        from claude_agent_sdk import query

        options = self._options(
            settings,
            allowed_tools=[
                settings.mcp_tool("list_task_lists"),
                settings.mcp_tool("list_tasks"),
                "StructuredOutput",
            ],
            max_turns=15,
            output_format={"type": "json_schema", "schema": prompts.LIST_TASKS_SCHEMA},
        )
        result = None
        async for msg in query(prompt=prompts.list_tasks_prompt(settings), options=options):
            if type(msg).__name__ == "ResultMessage":
                result = msg
        if result is None:
            raise BackendError("Claude Code returned no result.")
        data = result.structured_output if isinstance(result.structured_output, dict) else None
        data = data or extract_json(result.result)
        if not data:
            raise BackendError(result.result or "Could not read the task list from Claude.")
        if data.get("error") and not data.get("tasks"):
            raise BackendError(str(data["error"]))
        list_id = str(data.get("task_list_id") or "")
        return [
            TaskInfo(
                id=str(t["id"]),
                title=str(t.get("title") or "(untitled)"),
                notes=str(t.get("notes") or ""),
                due=str(t.get("due") or ""),
                list_id=list_id,
            )
            for t in data.get("tasks") or []
            if t.get("id")
        ]

    async def run_task(self, settings: Settings, task: TaskInfo) -> AsyncIterator[TaskEvent]:
        from claude_agent_sdk import query

        options = self._options(
            settings,
            system_prompt={
                "type": "preset",
                "preset": "claude_code",
                "append": prompts.research_system_append(settings, task),
            },
            tools=["WebSearch", "WebFetch", "Read", "Write", "Edit", "Glob"],
            allowed_tools=[
                "WebSearch",
                "WebFetch",
                "Read",
                "Write",
                "Edit",
                "Glob",
                "StructuredOutput",
                settings.mcp_tool("list_task_lists"),
                settings.mcp_tool("get_task"),
                settings.mcp_tool("patch_task"),
                settings.mcp_tool("update_task"),
            ],
            max_turns=settings.max_turns_per_task,
            output_format={"type": "json_schema", "schema": prompts.RESEARCH_SCHEMA},
        )
        parser = StreamParser()
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

    async def list_tasks(self, settings: Settings) -> list[TaskInfo]:
        await asyncio.sleep(self.delay)
        return [
            TaskInfo("t1", "Textual 8 — what's new", "Focus on the new widgets.", list_id="L"),
            TaskInfo("t2", "uv workspaces", "", list_id="L"),
            TaskInfo("t3", "that thing from the meeting", "", list_id="L"),
        ]

    async def run_task(self, settings: Settings, task: TaskInfo) -> AsyncIterator[TaskEvent]:
        usage = Usage()

        async def step(ev: TaskEvent, tokens: int = 0) -> AsyncIterator[TaskEvent]:
            nonlocal usage
            await asyncio.sleep(self.delay)
            yield ev
            if tokens:
                usage = usage + Usage(input_tokens=tokens, output_tokens=tokens // 4)
                yield UsageEvent(usage)

        async for e in step(PhaseEvent(Phase.SCOUTING)):
            yield e
        async for e in step(ToolEvent("WebSearch", task.title), 1800):
            yield e
        yield RateLimitEvt("allowed", "five_hour", 0.42, int(dt.datetime.now().timestamp()) + 3600)
        if "thing" in task.title:
            yield TaskOutcome(Phase.SKIPPED, reason="Title too vague to identify a subject.",
                              usage=usage, cost_usd=0.01, num_turns=2)
            return
        async for e in step(PhaseEvent(Phase.DIGGING)):
            yield e
        for q in ("official docs", "changelog", "benchmarks"):
            async for e in step(ToolEvent("WebFetch", f"{task.title} {q}"), 4000):
                yield e
        async for e in step(PhaseEvent(Phase.WRITING)):
            yield e
        settings.notes_dir.mkdir(parents=True, exist_ok=True)
        path = Path(settings.notes_dir) / f"{dt.date.today()} - {slugify(task.title)}.md"
        path.write_text(f"# {task.title}\n\n(fake note)\n", encoding="utf-8")
        async for e in step(ToolEvent("Write", str(path)), 3000):
            yield e
        async for e in step(PhaseEvent(Phase.COMPLETING)):
            yield e
        async for e in step(ToolEvent("patch_task", task.id), 500):
            yield e
        yield TaskOutcome(Phase.DONE, summary=f"Researched {task.title} and wrote a note.",
                          result_path=str(path), sources=["https://example.com"],
                          usage=usage, cost_usd=0.12, num_turns=9)


def default_backend() -> Backend:
    if os.environ.get("TECHNO_WATCH_FAKE"):
        return FakeBackend()
    return ClaudeCodeBackend()
