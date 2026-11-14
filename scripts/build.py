"""Build a single, self-contained executable of techno-watch-bot with PyInstaller.

    uv run --group build python scripts/build.py              # -> dist/techno-watch-bot-<os>-<arch>
    uv run --group build python scripts/build.py --with-claude-cli

The executable contains Python and every dependency, so the person you share it
with does not need Python or uv. They still need Claude Code installed and logged
in (`claude` on their PATH), unless you build with --with-claude-cli, which embeds
the CLI shipped with claude-agent-sdk (+~340 MB).

PyInstaller does not cross-compile: build on each OS you want to share for
(Linux, macOS, Windows).
"""
from __future__ import annotations

import argparse
import os
import platform
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def artifact_name() -> str:
    system = {"darwin": "macos", "win32": "windows"}.get(sys.platform, sys.platform)
    machine = platform.machine().lower().replace("amd64", "x86_64").replace("aarch64", "arm64")
    return f"techno-watch-bot-{system}-{machine}"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--with-claude-cli", action="store_true",
                        help="Embed the Claude Code CLI bundled with claude-agent-sdk.")
    args = parser.parse_args()

    try:
        import PyInstaller.__main__ as pyinstaller
    except ImportError:
        print("PyInstaller is missing: run with `uv run --group build python scripts/build.py`.")
        return 1

    name = artifact_name()
    work = ROOT / "build"
    opts = [
        str(ROOT / "techno_watch_bot" / "__main__.py"),
        "--onefile",
        "--name", name,
        "--distpath", str(ROOT / "dist"),
        "--workpath", str(work),
        "--specpath", str(work),
        "--noconfirm",
        "--clean",
        # Textual loads widgets lazily and ships CSS / data files.
        "--collect-all", "textual",
        # Rich / Textual markdown highlighting resolves Pygments lexers dynamically.
        "--collect-submodules", "pygments",
        "--collect-submodules", "claude_agent_sdk",
        # python-slugify imports text_unidecode (and its data file) lazily.
        "--collect-all", "text_unidecode",
        "--collect-submodules", "techno_watch_bot",
    ]

    if args.with_claude_cli:
        import claude_agent_sdk

        bundled = Path(claude_agent_sdk.__file__).parent / "_bundled"
        cli = next((p for p in bundled.glob("claude*") if p.is_file()), None)
        if cli is None:
            print(f"No bundled Claude CLI found in {bundled}.")
            return 1
        opts += ["--add-binary", f"{cli}{os.pathsep}claude_agent_sdk/_bundled"]

    pyinstaller.run(opts)
    out = ROOT / "dist" / (name + (".exe" if sys.platform == "win32" else ""))
    shutil.rmtree(work, ignore_errors=True)
    size = out.stat().st_size / 1e6
    print(f"\nBuilt {out} ({size:.0f} MB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
