"""m3u8 read/write helpers used by extract-playlists, import, and sort."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class M3uEntry:
    duration_seconds: int | None
    title: str | None
    location: str


def write_m3u(path: Path, entries: list[M3uEntry]) -> None:
    """Write an extended m3u8 with #EXTM3U header and #EXTINF lines."""
    path.parent.mkdir(parents=True, exist_ok=True)
    lines: list[str] = ["#EXTM3U"]
    for e in entries:
        if e.duration_seconds is not None and e.title is not None:
            lines.append(f"#EXTINF:{e.duration_seconds},{e.title}")
        lines.append(e.location)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def read_m3u(path: Path) -> list[str]:
    """Read an m3u/m3u8 file and return the ordered list of track locations.

    Comment lines (starting with `#`) and blank lines are ignored. Returned paths
    are passed through unchanged - callers handle decoding/normalization.
    """
    locations: list[str] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        locations.append(line)
    return locations
