"""CLI for veille-tech — list tasks and run research from the terminal."""
from __future__ import annotations

import datetime as dt
import logging
from typing import Literal

import typer
from rich.console import Console
from rich.logging import RichHandler
from rich.table import Table

from .config import Config
from .google_tasks import GTask, complete_task, ensure_authenticated, fetch_open_tasks, find_list_id
from .research import render_note, research_task
from .vault import INBOX, VALIDATED, VaultNote, list_notes, validate_note, write_note

console = Console()
err_console = Console(stderr=True)

app = typer.Typer(
    no_args_is_help=True,
    help="veille-tech CLI — list tasks and run tech-watch research.",
)

_verbose = typer.Option(False, "--verbose", "-v", help="Enable debug logging.")

_NOISY_LOGGERS = (
    "httpcore",
    "httpx",
    "anthropic._base_client",
    "urllib3",
    "googleapiclient.discovery",
    "googleapiclient.discovery_cache",
    "google.auth.transport.requests",
)


def _setup_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.WARNING
    logging.basicConfig(
        level=level,
        format="%(message)s",
        datefmt="[%X]",
        handlers=[RichHandler(console=err_console, show_path=verbose, markup=True)],
    )
    for name in _NOISY_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)


# ---------------------------------------------------------------------------
# Row type: (index, status, display_title, task_or_none, note_or_none)
# ---------------------------------------------------------------------------
Row = tuple[int, Literal["pending", "inbox", "validated"], str, GTask | None, VaultNote | None]


def _load_rows(config: Config, list_id: str, include_validated: bool = False) -> list[Row]:
    """Build the unified row list: pending tasks first, then inbox, then validated."""
    rows: list[Row] = []
    idx = 1

    try:
        tasks = fetch_open_tasks(config, list_id)
    except Exception as exc:  # noqa: BLE001
        err_console.print(f"[red]Failed to load tasks:[/] {exc}")
        raise typer.Exit(1)

    for task in tasks:
        rows.append((idx, "pending", task.title, task, None))
        idx += 1

    for note in list_notes(config.vault_folder, INBOX):
        rows.append((idx, "inbox", note.title, None, note))
        idx += 1

    if include_validated:
        for note in list_notes(config.vault_folder, VALIDATED):
            rows.append((idx, "validated", note.title, None, note))
            idx += 1

    return rows


def _bootstrap(include_validated: bool = False) -> tuple[Config, str, list[Row]]:
    """Load config, authenticate, return (config, list_id, rows)."""
    try:
        config = Config.load()
    except FileNotFoundError as exc:
        err_console.print(f"[red]Config error:[/] {exc}")
        raise typer.Exit(1)

    try:
        ensure_authenticated(config)
    except Exception as exc:  # noqa: BLE001
        err_console.print(f"[red]Auth failed:[/] {exc}")
        raise typer.Exit(1)

    try:
        list_id = find_list_id(config, config.task_list_name)
    except Exception as exc:  # noqa: BLE001
        err_console.print(f"[red]Failed to find task list:[/] {exc}")
        raise typer.Exit(1)

    rows = _load_rows(config, list_id, include_validated=include_validated)
    return config, list_id, rows


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

@app.command("list")
def list_tasks(
    all_notes: bool = typer.Option(False, "--all", "-a", help="Also show validated notes."),
    verbose: bool = _verbose,
) -> None:
    """List pending tasks and inbox notes (add --all to include validated)."""
    _setup_logging(verbose)
    config, _, rows = _bootstrap(include_validated=all_notes)

    table = Table(title=f"Veille tech — {config.task_list_name}", show_lines=False, expand=False)
    table.add_column("#", style="dim", width=4, justify="right")
    table.add_column("Status", width=12)
    table.add_column("Title")
    table.add_column("File", style="dim")

    n_pending = n_inbox = n_validated = 0
    for idx, status, title, _task, note in rows:
        if status == "pending":
            table.add_row(str(idx), "[yellow]pending[/]", title, "")
            n_pending += 1
        elif status == "inbox":
            table.add_row(str(idx), "[blue]inbox[/]", title, note.path.name if note else "")
            n_inbox += 1
        else:
            table.add_row(str(idx), "[green]validated[/]", title, note.path.name if note else "")
            n_validated += 1

    console.print(table)
    summary = (
        f"  [yellow]{n_pending} pending[/]  •  [blue]{n_inbox} inbox[/]"
    )
    if all_notes:
        summary += f"  •  [green]{n_validated} validated[/]"
    else:
        summary += "  [dim](use --all to show validated)[/]"
    console.print(summary)


@app.command("run")
def run_research(
    indices: list[int] = typer.Argument(
        ...,
        help="Task number(s) to research, as shown by `vtc list` (pending only).",
    ),
    force: bool = typer.Option(False, "--force", "-f", help="Re-research even if a note exists."),
    verbose: bool = _verbose,
) -> None:
    """Run Claude research on pending tasks and write notes to inbox/."""
    _setup_logging(verbose)
    log = logging.getLogger(__name__)
    config, _, rows = _bootstrap()

    idx_set = set(indices)
    pending = {i: t for i, status, _, t, _ in rows if status == "pending" and i in idx_set}
    unknown = idx_set - set(pending)
    if unknown:
        err_console.print(
            f"[yellow]Indices not found among pending tasks:[/] {sorted(unknown)}  "
            f"[dim](inbox/validated rows cannot be re-run)[/]"
        )

    if not pending:
        console.print("[yellow]No pending tasks to research.[/]")
        raise typer.Exit(0)

    console.print(f"\n[bold]Researching {len(pending)} task(s)…[/]\n")
    ok = 0
    for idx, task in sorted(pending.items()):
        console.rule(f"[bold][{idx}] {task.title}")
        started = dt.datetime.now()
        try:
            with console.status("[dim]Calling Claude (web search in progress)…[/]"):
                result = research_task(
                    task,
                    api_key=config.anthropic_api_key,
                    model=config.model,
                    max_web_searches=config.max_web_searches,
                )
            filename, body = render_note(task, result)
            path = write_note(config.vault_folder, filename, body)
            elapsed = (dt.datetime.now() - started).total_seconds()
            console.print(
                f"  [green]✓[/] [bold]{path.name}[/]  "
                f"[dim]{result.used_searches} search(es) · {elapsed:.0f}s[/]"
            )

            if config.complete_on_success:
                try:
                    complete_task(config, task)
                    console.print("  [dim]→ marked as completed in Google Tasks[/]")
                except Exception as exc:  # noqa: BLE001
                    console.print(f"  [yellow]⚠ could not mark completed:[/] {exc}")

            ok += 1
        except Exception as exc:  # noqa: BLE001
            elapsed = (dt.datetime.now() - started).total_seconds()
            console.print(f"  [red]✗ {exc}[/] [dim]({elapsed:.0f}s)[/]")
            log.exception("Research failed for task %s", task.id)

    console.print()
    if ok == len(pending):
        console.print(f"[bold green]All {ok} task(s) done.[/]")
    else:
        console.print(f"[bold]{ok}/{len(pending)} succeeded.[/]")
        raise typer.Exit(1)


@app.command("validate")
def validate_tasks(
    indices: list[int] = typer.Argument(
        ...,
        help="Task number(s) to validate, as shown by `vtc list` (inbox only).",
    ),
    verbose: bool = _verbose,
) -> None:
    """Move inbox notes to validated/."""
    _setup_logging(verbose)
    config, _, rows = _bootstrap()

    idx_set = set(indices)
    inbox = {i: note for i, status, _, _, note in rows if status == "inbox" and i in idx_set}
    unknown = idx_set - set(inbox)
    if unknown:
        err_console.print(
            f"[yellow]Indices not found among inbox notes:[/] {sorted(unknown)}  "
            f"[dim](only inbox rows can be validated)[/]"
        )

    if not inbox:
        console.print("[yellow]No inbox notes to validate.[/]")
        raise typer.Exit(0)

    ok = 0
    for idx, note in sorted(inbox.items()):
        try:
            new_path = validate_note(config.vault_folder, note.path)
            console.print(f"  [{idx}] [green]✓[/] validated/  [dim]{new_path.name}[/]")
            ok += 1
        except Exception as exc:  # noqa: BLE001
            console.print(f"  [{idx}] [red]✗ Could not move note:[/] {exc}")

    console.print()
    console.print(f"[bold green]{ok}/{len(inbox)} validated.[/]" if ok else "[bold red]Nothing validated.[/]")


def main() -> None:
    app()


if __name__ == "__main__":
    main()
