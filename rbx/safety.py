"""Safety helpers shared by every destructive command.

Goal: prevent the user from corrupting their rekordbox library accidentally.
The two protections are:
  1. Refuse to run while rekordbox is open (it holds the master.db lock).
  2. Require a recent backup, unless the user passes --no-backup-check.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from pathlib import Path

import psutil
import typer
from rich.console import Console

console = Console()

# Match rekordbox the app and its agent processes, but NOT process titles that
# happen to include "rekordbox-playlist-extractor" (Cursor and similar tools
# bake the workspace name into their helper process titles).
_REKORDBOX_PROC = re.compile(r"(?:^|/)rekordbox(?:agent\w*)?$", re.IGNORECASE)


def assert_rekordbox_closed() -> None:
    """Abort if rekordbox is currently running.

    pyrekordbox can read the encrypted DB while rekordbox is open, but writes
    will collide with rekordbox's own SQLite connection. Better to fail loudly.
    """
    for proc in psutil.process_iter(["name", "exe"]):
        if _is_rekordbox(proc.info.get("name"), proc.info.get("exe")):
            console.print(
                "[red]rekordbox is currently running.[/red] "
                "Quit it before running destructive rbx commands."
            )
            raise typer.Exit(code=2)


def _is_rekordbox(name: str | None, exe: str | None) -> bool:
    if exe and _REKORDBOX_PROC.search(exe):
        return True
    if name and _REKORDBOX_PROC.match(name):
        return True
    return False


def assert_recent_backup(backups_dir: Path, max_age: timedelta = timedelta(days=1)) -> None:
    """Abort if there is no master.db backup younger than `max_age`.

    Run `rbx backup` first (or pass --no-backup-check to override).
    """
    if not backups_dir.exists():
        _no_backup_error(backups_dir)
    candidates = sorted(backups_dir.glob("*/master.db"))
    if not candidates:
        _no_backup_error(backups_dir)
    newest = max(candidates, key=lambda p: p.stat().st_mtime)
    age = datetime.now() - datetime.fromtimestamp(newest.stat().st_mtime)
    if age > max_age:
        console.print(
            f"[red]Newest backup is {age} old (limit: {max_age}).[/red] "
            "Run `rbx backup` first or pass --no-backup-check."
        )
        raise typer.Exit(code=2)


def _no_backup_error(backups_dir: Path) -> None:
    console.print(
        f"[red]No master.db backup found under {backups_dir}.[/red] "
        "Run `rbx backup` first or pass --no-backup-check."
    )
    raise typer.Exit(code=2)
