"""rbx expand-dates - give every track a unique DateAdded timestamp at minute
(or second) precision, preserving the natural (StockDate, created_at) order.

Algorithm: stable-sort all tracks ascending by
(snapped StockDate, created_at, ContentID), then sweep backward from the
latest item. Each track keeps its snapped timestamp if it's strictly less
than the previously-assigned one; otherwise it gets pushed back by one
resolution step. Cascades may cross day boundaries — that's intentional,
and keeps the most recent timestamps anchored exactly where rekordbox
already had them.

Why `created_at` as the within-day tiebreaker: rekordbox stores `StockDate`
(the user-visible "Date Added") at date-only precision, so same-day cohorts
need a tiebreaker. `ContentID` is essentially random — it doesn't follow
import order. The SQLite row's `created_at` column has millisecond precision
and matches the order rekordbox's UI actually displays. ContentID stays as a
last-resort tiebreaker for the rare case of identical `created_at` values.

Idempotent: tracks that already have unique timestamps at the chosen
resolution are left untouched. Safe to re-run.

NOTE on storage: rekordbox stores StockDate as a VARCHAR. We write
'YYYY-MM-DD HH:MM:SS' which sorts lexicographically the way you want.
Verify visually in rekordbox after running on a backup.
"""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console

from rbx.dates import ExpandItem, Resolution, expand_unique_backward
from rbx.db import open_db
from rbx.safety import assert_recent_backup

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
    """Expand DateAdded so every track has a unique timestamp."""
    if not dry_run and not no_backup_check:
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
        items.append(
            ExpandItem(
                id=str(c.ID),
                current=parsed,
                sort_key=int(c.ID),
                tiebreaker_dt=c.created_at,
            )
        )
        by_id[str(c.ID)] = c

    new_dates = expand_unique_backward(items, resolution=resolution)

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
    spillover = 0
    for item_id, new_dt in new_dates.items():
        original = _parse_stockdate(by_id[item_id].StockDate)
        if original is None:
            continue
        original_day = original.date() if isinstance(original, datetime) else original
        if new_dt.date() != original_day:
            spillover += 1
    if spillover:
        console.print(
            f"[yellow]{spillover}[/yellow] track(s) will be pushed into a "
            f"different day to resolve cascading collisions."
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
