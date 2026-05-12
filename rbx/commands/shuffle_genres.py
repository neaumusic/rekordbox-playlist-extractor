"""rbx shuffle-genres - assign every alive track a unique random Genre in master.db.

This is the iOS-rekordbox shuffle workaround: sorting by Genre on iOS becomes
effectively a shuffle when every track has a distinct random-numeric Genre name.

Algorithm:
  1. Sort alive tracks deterministically by ContentID (so a given --seed always
     produces the same assignment).
  2. Build a random permutation of [0, N) where N is the track count.
  3. Format each value as a zero-padded numeric string of width len(str(N-1)) —
     e.g. for N=3869, names are "0000".."3868".
  4. For each track at index i, get-or-create djmdGenre with name perm[i] and
     point the track's GenreID at it. Re-running with the same N reuses the
     same N rows; tracks just get shuffled across them. No tombstone churn.

Why a permutation rather than independent random ints: it's the minimal namespace
that produces a true shuffle. Re-running doesn't keep growing djmdGenre. Sorting
on iOS still works because rekordbox sorts genres lexicographically and the
zero-padded values sort numerically.

When track count grows past the current width's capacity (e.g. crossing 10,000),
all old name strings become orphaned in one go and rekordbox will tombstone them
on next cloud sync. Run `rbx purge-tombstones --include-genres` after a full
cloud-account wipe to clean those up.
"""

from __future__ import annotations

import random
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console

from rbx.db import open_db
from rbx.safety import assert_recent_backup

console = Console()


def run(
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
    """Give every alive track a unique permuted Genre and write it to master.db."""
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

    names = _permuted_names(len(contents), rng)

    # Existing rows indexed by Name (includes tombstones — we resurrect those by
    # name match rather than creating a duplicate, which add_genre() would reject).
    existing_by_name = {g.Name: g for g in db.get_genre()}

    preview_pairs = list(zip(contents[:3], names[:3], strict=True))
    for c, name in preview_pairs:
        console.print(f"  - track {c.ID} ({c.Title!r}): Genre -> {name!r}")
    if len(contents) > 3:
        console.print(f"  ... and {len(contents) - 3} more")

    reused = sum(1 for n in names if n in existing_by_name)
    created = len(names) - reused
    console.print(
        f"Plan: reuse [bold]{reused}[/bold] existing genre rows, "
        f"create [bold]{created}[/bold] new ones."
    )

    if dry_run:
        console.print("[yellow]--dry-run: nothing committed[/yellow]")
        return

    for c, name in zip(contents, names, strict=True):
        existing = existing_by_name.get(name)
        if existing is None:
            new_genre = db.add_genre(name)
            existing_by_name[name] = new_genre
            c.GenreID = new_genre.ID
        else:
            # Resurrect if rekordbox had tombstoned it from a previous run; reuse otherwise.
            if existing.rb_local_deleted:
                existing.rb_local_deleted = 0
            c.GenreID = existing.ID

    db.commit()
    console.print(f"[green]Assigned {len(contents)} permuted genres; committed.[/green]")


def _permuted_names(n: int, rng: random.Random) -> list[str]:
    """Return a random permutation of [0, n) as zero-padded strings of uniform width.

    Width is len(str(n-1)) so the namespace exactly matches the track count and
    re-runs reuse the same rows. For n=1 we use width=1 (single "0").
    """
    width = max(1, len(str(n - 1)))
    indices = list(range(n))
    rng.shuffle(indices)
    return [f"{i:0{width}d}" for i in indices]
