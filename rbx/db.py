"""Lazy Rekordbox6Database factory and small helpers used by destructive commands.

In pyrekordbox 0.4.x the class is `Rekordbox6Database`, despite the docs sometimes
calling it `MasterDatabase`. It works for both rekordbox 6 and 7.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pyrekordbox import Rekordbox6Database


def open_db() -> Rekordbox6Database:
    """Open the rekordbox 6/7 master.db. pyrekordbox auto-discovers the path and key.

    Suppresses pyrekordbox's per-playlist "not found in masterPlaylists6.xml" warnings
    that flood stderr during commit. Those warnings are emitted because pyrekordbox
    expects playlists to exist in that auxiliary file, but rekordbox 7 doesn't keep
    every playlist there. They're harmless for our use cases (cue deletion, StockDate
    rewrites). Pass `--verbose` to see them again.
    """
    from pyrekordbox import Rekordbox6Database

    logging.getLogger("pyrekordbox.db6.database").setLevel(logging.ERROR)
    return Rekordbox6Database()
