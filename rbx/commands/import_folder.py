"""rbx import-folder - add or re-stamp a folder's audio files in StockDate order."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console

from rbx.dates import Resolution
from rbx.db import open_db
from rbx.safety import assert_recent_backup, assert_rekordbox_closed

console = Console()

DEFAULT_EXTENSIONS = {".aiff", ".aif", ".mp3", ".wav", ".flac", ".m4a", ".aac", ".ogg"}
DATETIME_FMT = "%Y-%m-%d %H:%M:%S"


class SortBy(StrEnum):
    NAME = "name"
    MTIME = "mtime"
    CTIME = "ctime"


def run(
    folder: Annotated[Path, typer.Argument(help="Folder of audio files to import.")],
    sort: Annotated[
        SortBy,
        typer.Option("--sort", help="Sort key for assigning sequential StockDates."),
    ] = SortBy.NAME,
    reverse: Annotated[
        bool,
        typer.Option("--reverse", help="Reverse the sort order."),
    ] = False,
    resolution: Annotated[
        Resolution,
        typer.Option("--resolution", help="Spacing between consecutive StockDates."),
    ] = Resolution.MINUTE,
    recursive: Annotated[
        bool,
        typer.Option("--recursive/--no-recursive", help="Recurse into subdirectories."),
    ] = False,
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
    """Import or re-stamp a folder so each track gets sequential StockDates from now().

    Existing tracks (matched by absolute path against DjmdContent.OrgFolderPath) get
    their StockDate updated. New files are added via db.add_content() with the same
    StockDate. After import, in rekordbox right-click > Reload Tag to populate
    metadata for any newly added tracks.
    """
    if not folder.is_dir():
        console.print(f"[red]{folder} is not a directory.[/red]")
        raise typer.Exit(code=1)

    if not dry_run:
        assert_rekordbox_closed()
        if not no_backup_check:
            assert_recent_backup(backups_dir)

    files = _list_audio_files(folder, recursive=recursive)
    if not files:
        console.print(f"[yellow]No audio files found in {folder}.[/yellow]")
        return
    files.sort(key=_sort_key(sort), reverse=reverse)

    console.print(f"Found {len(files)} file(s); first 3:")
    for f in files[:3]:
        console.print(f"  - {f.name}")

    db = open_db()
    by_path = {c.OrgFolderPath: c for c in db.get_content() if c.OrgFolderPath}

    base = datetime.now().replace(microsecond=0)
    if resolution is Resolution.MINUTE:
        base = base.replace(second=0)
    step = resolution.delta

    plan: list[tuple[Path, datetime, str]] = []
    for i, f in enumerate(files):
        ts = base + step * i
        ts_str = ts.strftime(DATETIME_FMT)
        existing = by_path.get(str(f.resolve()))
        action = "update" if existing else "add"
        plan.append((f, ts, ts_str))
        if i < 5:
            console.print(f"  {action}: {f.name} -> {ts_str}")
    if len(plan) > 5:
        console.print(f"  ... and {len(plan) - 5} more")

    if dry_run:
        console.print("[yellow]--dry-run: nothing committed[/yellow]")
        return

    added = updated = 0
    for f, _, ts_str in plan:
        abs_path = str(f.resolve())
        existing = by_path.get(abs_path)
        if existing is not None:
            existing.StockDate = ts_str
            updated += 1
        else:
            try:
                content = db.add_content(abs_path, StockDate=ts_str)
                by_path[content.OrgFolderPath] = content
                added += 1
            except Exception as exc:
                console.print(f"[yellow]skip {f.name}: {exc}[/yellow]")

    db.commit()
    console.print(
        f"[green]Imported: added {added}, updated {updated}; committed.[/green] "
        "[dim]Reload Tag in rekordbox to populate metadata for added tracks.[/dim]"
    )


def _list_audio_files(folder: Path, recursive: bool) -> list[Path]:
    pattern = "**/*" if recursive else "*"
    return [
        p
        for p in folder.glob(pattern)
        if p.is_file() and p.suffix.lower() in DEFAULT_EXTENSIONS
    ]


def _sort_key(sort: SortBy):
    if sort is SortBy.NAME:
        return lambda p: p.name.lower()
    if sort is SortBy.MTIME:
        return lambda p: p.stat().st_mtime
    return lambda p: getattr(p.stat(), "st_birthtime", p.stat().st_ctime)
