"""rbx expand-dates - give every alive track a unique value in the chosen field,
preserving the natural (StockDate, created_at, ContentID) order.

Default target ``stockdate`` keeps the original minute/second/day-precision
DateAdded behavior: stable-sort all tracks ascending by
(snapped StockDate, created_at, ContentID), then sweep backward from the
latest item. Each track keeps its snapped timestamp if it's strictly less
than the previously-assigned one; otherwise it gets pushed back by one
resolution step. Cascades may cross day boundaries — that's intentional, and
keeps the most recent timestamps anchored exactly where rekordbox already
had them.

``--target year`` / ``--target genre`` / ``--target album`` are the
iOS-friendly siblings: walk the same StockDate order from newest to oldest
and assign each track a unique ReleaseYear (or zero-padded Genre/Album name)
starting at the anchor (year 0 / "0000"). Tracks with no StockDate are
skipped.

Why ``created_at`` as the within-day tiebreaker for stockdate: rekordbox
stores ``StockDate`` (the user-visible "Date Added") at date-only precision,
so same-day cohorts need a tiebreaker. ``ContentID`` is essentially random —
it doesn't follow import order. The SQLite row's ``created_at`` column has
millisecond precision and matches the order rekordbox's UI actually displays.
ContentID stays as a last-resort tiebreaker for the rare case of identical
``created_at`` values.

Idempotent for stockdate: tracks already at the chosen resolution are left
untouched. For year/genre/album the assignment is fully deterministic given
the (StockDate, created_at, ID) order, so re-running with the same library
state produces the same output.

NOTE on storage: rekordbox stores StockDate as a VARCHAR. We write
'YYYY-MM-DD HH:MM:SS' which sorts lexicographically the way you want.
Verify visually in rekordbox after running on a backup.
"""

from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any

import typer
from rich.console import Console

from rbx.dates import (
    ExpandItem,
    IntSpace,
    PaddedIntSpace,
    ReorderItem,
    Resolution,
    ValueSpace,
    expand_unique_backward,
    pack_for_sort_field,
)
from rbx.db import (
    existing_named_rows_by_name,
    get_or_create_named_row,
    named_field_spec,
    open_db,
)
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
    target: Annotated[
        Target,
        typer.Option(
            "--target",
            help=(
                "Field to expand into a unique value per track. Default "
                "'stockdate' keeps minute/second/day-precision DateAdded "
                "behavior (broken on iOS after sync). 'year' assigns sequential "
                "ReleaseYear values (newest=0); 'genre'/'album' assign "
                "zero-padded numeric names. Use 'year' / 'genre' / 'album' for "
                "the iOS-friendly equivalent of expand-dates."
            ),
        ),
    ] = Target.STOCKDATE,
    resolution: Annotated[
        Resolution,
        typer.Option(
            "--resolution",
            help=("Target precision when --target=stockdate. Ignored for year/genre/album."),
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
    """Expand the chosen field so every track has a unique value."""
    if not dry_run and not no_backup_check:
        assert_recent_backup(backups_dir)

    db = open_db()
    contents = list(db.get_content())
    console.print(f"Loaded {len(contents)} tracks from master.db")

    match target:
        case Target.STOCKDATE:
            _expand_stockdate(db, contents, resolution, dry_run)
        case Target.YEAR:
            _expand_to_field(
                db,
                contents,
                space=IntSpace(min_value=0, max_value=9999),
                dry_run=dry_run,
                target_label="ReleaseYear",
                read_current=lambda c: c.ReleaseYear,
                apply_value=_apply_year,
            )
        case Target.GENRE:
            _expand_to_named_field(db, contents, dry_run, field="genre")
        case Target.ALBUM:
            _expand_to_named_field(db, contents, dry_run, field="album")


def _expand_stockdate(
    db: Rekordbox6Database,
    contents: list[DjmdContent],
    resolution: Resolution,
    dry_run: bool,
) -> None:
    """Original date-based expand: cascade-preserve each track's StockDate."""
    items: list[ExpandItem] = []
    by_id: dict[str, DjmdContent] = {}
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


def _expand_to_field(
    db: Rekordbox6Database,
    contents: list[DjmdContent],
    *,
    space: ValueSpace,
    dry_run: bool,
    target_label: str,
    read_current: Any,
    apply_value: Any,
) -> None:
    """Walk alive tracks in StockDate order; assign each a unique slot in
    `space`. Newest gets the anchor; each older gets one step further.

    Uses ``pack_for_sort_field`` with the entire StockDate-sorted library as
    the m3u block (and no others). This reuses the same anchor + cascade
    primitive as ``rbx sort``, just packing every alive track in one go.
    """
    items_in_order = _alive_in_stockdate_order(contents)
    if not items_in_order:
        console.print(f"[yellow]No tracks with a StockDate to expand into {target_label}.[/yellow]")
        return

    m3u = [ReorderItem(id=str(c.ID), current=0) for c in items_in_order]
    new_values = pack_for_sort_field(m3u, [], space)

    by_id = {str(c.ID): c for c in items_in_order}
    changes: list[tuple[DjmdContent, Any]] = []
    for item_id, new_value in new_values.items():
        c = by_id[item_id]
        if read_current(c) == new_value:
            continue
        changes.append((c, new_value))

    console.print(
        f"[bold]{len(changes)}[/bold] tracks need {target_label} updates "
        f"({len(items_in_order) - len(changes)} already at target)"
    )
    overflow = sum(1 for v in new_values.values() if isinstance(v, int) and v >= 9999)
    if overflow > 1 and target_label == "ReleaseYear":
        console.print(
            f"[yellow]{overflow}[/yellow] track(s) clamped to ReleaseYear=9999 "
            f"(library exceeds the 0..9999 capacity)."
        )

    for c, new_value in changes[:5]:
        console.print(
            f"  - track {c.ID} ({c.Title!r}): {target_label} {read_current(c)!r} -> {new_value!r}"
        )
    if len(changes) > 5:
        console.print(f"  ... and {len(changes) - 5} more")

    if dry_run:
        console.print("[yellow]--dry-run: nothing committed[/yellow]")
        return

    for c, new_value in changes:
        apply_value(db, c, new_value)
    db.commit()
    console.print(f"[green]Updated {len(changes)} tracks; committed.[/green]")


def _expand_to_named_field(
    db: Rekordbox6Database,
    contents: list[DjmdContent],
    dry_run: bool,
    *,
    field: str,
) -> None:
    """Genre/Album variant of _expand_to_field: looks up / creates rows by name."""
    items_in_order = _alive_in_stockdate_order(contents)
    if not items_in_order:
        label = "Genre" if field == "genre" else "Album"
        console.print(f"[yellow]No tracks with a StockDate to expand into {label}.[/yellow]")
        return

    width = max(1, len(str(len(items_in_order) - 1)))
    space = PaddedIntSpace(width=width)

    field_label, name_attr, set_id_attr = named_field_spec(field)
    m3u = [ReorderItem(id=str(c.ID), current=0) for c in items_in_order]
    new_values = pack_for_sort_field(m3u, [], space)

    by_id = {str(c.ID): c for c in items_in_order}
    changes: list[tuple[DjmdContent, str]] = []
    for item_id, new_name in new_values.items():
        c = by_id[item_id]
        if (getattr(c, name_attr) or "") == new_name:
            continue
        changes.append((c, str(new_name)))

    console.print(
        f"[bold]{len(changes)}[/bold] tracks need {field_label} updates "
        f"({len(items_in_order) - len(changes)} already at target)"
    )

    for c, new_name in changes[:5]:
        existing = getattr(c, name_attr) or ""
        console.print(f"  - track {c.ID} ({c.Title!r}): {field_label} {existing!r} -> {new_name!r}")
    if len(changes) > 5:
        console.print(f"  ... and {len(changes) - 5} more")

    if dry_run:
        console.print("[yellow]--dry-run: nothing committed[/yellow]")
        return

    name_to_row = existing_named_rows_by_name(db, field)
    for c, new_name in changes:
        row = get_or_create_named_row(db, field, new_name, name_to_row)
        setattr(c, set_id_attr, row.ID)
    db.commit()
    console.print(f"[green]Updated {len(changes)} tracks; committed.[/green]")


def _alive_in_stockdate_order(contents: list[DjmdContent]) -> list[DjmdContent]:
    """Return alive tracks with a parseable StockDate, sorted oldest-first by
    (StockDate, created_at, ContentID)."""
    rows = []
    for c in contents:
        if c.rb_local_deleted:
            continue
        parsed = _parse_stockdate(c.StockDate)
        if parsed is None:
            continue
        # Promote date to datetime midnight so the (dt, created_at, id) tuple sorts uniformly.
        if isinstance(parsed, datetime):
            dt = parsed
        else:
            dt = datetime.combine(parsed, datetime.min.time())
        rows.append((dt, c.created_at or datetime.min, int(c.ID), c))
    rows.sort()
    return [c for _, _, _, c in rows]


def _apply_year(_db: Rekordbox6Database, c: DjmdContent, new_value: int) -> None:
    c.ReleaseYear = int(new_value)


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
