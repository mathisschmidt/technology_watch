# techno-watch-bot

A terminal UI that has **Claude Code** research your Google Tasks.

For each task Claude:
1. reads the title and description,
2. runs a few quick web searches and decides whether there is enough to dig into
   (if not, it skips the task and says why),
3. digs deeper and collects as much information as it can,
4. writes the result into your notes folder, in the format the task asks for or
   by default as a structured markdown note,
5. marks the task as completed in Google Tasks.

After each run the app writes a **research-report** (tasks done, skipped or failed
with the reason, tokens and cost per task and in total). Only the newest 5 reports
are kept (you can change this in the settings).

## Requirements

- Python ≥ 3.11 and [uv](https://docs.astral.sh/uv/)
- [Claude Code](https://code.claude.com) installed and logged in (`claude` works in your terminal)
- A Google Tasks MCP server configured in Claude Code (default name: `google-task`)
  that exposes `list_task_lists`, `list_tasks`, `get_task` and `patch_task`

Python never talks to Google or to the Anthropic API directly. Everything goes
through Claude Code, using your login and your MCP servers.

## Install and run

```bash
uv sync
```

```bash
uv run techno-watch-bot
```

On first launch the Settings screen opens. Use `--fake` to try the UI with a
simulated backend (no Claude calls):

```bash
uv run techno-watch-bot --fake
```

## Screens

| Screen | Keys |
|---|---|
| **Home**: menu and current settings (task list, folders, MCP, model) | `r` run · `h` history · `s` settings · `q` quit |
| **Task picker**: open tasks fetched by Claude; first row is "★ All tasks" | ↑/↓ + Enter · `a` run all · `r` refresh · Esc back |
| **Monitor**: queue and statuses, current step, tokens, cost, time, Claude limits, activity log | `c` cancel · `o` open report · Esc home (when finished) |
| **History**: the last reports; Enter opens one | Esc back |
| **Settings**: every path and option, saved to the config file | Ctrl+S save · Esc back |

## Settings

Stored in `~/.config/techno-watch-bot/config.toml` (override the folder with
`TECHNO_WATCH_CONFIG_DIR`). All of them can be edited from the UI.

| Key | Default | Meaning |
|---|---|---|
| `task_list_name` | `Veille tech` | Google Tasks list to read |
| `notes_dir` | `~/techno-watch/notes` | Where results are written (e.g. an Obsidian vault folder) |
| `reports_dir` | `~/.local/share/techno-watch-bot/reports` | Where research-reports are stored |
| `max_reports` | `5` | Older reports are deleted beyond this |
| `google_task_mcp_name` | `google-task` | Name of the MCP server in Claude Code |
| `model` | *(empty)* | Empty means Claude Code's default model |
| `max_turns_per_task` | `40` | Hard stop for one task |
| `scout_max_searches` | `3` | Quick searches before the dig / skip decision |
| `claude_cli_path` | *(empty)* | Custom `claude` binary |

## Safety

Each task runs as a separate headless Claude Code session with
`permission_mode="dontAsk"`. Claude can only use WebSearch, WebFetch,
Read/Write/Edit/Glob (working directory = notes folder), and the read and
patch Google Tasks tools. Bash and the delete tools are disallowed.

## Development

```bash
uv run pytest
```

Tests cover config, report rendering and rotation, parsing of SDK messages,
the runner (with a fake backend, failures and cancellation), and the Textual
screens through `App.run_test()`.
