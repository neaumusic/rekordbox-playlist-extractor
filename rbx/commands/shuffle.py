"""rbx shuffle - assign random values across one or more track fields so iOS
sort-by-{genre, album, year} effectively shuffles the library.

Used as the iOS-rekordbox shuffle workaround. Sorting by Genre/Album on iOS
becomes a shuffle when every track has a distinct random-numeric Name; sorting
by ReleaseYear becomes a shuffle when every track has a distinct ReleaseYear
(0..9999, with overflow tracks all sharing year 9999 if the library exceeds
10K tracks).

Multi-target: pass `--target` more than once to populate several fields in one
run, each with its OWN independent random permutation. Then on iOS the user
can sort by any of those fields and get a different shuffle.

Algorithm:
  1. Sort alive tracks deterministically by ContentID (so a given --seed always
     produces the same assignment).
  2. For each requested target, draw an independent random permutation of
     [0, N) where N is the track count.
  3. Format each value for the target:
       - genre/album: zero-padded numeric string of width len(str(N-1)) so the
         lexicographic sort matches the numeric sort.
       - year: integer in [0, 9999], clamping any index >= 10000 to 9999.
  4. For genre/album, get-or-create DjmdGenre/DjmdAlbum rows by Name and point
     each track's GenreID/AlbumID at the matching row. For year, set
     ReleaseYear directly.

Re-running with the same N (and same target set) reuses the same Genre/Album
rows: the namespace is fully claimed; only the per-track assignment shuffles.
No tombstone churn.

When track count grows past the current width's capacity (e.g. crossing
10,000), all old name strings become orphaned in one go and rekordbox will
tombstone them on next cloud sync. Run `rbx purge-tombstones --include-genres`
after a full cloud-account wipe to clean those up.
"""

from __future__ import annotations

import random
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, Annotated

import typer
from rich.console import Console

from rbx.db import (
    NamedField,
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

YEAR_CAP = 9999


class Target(StrEnum):
    GENRE = "genre"
    ALBUM = "album"
    YEAR = "year"


def run(
    target: Annotated[
        list[Target] | None,
        typer.Option(
            "--target",
            help=(
                "Field(s) to shuffle. Repeat to write multiple fields with "
                "independent permutations (e.g. --target genre --target year). "
                "Default: genre."
            ),
        ),
    ] = None,
    seed: Annotated[
        int | None,
        typer.Option("--seed", help="Optional RNG seed for reproducibility."),
    ] = None,
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
    """Give every alive track a unique permuted value in each requested target field."""
    targets = list(target) if target else [Target.GENRE]
    seen: set[Target] = set()
    deduped: list[Target] = []
    for t in targets:
        if t in seen:
            continue
        seen.add(t)
        deduped.append(t)
    targets = deduped

    if not dry_run and not no_backup_check:
        assert_recent_backup(backups_dir)

    rng = random.Random(seed)
    db = open_db()

    contents = sorted(
        (c for c in db.get_content() if not c.rb_local_deleted),
        key=lambda c: int(c.ID),
    )
    console.print(f"Loaded [bold]{len(contents)}[/bold] alive tracks from master.db")
    if not contents:
        console.print("[yellow]No tracks to shuffle.[/yellow]")
        return

    width = max(1, len(str(len(contents) - 1)))

    for t in targets:
        match t:
            case Target.GENRE:
                _shuffle_named(db, contents, rng, width, dry_run, field="genre")
            case Target.ALBUM:
                _shuffle_named(db, contents, rng, width, dry_run, field="album")
            case Target.YEAR:
                _shuffle_year(db, contents, rng, dry_run)

    if dry_run:
        return
    db.commit()
    console.print(
        f"[green]Committed shuffles for {len(contents)} tracks across "
        f"{len(targets)} target(s): {', '.join(t.value for t in targets)}.[/green]"
    )


def _shuffle_named(
    db: Rekordbox6Database,
    contents: list[DjmdContent],
    rng: random.Random,
    width: int,
    dry_run: bool,
    *,
    field: NamedField,
) -> None:
    """Assign every alive track a unique permuted Genre or Album name."""
    names = _permuted_padded_names(len(contents), width, rng)

    label, _, set_id_attr = named_field_spec(field)
    existing_by_name = existing_named_rows_by_name(db, field)

    preview_pairs = list(zip(contents[:3], names[:3], strict=True))
    for c, name in preview_pairs:
        console.print(f"  - track {c.ID} ({c.Title!r}): {label} -> {name!r}")
    if len(contents) > 3:
        console.print(f"  ... and {len(contents) - 3} more")

    reused = sum(1 for n in names if n in existing_by_name)
    created = len(names) - reused
    console.print(
        f"Plan ({label}): reuse [bold]{reused}[/bold] existing rows, "
        f"create [bold]{created}[/bold] new ones."
    )

    if dry_run:
        console.print(f"[yellow]--dry-run ({label}): nothing committed[/yellow]")
        return

    for c, name in zip(contents, names, strict=True):
        row = get_or_create_named_row(db, field, name, existing_by_name)
        setattr(c, set_id_attr, row.ID)
    console.print(f"[green]Assigned {len(contents)} permuted {label} names.[/green]")


def _shuffle_year(
    db: Rekordbox6Database,
    contents: list[DjmdContent],
    rng: random.Random,
    dry_run: bool,
) -> None:
    """Assign every alive track a permuted ReleaseYear in [0, min(N, 10000)).

    N>10000: indices 10000..N-1 all clamp to YEAR_CAP=9999 (the older end), so
    the bottom of the iOS-by-year list collapses into one share-bucket.
    """
    n = len(contents)
    indices = list(range(n))
    rng.shuffle(indices)
    years = [min(i, YEAR_CAP) for i in indices]

    overflow = sum(1 for i in indices if i > YEAR_CAP)
    console.print(
        f"Plan (Year): writing ReleaseYear from a permutation of [0, {n}); "
        f"{overflow} track(s) clamped to {YEAR_CAP}."
        if overflow
        else f"Plan (Year): writing ReleaseYear from a permutation of [0, {n})."
    )

    preview_pairs = list(zip(contents[:3], years[:3], strict=True))
    for c, y in preview_pairs:
        console.print(f"  - track {c.ID} ({c.Title!r}): ReleaseYear -> {y}")
    if n > 3:
        console.print(f"  ... and {n - 3} more")

    if dry_run:
        console.print("[yellow]--dry-run (Year): nothing committed[/yellow]")
        return

    for c, y in zip(contents, years, strict=True):
        c.ReleaseYear = y
    console.print(f"[green]Assigned {n} permuted ReleaseYear values.[/green]")


def _permuted_padded_names(n: int, width: int, rng: random.Random) -> list[str]:
    """Return a random permutation of [0, n) as zero-padded strings of given width."""
    indices = list(range(n))
    rng.shuffle(indices)
    return [f"{i:0{width}d}" for i in indices]
