"""rbx reorder - rewrite StockDate so tracks sort in the order given by an m3u8."""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from typing import Annotated
from urllib.parse import unquote, urlparse

import typer
from rich.console import Console

from rbx.dates import ReorderItem, Resolution, redistribute_range
from rbx.db import open_db
from rbx.m3u import read_m3u
from rbx.safety import assert_recent_backup, assert_rekordbox_closed

console = Console()

DATE_FMT = "%Y-%m-%d"
DATETIME_FMT = "%Y-%m-%d %H:%M:%S"


def run(
    from_path: Annotated[
        Path,
        typer.Option("--from", help="Path to the m3u/m3u8 listing tracks in desired order."),
    ],
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
    """Rewrite StockDate on the listed tracks so they sort in m3u order."""
    if not from_path.exists():
        console.print(f"[red]{from_path} not found.[/red]")
        raise typer.Exit(code=1)

    if not dry_run:
        assert_rekordbox_closed()
        if not no_backup_check:
            assert_recent_backup(backups_dir)

    locations = read_m3u(from_path)
    if not locations:
        console.print("[yellow]m3u contained no track lines; nothing to do.[/yellow]")
        return

    db = open_db()
    by_path = {c.OrgFolderPath: c for c in db.get_content() if c.OrgFolderPath}

    resolved: list[tuple[str, object]] = []
    misses: list[str] = []
    for raw in locations:
        norm = _normalize_to_orgfolder(raw)
        match = by_path.get(norm)
        if match is None:
            misses.append(raw)
            continue
        resolved.append((raw, match))

    if misses:
        console.print(f"[yellow]{len(misses)} m3u line(s) did not match a track:[/yellow]")
        for m in misses[:5]:
            console.print(f"  - {m}")
        if len(misses) > 5:
            console.print(f"  ... and {len(misses) - 5} more")

    items: list[ReorderItem] = []
    for _, content in resolved:
        current = _parse_stockdate(content.StockDate) or datetime.now()
        items.append(ReorderItem(id=str(content.ID), current=current))

    if not items:
        console.print("[red]No m3u tracks could be resolved against master.db. Aborting.[/red]")
        raise typer.Exit(code=1)

    new_dates = redistribute_range(items, resolution=resolution)

    by_id = {str(c.ID): c for _, c in resolved}
    changes = []
    for item_id, new_dt in new_dates.items():
        c = by_id[item_id]
        new_str = new_dt.strftime(DATETIME_FMT)
        if c.StockDate != new_str:
            changes.append((c, new_str))

    console.print(
        f"[bold]{len(changes)}[/bold] tracks need updates "
        f"({len(items) - len(changes)} already at target)"
    )

    for c, new_str in changes[:5]:
        console.print(f"  - {c.ID} {c.Title!r}: {c.StockDate!r} -> {new_str!r}")
    if len(changes) > 5:
        console.print(f"  ... and {len(changes) - 5} more")

    if dry_run:
        console.print("[yellow]--dry-run: nothing committed[/yellow]")
        return

    for c, new_str in changes:
        c.StockDate = new_str
    db.commit()
    console.print(f"[green]Updated {len(changes)} tracks; committed.[/green]")


def _normalize_to_orgfolder(raw: str) -> str:
    """Convert an m3u line to the form stored in DjmdContent.OrgFolderPath.

    OrgFolderPath looks like '/Users/james/.../Foo.aiff' (absolute, decoded).
    Inputs may be either:
      - file://localhost/Users/.../Foo%20Bar.aiff (URL-encoded URI)
      - /Users/.../Foo Bar.aiff                   (raw absolute path)
    """
    if raw.startswith("file://"):
        parsed = urlparse(raw)
        return unquote(parsed.path)
    return unquote(raw) if "%" in raw else raw


def _parse_stockdate(raw: str | None) -> date | datetime | None:
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
