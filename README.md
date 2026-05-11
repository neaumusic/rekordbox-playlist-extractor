# rbx — Rekordbox Library CLI

Python CLI for working with rekordbox 7's `master.db` (with an XML round-trip kept for the iOS-sync genre workaround).

Replaces the original TypeScript scripts (kept in `legacy-ts/` for reference).

## What it does

- **`extract-playlists`** — walk the playlist tree in `rekordbox.xml` and write every playlist (including smart playlists) to `playlists/*.m3u8`.
- **`randomize-genres`** — copy the XML and overwrite every track's Genre with a random string. Re-import in rekordbox to bypass iOS rekordbox's lack of native shuffle (sort by Genre = random playback).
- **`strip-hot-cues`** — delete hot cue rows from `master.db`. The baked-in track Colour stays. Useful for cleaning up legacy "Track Color" cues.
- **`expand-dates`** — one-time migration: give every track a unique `DateAdded` timestamp at minute precision within its existing day. Required before `reorder`.
- **`reorder`** — rewrite `DateAdded` on the tracks listed in an m3u8 so they sort in that order. Anchors to the input's existing date range when possible; expands symmetrically when not.
- **`import-folder`** — add or re-stamp a folder of audio files with sequential `DateAdded` timestamps from now(). Choose sort order with `--sort name|mtime|ctime` and `--reverse`.
- **`backup`** — snapshot `master.db` (+ WAL/SHM sidecars) to `./backups/<timestamp>/`.

## Install

One-time:

```bash
brew install uv
```

In this repo:

```bash
uv sync
```

That creates `.venv/` and installs everything from `uv.lock`. Run anything with:

```bash
uv run rbx --help
```

## Safety

**ALWAYS back up rekordbox's library before running write commands.**

Two layers of protection are baked in:

1. Every destructive command refuses to run while rekordbox is open (it holds the SQLite lock).
2. Every destructive command requires a `master.db` backup younger than 24 hours under `./backups/`. Either run `rbx backup` first, or pass `--no-backup-check` when you know what you're doing.

Use `--dry-run` on any destructive command to preview changes without committing.

## Typical workflows

### First-time migration (run once)

```bash
rbx backup
rbx expand-dates --dry-run        # review the changes
rbx expand-dates                  # commit
```

This gives every track a unique within-day timestamp, which is the foundation for predictable sort-by-DateAdded behavior.

### Reorder a set of tracks via m3u8

```bash
rbx extract-playlists                            # write rekordbox.xml's playlists to ./playlists/
# edit "playlists/MY SET.m3u8" by reordering its lines in any text editor
rbx backup
rbx reorder --from "playlists/MY SET.m3u8" --dry-run
rbx reorder --from "playlists/MY SET.m3u8"
```

After committing, reopen rekordbox — sort by Date Added — the tracks will be in your m3u order. Cloud sync to iOS preserves the new order because it's the underlying `DateAdded`, not a per-playlist position.

### Import a numbered folder in order

```bash
rbx backup
rbx import-folder ~/Downloads/incoming --sort name --dry-run
rbx import-folder ~/Downloads/incoming --sort name
```

Then in rekordbox, right-click the new tracks > **Reload Tag** to populate metadata (rekordbox does this from the file's ID3/AIFF tags).

### Genre randomization for iOS shuffle

```bash
# In rekordbox: File > Export Collection in xml format > rekordbox.xml
rbx randomize-genres
# In rekordbox: File > Import (rekordbox-modified.xml). Sync to iOS. Sort by Genre = shuffle.
```

### Strip baked-in "Track Color" hot cues

```bash
rbx backup
rbx strip-hot-cues --comment "Track Color" --dry-run
rbx strip-hot-cues --comment "Track Color"
```

The track's Colour itself is preserved — only the cue rows are deleted.

## Caveats

- **Rekordbox 7 only**: `pyrekordbox` is tested against rekordbox 7.0.9 and earlier. A newer build may break the auto-key-extraction; pin pyrekordbox or wait for an update if so.
- **`StockDate` storage**: rekordbox stores DateAdded as a `VARCHAR` string. We write `'YYYY-MM-DD HH:MM:SS'` which sorts lexicographically the way you want, but rekordbox's UI may or may not gracefully display the time portion. Verify on a backup before committing to it.
- **Smart playlists** are evaluated by rekordbox at XML-export time; `extract-playlists` reads them from XML for that reason. The DB only stores the conditions.
- **Cloud sync**: any DB write triggers a cloud sync next time you open rekordbox. Keep a backup until you've confirmed the iOS app reflects the change as expected.

## Development

```bash
uv run pytest          # unit tests (dates module)
uv run ruff check .    # lint
uv run ruff format .   # format
```

## Project layout

```
rbx/
  cli.py                # typer entrypoint
  db.py                 # Rekordbox6Database factory
  xml.py                # RekordboxXml loader
  m3u.py                # m3u8 read/write
  dates.py              # pure timestamp algorithms (covered by tests)
  safety.py             # rekordbox-running + recent-backup guards
  commands/
    backup.py
    extract_playlists.py
    randomize_genres.py
    strip_hot_cues.py
    expand_dates.py
    reorder.py
    import_folder.py
tests/
  test_dates.py
legacy-ts/              # original TypeScript scripts, kept for reference
```

## Roadmap

A TypeScript Electron / React Native GUI is planned. The CLI (`rbx`) is the foundational layer; the GUI will shell out to it.
