"""Research-reports: render, write, rotate (keep N newest), list history."""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from pathlib import Path

from .events import Phase, TaskRun, Usage

SUFFIX = "-research-report.md"


def fmt_duration(seconds: float) -> str:
    seconds = int(round(seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}h{m:02d}m{s:02d}s" if h else f"{m}m{s:02d}s" if m else f"{s}s"


def fmt_tokens(n: int) -> str:
    return f"{n / 1000:.1f}k" if n >= 1000 else str(n)


def _cost(runs: list[TaskRun]) -> float | None:
    costs = [r.cost_usd for r in runs if r.cost_usd is not None]
    return sum(costs) if costs else None


def _cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


def render_report(
    runs: list[TaskRun],
    *,
    task_list: str,
    model: str,
    started_at: dt.datetime,
    finished_at: dt.datetime,
) -> str:
    total = sum((r.usage for r in runs), Usage())
    cost = _cost(runs)
    counts = {p: sum(1 for r in runs if r.status == p) for p in Phase}
    duration = (finished_at - started_at).total_seconds()

    lines = [
        "---",
        f"date: {started_at.isoformat(timespec='seconds')}",
        f'task_list: "{task_list}"',
        f"model: {model or 'default'}",
        f"n_tasks: {len(runs)}",
        f"n_done: {counts[Phase.DONE]}",
        f"n_skipped: {counts[Phase.SKIPPED]}",
        f"n_failed: {counts[Phase.FAILED]}",
        f"n_cancelled: {counts[Phase.CANCELLED]}",
        f"total_tokens: {total.total}",
        f"total_cost_usd: {cost:.4f}" if cost is not None else "total_cost_usd: null",
        f"duration_s: {duration:.0f}",
        "---",
        "",
        f"# Research report — {started_at:%Y-%m-%d %H:%M}",
        "",
        f"Task list **{task_list}** · {len(runs)} task(s): "
        f"✅ {counts[Phase.DONE]} done · ⏭️ {counts[Phase.SKIPPED]} skipped · "
        f"❌ {counts[Phase.FAILED]} failed"
        + (f" · 🚫 {counts[Phase.CANCELLED]} cancelled" if counts[Phase.CANCELLED] else ""),
        "",
        "## Summary",
        "",
        "| # | Task | Status | Result | Tokens | Duration |",
        "|---|------|--------|--------|-------:|---------:|",
    ]
    for i, r in enumerate(runs, 1):
        lines.append(
            f"| {i} | {_cell(r.task.title)} | {r.status.icon} {r.status.value} | "
            f"{_cell(r.result_name) or '—'} | {fmt_tokens(r.usage.total)} | "
            f"{fmt_duration(r.duration_s)} |"
        )

    lines += ["", "## Details", ""]
    for i, r in enumerate(runs, 1):
        lines += [f"### {i}. {r.task.title}", "", f"- **Status:** {r.status.icon} {r.status.value}"]
        if r.result_path:
            lines.append(f"- **Result:** `{r.result_path}`")
        if r.status != Phase.DONE and r.reason:
            lines.append(f"- **Why not done:** {r.reason}")
        u = r.usage
        lines.append(
            f"- **Tokens:** {u.total} (in {u.input_tokens}, out {u.output_tokens}, "
            f"cache read {u.cache_read_tokens}, cache write {u.cache_creation_tokens})"
        )
        if r.cost_usd is not None:
            lines.append(f"- **Cost:** ${r.cost_usd:.4f}")
        lines.append(f"- **Duration:** {fmt_duration(r.duration_s)} · {r.num_turns} turn(s)")
        if r.sources:
            lines.append(f"- **Sources:** {len(r.sources)}")
        if r.summary:
            lines += ["", r.summary]
        lines.append("")

    if len(runs) > 1:
        lines += [
            "## Totals",
            "",
            f"- **Tokens:** {total.total} (in {total.input_tokens}, out {total.output_tokens}, "
            f"cache read {total.cache_read_tokens}, cache write {total.cache_creation_tokens})",
        ]
        if cost is not None:
            lines.append(f"- **Cost:** ${cost:.4f}")
        lines += [f"- **Duration:** {fmt_duration(duration)}", ""]
    return "\n".join(lines)


def write_report(reports_dir: Path, content: str, started_at: dt.datetime, keep: int) -> Path:
    reports_dir.mkdir(parents=True, exist_ok=True)
    path = reports_dir / f"{started_at:%Y-%m-%d_%H%M%S}{SUFFIX}"
    n = 2
    while path.exists():
        path = reports_dir / f"{started_at:%Y-%m-%d_%H%M%S}-{n}{SUFFIX}"
        n += 1
    path.write_text(content, encoding="utf-8")
    rotate(reports_dir, keep)
    return path


def _report_files(reports_dir: Path) -> list[Path]:
    if not reports_dir.exists():
        return []
    # File names start with a sortable timestamp: newest first.
    return sorted(reports_dir.glob(f"*{SUFFIX}"), key=lambda p: p.name, reverse=True)


def rotate(reports_dir: Path, keep: int) -> list[Path]:
    removed = []
    for old in _report_files(reports_dir)[keep:]:
        old.unlink(missing_ok=True)
        removed.append(old)
    return removed


@dataclass
class ReportInfo:
    path: Path
    meta: dict[str, str]

    @property
    def date(self) -> str:
        return self.meta.get("date", self.path.name[:17]).replace("T", " ")

    def get_int(self, key: str) -> int:
        try:
            return int(self.meta.get(key, 0))
        except ValueError:
            return 0


def _frontmatter(text: str) -> dict[str, str]:
    if not text.startswith("---\n"):
        return {}
    end = text.find("\n---", 4)
    meta = {}
    for line in text[4:end].splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            meta[k.strip()] = v.strip().strip('"')
    return meta


def list_reports(reports_dir: Path) -> list[ReportInfo]:
    out = []
    for p in _report_files(reports_dir):
        try:
            head = p.read_text(encoding="utf-8")[:2048]
        except OSError:
            continue
        out.append(ReportInfo(p, _frontmatter(head)))
    return out
