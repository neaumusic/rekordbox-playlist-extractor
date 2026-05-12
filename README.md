# rbx

Personal utilities for wrangling Rekordbox 7's database — sorting playlists by `DateAdded`, cleaning up MyTags, shuffling genres for iOS, etc.

This is a WIP toolbox I built for my own DJ workflow. It works for me but it's rough around the edges. If you want to use or adapt something, clone the repo and poke around (opening it in Cursor and asking the AI about it is probably the fastest way to understand what's going on).

Uses [`pyrekordbox`](https://github.com/dylanljones/pyrekordbox) for direct `master.db` access. Legacy TypeScript scripts live in `legacy-ts/`.

## Setup

```bash
brew install uv
uv sync
uv run rbx --help
```

## Safety

Every destructive command:

- refuses to run while rekordbox is open (SQLite lock),
- requires a recent `master.db` backup under `./backups/`,
- accepts `--dry-run`.

Run `rbx backup` first.

## Commands

- **`backup`** — snapshot `master.db` (+ WAL/SHM) to `./backups/<timestamp>/`.
- **`extract-playlists`** — dump every playlist from `rekordbox.xml` to `playlists/*.m3u8`.
- **`expand-dates`** — give every track a unique `DateAdded` at minute precision, preserving natural order. Idempotent.
- **`sort --from <m3u8>`** — rewrite `DateAdded` so tracks sort in the m3u8's order. Last line = anchor; all inserted / cascade backward at that time (it will be the newest entry).
- **`import <dir> --sort name|mtime|ctime`** — capture a folder as an m3u8. Pair with drag-drop into rekordbox + `rbx sort`.
- **`shuffle-genres`** — assign every alive track a random Genre name for iOS shuffle workaround.
- **`clean-mytags`** — tombstone junk MyTag categories/tags (e.g. Lexicon leftovers). Cloud-friendly.
- **`purge-tombstones`** — hard-delete tombstones + orphan rows. Only safe after a full cloud wipe.

## Quick examples

```bash
# Sort a playlist by a hand-edited m3u8
rbx extract-playlists
rbx backup
rbx sort --from "playlists/MY SET.m3u8" --dry-run
rbx sort --from "playlists/MY SET.m3u8"

# Import a folder
rbx import ~/Downloads/incoming --sort ctime
# drag-drop into rekordbox, quit, then:
rbx backup && rbx sort --from ~/Downloads/incoming/incoming.m3u8

# Clean up MyTags
rbx backup && rbx clean-mytags --dry-run && rbx clean-mytags

# Shuffle genres for iOS
rbx backup && rbx shuffle-genres
```

## Dev

```bash
uv run pytest
uv run ruff check .
uv run ruff format .
```
