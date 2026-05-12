"""rbx purge-tombstones - hard-delete soft-deleted rows across master.db tables.

Rekordbox never hard-deletes from `djmdContent`, `djmdGenre`, `djmdMyTag`, or
`djmdSongMyTag`. Removed entries stay around as "tombstones" (rb_local_deleted
= 1) so cloud sync can reconcile deletions across devices. After enough churn
this can balloon to tens of thousands of rows per table — which is harmless
but implicated in flaky cloud-sync behavior on bloated libraries.

This command:
  1. Finds every tombstone row in djmdContent.
  2. Finds every child row in tables that FK-reference djmdContent.ID and points
     at a tombstone (otherwise the parent DELETE would fail on FK constraints).
  3. Bulk-deletes the orphan child rows, then the content tombstones.
  4. (Optional, --include-genres) Hard-deletes every tombstoned djmdGenre row not
     referenced by any remaining alive djmdContent. djmdContent.GenreID is the
     only FK into djmdGenre. Genre tombstones are gated behind a flag because
     `shuffle-genres` reuses the same djmdGenre rows across re-shuffles, so
     normal operation doesn't accumulate genre tombstones. The flag is a
     one-time cleanup for legacy XML-round-trip residue or for the wave of
     tombstones created when track count crosses a width boundary.
  5. (Optional, --include-mytags) Hard-deletes every tombstoned djmdSongMyTag
     row, then every tombstoned djmdMyTag row. This is independent cleanup
     from the djmdContent pass: Lexicon-style auto-tagging and a `clean-mytags`
     sweep both leave huge piles of song-tag tombstones (often 10–20× the live
     row count) that cloud sync no longer needs once peers have reconciled.

WARNING — RUN ONLY ON A FULL CLOUD WIPE:
This command is only safe immediately after you've wiped your rekordbox cloud
library (every peer device cleared, cloud state reset). Otherwise cloud sync may
try to re-introduce these rows from a peer that still has them, and you'll see
"deleted" tracks reappear. The command refuses to run without a recent local
backup, so you can always roll back via the snapshot under ./backups/.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from sqlalchemy import func, select

from rbx.db import open_db
from rbx.safety import assert_recent_backup

console = Console()

# Every table that holds an FK -> djmdContent.ID, discovered via SQLAlchemy
# metadata. Keep this list sorted; if pyrekordbox adds a new table in a future
# release we want the failure mode to be a loud FK error during commit, not a
# silent miss.
_CHILD_REFS: tuple[tuple[str, str], ...] = (
    ("contentActiveCensor", "ContentID"),
    ("contentCue", "ContentID"),
    ("contentFile", "ContentID"),
    ("djmdActiveCensor", "ContentID"),
    ("djmdCue", "ContentID"),
    ("djmdMixerParam", "ContentID"),
    ("djmdSongHistory", "ContentID"),
    ("djmdSongHotCueBanklist", "ContentID"),
    ("djmdSongMyTag", "ContentID"),
    ("djmdSongPlaylist", "ContentID"),
    ("djmdSongRelatedTracks", "ContentID"),
    ("djmdSongSampler", "ContentID"),
    ("djmdSongTagList", "ContentID"),
)

# Chunk IN-clause batches so we never bump into SQLite's host-parameter limit
# (999 on older builds, 32k on newer). 500 is comfortably under both.
_BATCH = 500


def run(
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", help="Show what would be deleted without committing."),
    ] = False,
    include_genres: Annotated[
        bool,
        typer.Option(
            "--include-genres",
            help=(
                "Also hard-delete tombstoned djmdGenre rows not referenced by any alive "
                "track. One-time cleanup; only safe immediately after a full cloud wipe."
            ),
        ),
    ] = False,
    include_mytags: Annotated[
        bool,
        typer.Option(
            "--include-mytags",
            help=(
                "Also hard-delete tombstoned djmdSongMyTag and djmdMyTag rows. "
                "Mainly cleans up the song-tag tombstone bloat left behind by Lexicon "
                "auto-tagging or a previous `clean-mytags` run. Cloud-wipe-only."
            ),
        ),
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
    """Hard-delete soft-deleted djmdContent rows (+ orphan child rows; optionally djmdGenre).

    ONLY SAFE AFTER A FULL CLOUD WIPE. Otherwise a peer's cloud state will push
    the deleted rows back on next sync.
    """
    if not dry_run and not no_backup_check:
        assert_recent_backup(backups_dir)

    from pyrekordbox.db6 import tables

    db = open_db()
    metadata = tables.Base.metadata

    tombstone_ids = [c.ID for c in db.get_content() if c.rb_local_deleted]
    console.print(f"Found [bold]{len(tombstone_ids)}[/bold] tombstones in djmdContent.")

    plan: list[tuple[str, int]] = []
    total_orphans = 0
    if tombstone_ids:
        for tname, col in _CHILD_REFS:
            table = metadata.tables[tname]
            col_obj = table.c[col]
            orphan = 0
            for chunk in _chunks(tombstone_ids, _BATCH):
                rows = db.session.execute(
                    table.select().with_only_columns(col_obj).where(col_obj.in_(chunk))
                ).fetchall()
                orphan += len(rows)
            plan.append((tname, orphan))

        total_orphans = sum(n for _, n in plan)
        console.print(f"Orphan rows referencing tombstones: [bold]{total_orphans}[/bold]")
        for tname, n in plan:
            if n:
                console.print(f"  - {tname}: {n}")

    genre_tombstones = 0
    if include_genres:
        # Count tombstoned djmdGenre rows not referenced by any alive djmdContent.
        # After content tombstones are deleted, alive content is all that remains
        # to reference genres, so this is the exact set we can hard-delete.
        genre_tombstones = _count_purgeable_genre_tombstones(db, metadata)
        console.print(
            f"Tombstoned djmdGenre rows safe to hard-delete: [bold]{genre_tombstones}[/bold]"
        )

    song_mytag_tombstones = 0
    mytag_tombstones = 0
    if include_mytags:
        song_mytag_tombstones = _count_tombstones(db, metadata, "djmdSongMyTag")
        mytag_tombstones = _count_tombstones(db, metadata, "djmdMyTag")
        console.print(
            f"Tombstoned djmdSongMyTag rows: [bold]{song_mytag_tombstones}[/bold]; "
            f"djmdMyTag rows: [bold]{mytag_tombstones}[/bold]"
        )

    if (
        not tombstone_ids
        and not genre_tombstones
        and not song_mytag_tombstones
        and not mytag_tombstones
    ):
        console.print("[green]Nothing to do.[/green]")
        return

    if dry_run:
        console.print("[yellow]--dry-run: nothing committed[/yellow]")
        return

    deleted_parents = 0
    if tombstone_ids:
        console.print("Deleting orphan child rows...")
        for tname, orphan_count in plan:
            if not orphan_count:
                continue
            table = metadata.tables[tname]
            col_obj = table.c["ContentID"]
            deleted = 0
            for chunk in _chunks(tombstone_ids, _BATCH):
                result = db.session.execute(table.delete().where(col_obj.in_(chunk)))
                deleted += result.rowcount or 0
            console.print(f"  - {tname}: {deleted}")

        console.print("Deleting tombstones from djmdContent...")
        content_tbl = metadata.tables["djmdContent"]
        content_id = content_tbl.c["ID"]
        for chunk in _chunks(tombstone_ids, _BATCH):
            result = db.session.execute(content_tbl.delete().where(content_id.in_(chunk)))
            deleted_parents += result.rowcount or 0

    deleted_genres = 0
    if include_genres and genre_tombstones:
        console.print("Deleting tombstones from djmdGenre...")
        deleted_genres = _delete_purgeable_genre_tombstones(db, metadata)
        console.print(f"  - djmdGenre: {deleted_genres}")

    deleted_song_mytags = 0
    deleted_mytags = 0
    if include_mytags:
        # Child table first (djmdSongMyTag has FK -> djmdMyTag.ID). Safe under
        # either FK enforcement state; required if rekordbox ever turns FKs on.
        if song_mytag_tombstones:
            console.print("Deleting tombstones from djmdSongMyTag...")
            deleted_song_mytags = _delete_tombstones(db, metadata, "djmdSongMyTag")
            console.print(f"  - djmdSongMyTag: {deleted_song_mytags}")
        if mytag_tombstones:
            console.print("Deleting tombstones from djmdMyTag...")
            deleted_mytags = _delete_tombstones(db, metadata, "djmdMyTag")
            console.print(f"  - djmdMyTag: {deleted_mytags}")

    db.commit()
    msg = f"[green]Deleted {deleted_parents} content tombstones, {total_orphans} orphan child rows"
    if include_genres:
        msg += f", {deleted_genres} genre tombstones"
    if include_mytags:
        msg += f", {deleted_song_mytags} song-mytag tombstones, {deleted_mytags} mytag tombstones"
    msg += "; committed.[/green]"
    console.print(msg)


def _count_purgeable_genre_tombstones(db, metadata) -> int:
    """Count tombstoned djmdGenre rows with no remaining alive djmdContent reference."""
    genre_tbl = metadata.tables["djmdGenre"]
    rows = db.session.execute(
        genre_tbl.select()
        .with_only_columns(genre_tbl.c.ID)
        .where(genre_tbl.c.rb_local_deleted == 1)
        .where(genre_tbl.c.ID.notin_(_alive_genre_ids_subq(metadata)))
    ).fetchall()
    return len(rows)


def _delete_purgeable_genre_tombstones(db, metadata) -> int:
    """Hard-delete tombstoned djmdGenre rows with no alive djmdContent reference."""
    genre_tbl = metadata.tables["djmdGenre"]
    result = db.session.execute(
        genre_tbl.delete()
        .where(genre_tbl.c.rb_local_deleted == 1)
        .where(genre_tbl.c.ID.notin_(_alive_genre_ids_subq(metadata)))
    )
    return result.rowcount or 0


def _alive_genre_ids_subq(metadata):
    """Subquery: GenreIDs referenced by any alive djmdContent row."""
    content_tbl = metadata.tables["djmdContent"]
    return (
        content_tbl.select()
        .with_only_columns(content_tbl.c.GenreID)
        .where(content_tbl.c.GenreID.isnot(None))
        .where(content_tbl.c.rb_local_deleted == 0)
    )


def _count_tombstones(db, metadata, table_name: str) -> int:
    """Count rows in `table_name` with rb_local_deleted=1."""
    tbl = metadata.tables[table_name]
    return db.session.execute(
        select(func.count()).select_from(tbl).where(tbl.c.rb_local_deleted == 1)
    ).scalar_one()


def _delete_tombstones(db, metadata, table_name: str) -> int:
    """Hard-delete every row in `table_name` with rb_local_deleted=1."""
    tbl = metadata.tables[table_name]
    result = db.session.execute(tbl.delete().where(tbl.c.rb_local_deleted == 1))
    return result.rowcount or 0


def _chunks(seq: list[str], n: int):
    """Yield successive n-sized chunks from seq."""
    for i in range(0, len(seq), n):
        yield seq[i : i + n]
