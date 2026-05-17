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


def resolve_db_path() -> Path | None:
    """Find master.db across rekordbox 7/6/5, in that order."""
    for section in ("rekordbox7", "rekordbox6", "rekordbox5"):
        conf = get_config(section)
        if conf and conf.get("db_path"):
            return Path(conf["db_path"])
    return None


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

    db_path = resolve_db_path()
    if db_path is None:
        console.print("[red]Could not locate a rekordbox installation (5/6/7).[/red]")
        raise typer.Exit(code=1)
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
