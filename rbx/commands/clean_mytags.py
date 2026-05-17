"""rbx clean-mytags - prune MyTag categories/tags back to a user-curated subset.

Third-party apps (Lexicon being the obvious culprit) sometimes write extra MyTag
categories into rekordbox and inject their own default tag names into the three
built-in categories (Genre, Components, Situation). Removing them through the
rekordbox UI is one-by-one and tedious.

This command does the cleanup in bulk:

  1. Tombstones every MyTag category that isn't one of rekordbox's four
     built-ins (IDs 1=Genre, 2=Components, 3=Situation, 4=Untitled Column).
     The IDs are stable: rekordbox seeds them on first launch and never
     reassigns them, and the UI doesn't let you add a fifth. Anything beyond
     ID 4 was written by a third-party tool directly to master.db.
  2. Within those four default categories, tombstones every child tag whose
     name does NOT match `--keep-pattern`. The default pattern `^[^a-z]+$`
     (no lowercase letters anywhere in the name) is tuned to this library's
     convention of writing custom tags in ALL CAPS or with a `__` prefix; it
     reliably separates user tags from mixed-case Lexicon/rekordbox defaults
     like 'Acid House', 'Peak Time', 'Sub Bass'. Override with a different
     regex if your style is different.
  3. Tombstones every alive djmdSongMyTag row that references any of the tags
     above. Without this, the song-link rows would point at non-existent
     MyTagIDs after commit.

Why tombstones and not hard deletes? Cloud sync watches `rb_local_deleted=1`
+ a bumped `rb_local_usn`; that's the only signal that propagates "this row
is gone" to other devices. A hard `DELETE` is silent — the cloud retains its
copy and other devices keep pulling it back. We mirror rekordbox's own
UI-delete shape: scrub the data columns to NULL, flip `rb_data_status`
256 → 262, set `rb_local_deleted=1` + `rb_local_synced=0`, bump
`rb_local_usn`. djmdSongMyTag tombstones preserve MyTagID/ContentID/TrackNo
(rekordbox keeps the FK identity even on deletion).

Safe to run while the cloud has alive copies of these rows — on next
rekordbox open, the cloud agent uploads the tombstones, peers reconcile,
the rows disappear everywhere.

Tombstoned MyTag and djmdSongMyTag rows from prior runs (or rekordbox UI
deletes) are left alone — that's `purge-tombstones --include-mytags`, which
hard-deletes tombstones to reclaim row count once you've wiped the cloud.

The `--ensure-defaults` flag re-creates any of the four built-in categories
that are missing (so e.g. a deleted "Untitled Column" rename slot gets
restored). Useful when a third-party app or manual cleanup deleted one.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING, Annotated

import typer
from rich.console import Console

from rbx.db import open_db
from rbx.safety import assert_recent_backup

if TYPE_CHECKING:
    from pyrekordbox.db6.tables import DjmdMyTag, DjmdSongMyTag

console = Console()

# rekordbox's four built-in MyTag categories with their canonical seeds. ID
# assignment is stable across installs: 1=Genre, 2=Components, 3=Situation,
# 4=Untitled Column (the rename slot). The rekordbox UI doesn't let you create
# a fifth, so anything else at ParentID='root' was written by a third-party
# tool. Used both as the keep-set during cleanup and as the seed values for
# --ensure-defaults.
#
# Canonical shape of a category row vs a tag row in djmdMyTag (verified against
# a fresh rekordbox 7 install):
#   - Category: Attribute=1, ParentID='root', UUID=<string-equal-to-ID>
#   - Tag:      Attribute=0, ParentID=<category ID>, UUID=<uuid4 hex>
# Getting Attribute wrong is what makes rekordbox render a row as a tag inside
# the parent listing instead of as a category header.
_DEFAULT_CATEGORIES: tuple[tuple[str, int, str], ...] = (
    ("1", 1, "Genre"),
    ("2", 2, "Components"),
    ("3", 3, "Situation"),
    ("4", 4, "Untitled Column"),
)
_DEFAULT_CATEGORY_IDS: frozenset[str] = frozenset(cid for cid, _, _ in _DEFAULT_CATEGORIES)
_CATEGORY_ATTRIBUTE: int = 1

# rekordbox's tombstone shape: `rb_data_status` is 256 (0x100) on alive rows and
# 262 (0x106) on tombstones, across djmdMyTag and djmdSongMyTag. Empirically
# verified against UI-deleted rows in this user's DB; setting it lets the cloud
# agent recognize the row as a "real" rekordbox tombstone rather than a malformed
# delete.
_ALIVE_DATA_STATUS: int = 256
_TOMBSTONE_DATA_STATUS: int = 262

# Keep-pattern default: tag names containing zero lowercase letters. Matches the
# convention of ALL-CAPS tags (e.g. 'BEAT / INSTRUMENTAL / LOFI') and `__`-prefixed
# ones (e.g. '__BAD GRID'); rejects mixed-case names like 'Acid House' that
# Lexicon and rekordbox seed by default.
_DEFAULT_KEEP_PATTERN = r"^[^a-z]+$"


def run(
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", help="Show what would be deleted without committing."),
    ] = False,
    keep_pattern: Annotated[
        str,
        typer.Option(
            "--keep-pattern",
            help=(
                "Regex matched against tag names within the four default categories. "
                "Names matching are kept; non-matches are deleted. "
                f"Default: {_DEFAULT_KEEP_PATTERN!r} (keep tags with no lowercase letters)."
            ),
        ),
    ] = _DEFAULT_KEEP_PATTERN,
    ensure_defaults: Annotated[
        bool,
        typer.Option(
            "--ensure-defaults",
            help=(
                "Re-create any of the four built-in MyTag categories (Genre, Components, "
                "Situation, Untitled Column) that are currently missing from the DB."
            ),
        ),
    ] = False,
    force_cloud_usn: Annotated[
        bool,
        typer.Option(
            "--force-cloud-usn",
            help=(
                "Before tombstoning, bump the local USN counter (`localUpdateCount`) "
                "above the cloud's `lastUpdateCount` so each new tombstone gets a "
                "rb_local_usn higher than any value the cloud has stamped on its alive "
                "version of the row. Use this when prior tombstoning attempts have been "
                "reverted by cloud sync (i.e. the cloud's per-row `usn` is way ahead of "
                "your local USN, so the cloud server treats your changes as stale)."
            ),
        ),
    ] = False,
    no_backup_check: Annotated[
        bool,
        typer.Option("--no-backup-check", help="Skip the recent-backup safety check."),
    ] = False,
    allow_rekordbox_open: Annotated[
        bool,
        typer.Option(
            "--allow-rekordbox-open",
            help=(
                "Bypass the rekordbox-running safety check and attempt to write while "
                "rekordbox is open. Use only when normal closed-app tombstoning keeps "
                "getting reverted by cloud sync."
            ),
        ),
    ] = False,
    backups_dir: Annotated[
        Path,
        typer.Option("--backups-dir", help="Where to look for recent backups."),
    ] = Path("backups"),
) -> None:
    """Tombstone non-default MyTag categories + non-matching tags inside the 4 defaults.

    Keeps only rekordbox's built-in categories (IDs 1=Genre, 2=Components,
    3=Situation, 4=Untitled Column). Within those, keeps only tags whose
    names match --keep-pattern. Everything else — extra categories, their
    child tags, and injected default tags — is tombstoned along with all alive
    djmdSongMyTag rows referencing them.

    Tombstones (not hard deletes) are what cloud sync needs to propagate
    "this row is gone" to other devices. On next rekordbox open the cloud
    agent uploads the tombstones, peers reconcile, the rows disappear
    everywhere. Run `purge-tombstones --include-mytags` later, after a cloud
    wipe, to hard-delete the tombstones if you want to reclaim row count.

    Pass --ensure-defaults to also re-create any of the four built-in
    categories that are missing.
    """
    if not dry_run and not no_backup_check:
        assert_recent_backup(backups_dir)

    try:
        pattern = re.compile(keep_pattern)
    except re.error as e:
        console.print(f"[red]Invalid --keep-pattern regex: {e}[/red]")
        raise typer.Exit(code=2) from e

    from pyrekordbox.db6 import tables

    db = open_db(allow_rekordbox_open=allow_rekordbox_open)

    # Snapshot every alive djmdMyTag row in one pass; we'll partition it in-memory.
    # Tombstoned rows (rb_local_deleted=1) are skipped — they're cloud-sync
    # ghosts and re-deleting them is a no-op that'd just churn USNs.
    alive_tags: list[DjmdMyTag] = (
        db.session.query(tables.DjmdMyTag).filter(tables.DjmdMyTag.rb_local_deleted == 0).all()
    )

    name_by_id: dict[str, str] = {t.ID: (t.Name or "") for t in alive_tags}

    extra_categories: list[DjmdMyTag] = [
        t for t in alive_tags if t.ParentID == "root" and t.ID not in _DEFAULT_CATEGORY_IDS
    ]
    extra_category_ids: set[str] = {c.ID for c in extra_categories}

    extra_children: list[DjmdMyTag] = [t for t in alive_tags if t.ParentID in extra_category_ids]

    # Inside the 3 default categories, kill anything that doesn't match the keep regex.
    default_cat_tags_to_delete: list[DjmdMyTag] = [
        t
        for t in alive_tags
        if t.ParentID in _DEFAULT_CATEGORY_IDS and not pattern.match(t.Name or "")
    ]

    tag_ids_to_delete: set[str] = set()
    tag_ids_to_delete.update(c.ID for c in extra_categories)
    tag_ids_to_delete.update(t.ID for t in extra_children)
    tag_ids_to_delete.update(t.ID for t in default_cat_tags_to_delete)

    song_links_to_delete: list[DjmdSongMyTag] = []
    if tag_ids_to_delete:
        # Chunked .in_() to dodge SQLite's host-parameter limit (~999 on older builds).
        song_links_to_delete = [
            link
            for chunk in _chunks(sorted(tag_ids_to_delete), 500)
            for link in db.session.query(tables.DjmdSongMyTag)
            .filter(
                tables.DjmdSongMyTag.MyTagID.in_(chunk),
                tables.DjmdSongMyTag.rb_local_deleted == 0,
            )
            .all()
        ]

    # Default-category restoration. For each of the four canonical IDs we
    # either need to (a) insert a fresh row if none exists, or (b) repair an
    # existing row whose shape is wrong (Attribute != 1, UUID != ID).
    # Repairing matters because Attribute=0 makes rekordbox render the row as
    # a tag-inside-a-category instead of as a category header.
    existing_default_rows: dict[str, DjmdMyTag] = {
        r.ID: r
        for r in db.session.query(tables.DjmdMyTag).filter(
            tables.DjmdMyTag.ID.in_(_DEFAULT_CATEGORY_IDS)
        )
    }
    defaults_to_create: list[tuple[str, int, str]] = []
    defaults_to_repair: list[tuple[DjmdMyTag, dict[str, object]]] = []
    if ensure_defaults:
        for cid, seq, name in _DEFAULT_CATEGORIES:
            row = existing_default_rows.get(cid)
            if row is None:
                defaults_to_create.append((cid, seq, name))
                continue
            fixes: dict[str, object] = {}
            # If the row is a scrubbed tombstone (rb_local_deleted=1, Name/Seq NULLed
            # by a prior _tombstone(scrub_data=True)), restoring rb_local_deleted=0
            # alone leaves a malformed alive row. Restore the canonical Name/Seq and
            # un-tombstone rb_data_status too.
            if row.Name != name:
                fixes["Name"] = name
            if row.Seq != seq:
                fixes["Seq"] = seq
            if row.Attribute != _CATEGORY_ATTRIBUTE:
                fixes["Attribute"] = _CATEGORY_ATTRIBUTE
            if row.ParentID != "root":
                fixes["ParentID"] = "root"
            if cid != row.UUID:
                fixes["UUID"] = cid
            if row.rb_local_deleted != 0:
                fixes["rb_local_deleted"] = 0
            if row.rb_data_status != _ALIVE_DATA_STATUS:
                fixes["rb_data_status"] = _ALIVE_DATA_STATUS
            if fixes:
                defaults_to_repair.append((row, fixes))

    _print_plan(
        extra_categories=extra_categories,
        extra_children=extra_children,
        default_cat_tags=default_cat_tags_to_delete,
        song_links=song_links_to_delete,
        name_by_id=name_by_id,
        defaults_to_create=defaults_to_create,
        defaults_to_repair=defaults_to_repair,
        ensure_defaults=ensure_defaults,
    )

    if (
        not tag_ids_to_delete
        and not song_links_to_delete
        and not defaults_to_create
        and not defaults_to_repair
    ):
        console.print("[green]Nothing to do.[/green]")
        return

    if dry_run:
        if force_cloud_usn:
            _print_force_cloud_usn_plan(db, tag_ids_to_delete, song_links_to_delete)
        console.print("\n[yellow]--dry-run: nothing committed[/yellow]")
        return

    if force_cloud_usn:
        _bump_local_usn_above_cloud(db, n_pending=len(tag_ids_to_delete) + len(song_links_to_delete))

    # Tombstoning order doesn't actually matter for FK correctness (the rows
    # stay in the table, just with rb_local_deleted=1), but we follow the same
    # child-first pattern as the old hard-delete code so any FK-on debug
    # output remains familiar.
    console.print("Tombstoning song-tag links...")
    for link in song_links_to_delete:
        _tombstone(link, db)

    console.print("Tombstoning tags inside default categories...")
    for t in default_cat_tags_to_delete:
        _tombstone(t, db, scrub_data=True)

    console.print("Tombstoning extra-category child tags...")
    for t in extra_children:
        _tombstone(t, db, scrub_data=True)

    console.print("Tombstoning extra categories...")
    for c in extra_categories:
        _tombstone(c, db, scrub_data=True)

    if defaults_to_create:
        console.print("Re-creating missing default categories...")
        for cid, seq, name in defaults_to_create:
            row = tables.DjmdMyTag.create(
                ID=cid,
                Seq=seq,
                Name=name,
                Attribute=_CATEGORY_ATTRIBUTE,
                ParentID="root",
                # Default categories conventionally use their string ID as the UUID.
                # uuid4() works at the DB level (UUID is just a label), but matching
                # rekordbox's seed shape keeps third-party tools that key off UUID happy.
                UUID=cid,
                # Match the rb_data_status=256 value rekordbox writes on its own
                # alive rows; the default on the SQLAlchemy model is 0, which would
                # produce a malformed-looking row that cloud sync might handle oddly.
                rb_data_status=_ALIVE_DATA_STATUS,
            )
            db.add(row)

    if defaults_to_repair:
        console.print("Repairing default category rows with wrong shape...")
        for row, fixes in defaults_to_repair:
            for col, val in fixes.items():
                setattr(row, col, val)

    _commit(db, allow_rekordbox_open=allow_rekordbox_open)
    msg = (
        f"[green]Tombstoned {len(song_links_to_delete)} song-tag links and "
        f"{len(tag_ids_to_delete)} MyTag rows"
    )
    if defaults_to_create:
        msg += f"; created {len(defaults_to_create)} default categories"
    if defaults_to_repair:
        msg += f"; repaired {len(defaults_to_repair)} default category rows"
    msg += "; committed.[/green]"
    console.print(msg)


def _tombstone(row, db, *, scrub_data: bool = False) -> None:
    """Soft-delete a djmdMyTag or djmdSongMyTag row in the rekordbox-native shape.

    Mirrors what rekordbox itself writes when you delete from the UI (verified
    against the user's existing tombstones):
      - Set ``rb_data_status = 262`` (was 256 on alive rows).
      - Set ``rb_local_deleted = 1``.
      - Set ``rb_local_synced = 0`` so the cloud agent picks it up on next sync.
      - For djmdMyTag rows (``scrub_data=True``): NULL out Name/Attribute/ParentID/Seq.
        rekordbox scrubs these on UI delete, presumably to free the slots in case
        a peer creates a new category with overlapping name.
      - For djmdSongMyTag rows (``scrub_data=False``): leave ContentID/MyTagID/TrackNo
        intact — rekordbox preserves the FK identity even on tombstoning.

    Registry tracking is briefly disabled while we mutate columns so a single
    update event is logged per row (otherwise pyrekordbox bumps the global USN
    once per attribute write, producing 5x bloat for a MyTag tombstone). The
    one re-enabled ``on_update`` call is what fires the USN bump on commit —
    that bumped ``rb_local_usn`` is the signal the cloud agent uses to detect
    the change and propagate it.
    """
    with db.registry.disabled():
        if scrub_data:
            row.Seq = None
            row.Name = None
            row.Attribute = None
            row.ParentID = None
        row.rb_data_status = _TOMBSTONE_DATA_STATUS
        row.rb_local_deleted = 1
        row.rb_local_synced = 0
    db.registry.on_update(row, "rb_local_deleted", 1)


def _print_plan(
    *,
    extra_categories: list[DjmdMyTag],
    extra_children: list[DjmdMyTag],
    default_cat_tags: list[DjmdMyTag],
    song_links: list[DjmdSongMyTag],
    name_by_id: dict[str, str],
    defaults_to_create: list[tuple[str, int, str]],
    defaults_to_repair: list[tuple[DjmdMyTag, dict[str, object]]],
    ensure_defaults: bool,
) -> None:
    """Pretty-print the tombstoning plan. Mirrors the structure of purge-tombstones."""
    console.print(f"[bold]Extra categories[/bold] to tombstone: {len(extra_categories)}")
    for c in extra_categories:
        n_kids = sum(1 for t in extra_children if t.ParentID == c.ID)
        console.print(f"  - ID={c.ID:<5} {c.Name!r}  ({n_kids} child tag(s))")

    console.print(
        f"\n[bold]Tags inside default categories[/bold] to tombstone: {len(default_cat_tags)}"
    )
    by_parent: dict[str, list[str]] = {}
    for t in default_cat_tags:
        by_parent.setdefault(t.ParentID, []).append(t.Name or "")
    for pid, names in by_parent.items():
        parent_name = name_by_id.get(pid, pid)
        console.print(f"  - {parent_name} ({len(names)}): " + ", ".join(repr(n) for n in names))

    console.print(f"\n[bold]djmdSongMyTag rows[/bold] (alive) to tombstone: {len(song_links)}")

    if ensure_defaults:
        console.print(f"\n[bold]Default categories[/bold] to re-create: {len(defaults_to_create)}")
        for cid, seq, name in defaults_to_create:
            console.print(f"  - ID={cid:<5} Seq={seq} {name!r}")
        console.print(f"\n[bold]Default categories[/bold] to repair: {len(defaults_to_repair)}")
        for row, fixes in defaults_to_repair:
            fix_str = ", ".join(f"{k}={v!r}" for k, v in fixes.items())
            console.print(f"  - ID={row.ID:<5} Name={row.Name!r}  set: {fix_str}")


def _bump_local_usn_above_cloud(db, *, n_pending: int) -> None:
    """Raise ``localUpdateCount`` so subsequent rb_local_usn assignments outrank the cloud's USN.

    Rekordbox's cloud sync compares each row's USN to its server-side counterpart when
    deciding whether to accept a local change. The cloud's authoritative max USN is
    stored in the ``lastUpdateCount`` agentRegistry row. If our local USN is below it,
    the cloud server can treat our pushes as based on stale state and silently revert
    them on next merge.

    Bumping ``localUpdateCount`` to ``lastUpdateCount + buffer`` (with ``buffer`` large
    enough to also cover the pending tombstones) means each new tombstone's
    ``rb_local_usn`` exceeds anything the cloud has stamped on that row, so the cloud
    server should accept the deletion as the new latest state.

    This is a sledgehammer; only call it when ordinary tombstoning has been observed
    to revert. Bumping the USN doesn't damage anything on its own — the counter is
    monotonic and rekordbox will keep incrementing from wherever we land.
    """
    local_reg = db.get_agent_registry(registry_id="localUpdateCount")
    cloud_reg = db.get_agent_registry(registry_id="lastUpdateCount")
    local_usn = local_reg.int_1 or 0
    cloud_usn = cloud_reg.int_1 or 0
    # Buffer covers the pending tombstones plus headroom for future cloud touches that
    # might land between our commit and the next rekordbox open.
    target = cloud_usn + n_pending + 1000
    if local_usn >= target:
        console.print(
            f"[dim]localUpdateCount ({local_usn}) already exceeds cloud target ({target}); "
            "skipping bump.[/dim]"
        )
        return
    console.print(
        f"[bold]Bumping localUpdateCount[/bold]: {local_usn} → {target} "
        f"(cloud lastUpdateCount={cloud_usn}, pending={n_pending}, +1000 buffer)"
    )
    with db.registry.disabled():
        local_reg.int_1 = target


def _print_force_cloud_usn_plan(db, tag_ids_to_delete, song_links_to_delete) -> None:
    local_reg = db.get_agent_registry(registry_id="localUpdateCount")
    cloud_reg = db.get_agent_registry(registry_id="lastUpdateCount")
    local_usn = local_reg.int_1 or 0
    cloud_usn = cloud_reg.int_1 or 0
    n = len(tag_ids_to_delete) + len(song_links_to_delete)
    target = cloud_usn + n + 1000
    console.print(
        f"\n[bold]--force-cloud-usn[/bold]: would bump localUpdateCount {local_usn} → {target} "
        f"(cloud lastUpdateCount={cloud_usn})"
    )


def _commit(db, *, allow_rekordbox_open: bool) -> None:
    """Commit with pyrekordbox safety checks unless explicit override was requested."""
    if not allow_rekordbox_open:
        db.commit()
        return

    # pyrekordbox blocks db.commit() whenever rekordbox is running; this explicit
    # escape hatch mirrors commit(autoinc=True) but skips only that process check.
    db.registry.autoincrement_local_update_count(set_row_usn=True)
    db.session.commit()
    db.registry.clear_buffer()


def _chunks(seq: list[str], n: int):
    """Yield successive n-sized chunks from seq."""
    for i in range(0, len(seq), n):
        yield seq[i : i + n]
