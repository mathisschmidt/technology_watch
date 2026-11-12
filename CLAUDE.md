# techno-watch-bot

Textual TUI (launched with Typer) that drives **Claude Code** through `claude-agent-sdk`
to research open Google Tasks, write the results to a notes folder, mark the tasks done,
and write a research-report per run.

## Architecture

```
techno_watch_bot/
├── cli.py        # Typer entrypoint `techno-watch-bot` (--fake for the simulated backend)
├── app.py        # Textual App: holds settings + backend, opens Home (and Settings on first run)
├── config.py     # Settings (Pydantic) <-> ~/.config/techno-watch-bot/config.toml
├── claude.py     # ClaudeCodeBackend (SDK query), StreamParser (SDK msgs -> events), FakeBackend
├── prompts.py    # list-tasks prompt, research system prompt, JSON schemas, default note structure
├── events.py     # TaskInfo, Phase, Usage, task events, TaskRun
├── runner.py     # sequential runner, graceful cancel, writes the report at the end
├── reports.py    # render / write / rotate (keep max_reports) / list reports
└── screens/      # home, picker, monitor, history (+ ReportScreen), settings
```

## Running

```bash
uv sync
```

```bash
uv run techno-watch-bot
```

```bash
uv run techno-watch-bot --fake
```

```bash
uv run pytest
```

## Key design points

- No Google or Anthropic code in Python: Claude Code uses the user's `google-task` MCP
  (`setting_sources=["user","project","local"]`), WebSearch and WebFetch.
- Outcomes come back as structured output (`output_format` JSON schema), with a fallback
  that parses JSON from the result text.
- Live progress: Claude writes `PHASE: scouting|digging|writing|completing` lines, and
  tool calls (Write -> writing, patch_task -> completing) also move the phase forward.
- Tokens come from `AssistantMessage.usage` (deduped by message_id) live, then from
  `ResultMessage.usage` (authoritative). Limits come from `RateLimitEvent`.
- Python writes the reports, not Claude, so they are always in the same format.
- To change the research behaviour or the default note format, edit `prompts.py`.
