"""Regenerate the README screenshots and demo GIF from the simulated backend.

    uv run --with playwright --with pillow python scripts/screenshots.py

Needs a Chromium for Playwright (`playwright install chromium`, or set
CHROMIUM_PATH to an existing binary). Writes docs/screenshots/*.png and
docs/demo.gif. No Claude or Google call is made.
"""
from __future__ import annotations

import asyncio
import math
import os
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs"
SIZE = (118, 34)  # terminal columns x rows

# A throwaway home folder, so paths display as ~/… and nothing real is touched.
_HOME = Path(tempfile.mkdtemp(prefix="twb-shots-"))
os.environ["HOME"] = str(_HOME)
os.environ["TECHNO_WATCH_CONFIG_DIR"] = str(_HOME / ".config" / "techno-watch-bot")
sys.path.insert(0, str(ROOT))

from techno_watch_bot.app import TechnoWatchApp  # noqa: E402
from techno_watch_bot.claude import FakeBackend  # noqa: E402
from techno_watch_bot.config import Settings  # noqa: E402


class DemoBackend(FakeBackend):
    def status(self, settings: Settings) -> str:
        return "connected, 29 days left"


def demo_settings() -> Settings:
    s = Settings(
        task_list_name="Veille tech",
        notes_dir=_HOME / "Obsidian" / "Tech watch",
        reports_dir=_HOME / "Obsidian" / "Tech watch" / "_reports",
        tasks_mcp_url="https://tasks.example.com",
    )
    s.save()
    s.ensure_dirs()
    return s


async def capture(svg_dir: Path) -> list[Path]:
    frames: list[Path] = []
    backend = DemoBackend(delay=0.35)
    app = TechnoWatchApp(backend=backend, settings=demo_settings(), task_source=backend)

    async with app.run_test(size=SIZE) as pilot:
        def shot(name: str) -> None:
            app.save_screenshot(filename=f"{name}.svg", path=str(svg_dir))

        def frame(repeat: int = 1) -> None:
            for _ in range(repeat):
                name = f"frame_{len(frames):03d}"
                app.save_screenshot(filename=f"{name}.svg", path=str(svg_dir))
                frames.append(svg_dir / f"{name}.svg")

        await pilot.pause(0.3)
        shot("home")
        frame(4)

        await pilot.press("r")
        frame(1)
        await pilot.pause(0.6)
        shot("picker")
        frame(4)

        await pilot.press("enter")  # "★ All tasks"
        monitor = app.screen
        taken_mid = False
        while getattr(monitor, "result", None) is None:
            await pilot.pause(0.45)
            frame()
            current = getattr(monitor, "current", None)
            if not taken_mid and current == 1 and monitor.last_tool.startswith("WebFetch"):
                shot("monitor")
                taken_mid = True
        await pilot.pause(0.2)
        shot("monitor_done")
        frame(6)

        await pilot.press("o")
        await pilot.pause(0.5)
        shot("report")
        frame(6)

        await pilot.press("escape", "escape")
        await pilot.press("h")
        await pilot.pause(0.3)
        shot("history")

        await pilot.press("escape", "s")
        await pilot.pause(0.3)
        shot("settings")
    return frames


def render(svg_dir: Path, frames: list[Path]) -> None:
    from PIL import Image
    from playwright.sync_api import sync_playwright

    shots = OUT / "screenshots"
    shots.mkdir(parents=True, exist_ok=True)
    launch = {"executable_path": os.environ["CHROMIUM_PATH"]} if os.environ.get("CHROMIUM_PATH") else {}

    with sync_playwright() as p:
        browser = p.chromium.launch(**launch)

        def to_png(svg: Path, png: Path, scale: int) -> None:
            # Textual's SVG scales to its container: size the page to its viewBox.
            m = re.search(r'viewBox="0 0 ([\d.]+) ([\d.]+)"', svg.read_text(encoding="utf-8"))
            width, height = (math.ceil(float(v)) for v in m.groups())
            page = browser.new_page(device_scale_factor=scale,
                                    viewport={"width": width, "height": height})
            page.goto(svg.as_uri())
            page.screenshot(path=str(png), omit_background=True,
                            clip={"x": 0, "y": 0, "width": width, "height": height})
            page.close()

        for name in ("home", "picker", "monitor", "monitor_done", "report", "history", "settings"):
            to_png(svg_dir / f"{name}.svg", shots / f"{name}.png", scale=2)
            print("wrote", shots / f"{name}.png")

        pngs = []
        for svg in frames:
            png = svg.with_suffix(".png")
            to_png(svg, png, scale=1)
            pngs.append(png)
        browser.close()

    images = [Image.open(p).convert("RGB") for p in pngs]
    palette = images[len(images) // 2].quantize(colors=128, method=Image.Quantize.MEDIANCUT)
    gif_frames = [im.quantize(palette=palette, dither=Image.Dither.NONE) for im in images]
    durations = [450] * len(gif_frames)
    durations[-1] = 2500
    gif = OUT / "demo.gif"
    gif_frames[0].save(gif, save_all=True, append_images=gif_frames[1:], duration=durations,
                       loop=0, optimize=True, disposal=1)
    print("wrote", gif, f"({gif.stat().st_size / 1e6:.1f} MB, {len(gif_frames)} frames)")


def main() -> None:
    svg_dir = _HOME / "svg"
    svg_dir.mkdir()
    frames = asyncio.run(capture(svg_dir))
    render(svg_dir, frames)


if __name__ == "__main__":
    main()
