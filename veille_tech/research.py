"""Calls Claude with web search and renders a markdown note."""
from __future__ import annotations

import datetime as dt
import logging
import re
from dataclasses import dataclass
from pathlib import Path

import yaml
from anthropic import Anthropic
from slugify import slugify

from .google_tasks import GTask

log = logging.getLogger(__name__)

# Sections file lives next to the project root (one level up from this package).
_SECTIONS_FILE = Path(__file__).parent.parent / "sections.yaml"

_PROMPT_TEMPLATE = """\
You are a tech-watch research assistant.

For each topic you receive:
1. Search the web for current, authoritative sources (official docs, primary
   sources, recent reputable articles). Prefer recent material.
2. From the section menu below, choose the sections that best fit the topic.
   Include at least 3. You may use all of them when the topic warrants it.
3. Produce ONE markdown note using ONLY the chosen sections, in the order
   they appear in the menu.

--- SECTION MENU ---
{sections_block}
--- END MENU ---

RULES:
- If you cannot research the topic — the link is inaccessible, the content is
  behind a login, there is not enough material to produce a useful note, or the
  topic is too vague — output ONLY this single line and nothing else:
  `ERROR: <one sentence explaining why>`
- Otherwise, your VERY FIRST line must be `# <concise title>` — a short,
  punchy title (max 8 words, no trailing period).
- Immediately after the title line, start your first `##` section. Zero prose
  between them — no "I'll research…", no "Here is…", nothing.
- Output ONLY the markdown. No preamble, no frontmatter, no code fences.
- Be specific. Names, versions, numbers, dates.
- If sources disagree, say so.
"""


def _load_sections(path: Path = _SECTIONS_FILE) -> str:
    """Return the sections block to inject into the prompt."""
    if not path.exists():
        log.warning("sections.yaml not found at %s — using empty section list", path)
        return "(no sections defined)"
    with path.open() as fh:
        data = yaml.safe_load(fh)
    lines: list[str] = []
    for s in data.get("sections", []):
        lines.append(f"## {s['title']}")
        lines.append(s["description"].strip())
        lines.append("")
    return "\n".join(lines).strip()


def _build_system_prompt() -> str:
    return _PROMPT_TEMPLATE.format(sections_block=_load_sections())


@dataclass
class ResearchResult:
    body_md: str
    used_searches: int
    title: str = ""


def research_task(
    task: GTask,
    *,
    api_key: str,
    model: str,
    max_web_searches: int,
) -> ResearchResult:
    """Run a single research pass and return the rendered markdown body."""
    log.debug("research_task start: id=%s title=%r", task.id, task.title)
    client = Anthropic(api_key=api_key)

    user_message = f"Topic: {task.title}"
    if task.notes.strip():
        user_message += f"\n\nAdditional context from my task notes:\n{task.notes.strip()}"
    user_message += "\n\nDo the research and produce the note as specified."

    log.debug(
        "Calling Claude model=%s max_tokens=4096 max_web_searches=%d",
        model, max_web_searches,
    )
    log.debug("User message:\n%s", user_message)

    response = client.messages.create(
        model=model,
        max_tokens=4096,
        system=_build_system_prompt(),
        messages=[{"role": "user", "content": user_message}],
        tools=[{
            "type": "web_search_20260209",
            "name": "web_search",
            "max_uses": max_web_searches,
        }],
    )

    log.debug(
        "Response received: stop_reason=%s  input_tokens=%d  output_tokens=%d  blocks=%d",
        response.stop_reason,
        response.usage.input_tokens,
        response.usage.output_tokens,
        len(response.content),
    )

    # Concatenate all text blocks from the final assistant turn.
    text_parts: list[str] = []
    used_searches = 0
    for block in response.content:
        btype = getattr(block, "type", None)
        if btype == "text":
            text_parts.append(block.text)
            log.debug("  [text] %d chars", len(block.text))
        elif btype == "server_tool_use":
            used_searches += 1
            query = getattr(block, "input", {}).get("query", "?")
            log.debug("  [web_search #%d] query=%r", used_searches, query)
        else:
            log.debug("  [block type=%s]", btype)

    body = "\n".join(text_parts).strip()
    # Strip an outer code fence if Claude wrapped the whole note.
    body = re.sub(r"^```(?:markdown)?\s*\n", "", body)
    body = re.sub(r"\n```\s*$", "", body)
    # Bail out early if Claude signalled it cannot research the topic.
    error_match = re.match(r"^ERROR:\s*(.+)$", body, re.IGNORECASE)
    if error_match:
        raise ValueError(error_match.group(1).strip())
    # Extract the generated title from the leading `# …` line.
    generated_title = ""
    title_match = re.match(r"^#\s+(.+)$", body, re.MULTILINE)
    if title_match:
        generated_title = title_match.group(1).strip()
        body = body[title_match.end():].lstrip("\n")
    # Strip any remaining preamble prose before the first ## heading.
    first_heading = re.search(r"^##\s", body, re.MULTILINE)
    if first_heading:
        body = body[first_heading.start():]
    # Normalize multiple consecutive blank lines to a single blank line.
    body = re.sub(r"\n{3,}", "\n\n", body)
    log.debug("Final note body: %d chars, title=%r", len(body), generated_title)
    return ResearchResult(body_md=body, used_searches=used_searches, title=generated_title)


def render_note(task: GTask, result: ResearchResult) -> tuple[str, str]:
    """Return (filename, full_markdown_with_frontmatter)."""
    today = dt.date.today().isoformat()
    display_title = result.title or task.title
    slug = slugify(display_title, max_length=60) or "untitled"
    filename = f"{today} - {slug}.md"

    safe_title = display_title.replace('"', '\\"')

    frontmatter = (
        "---\n"
        f'title: "{safe_title}"\n'
        f"date: {today}\n"
        f"google_task_id: {task.id}\n"
        f"web_searches_used: {result.used_searches}\n"
        "tags:\n"
        "  - veille-tech\n"
        "---\n\n"
    )
    return filename, frontmatter + result.body_md + "\n"
