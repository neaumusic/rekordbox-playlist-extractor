"""rbx restore - copy a backup'd master.db (+ WAL/SHM) back over the live one.

Mirror of `rbx backup`: our backups are raw `shutil.copy2` snapshots of the
SQLite files rekordbox actually runs against, so "restore" is just a reverse
copy. The only subtleties:

  1. Move (not delete) the live files aside with a `.replaced-<timestamp>`
     suffix so a botched restore is recoverable. The user can clean these up
     once they've confirmed rekordbox opens cleanly.
  2. Always remove any stale `master.db-wal`/`-shm` from the live directory
     before copying, even if our backup doesn't have them. A leftover WAL
     written against the *previous* master.db would corrupt the restored one
     on next open.

CLOUD CAVEAT: if you've used rekordbox Cloud since the backup, opening
rekordbox after a local restore can let the cloud push its newer state back
over the restored library. Sign out of cloud first if you're recovering from
a real disaster.
"""

from __future__ import annotations

import shutil
from datetime import datetime
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console

from rbx.commands.backup import resolve_db_path
from rbx.safety import assert_rekordbox_closed

console = Console()

_SUFFIXES: tuple[str, ...] = ("", "-wal", "-shm")


def run(
    from_: Annotated[
        str | None,
        typer.Option(
            "--from",
            help=(
                "Backup to restore. Either a timestamp dir name under --backups-dir "
                "(e.g. 2026-05-17_12-39-36) or a path to any directory containing "
                "master.db. Defaults to the newest backup under --backups-dir."
            ),
        ),
    ] = None,
    backups_dir: Annotated[
        Path,
        typer.Option("--backups-dir", help="Backups root directory."),
    ] = Path("backups"),
    list_only: Annotated[
        bool,
        typer.Option("--list", help="List available backups and exit."),
    ] = False,
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", help="Show the plan without copying anything."),
    ] = False,
    skip_running_check: Annotated[
        bool,
        typer.Option(
            "--allow-running",
            help=(
                "Skip the rekordbox-running check (NOT recommended; restore will "
                "fight the live SQLite connection)."
            ),
        ),
    ] = False,
    yes: Annotated[
        bool,
        typer.Option("--yes", "-y", help="Skip the confirmation prompt."),
    ] = False,
) -> None:
    """Restore master.db (+ WAL/SHM) from a `rbx backup` snapshot into rekordbox's live location."""
    if list_only:
        _list_backups(backups_dir)
        return

    if not skip_running_check:
        assert_rekordbox_closed()

    live_db = resolve_db_path()
    if live_db is None:
        console.print("[red]Could not locate a rekordbox installation (5/6/7).[/red]")
        raise typer.Exit(code=1)

    source_dir = _resolve_source(from_, backups_dir)
    source_db = source_dir / "master.db"
    if not source_db.exists():
        console.print(f"[red]No master.db inside {source_dir}.[/red]")
        raise typer.Exit(code=1)

    size_mb = source_db.stat().st_size / 1_000_000
    age = _human_age(datetime.fromtimestamp(source_db.stat().st_mtime))
    console.print(f"[bold]Live DB:[/bold]   {live_db}")
    console.print(f"[bold]Backup:[/bold]    {source_db} ({size_mb:.1f} MB, {age})")

    stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    plan: list[tuple[Path, Path | None, Path | None]] = []
    # (live_file, backup_file_to_copy_or_None, archive_dest_or_None)
    for suffix in _SUFFIXES:
        live = live_db.with_name(live_db.name + suffix)
        backup = source_dir / live.name
        archive = live.with_name(live.name + f".replaced-{stamp}") if live.exists() else None
        backup_to_copy = backup if backup.exists() else None
        if not live.exists() and not backup_to_copy:
            continue
        plan.append((live, backup_to_copy, archive))

    console.print("[bold]Plan:[/bold]")
    for live, backup, archive in plan:
        actions = []
        if archive is not None:
            actions.append(f"move aside -> {archive.name}")
        if backup is not None:
            actions.append(f"copy from backup {backup.name}")
        else:
            actions.append("(no backup file; live file will be removed only)")
        console.print(f"  - {live.name}: {'; '.join(actions)}")

    if dry_run:
        console.print("[yellow]--dry-run: nothing changed[/yellow]")
        return

    if not yes:
        typer.confirm("Proceed with restore?", abort=True)

    for live, _backup, archive in plan:
        if archive is not None:
            live.rename(archive)

    copied = 0
    for live, backup, _archive in plan:
        if backup is not None:
            shutil.copy2(backup, live)
            copied += 1

    console.print(
        f"[green]Restored {copied} file(s) into {live_db.parent}.[/green] "
        f"Old files preserved with .replaced-{stamp} suffix."
    )


def _resolve_source(from_: str | None, backups_dir: Path) -> Path:
    """Pick the backup directory to restore from."""
    if from_:
        candidate = Path(from_)
        if not (candidate / "master.db").exists():
            candidate = backups_dir / from_
        if not (candidate / "master.db").exists():
            console.print(
                f"[red]Could not find master.db at {from_} or {backups_dir / from_}.[/red]"
            )
            raise typer.Exit(code=1)
        return candidate

    if not backups_dir.exists():
        console.print(f"[red]No backups directory at {backups_dir}.[/red] Run `rbx backup` first.")
        raise typer.Exit(code=1)
    candidates = sorted(backups_dir.glob("*/master.db"), key=lambda p: p.stat().st_mtime)
    if not candidates:
        console.print(f"[red]No backups found under {backups_dir}.[/red] Run `rbx backup` first.")
        raise typer.Exit(code=1)
    return candidates[-1].parent


def _list_backups(backups_dir: Path) -> None:
    if not backups_dir.exists():
        console.print(f"[yellow]No backups directory at {backups_dir}.[/yellow]")
        return
    rows = sorted(backups_dir.glob("*/master.db"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not rows:
        console.print(f"[yellow]No backups under {backups_dir}.[/yellow]")
        return
    console.print(f"[bold]Backups under {backups_dir} (newest first):[/bold]")
    for db in rows:
        st = db.stat()
        size_mb = st.st_size / 1_000_000
        age = _human_age(datetime.fromtimestamp(st.st_mtime))
        console.print(f"  {db.parent.name}   {size_mb:>7.1f} MB   {age}")


def _human_age(when: datetime) -> str:
    delta = datetime.now() - when
    seconds = int(delta.total_seconds())
    if seconds < 60:
        return f"{seconds}s ago"
    if seconds < 3600:
        return f"{seconds // 60}m ago"
    if seconds < 86400:
        return f"{seconds // 3600}h ago"
    return f"{seconds // 86400}d ago"
