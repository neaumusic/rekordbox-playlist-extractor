"""rbx strip-hot-cues - delete hot-cue rows from master.db.

The track Colour (DjmdContent.ColorID) is independent of cue rows, so removing
the cue itself preserves the baked-in track color.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console

from rbx.db import open_db
from rbx.safety import assert_recent_backup, assert_rekordbox_closed

console = Console()


def run(
    keep_memory_cues: Annotated[
        bool,
        typer.Option(
            "--keep-memory-cues/--include-memory-cues",
            help="When true (default) only hot cues are deleted; memory cues are preserved.",
        ),
    ] = True,
    comment_contains: Annotated[
        str | None,
        typer.Option(
            "--comment",
            help="Only delete cues whose Comment contains this substring (case-insensitive).",
        ),
    ] = None,
    color: Annotated[
        int | None,
        typer.Option("--color", help="Only delete cues with this exact Color value."),
    ] = None,
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", help="Show what would be deleted without committing."),
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
    """Delete cue rows from master.db (hot cues by default)."""
    if not dry_run:
        assert_rekordbox_closed()
        if not no_backup_check:
            assert_recent_backup(backups_dir)

    db = open_db()
    cues = list(db.get_cue())

    matches = [c for c in cues if _matches(c, keep_memory_cues, comment_contains, color)]
    console.print(
        f"Scanning {len(cues)} cues; "
        f"[bold]{len(matches)}[/bold] match the filter."
    )

    if not matches:
        return

    sample = matches[: min(5, len(matches))]
    for c in sample:
        console.print(
            f"  - cue {c.ID} on track {c.ContentID} "
            f"(Kind={c.Kind}, Color={c.Color}, Comment={c.Comment!r})"
        )
    if len(matches) > len(sample):
        console.print(f"  ... and {len(matches) - len(sample)} more")

    if dry_run:
        console.print("[yellow]--dry-run: nothing committed[/yellow]")
        return

    for c in matches:
        db.delete(c)
    db.commit()
    console.print(f"[green]Deleted {len(matches)} cues; committed.[/green]")


def _matches(cue, keep_memory_cues: bool, comment_contains: str | None, color: int | None) -> bool:
    if keep_memory_cues and not cue.is_hot_cue:
        return False
    if comment_contains is not None and (
        not cue.Comment or comment_contains.lower() not in cue.Comment.lower()
    ):
        return False
    return color is None or cue.Color == color
