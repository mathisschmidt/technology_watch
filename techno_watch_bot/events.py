"""Plain data types shared between the Claude backend, the runner and the UI."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path


@dataclass
class TaskInfo:
    id: str
    title: str
    notes: str = ""
    due: str = ""
    list_id: str = ""


class Phase(str, Enum):
    PENDING = "pending"
    SCOUTING = "scouting"
    DIGGING = "digging"
    WRITING = "writing"
    COMPLETING = "completing"
    DONE = "done"
    SKIPPED = "skipped"
    FAILED = "failed"
    CANCELLED = "cancelled"

    @property
    def icon(self) -> str:
        return _ICONS[self]

    @property
    def finished(self) -> bool:
        return self in (Phase.DONE, Phase.SKIPPED, Phase.FAILED, Phase.CANCELLED)


_ICONS = {
    Phase.PENDING: "⏳",
    Phase.SCOUTING: "🔎",
    Phase.DIGGING: "📚",
    Phase.WRITING: "✍️",
    Phase.COMPLETING: "☑️",
    Phase.DONE: "✅",
    Phase.SKIPPED: "⏭️",
    Phase.FAILED: "❌",
    Phase.CANCELLED: "🚫",
}


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_creation_tokens: int = 0

    @property
    def total(self) -> int:
        return (
            self.input_tokens
            + self.output_tokens
            + self.cache_read_tokens
            + self.cache_creation_tokens
        )

    def __add__(self, other: "Usage") -> "Usage":
        return Usage(
            self.input_tokens + other.input_tokens,
            self.output_tokens + other.output_tokens,
            self.cache_read_tokens + other.cache_read_tokens,
            self.cache_creation_tokens + other.cache_creation_tokens,
        )

    @classmethod
    def from_api(cls, raw: dict | None) -> "Usage":
        raw = raw or {}
        return cls(
            input_tokens=int(raw.get("input_tokens") or 0),
            output_tokens=int(raw.get("output_tokens") or 0),
            cache_read_tokens=int(raw.get("cache_read_input_tokens") or 0),
            cache_creation_tokens=int(raw.get("cache_creation_input_tokens") or 0),
        )


# --------------------------------------------------------------------------
# Events streamed by a backend while one task runs
# --------------------------------------------------------------------------
@dataclass
class PhaseEvent:
    phase: Phase


@dataclass
class ToolEvent:
    name: str
    detail: str = ""


@dataclass
class UsageEvent:
    """Running (cumulative) usage of the current task."""

    usage: Usage
    cost_usd: float | None = None


@dataclass
class RateLimitEvt:
    status: str
    limit_type: str | None = None
    utilization: float | None = None
    resets_at: int | None = None


@dataclass
class TaskOutcome:
    """Final event of a task: what Claude reported + authoritative metrics."""

    status: Phase  # DONE / SKIPPED / FAILED
    summary: str = ""
    reason: str = ""
    result_path: str = ""
    sources: list[str] = field(default_factory=list)
    usage: Usage = field(default_factory=Usage)
    cost_usd: float | None = None
    num_turns: int = 0


TaskEvent = PhaseEvent | ToolEvent | UsageEvent | RateLimitEvt | TaskOutcome


# --------------------------------------------------------------------------
# Aggregated record of one task, used by reports
# --------------------------------------------------------------------------
@dataclass
class TaskRun:
    task: TaskInfo
    status: Phase = Phase.PENDING
    summary: str = ""
    reason: str = ""
    result_path: str = ""
    sources: list[str] = field(default_factory=list)
    usage: Usage = field(default_factory=Usage)
    cost_usd: float | None = None
    num_turns: int = 0
    duration_s: float = 0.0

    @property
    def result_name(self) -> str:
        return Path(self.result_path).name if self.result_path else ""
