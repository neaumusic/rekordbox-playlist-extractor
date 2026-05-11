"""rbx backup - timestamped copy of rekordbox's master.db (and WAL/SHM sidecars)."""

from __future__ import annotations

import shutil
from datetime import datetime
from pathlib import Path
from typing import Annotated

import typer
from pyrekordbox.config import get_config
from rich.console import Console

from rbx.safety import assert_rekordbox_closed

console = Console()


def run(
    out_dir: Annotated[
        Path,
        typer.Option("--out", help="Backups root directory."),
    ] = Path("backups"),
    skip_running_check: Annotated[
        bool,
        typer.Option(
            "--allow-running",
            help="Skip the rekordbox-running check (NOT recommended; backup may be inconsistent).",
        ),
    ] = False,
) -> None:
    """Snapshot master.db (+ WAL/SHM sidecars) into ./backups/<timestamp>/."""
    if not skip_running_check:
        assert_rekordbox_closed()

    db_path = Path(get_config("rekordbox6", "db_path"))
    if not db_path.exists():
        console.print(f"[red]master.db not found at {db_path}.[/red]")
        raise typer.Exit(code=1)

    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    target_dir = out_dir / timestamp
    target_dir.mkdir(parents=True, exist_ok=True)

    copied: list[Path] = []
    for suffix in ("", "-wal", "-shm"):
        src = db_path.with_name(db_path.name + suffix)
        if not src.exists():
            continue
        dst = target_dir / src.name
        shutil.copy2(src, dst)
        copied.append(dst)

    total_bytes = sum(p.stat().st_size for p in copied)
    console.print(
        f"[green]Backed up {len(copied)} file(s) "
        f"({total_bytes / 1_000_000:.1f} MB) to {target_dir}[/green]"
    )
    for p in copied:
        console.print(f"  - {p.name}")
