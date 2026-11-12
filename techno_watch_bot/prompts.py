"""Prompts and JSON schemas sent to Claude Code."""
from __future__ import annotations

import datetime as dt

from .config import Settings
from .events import TaskInfo

# ---------------------------------------------------------------- list tasks
LIST_TASKS_SCHEMA = {
    "type": "object",
    "properties": {
        "task_list_id": {"type": "string"},
        "tasks": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "title": {"type": "string"},
                    "notes": {"type": "string"},
                    "due": {"type": "string"},
                },
                "required": ["id", "title"],
            },
        },
        "error": {"type": "string"},
    },
    "required": ["tasks"],
}


def list_tasks_prompt(settings: Settings) -> str:
    return f"""\
Using ONLY the Google Tasks MCP tools ({settings.mcp_tool("list_task_lists")} and
{settings.mcp_tool("list_tasks")}):
1. Find the task list whose title is exactly "{settings.task_list_name}"
   (case-insensitive match is acceptable).
2. List its OPEN tasks (not completed, not deleted, not hidden). Follow pagination.
3. Return them as structured output: task_list_id, and for each task its id,
   title, notes (empty string if none) and due (empty string if none).
If the list does not exist or a tool fails, return an empty `tasks` array and
explain in `error`. Do not modify anything. Do not search the web.
"""


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
        "task_marked_done": {"type": "boolean"},
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
unattended (nobody can answer questions). You work on ONE Google Task.

Report progress by writing, on its own line, `PHASE: <name>` whenever you
enter a phase: scouting, digging, writing, completing.

Process:
1. PHASE: scouting — read the task title and description. Do at most
   {scout_max} quick web searches to understand what the subject is.
   Decide whether there is enough public information to research it seriously.
   If NOT (too vague, private, inaccessible, nothing found): stop immediately,
   do NOT modify the task, and return status "skipped" with a clear reason.
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
4. PHASE: completing — mark the task as completed with
   {patch_tool} (status: "completed"), using task list id `{list_id}`
   and task id `{task_id}`. Only do this if step 3 succeeded.
5. Return the structured output. status "done" only if the result file exists
   AND the task was marked completed; "failed" if something broke (explain in
   reason).

Rules: never delete tasks or files; do not touch files outside the notes
folder; do not ask questions.

{default_structure}
"""


def research_system_append(settings: Settings, task: TaskInfo) -> str:
    today = dt.date.today().isoformat()
    return RESEARCH_SYSTEM_APPEND.format(
        scout_max=settings.scout_max_searches,
        notes_dir=settings.notes_dir,
        today=today,
        patch_tool=settings.mcp_tool("patch_task"),
        list_id=task.list_id or "(find it with list_task_lists)",
        task_id=task.id,
        default_structure=DEFAULT_NOTE_STRUCTURE.format(today=today, task_id=task.id),
    )


def research_user_prompt(task: TaskInfo) -> str:
    parts = [f"Task title: {task.title}", f"Task id: {task.id}"]
    if task.due:
        parts.append(f"Due: {task.due}")
    parts.append("Task description:")
    parts.append(task.notes.strip() or "(no description)")
    parts.append("\nStart now with PHASE: scouting.")
    return "\n".join(parts)
