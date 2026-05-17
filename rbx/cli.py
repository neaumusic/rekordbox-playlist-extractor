"""Top-level Typer app. Subcommands wire in from rbx.commands.*."""

from __future__ import annotations

import typer

from rbx.commands import (
    backup,
    clean_mytags,
    expand_dates,
    extract_playlists,
    import_cmd,
    purge_tombstones,
    restore,
    shuffle,
    sort,
)

app = typer.Typer(
    name="rbx",
    help="Rekordbox library CLI: playlists, hot cues, DateAdded sorting.",
    no_args_is_help=True,
    add_completion=False,
)

app.command("backup")(backup.run)
app.command("restore")(restore.run)
app.command("extract-playlists")(extract_playlists.run)
app.command("shuffle")(shuffle.run)
# Pre-multi-target name kept as an alias so existing muscle memory still works.
app.command("shuffle-genres", hidden=True)(shuffle.run)
app.command("expand-dates")(expand_dates.run)
app.command("sort")(sort.run)
app.command("import")(import_cmd.run)
app.command("purge-tombstones")(purge_tombstones.run)
app.command("clean-mytags")(clean_mytags.run)


if __name__ == "__main__":
    app()
