# techno-watch-bot

Textual TUI (launched with Typer). Python lists and completes Google Tasks by calling the
user's google-tasks-mcp server directly; **Claude Code** (via `claude-agent-sdk`) only
researches each task and writes the result to a notes folder. One research-report per run.

## Architecture

```
techno_watch_bot/
├── cli.py        # Typer entrypoint `techno-watch-bot` (--fake for the simulated backend)
├── app.py        # Textual App: holds settings + backend, opens Home (and Settings on first run)
├── config.py     # Settings (Pydantic) <-> ~/.config/techno-watch-bot/config.toml
├── claude.py     # ClaudeCodeBackend (SDK query), StreamParser (SDK msgs -> events), FakeBackend
├── tasks_api.py  # google-tasks-mcp client: OAuth (DCR+PKCE+loopback), tools/call over HTTP
├── prompts.py    # research system prompt, JSON schema, default note structure
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

```bash
uv run --group build python scripts/build.py
```

```bash
uv run --with playwright --with pillow python scripts/screenshots.py
```

`scripts/screenshots.py` regenerates `docs/screenshots/*.png` and `docs/demo.gif` from the
fake backend (set `CHROMIUM_PATH` if Playwright's Chromium isn't installed). Re-run it after
UI changes so the README stays accurate.

`scripts/build.py` builds a single-file executable with PyInstaller into `dist/`
(`--with-claude-cli` embeds the SDK's bundled CLI). If a dependency loads modules or
data lazily, add a `--collect-*` option in `scripts/build.py`, then smoke-test the
binary with `--fake`.

## Key design points

- Task management is Python only: `tasks_api.McpTasksClient` POSTs JSON-RPC `tools/call`
  to `<tasks_mcp_url>/mcp` (stateless server, SSE responses) with a Bearer token.
  Tool output is JSON, sometimes followed by warnings (parsed with `raw_decode`).
- Token: 30 days, no refresh; cached in `~/.config/techno-watch-bot/mcp_token.json` (0600).
  401 or expired -> `AuthRequired` -> "Connect Google Tasks" on Home or `c` in the picker.
- Claude runs with no MCP servers (`strict_mcp_config`), `dontAsk`, and only web +
  file tools. The runner marks the task done only if Claude says done AND the file exists.
- Outcomes come back as structured output (`output_format` JSON schema), with a fallback
  that parses JSON from the result text.
- Live progress: Claude writes `PHASE: scouting|digging|writing` lines (Write/Edit tool
  calls also move it to writing); the runner adds `completing` while it patches the task.
- Tokens come from `AssistantMessage.usage` (deduped by message_id) live, then from
  `ResultMessage.usage` (authoritative). Limits come from `RateLimitEvent`.
- Python writes the reports, not Claude, so they are always in the same format.
- To change the research behaviour or the default note format, edit `prompts.py`.
