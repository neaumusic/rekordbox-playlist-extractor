"""rbx sort - rewrite StockDate so the m3u tracks sort in the given order,
anchoring on the m3u's last entry and cascading colliding library tracks earlier
to keep all dates globally unique.

Convention: m3u top = oldest, m3u bottom = newest (the anchor). Same convention
`rbx import` writes — so the natural workflow is `import` -> drag-drop into
rekordbox -> `sort` with no manual editing.

Recommended preliminary step: `rbx expand-dates`. Without it, any pre-existing
sub-resolution duplicates in the library will get rearranged here too.
"""

from __future__ import annotations

import os
import unicodedata
from datetime import date, datetime
from pathlib import Path
from typing import Annotated
from urllib.parse import unquote, urlparse

import typer
from rich.console import Console

from rbx.dates import ExpandItem, ReorderItem, Resolution, pack_for_sort
from rbx.db import open_db
from rbx.m3u import read_m3u
from rbx.safety import assert_recent_backup

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
    """Anchor on the m3u's last entry; pack the rest backward; cascade library colliders."""
    if not from_path.exists():
        console.print(f"[red]{from_path} not found.[/red]")
        raise typer.Exit(code=1)

    if not dry_run and not no_backup_check:
        assert_recent_backup(backups_dir)

    locations = read_m3u(from_path)
    if not locations:
        console.print("[yellow]m3u contained no track lines; nothing to do.[/yellow]")
        return

    db = open_db()
    contents = list(db.get_content())
    by_path: dict[str, object] = {}
    for c in contents:
        for p in (c.FolderPath, c.OrgFolderPath):
            if not p:
                continue
            for alias in _aliases(p):
                by_path.setdefault(alias, c)

    resolved: list[tuple[str, object]] = []
    misses: list[str] = []
    for raw in locations:
        norm = _normalize_to_orgfolder(raw)
        match = None
        for alias in _aliases(norm):
            match = by_path.get(alias)
            if match is not None:
                break
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

    if not resolved:
        console.print("[red]No m3u tracks could be resolved against master.db. Aborting.[/red]")
        raise typer.Exit(code=1)

    m3u_items: list[ReorderItem] = []
    for _, content in resolved:
        current = _parse_stockdate(content.StockDate) or datetime.now()
        m3u_items.append(ReorderItem(id=str(content.ID), current=current))

    m3u_id_set = {it.id for it in m3u_items}
    other_items: list[ExpandItem] = []
    for c in contents:
        cid = str(c.ID)
        if cid in m3u_id_set:
            continue
        parsed = _parse_stockdate(c.StockDate)
        if parsed is None:
            continue
        other_items.append(
            ExpandItem(
                id=cid,
                current=parsed,
                sort_key=int(c.ID),
                tiebreaker_dt=c.created_at,
            )
        )

    new_dates = pack_for_sort(m3u_items, other_items, resolution=resolution)

    by_id: dict[str, object] = {str(c.ID): c for c in contents}
    m3u_changes: list[tuple[object, str]] = []
    other_changes: list[tuple[object, str]] = []
    for item_id, new_dt in new_dates.items():
        c = by_id[item_id]
        new_str = new_dt.strftime(DATETIME_FMT)
        if c.StockDate == new_str:
            continue
        if item_id in m3u_id_set:
            m3u_changes.append((c, new_str))
        else:
            other_changes.append((c, new_str))

    console.print(
        f"[bold]m3u:[/bold] {len(m3u_changes)} of {len(m3u_items)} tracks need updates "
        f"({len(m3u_items) - len(m3u_changes)} already at target)"
    )
    if other_changes:
        console.print(
            f"[bold]library cascade:[/bold] {len(other_changes)} non-m3u track(s) "
            f"will be shifted earlier to avoid collisions"
        )
    else:
        console.print("[bold]library cascade:[/bold] no non-m3u tracks affected")

    _print_order_preview(resolved, new_dates)

    for c, new_str in m3u_changes[:5]:
        console.print(f"  - {c.ID} {c.Title!r}: {c.StockDate!r} -> {new_str!r}")
    if len(m3u_changes) > 5:
        console.print(f"  ... and {len(m3u_changes) - 5} more m3u changes")

    if other_changes:
        console.print("[dim]library cascade samples:[/dim]")
        for c, new_str in other_changes[:3]:
            console.print(f"  - {c.ID} {c.Title!r}: {c.StockDate!r} -> {new_str!r}")
        if len(other_changes) > 3:
            console.print(f"  ... and {len(other_changes) - 3} more cascade changes")

    if dry_run:
        console.print("[yellow]--dry-run: nothing committed[/yellow]")
        return

    for c, new_str in m3u_changes:
        c.StockDate = new_str
    for c, new_str in other_changes:
        c.StockDate = new_str
    db.commit()
    console.print(
        f"[green]Updated {len(m3u_changes) + len(other_changes)} tracks "
        f"({len(m3u_changes)} m3u + {len(other_changes)} cascade); committed.[/green]"
    )


def _print_order_preview(
    resolved: list[tuple[str, object]],
    new_dates: dict[str, datetime],
) -> None:
    """Show what the first/last m3u entries will become, oriented to the rekordbox UI.

    Catches a flipped m3u before it inverts the playlist. Showing this in
    "rekordbox UI order" (Date Added descending, newest on top) so the user
    can mentally line it up with what they'd see after committing.
    """
    first = resolved[0]
    last = resolved[-1]
    first_dt = new_dates[str(first[1].ID)]
    last_dt = new_dates[str(last[1].ID)]
    console.print(
        "[yellow]Sanity check[/yellow] (rekordbox sorted by Date Added desc, newest on top):"
    )
    console.print(
        f"  top of UI:    [dim]{last[0]}[/dim] -> [cyan]{last_dt.strftime(DATETIME_FMT)}[/cyan]  "
        "[dim](newest, last in m3u8 = anchor)[/dim]"
    )
    console.print(
        f"  bottom of UI: [dim]{first[0]}[/dim] -> [cyan]{first_dt.strftime(DATETIME_FMT)}[/cyan]  "
        "[dim](oldest, first in m3u8)[/dim]"
    )


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


def _aliases(s: str) -> list[str]:
    """Return all string forms of the same path we should try to match.

    Two macOS-specific traps we paper over:
      1. NFC vs NFD: filesystem paths come back NFD (e.g. 'e' + combining accent);
         rekordbox stores `FolderPath` in NFC. Same string, different bytes.
      2. Symlink aliases: ~/Dropbox and ~/Library/CloudStorage/Dropbox point at
         the same files but one is a symlink to the other. Whichever one the
         user originally dragged into rekordbox is what got stored.

    Returns NFC plus, when different, the realpath. realpath() on a non-existent
    file just echoes the input, so this is safe for tracks whose files moved.
    """
    nfc = unicodedata.normalize("NFC", s)
    real = os.path.realpath(nfc)
    return [nfc, real] if real != nfc else [nfc]


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
