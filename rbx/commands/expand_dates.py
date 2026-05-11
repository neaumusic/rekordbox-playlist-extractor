"""rbx expand-dates - one-time migration that gives every track a unique
DateAdded timestamp at minute (or second) precision within its existing day.

Required as a foundation for `rbx reorder`. Idempotent: tracks that already have
unique within-day timestamps at the chosen resolution are left untouched.

NOTE on storage: rekordbox stores StockDate as a VARCHAR. We write
'YYYY-MM-DD HH:MM:SS' which sorts lexicographically the way you want, and
old plain-date rows naturally sort before any new HH:MM:SS rows on the same day.
Verify visually in rekordbox after running on a backup.
"""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console

from rbx.dates import ExpandItem, Resolution, expand_within_day
from rbx.db import open_db
from rbx.safety import assert_recent_backup, assert_rekordbox_closed

console = Console()

DATE_FMT = "%Y-%m-%d"
DATETIME_FMT = "%Y-%m-%d %H:%M:%S"


def run(
    resolution: Annotated[
        Resolution,
        typer.Option("--resolution", help="Target precision for unique timestamps."),
    ] = Resolution.MINUTE,
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", help="Show what would change without committing."),
    ] = False,
    no_backup_check: Annotated[
        bool,
        typer.Option("--no-backup-check", help="Skip the recent-backup safety check."),
    ] = False,
    backups_dir: Annotated[
        Path,
        typer.Option("--backups-dir", help="Where to look for recent backups."),
    ] = Path("backups"),
) -> None:
    """Expand DateAdded so every track has a unique within-day timestamp."""
    if not dry_run:
        assert_rekordbox_closed()
        if not no_backup_check:
            assert_recent_backup(backups_dir)

    db = open_db()
    contents = list(db.get_content())
    console.print(f"Loaded {len(contents)} tracks from master.db")

    items: list[ExpandItem] = []
    by_id = {}
    for c in contents:
        parsed = _parse_stockdate(c.StockDate)
        if parsed is None:
            continue
        items.append(ExpandItem(id=str(c.ID), current=parsed, sort_key=int(c.ID)))
        by_id[str(c.ID)] = c

    new_dates = expand_within_day(items, resolution=resolution)

    changes = []
    for item_id, new_dt in new_dates.items():
        c = by_id[item_id]
        new_str = new_dt.strftime(DATETIME_FMT)
        if c.StockDate != new_str:
            changes.append((c, new_str))

    console.print(
        f"[bold]{len(changes)}[/bold] tracks need updates "
        f"({len(new_dates) - len(changes)} already unique at {resolution.value} resolution)"
    )

    for c, new_str in changes[:5]:
        console.print(f"  - track {c.ID} ({c.Title!r}): {c.StockDate!r} -> {new_str!r}")
    if len(changes) > 5:
        console.print(f"  ... and {len(changes) - 5} more")

    if dry_run:
        console.print("[yellow]--dry-run: nothing committed[/yellow]")
        return

    for c, new_str in changes:
        c.StockDate = new_str
    db.commit()
    console.print(f"[green]Updated {len(changes)} tracks; committed.[/green]")


def _parse_stockdate(raw: str | None) -> date | datetime | None:
    """Parse a StockDate string into date or datetime; return None if blank."""
    if not raw:
        return None
    raw = raw.strip()
    for fmt in (DATETIME_FMT, "%Y-%m-%dT%H:%M:%S", DATE_FMT):
        try:
            parsed = datetime.strptime(raw, fmt)
            return parsed if fmt != DATE_FMT else parsed.date()
        except ValueError:
            continue
    return None
