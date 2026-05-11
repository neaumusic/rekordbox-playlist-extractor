"""Thin wrapper around pyrekordbox.rbxml.RekordboxXml for the legacy XML round-trip flow."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pyrekordbox.rbxml import RekordboxXml


def load_xml(path: str | Path) -> RekordboxXml:
    from pyrekordbox.rbxml import RekordboxXml

    return RekordboxXml(str(path))
