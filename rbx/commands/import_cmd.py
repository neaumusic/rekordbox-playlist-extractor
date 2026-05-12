"""rbx import - write an m3u8 of a folder's audio files in a chosen order.

Step 1 of the import workflow. The CLI deliberately doesn't add tracks to the
rekordbox DB itself; pyrekordbox's `add_content` skips rekordbox's real import
pipeline (analysis queue, cloud-storage detection, tag reading) and produces
second-class rows. So we only capture the desired track order here, and leave
the actual import to rekordbox.

  1. rbx import <folder> [--sort ctime]
  2. Drag-drop the folder into rekordbox so it does a proper import.
  3. Quit rekordbox, then `rbx sort --from <folder>/<folder>.m3u8` to restamp
     StockDates so the tracks sort in the order this playlist captured.

No rekordbox database is touched here; this is a pure file-system operation.
"""

from __future__ import annotations

import unicodedata
from enum import StrEnum
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console

from rbx.m3u import M3uEntry, write_m3u

console = Console()

DEFAULT_EXTENSIONS = {".aiff", ".aif", ".mp3", ".wav", ".flac", ".m4a", ".aac", ".ogg"}


class SortBy(StrEnum):
    NAME = "name"
    MTIME = "mtime"
    CTIME = "ctime"


def run(
    folder: Annotated[Path, typer.Argument(help="Folder of audio files to capture.")],
    out: Annotated[
        Path | None,
        typer.Option(
            "--out",
            help="Override the m3u8 output path. Defaults to <folder>/<folder>.m3u8.",
        ),
    ] = None,
    sort: Annotated[
        SortBy,
        typer.Option("--sort", help="Sort key controlling track order in the playlist."),
    ] = SortBy.NAME,
    reverse: Annotated[
        bool,
        typer.Option("--reverse", help="Reverse the sort order."),
    ] = False,
    recursive: Annotated[
        bool,
        typer.Option("--recursive/--no-recursive", help="Recurse into subdirectories."),
    ] = False,
) -> None:
    """Capture a folder's tracks (in chosen order) as an m3u8 alongside the audio."""
    if not folder.is_dir():
        console.print(f"[red]{folder} is not a directory.[/red]")
        raise typer.Exit(code=1)

    files = _list_audio_files(folder, recursive=recursive)
    if not files:
        console.print(f"[yellow]No audio files found in {folder}.[/yellow]")
        raise typer.Exit(code=1)

    files.sort(key=_sort_key(sort), reverse=reverse)

    out_path = out if out is not None else folder / f"{folder.name}.m3u8"
    entries = [
        M3uEntry(
            duration_seconds=None,
            title=None,
            location=unicodedata.normalize("NFC", str(f.resolve())),
        )
        for f in files
    ]
    write_m3u(out_path, entries)

    console.print(
        f"[green]Wrote {len(entries)} track(s) to {out_path}[/green] "
        f"(sorted by {sort.value}{', reversed' if reverse else ''})"
    )
    console.print(
        "[yellow]Sanity check[/yellow] (assuming rekordbox is sorted by Date Added "
        "descending, newest on top):"
    )
    console.print(
        f"  top of UI:    [cyan]{files[-1].name}[/cyan]  "
        "[dim](newest StockDate, last in m3u8)[/dim]"
    )
    console.print(
        f"  bottom of UI: [cyan]{files[0].name}[/cyan]  "
        "[dim](oldest StockDate, first in m3u8)[/dim]"
    )
    console.print(
        "\n[dim]Next: drag-drop the folder into rekordbox, quit rekordbox, then "
        f"run `rbx sort --from {_shell_quote(out_path)}` to restamp StockDates "
        "in this order.[/dim]"
    )


def _list_audio_files(folder: Path, recursive: bool) -> list[Path]:
    pattern = "**/*" if recursive else "*"
    return [
        p
        for p in folder.glob(pattern)
        if p.is_file() and p.suffix.lower() in DEFAULT_EXTENSIONS
    ]


def _sort_key(sort: SortBy):
    if sort is SortBy.NAME:
        return lambda p: p.name.lower()
    if sort is SortBy.MTIME:
        return lambda p: p.stat().st_mtime
    return lambda p: getattr(p.stat(), "st_birthtime", p.stat().st_ctime)


def _shell_quote(p: Path) -> str:
    s = str(p)
    return f'"{s}"' if " " in s else s
