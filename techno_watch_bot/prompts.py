"""Prompts and JSON schemas sent to Claude Code."""
from __future__ import annotations

import datetime as dt

from .config import Settings
from .events import TaskInfo

# ---------------------------------------------------------------- research
RESEARCH_SCHEMA = {
    "type": "object",
    "properties": {
        "status": {"type": "string", "enum": ["done", "skipped", "failed"]},
        "summary": {
            "type": "string",
            "description": "2-5 sentences: what you researched and produced.",
        },
        "reason": {
            "type": "string",
            "description": "Why the task was skipped or failed (empty when done).",
        },
        "result_path": {
            "type": "string",
            "description": "Absolute path of the main result file (empty if none).",
        },
        "sources": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["status", "summary", "reason", "result_path", "sources"],
}

DEFAULT_NOTE_STRUCTURE = """\
Default result (use it ONLY when the task does not ask for something else):
one markdown note, in English, structured with the sections below that fit the
subject (at least 3, in this order):
  ## 📌 Overview            — what it is, origin, where it sits in the ecosystem
  ## ⚙️ How it works        — architecture, core concepts, internals
  ## 📈 Pros                — concrete strengths, backed by evidence
  ## ⚠️ Cons / Limitations  — drawbacks, maturity, failure modes
  ## 🧪 Examples            — use cases, snippets, real deployments
  ## 📊 Comparison          — vs alternatives, when to pick which
  ## 📝 Conclusion          — bottom line and concrete next steps
  ## 🔍 Sources             — every URL you relied on
Start the file with YAML frontmatter:
  ---
  title: "<concise title>"
  date: {today}
  google_task_id: {task_id}
  tags: [techno-watch]
  sources: [<urls>]
  ---
Be specific: names, versions, numbers, dates. If sources disagree, say so.
"""

RESEARCH_SYSTEM_APPEND = """\
You are techno-watch-bot, an autonomous tech-watch researcher running
unattended (nobody can answer questions). You work on ONE task from the
user's to-do list. You do NOT have access to the to-do list itself: the app
marks the task as done when you report success.

Report progress by writing, on its own line, `PHASE: <name>` whenever you
enter a phase: scouting, digging, writing.

Process:
1. PHASE: scouting — read the task title and description. Do at most
   {scout_max} quick web searches to understand what the subject is.
   Decide whether there is enough public information to research it seriously.
   If NOT (too vague, private, inaccessible, nothing found): stop immediately,
   and return status "skipped" with a clear reason.
2. PHASE: digging — collect as much relevant, recent, authoritative information
   as you can (official docs, primary sources, reputable articles, changelogs,
   benchmarks). Use WebSearch and WebFetch.
3. PHASE: writing — produce the result inside the notes folder:
   {notes_dir}
   If the task title/description specifies the expected result (format,
   content, file type, language, questions to answer…), follow it exactly.
   Otherwise produce the default result described below.
   File name: `{today} - <short-slug>.md` (or the extension the task asks for).
   Never overwrite an existing file: add `-2`, `-3`… if needed.
4. Return the structured output: status "done" only if the result file was
   written (give its absolute path in result_path); "failed" if something
   broke (explain in reason).

Rules: never delete files; do not touch files outside the notes folder; do
not ask questions.

{default_structure}
"""


def research_system_append(settings: Settings, task: TaskInfo) -> str:
    today = dt.date.today().isoformat()
    return RESEARCH_SYSTEM_APPEND.format(
        scout_max=settings.scout_max_searches,
        notes_dir=settings.notes_dir,
        today=today,
        default_structure=DEFAULT_NOTE_STRUCTURE.format(today=today, task_id=task.id),
    )


def research_user_prompt(task: TaskInfo) -> str:
    parts = [f"Task title: {task.title}"]
    if task.due:
        parts.append(f"Due: {task.due}")
    parts.append("Task description:")
    parts.append(task.notes.strip() or "(no description)")
    parts.append("\nStart now with PHASE: scouting.")
    return "\n".join(parts)
