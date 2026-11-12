"""Entry point: `techno-watch-bot` opens the TUI."""
from __future__ import annotations

import os

import typer

cli = typer.Typer(add_completion=False, help="Claude Code–driven tech-watch TUI.")


@cli.command()
def run(
    fake: bool = typer.Option(
        False, "--fake", help="Use a simulated backend (no Claude calls) — for demos/tests."
    ),
) -> None:
    """Open the techno-watch-bot TUI."""
    if fake:
        os.environ["TECHNO_WATCH_FAKE"] = "1"
    from .app import TechnoWatchApp

    TechnoWatchApp().run()


def main() -> None:
    cli()


if __name__ == "__main__":
    main()
