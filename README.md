# veille-tech

> Turn your Google Tasks list into researched Obsidian notes — automatically.

**veille-tech** is a terminal app that picks up open tasks from a Google Tasks list, asks Claude to research each topic live on the web, and writes a structured markdown note straight into your Obsidian vault. Your Seafile (or any sync client) picks up the new files on its own.

```
┌─ veille-tech ──────────────────────── 12:34:00 ──────────────────────────────┐
│ Tasks – Veille tech          │ Preview                                        │
│──────────────────────────────│────────────────────────────────────────────────│
│ · ●  Bun 1.2 release         │ # Rust-based JS runtimes                       │
│ ✓ ⟳  Rust-based JS runtimes  │                                                │
│ · –  Deno Deploy pricing     │ Comparing Bun, Deno and Node on cold-start…    │
│                              │                                                │
│                              │ ---                                            │
│                              │ Status: running                                │
│──────────────────────────────────────────────────────────────────────────────│
│ → Rust-based JS runtimes                                                      │
│   ✓ wrote 2026-05-12 - rust-based-js-runtimes.md (6 searches, 38s)           │
│   marked task as completed                                                    │
│──────────────────────────────────────────────────────────────────────────────│
│  selected: 1/3   complete-on-success: ON   vault: ~/Seafile/Vault/Veille Tech │
│ space Toggle  a All  n None  r Research  c Toggle COS  R Reload  q Quit      │
└──────────────────────────────────────────────────────────────────────────────┘
```

---

## Features

- **Live web research** — Claude searches the web (up to N queries per task, configurable) and synthesises findings into a clean note
- **Structured output** — every note follows a fixed five-section template so your vault stays consistent
- **Idempotent** — tasks that already have a note are marked *skipped*; re-running is always safe
- **Completes tasks automatically** — optionally marks the Google Task as done after writing the note (toggle with `c` at runtime)
- **Async TUI** — the interface stays responsive while research runs in the background

---

## How it works

```
Google Tasks  →  veille-tech  →  Claude (web search)  →  Obsidian vault
     ↑                                                         ↓
     └──────────────── mark task completed ───────────────────┘
```

1. On launch, the app fetches all open tasks from your configured list
2. You select which ones to research (`space` to toggle, `a` for all)
3. Press `r` — for each selected task, Claude does live web searches and writes a structured note
4. The note lands in your vault folder with YAML frontmatter; your sync client handles the rest

---

## Prerequisites

- Python 3.10+
- [uv](https://docs.astral.sh/uv/) for dependency management
- An [Anthropic API key](https://console.anthropic.com/) with **web search enabled** (org-level toggle in Console settings)
- A Google Cloud project with the **Tasks API** enabled and OAuth 2.0 Desktop credentials

---

## Installation

```bash
git clone https://github.com/your-username/veille-tech
cd veille-tech
uv sync
```

---

## Configuration

Everything lives in a **single YAML file** at `~/.config/veille-tech/config.yaml`.

### 1. Get your Google OAuth credentials

1. Go to [Google Cloud Console](https://console.cloud.google.com/), create a project
2. Enable the **Google Tasks API**
3. Create an OAuth 2.0 credential of type **Desktop app**
4. Note the **Client ID** and **Client Secret** from the credential detail page

### 2. Create `~/.config/veille-tech/config.yaml`

> **Custom config location** — set `VEILLE_TECH_CONFIG_DIR` to use a different directory:
> ```bash
> export VEILLE_TECH_CONFIG_DIR=~/dotfiles/veille-tech
> ```
> The app will look for `config.yaml` inside that directory instead.

```yaml
# Folder inside your Obsidian vault where notes are written
vault_folder: ~/Seafile/MyVault/Veille Tech

# Google Tasks list name (case-insensitive)
task_list_name: Veille tech

# Mark the task as completed after writing its note
complete_on_success: true

# Anthropic API key
anthropic_api_key: "sk-ant-..."

# Claude model
model: claude-opus-4-7

# Max web searches per task
max_web_searches: 8

# Google OAuth2 credentials (from Cloud Console)
google_client_id: "xxxx.apps.googleusercontent.com"
google_client_secret: "GOCSPX-..."

# OAuth token — auto-managed after first login, do not edit
google_token: {}
```

The first launch opens a browser for Google authorization. The token is written back into `google_token` automatically and persisted in this same file.

---

## Usage

```bash
uv run veille-tech
```

### Keybindings

| Key     | Action                              |
|---------|-------------------------------------|
| `↑ / ↓` | Navigate the task list             |
| `space` | Toggle selection on current task    |
| `a`     | Select all tasks                    |
| `n`     | Deselect all tasks                  |
| `r`     | Research selected tasks             |
| `c`     | Toggle complete-on-success          |
| `R`     | Reload tasks from Google            |
| `q`     | Quit                                |

---

## Generated note format

Each note is written as a markdown file with YAML frontmatter:

```markdown
---
title: "Bun 1.2 release"
date: 2026-05-12
google_task_id: abc123xyz
web_searches_used: 6
tags:
  - veille-tech
---

## TL;DR
...

## Why it matters
...

## Key points
...

## Things to dig into
...

## Sources
- [Title](URL) — one-sentence note on what's in it.
```

Files are named `YYYY-MM-DD - slugified-title.md` and land directly in the configured vault folder.

---

## Project structure

```
veille_tech/
├── app.py           # Textual TUI — layout, keybindings, async worker
├── config.py        # Config loader (config.toml)
├── google_tasks.py  # Google Tasks API wrapper (OAuth2)
├── research.py      # Claude call with web_search tool + note renderer
└── vault.py         # Idempotent note writer
```

---

## License

MIT
