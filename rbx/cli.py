"""Top-level Typer app. Subcommands wire in from rbx.commands.*."""

from __future__ import annotations

import typer

from rbx.commands import (
    backup,
    expand_dates,
    extract_playlists,
    import_folder,
    randomize_genres,
    reorder,
)

app = typer.Typer(
    name="rbx",
    help="Rekordbox library CLI: playlists, hot cues, DateAdded reordering, folder import.",
    no_args_is_help=True,
    add_completion=False,
)

app.command("backup")(backup.run)
app.command("extract-playlists")(extract_playlists.run)
app.command("randomize-genres")(randomize_genres.run)
app.command("expand-dates")(expand_dates.run)
app.command("reorder")(reorder.run)
app.command("import-folder")(import_folder.run)


if __name__ == "__main__":
    app()
