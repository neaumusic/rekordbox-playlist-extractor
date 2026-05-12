"""Lazy Rekordbox6Database factory and small helpers used by destructive commands.

In pyrekordbox 0.4.x the class is `Rekordbox6Database`, despite the docs sometimes
calling it `MasterDatabase`. It works for both rekordbox 6 and 7.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from rbx.safety import assert_rekordbox_closed

if TYPE_CHECKING:
    from pyrekordbox import Rekordbox6Database


def open_db() -> Rekordbox6Database:
    """Open the rekordbox 6/7 master.db. pyrekordbox auto-discovers the path and key.

    Always refuses to open while rekordbox is running. pyrekordbox technically lets
    you read the DB with rekordbox open, but rekordbox holds its own SQLite handle
    and reading while it mutates state can yield inconsistent results — and any
    later commit() would collide. Doing the check here means there's no path
    (commands, scripts, ad-hoc inspection) that can bypass it.

    Also suppresses pyrekordbox's per-playlist "not found in masterPlaylists6.xml"
    warnings that flood stderr during commit. Pass `--verbose` to see them again.
    """
    from pyrekordbox import Rekordbox6Database

    assert_rekordbox_closed()
    logging.getLogger("pyrekordbox.db6.database").setLevel(logging.ERROR)
    return Rekordbox6Database()
