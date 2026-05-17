"""rbx sort - rewrite a chosen track field so the m3u tracks sort in the given order,
anchoring on the m3u's last entry and cascading colliding library tracks earlier
to keep all values globally unique.

Convention: m3u top = oldest, m3u bottom = newest (the anchor). Same convention
`rbx import` writes — so the natural workflow is `import` -> drag-drop into
rekordbox -> `sort` with no manual editing.

The default target is StockDate at minute precision (the original behavior).
For iOS sync, that field's time portion gets disregarded after sync and tracks
sharing a day collide on artist name. Override with `--target year` (or
`--target genre`/`--target album`) to write to a field iOS actually honors.
`--target stockdate --resolution day` is the destructive day-precision option
that some users may prefer over secondary fields.

Recommended preliminary step for stockdate target: `rbx expand-dates`. Without
it, any pre-existing sub-resolution duplicates in the library will get
rearranged here too.
"""

from __future__ import annotations

import os
import unicodedata
from datetime import date, datetime
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any
from urllib.parse import unquote, urlparse

import typer
from rich.console import Console

from rbx.dates import (
    DateSpace,
    ExpandItem,
    IntSpace,
    PaddedIntSpace,
    ReorderItem,
    Resolution,
    pack_for_sort_field,
)
from rbx.db import (
    existing_named_rows_by_name,
    get_or_create_named_row,
    named_field_spec,
    open_db,
)
from rbx.m3u import read_m3u
from rbx.safety import assert_recent_backup

if TYPE_CHECKING:
    from pyrekordbox.db6.tables import DjmdContent

    from pyrekordbox import Rekordbox6Database

console = Console()

DATE_FMT = "%Y-%m-%d"
DATETIME_FMT = "%Y-%m-%d %H:%M:%S"


class Target(StrEnum):
    STOCKDATE = "stockdate"
    YEAR = "year"
    GENRE = "genre"
    ALBUM = "album"


def run(
    from_path: Annotated[
        Path,
        typer.Option("--from", help="Path to the m3u/m3u8 listing tracks in desired order."),
    ],
    target: Annotated[
        Target,
        typer.Option(
            "--target",
            help=(
                "Field to write the sort order into. Default 'stockdate' is the "
                "original minute-precision DateAdded behavior (broken on iOS after "
                "sync). 'year' writes ReleaseYear (0..9999, newest=0) and is the "
                "recommended iOS-friendly option. 'genre'/'album' write zero-padded "
                "numeric names. Use 'stockdate' with --resolution day for the "
                "destructive day-precision DateAdded option."
            ),
        ),
    ] = Target.STOCKDATE,
    resolution: Annotated[
        Resolution,
        typer.Option(
            "--resolution",
            help=(
                "Target precision when --target=stockdate. 'minute' (default) keeps "
                "current behavior; 'day' is the destructive iOS-honored option."
            ),
        ),
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
    resolved, misses = _resolve_m3u_to_contents(locations, contents)

    if misses:
        console.print(f"[yellow]{len(misses)} m3u line(s) did not match a track:[/yellow]")
        for m in misses[:5]:
            console.print(f"  - {m}")
        if len(misses) > 5:
            console.print(f"  ... and {len(misses) - 5} more")

    if not resolved:
        console.print("[red]No m3u tracks could be resolved against master.db. Aborting.[/red]")
        raise typer.Exit(code=1)

    match target:
        case Target.STOCKDATE:
            _sort_to_stockdate(db, resolved, contents, resolution, dry_run)
        case Target.YEAR:
            _sort_to_year(db, resolved, contents, dry_run)
        case Target.GENRE:
            _sort_to_named_field(db, resolved, contents, dry_run, field="genre")
        case Target.ALBUM:
            _sort_to_named_field(db, resolved, contents, dry_run, field="album")


def _sort_to_stockdate(
    db: Rekordbox6Database,
    resolved: list[tuple[str, DjmdContent]],
    contents: list[DjmdContent],
    resolution: Resolution,
    dry_run: bool,
) -> None:
    """Original date-based sort: write minute/second/day-precision StockDate."""
    space = DateSpace(resolution)
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

    new_values = pack_for_sort_field(m3u_items, other_items, space)
    by_id: dict[str, DjmdContent] = {str(c.ID): c for c in contents}

    m3u_changes: list[tuple[DjmdContent, str]] = []
    other_changes: list[tuple[DjmdContent, str]] = []
    for item_id, new_dt in new_values.items():
        c = by_id[item_id]
        new_str = new_dt.strftime(DATETIME_FMT)
        if c.StockDate == new_str:
            continue
        if item_id in m3u_id_set:
            m3u_changes.append((c, new_str))
        else:
            other_changes.append((c, new_str))

    _print_change_summary(
        target_label=f"StockDate ({resolution.value})",
        m3u_total=len(m3u_items),
        m3u_changes=len(m3u_changes),
        other_changes=len(other_changes),
    )
    _print_order_preview_dates(resolved, new_values)

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


def _sort_to_year(
    db: Rekordbox6Database,
    resolved: list[tuple[str, DjmdContent]],
    contents: list[DjmdContent],
    dry_run: bool,
) -> None:
    """Write ReleaseYear: m3u block at 0..N-1 (newest=0), non-m3u keeps current."""
    space = IntSpace(min_value=0, max_value=9999)
    m3u_items = [ReorderItem(id=str(c.ID), current=0) for _, c in resolved]
    m3u_id_set = {it.id for it in m3u_items}

    other_items: list[ExpandItem] = []
    for c in contents:
        cid = str(c.ID)
        if cid in m3u_id_set:
            continue
        if c.ReleaseYear is None:
            continue
        other_items.append(
            ExpandItem(
                id=cid,
                current=int(c.ReleaseYear),
                sort_key=int(c.ID),
                tiebreaker_dt=c.created_at,
            )
        )

    new_values = pack_for_sort_field(m3u_items, other_items, space)
    by_id: dict[str, DjmdContent] = {str(c.ID): c for c in contents}

    m3u_changes: list[tuple[DjmdContent, int]] = []
    other_changes: list[tuple[DjmdContent, int]] = []
    for item_id, new_year in new_values.items():
        c = by_id[item_id]
        if c.ReleaseYear == new_year:
            continue
        if item_id in m3u_id_set:
            m3u_changes.append((c, int(new_year)))
        else:
            other_changes.append((c, int(new_year)))

    _print_change_summary(
        target_label="ReleaseYear",
        m3u_total=len(m3u_items),
        m3u_changes=len(m3u_changes),
        other_changes=len(other_changes),
    )
    _print_order_preview_simple(
        resolved, new_values, label="ReleaseYear", asc_top_label="lowest year"
    )

    for c, new_year in m3u_changes[:5]:
        console.print(f"  - {c.ID} {c.Title!r}: ReleaseYear {c.ReleaseYear!r} -> {new_year}")
    if len(m3u_changes) > 5:
        console.print(f"  ... and {len(m3u_changes) - 5} more m3u changes")

    if other_changes:
        console.print("[dim]library cascade samples:[/dim]")
        for c, new_year in other_changes[:3]:
            console.print(f"  - {c.ID} {c.Title!r}: ReleaseYear {c.ReleaseYear!r} -> {new_year}")
        if len(other_changes) > 3:
            console.print(f"  ... and {len(other_changes) - 3} more cascade changes")

    if dry_run:
        console.print("[yellow]--dry-run: nothing committed[/yellow]")
        return

    for c, new_year in m3u_changes:
        c.ReleaseYear = new_year
    for c, new_year in other_changes:
        c.ReleaseYear = new_year
    db.commit()
    console.print(
        f"[green]Updated {len(m3u_changes) + len(other_changes)} tracks "
        f"({len(m3u_changes)} m3u + {len(other_changes)} cascade); committed.[/green]"
    )


def _sort_to_named_field(
    db: Rekordbox6Database,
    resolved: list[tuple[str, DjmdContent]],
    contents: list[DjmdContent],
    dry_run: bool,
    *,
    field: str,
) -> None:
    """Write Genre or Album name: m3u block as zero-padded numerics, non-m3u
    cascades only if its existing name parses to an int in our namespace
    (rare; real genre/album names are non-numeric)."""
    width = max(1, len(str(max(len(resolved) - 1, 0))))
    space = PaddedIntSpace(width=width)

    m3u_items = [ReorderItem(id=str(c.ID), current=0) for _, c in resolved]
    m3u_id_set = {it.id for it in m3u_items}

    field_label, current_name_attr, set_id_attr = named_field_spec(field)

    other_items: list[ExpandItem] = []
    for c in contents:
        cid = str(c.ID)
        if cid in m3u_id_set:
            continue
        existing_name = getattr(c, current_name_attr) or ""
        if not _looks_numeric(existing_name):
            # Non-numeric existing names can't collide with our padded namespace.
            continue
        other_items.append(
            ExpandItem(
                id=cid,
                current=existing_name,
                sort_key=int(c.ID),
                tiebreaker_dt=c.created_at,
            )
        )

    new_values = pack_for_sort_field(m3u_items, other_items, space)
    by_id: dict[str, DjmdContent] = {str(c.ID): c for c in contents}

    m3u_changes: list[tuple[DjmdContent, str]] = []
    other_changes: list[tuple[DjmdContent, str]] = []
    for item_id, new_name in new_values.items():
        c = by_id[item_id]
        existing = getattr(c, current_name_attr) or ""
        if existing == new_name:
            continue
        if item_id in m3u_id_set:
            m3u_changes.append((c, str(new_name)))
        else:
            other_changes.append((c, str(new_name)))

    _print_change_summary(
        target_label=field_label,
        m3u_total=len(m3u_items),
        m3u_changes=len(m3u_changes),
        other_changes=len(other_changes),
    )
    _print_order_preview_simple(
        resolved, new_values, label=field_label, asc_top_label="lowest value"
    )

    for c, new_name in m3u_changes[:5]:
        existing = getattr(c, current_name_attr) or ""
        console.print(f"  - {c.ID} {c.Title!r}: {field_label} {existing!r} -> {new_name!r}")
    if len(m3u_changes) > 5:
        console.print(f"  ... and {len(m3u_changes) - 5} more m3u changes")

    if other_changes:
        console.print("[dim]library cascade samples:[/dim]")
        for c, new_name in other_changes[:3]:
            existing = getattr(c, current_name_attr) or ""
            console.print(f"  - {c.ID} {c.Title!r}: {field_label} {existing!r} -> {new_name!r}")
        if len(other_changes) > 3:
            console.print(f"  ... and {len(other_changes) - 3} more cascade changes")

    if dry_run:
        console.print("[yellow]--dry-run: nothing committed[/yellow]")
        return

    name_to_row = existing_named_rows_by_name(db, field)
    for c, new_name in m3u_changes:
        row = get_or_create_named_row(db, field, new_name, name_to_row)
        setattr(c, set_id_attr, row.ID)
    for c, new_name in other_changes:
        row = get_or_create_named_row(db, field, new_name, name_to_row)
        setattr(c, set_id_attr, row.ID)
    db.commit()
    console.print(
        f"[green]Updated {len(m3u_changes) + len(other_changes)} tracks "
        f"({len(m3u_changes)} m3u + {len(other_changes)} cascade); committed.[/green]"
    )


def _looks_numeric(s: str) -> bool:
    s = s.strip()
    return s.isdigit() if s else False


def _print_change_summary(
    *, target_label: str, m3u_total: int, m3u_changes: int, other_changes: int
) -> None:
    console.print(
        f"[bold]m3u ({target_label}):[/bold] {m3u_changes} of {m3u_total} tracks "
        f"need updates ({m3u_total - m3u_changes} already at target)"
    )
    if other_changes:
        console.print(
            f"[bold]library cascade:[/bold] {other_changes} non-m3u track(s) "
            f"will be shifted to avoid collisions"
        )
    else:
        console.print("[bold]library cascade:[/bold] no non-m3u tracks affected")


def _print_order_preview_dates(
    resolved: list[tuple[str, DjmdContent]],
    new_values: dict[str, datetime],
) -> None:
    """Show what the first/last m3u entries will become for date-based sort.

    Catches a flipped m3u before it inverts the playlist. Showing this in
    "rekordbox UI order" (Date Added descending, newest on top) so the user
    can mentally line it up with what they'd see after committing.
    """
    first = resolved[0]
    last = resolved[-1]
    first_dt = new_values[str(first[1].ID)]
    last_dt = new_values[str(last[1].ID)]
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


def _print_order_preview_simple(
    resolved: list[tuple[str, DjmdContent]],
    new_values: dict[str, Any],
    *,
    label: str,
    asc_top_label: str,
) -> None:
    """Show first/last m3u entries when target sorts ascending (year/genre/album).

    iOS sorts year/genre/album ASCENDING with the lowest value at the top —
    opposite of date-added-descending. We show "top of UI" = newest m3u
    (lowest value) so the user can sanity-check m3u direction.
    """
    first = resolved[0]
    last = resolved[-1]
    first_v = new_values[str(first[1].ID)]
    last_v = new_values[str(last[1].ID)]
    console.print(
        f"[yellow]Sanity check[/yellow] (iOS sorted by {label} ascending, {asc_top_label} on top):"
    )
    console.print(
        f"  top of UI:    [dim]{last[0]}[/dim] -> [cyan]{last_v}[/cyan]  "
        "[dim](newest, last in m3u8 = anchor)[/dim]"
    )
    console.print(
        f"  bottom of UI: [dim]{first[0]}[/dim] -> [cyan]{first_v}[/cyan]  "
        "[dim](oldest, first in m3u8)[/dim]"
    )


def _resolve_m3u_to_contents(
    locations: list[str], contents: list[DjmdContent]
) -> tuple[list[tuple[str, DjmdContent]], list[str]]:
    """Resolve every m3u line to a DjmdContent row, returning (resolved, misses)."""
    by_path: dict[str, DjmdContent] = {}
    for c in contents:
        for p in (c.FolderPath, c.OrgFolderPath):
            if not p:
                continue
            for alias in _aliases(p):
                by_path.setdefault(alias, c)

    resolved: list[tuple[str, DjmdContent]] = []
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

    return resolved, misses


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
