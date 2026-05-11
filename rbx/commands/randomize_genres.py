"""rbx randomize-genres - assign each track a random Genre value in a copy of the XML.

This is the iOS-rekordbox shuffle workaround: re-importing the modified XML lets you
sort by Genre on iOS to get effectively random playback order, which iOS rekordbox
otherwise has no way to do.
"""

from __future__ import annotations

import random
import shutil
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console

from rbx.xml import load_xml

console = Console()


def run(
    xml_path: Annotated[
        Path,
        typer.Option("--xml", help="Path to source rekordbox.xml export."),
    ] = Path("rekordbox.xml"),
    out_path: Annotated[
        Path,
        typer.Option("--out", help="Path to write the modified XML."),
    ] = Path("rekordbox-modified.xml"),
    seed: Annotated[
        int | None,
        typer.Option("--seed", help="Optional RNG seed for reproducibility."),
    ] = None,
) -> None:
    """Copy the XML and overwrite every track's Genre with a random string."""
    if not xml_path.exists():
        console.print(
            f"[red]{xml_path} not found.[/red] Export rekordbox.xml from rekordbox first."
        )
        raise typer.Exit(code=1)

    rng = random.Random(seed)

    shutil.copy2(xml_path, out_path)
    xml = load_xml(out_path)

    count = 0
    for i in range(xml.num_tracks):
        track = xml.get_track(i)
        track["Genre"] = str(rng.random())
        count += 1

    xml.save(path=str(out_path))
    console.print(f"[green]Randomized Genre on {count} tracks -> {out_path}[/green]")
