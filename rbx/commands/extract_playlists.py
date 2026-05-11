"""rbx extract-playlists - read rekordbox.xml and write m3u8 files.

Uses the XML rather than master.db so that smart playlists are included (rekordbox
evaluates them at XML-export time; the DB only stores their conditions).
"""

from __future__ import annotations

import random
import re
import shutil
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console

from rbx.m3u import M3uEntry, write_m3u
from rbx.xml import load_xml

console = Console()

FOLDER_DELIMITER = " "
INVALID_NAME_CHARS = re.compile(r'[/\\?%*:|"<>]')


def run(
    out_dir: Annotated[
        Path,
        typer.Option("--out", help="Output directory for m3u8 files."),
    ] = Path("playlists"),
    xml_path: Annotated[
        Path,
        typer.Option("--xml", help="Path to rekordbox.xml export."),
    ] = Path("rekordbox.xml"),
    shuffle: Annotated[
        bool,
        typer.Option("--shuffle", help="Shuffle tracks within each playlist."),
    ] = False,
    clean: Annotated[
        bool,
        typer.Option("--clean/--no-clean", help="Delete the output directory before writing."),
    ] = True,
) -> None:
    """Extract every playlist (including smart playlists) to <out>/<folder>/<name>.m3u8."""
    if not xml_path.exists():
        console.print(
            f"[red]{xml_path} not found.[/red] Export rekordbox.xml from "
            "rekordbox (File > Export Collection in xml format) first."
        )
        raise typer.Exit(code=1)

    if clean and out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    xml = load_xml(xml_path)

    written = 0
    for node in xml.root_playlist_folder.get_playlists():
        written += _walk(node, xml=xml, prefix="", out_dir=out_dir, shuffle=shuffle)

    console.print(f"[green]Wrote {written} playlist(s) to {out_dir}[/green]")


def _walk(node, xml, prefix: str, out_dir: Path, shuffle: bool) -> int:
    name = _sanitize(node.name)
    if node.is_folder:
        next_prefix = f"{prefix}{name}{FOLDER_DELIMITER}" if prefix else f"{name}{FOLDER_DELIMITER}"
        console.print(f"[dim]folder[/dim] {prefix}{name}/")
        return sum(_walk(c, xml, next_prefix, out_dir, shuffle) for c in node.get_playlists())

    filename = f"{prefix}{name}.m3u8" if prefix else f"{name}.m3u8"
    target = out_dir / filename
    _write_playlist(node, xml, target, shuffle=shuffle)
    console.print(f"  -> {target.name}")
    return 1


def _write_playlist(playlist, xml, target: Path, shuffle: bool) -> None:
    track_keys = list(playlist.get_tracks())
    tracks = [_resolve_track(xml, key, key_type=playlist.key_type) for key in track_keys]
    tracks = [t for t in tracks if t is not None]

    if shuffle:
        random.shuffle(tracks)
    else:
        tracks.sort(key=lambda t: str(t["DateAdded"] or ""), reverse=True)

    write_m3u(target, [_to_entry(t) for t in tracks])


def _resolve_track(xml, key, key_type: str):
    try:
        if key_type == "TrackID":
            return xml.get_track(TrackID=key)
        return xml.get_track(Location=key)
    except Exception:
        return None


def _to_entry(track) -> M3uEntry:
    artist = track["Artist"] or ""
    title = track["Name"] or ""
    raw_location = track["Location"] or ""
    location = _to_file_uri(raw_location)
    duration = track["TotalTime"]
    try:
        duration_int = int(duration) if duration is not None else None
    except (TypeError, ValueError):
        duration_int = None
    return M3uEntry(
        duration_seconds=duration_int,
        title=f"{artist} - {title}",
        location=location,
    )


def _to_file_uri(raw: str) -> str:
    """Normalize pyrekordbox's Location into the legacy `file://localhost/<abs path>` form."""
    if not raw:
        return ""
    if raw.startswith("file://"):
        return raw
    path = raw if raw.startswith("/") else f"/{raw}"
    return f"file://localhost{path}"


def _sanitize(name: str) -> str:
    return INVALID_NAME_CHARS.sub("-", name)
