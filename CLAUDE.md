# veille-tech

Textual TUI that reads open tasks from a Google Tasks list, asks Claude to research each one (with web search), and writes structured markdown notes into an Obsidian vault folder.

## Architecture

```
veille_tech/
├── app.py          # Textual TUI — master/detail layout + async worker
├── config.py       # loads ~/.config/veille-tech/config.toml
├── google_tasks.py # Google Tasks API wrapper (OAuth2)
├── research.py     # Claude call with web_search_20260209 tool
└── vault.py        # idempotent note writer
```

## Running

```bash
uv run veille-tech
```

The `.env` file in the project root is loaded automatically (`ANTHROPIC_API_KEY`).

## Venv / dependency management

Use `uv` — it manages the virtualenv automatically.

```bash
# install deps (first time or after pyproject.toml changes)
uv sync

# add a new dependency
uv add <package>

# run a one-off command inside the venv
uv run python -c "..."
```

## Key design points

- **Model**: `claude-opus-4-7` with `web_search_20260209` server tool (org-level toggle required in Anthropic Console)
- **Idempotency**: vault.py scans the first 1 KB of each `.md` for `google_task_id:` before writing
- **Async**: all blocking calls (Google API, Anthropic API, file I/O) go through `asyncio.to_thread` so the TUI stays responsive
- **Config**: `~/.config/veille-tech/config.toml` — see `.env.example` and README for the full template

## External config (not in repo)

Single file: `~/.config/veille-tech/config.yaml` — loaded and validated by Pydantic (`Config` model in `config.py`).

Contains: vault path, task list name, model settings, Google OAuth2 client credentials, and the cached OAuth token (`google_token` block). The token is written back automatically by `Config.save_token()` after each auth or refresh — no separate token file.

## Generated note structure

Each note has YAML frontmatter followed by five fixed sections in English:

- `TL;DR`
- `Why it matters`
- `Key points`
- `Things to dig into`
- `Sources`

To change the structure or language, edit `SYSTEM_PROMPT` in `research.py`.
