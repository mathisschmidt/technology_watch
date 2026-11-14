# techno-watch-bot

A terminal UI that has **Claude Code** research your Google Tasks.

The app reads your open tasks itself, by calling your
[google-tasks-mcp](https://github.com/mathisschmidt/google-tasks-mcp) server
directly from Python. For each task you pick, Claude:
1. reads the title and description,
2. runs a few quick web searches and decides whether there is enough to dig into
   (if not, it skips the task and says why),
3. digs deeper and collects as much information as it can,
4. writes the result into your notes folder, in the format the task asks for or
   by default as a structured markdown note.

When Claude reports success and the result file exists, the app marks the task
as completed in Google Tasks.

After each run the app writes a **research-report** (tasks done, skipped or failed
with the reason, tokens and cost per task and in total). Only the newest 5 reports
are kept (you can change this in the settings).

## Requirements

- Python ≥ 3.11 and [uv](https://docs.astral.sh/uv/)
- [Claude Code](https://code.claude.com) installed and logged in (`claude` works in your terminal)
- A running [google-tasks-mcp](https://github.com/mathisschmidt/google-tasks-mcp)
  server; set its URL in Settings (it is stored only in your local config file)

Claude's work goes through Claude Code, using your Claude login. Task management
goes through your MCP server: on Home, choose **Connect Google Tasks** once. Your
browser opens the server's sign-in (server password, then Google), and the token
is saved in `~/.config/techno-watch-bot/mcp_token.json` (mode 600). It lasts 30
days; after that the app asks you to connect again.

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

## Download

Ready-to-run files for Linux, macOS and Windows are on the
[Releases](https://github.com/mathisschmidt/technology_watch/releases) page.
To publish a new release, push a version tag; GitHub Actions builds and attaches
the files (`.github/workflows/release.yml`):

```bash
git tag v2.0.0
```

```bash
git push origin v2.0.0
```

## Build a single file to share

```bash
uv run --group build python scripts/build.py
```

This writes `dist/techno-watch-bot-<os>-<arch>` (about 30 MB, `.exe` on Windows): one
executable with Python and all dependencies inside, so the person you share it with
doesn't need Python or uv. They only run it:

```bash
./techno-watch-bot-linux-x86_64
```

They still need Claude Code installed and logged in (`claude` on their PATH), and
their own google-tasks-mcp URL in Settings. To embed the Claude Code CLI too (adds
about 340 MB), build with `--with-claude-cli`. PyInstaller doesn't cross-compile:
build on each OS you want to share for. On macOS, the person may need to allow the
file in System Settings → Privacy & Security the first time.

## Screens

| Screen | Keys |
|---|---|
| **Home**: menu, current settings and Google Tasks connection status | `r` run · `h` history · `s` settings · `c` connect · `q` quit |
| **Task picker**: open tasks of the list; first row is "★ All tasks" | ↑/↓ + Enter · `a` run all · `r` refresh · `c` connect · Esc back |
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
| `tasks_mcp_url` | *(empty)* | URL of your google-tasks-mcp server, e.g. `https://tasks.example.com` |
| `model` | *(empty)* | Empty means Claude Code's default model |
| `max_turns_per_task` | `40` | Hard stop for one task |
| `scout_max_searches` | `3` | Quick searches before the dig / skip decision |
| `claude_cli_path` | *(empty)* | Custom `claude` binary |

## Safety

Each task runs as a separate headless Claude Code session with
`permission_mode="dontAsk"` and no MCP servers. Claude can only use WebSearch,
WebFetch and Read/Write/Edit/Glob (working directory = notes folder); Bash is
disallowed. Claude has no access to Google Tasks: the app only lists tasks and
marks them completed (`list_task_lists`, `list_tasks`, `patch_task`), and never
deletes anything.

## Development

```bash
uv run pytest
```

Tests cover config, report rendering and rotation, parsing of SDK messages,
the MCP client (tool calls, pagination, errors, and the full OAuth sign-in
against a mocked server), the runner (fake backend, failures, cancellation),
and the Textual screens through `App.run_test()`.
