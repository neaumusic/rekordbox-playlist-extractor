"""Lazy Rekordbox6Database factory and small helpers used by destructive commands.

In pyrekordbox 0.4.x the class is `Rekordbox6Database`, despite the docs sometimes
calling it `MasterDatabase`. It works for both rekordbox 6 and 7.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Literal

from rbx.safety import assert_rekordbox_closed

if TYPE_CHECKING:
    from pyrekordbox import Rekordbox6Database


NamedField = Literal["genre", "album"]


def open_db(*, allow_rekordbox_open: bool = False) -> Rekordbox6Database:
    """Open the rekordbox 6/7 master.db. pyrekordbox auto-discovers the path and key.

    Refuses to open while rekordbox is running unless ``allow_rekordbox_open`` is
    explicitly set. pyrekordbox technically lets you read the DB with rekordbox
    open, but rekordbox holds its own SQLite handle and reading while it mutates
    state can yield inconsistent results — and any later commit() may collide.

    Also suppresses pyrekordbox's per-playlist "not found in masterPlaylists6.xml"
    warnings that flood stderr during commit. Pass `--verbose` to see them again.
    """
    from pyrekordbox import Rekordbox6Database

    if not allow_rekordbox_open:
        assert_rekordbox_closed()
    logging.getLogger("pyrekordbox.db6.database").setLevel(logging.ERROR)
    return Rekordbox6Database()


def named_field_spec(field: NamedField) -> tuple[str, str, str]:
    """Return ``(display_label, content_name_attr, content_id_attr)`` for a
    Genre/Album style field. Used by sort/shuffle/expand to keep the same
    naming conventions when writing to either the GenreID or AlbumID column.
    """
    if field == "genre":
        return "Genre", "GenreName", "GenreID"
    if field == "album":
        return "Album", "AlbumName", "AlbumID"
    raise ValueError(f"unsupported named field: {field!r}")


def existing_named_rows_by_name(db: Rekordbox6Database, field: NamedField) -> dict[str, Any]:
    """Index every existing genre/album row by Name (alive AND tombstoned).

    Tombstoned rows match by name and get resurrected rather than duplicated:
    ``db.add_genre/add_album`` would reject the duplicate name otherwise.
    """
    rows = list(db.get_genre()) if field == "genre" else list(db.get_album())
    return {r.Name: r for r in rows if r.Name is not None}


def get_or_create_named_row(
    db: Rekordbox6Database,
    field: NamedField,
    name: str,
    cache: dict[str, Any],
) -> Any:
    """Return an alive genre/album row for ``name``, creating or resurrecting one.

    ``cache`` is the dict returned by :func:`existing_named_rows_by_name` and is
    updated in place with any newly-created rows so subsequent lookups in the
    same run reuse them.
    """
    existing = cache.get(name)
    if existing is None:
        new_row = db.add_genre(name) if field == "genre" else db.add_album(name)
        cache[name] = new_row
        return new_row
    if existing.rb_local_deleted:
        existing.rb_local_deleted = 0
    return existing
