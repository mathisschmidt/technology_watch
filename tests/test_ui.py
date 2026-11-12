"""Textual pilot tests driving the app with the fake backend."""
from __future__ import annotations

from pathlib import Path

import pytest
from textual.widgets import DataTable, Input

from techno_watch_bot import config as config_mod
from techno_watch_bot.app import TechnoWatchApp
from techno_watch_bot.claude import FakeBackend
from techno_watch_bot.config import Settings
from techno_watch_bot.events import Phase
from techno_watch_bot.screens.history import HistoryScreen, ReportScreen
from techno_watch_bot.screens.home import HomeScreen
from techno_watch_bot.screens.monitor import MonitorScreen
from techno_watch_bot.screens.picker import PickerScreen
from techno_watch_bot.screens.settings import SettingsScreen


@pytest.fixture
def app(tmp_path: Path, monkeypatch) -> TechnoWatchApp:
    cfg = tmp_path / "config.toml"
    monkeypatch.setattr(config_mod, "CONFIG_FILE", cfg)
    monkeypatch.setattr("techno_watch_bot.screens.settings.CONFIG_FILE", cfg)
    s = Settings(notes_dir=tmp_path / "notes", reports_dir=tmp_path / "reports")
    s.save(cfg)
    return TechnoWatchApp(backend=FakeBackend(delay=0), settings=s)


async def _wait_for(pilot, cond, tries: int = 200) -> None:
    for _ in range(tries):
        if cond():
            return
        await pilot.pause(0.02)
    raise AssertionError("condition not met")


async def test_run_all_then_history(app: TechnoWatchApp) -> None:
    async with app.run_test(size=(140, 45)) as pilot:
        assert isinstance(app.screen, HomeScreen)
        await pilot.press("r")
        assert isinstance(app.screen, PickerScreen)
        await _wait_for(pilot, lambda: app.screen.query_one(DataTable).row_count == 4)
        await pilot.press("enter")  # first row = All tasks
        assert isinstance(app.screen, MonitorScreen)
        monitor = app.screen
        await _wait_for(pilot, lambda: monitor.result is not None)
        assert [r.status for r in monitor.result.runs] == [Phase.DONE, Phase.DONE, Phase.SKIPPED]
        await pilot.press("o")
        assert isinstance(app.screen, ReportScreen)
        await pilot.press("escape", "escape")
        assert isinstance(app.screen, HomeScreen)
        await pilot.press("h")
        assert isinstance(app.screen, HistoryScreen)
        assert app.screen.query_one(DataTable).row_count == 1


async def test_single_task(app: TechnoWatchApp) -> None:
    async with app.run_test(size=(140, 45)) as pilot:
        await pilot.press("r")
        await _wait_for(pilot, lambda: app.screen.query_one(DataTable).row_count == 4)
        await pilot.press("down", "down", "enter")  # second real task
        monitor = app.screen
        await _wait_for(pilot, lambda: monitor.result is not None)
        assert [r.task.id for r in monitor.result.runs] == ["t2"]


async def test_settings_save_and_validation(app: TechnoWatchApp, tmp_path: Path) -> None:
    async with app.run_test(size=(140, 60)) as pilot:
        await pilot.press("s")
        assert isinstance(app.screen, SettingsScreen)
        screen = app.screen
        screen.query_one("#f_max_reports", Input).value = "0"
        screen.action_save()
        await pilot.pause()
        assert isinstance(app.screen, SettingsScreen)  # invalid → stays
        screen.query_one("#f_max_reports", Input).value = "3"
        screen.query_one("#f_task_list_name", Input).value = "Other list"
        screen.action_save()
        await pilot.pause()
        assert isinstance(app.screen, HomeScreen)
        assert app.settings.max_reports == 3
        assert Settings.load(tmp_path / "config.toml").task_list_name == "Other list"
