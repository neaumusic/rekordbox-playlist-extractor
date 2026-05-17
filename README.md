# rbx

Personal utilities for wrangling Rekordbox 7's database — sorting playlists by `DateAdded`, cleaning up MyTags, shuffling genres for iOS, etc.

This is a WIP toolbox I built for my own DJ workflow. It works for me but it's rough around the edges. If you want to use or adapt something, clone the repo and poke around (opening it in Cursor and asking the AI about it is probably the fastest way to understand what's going on).

Uses [`pyrekordbox`](https://github.com/dylanljones/pyrekordbox) for direct `master.db` access. Legacy TypeScript scripts live in `legacy-ts/`.

## Setup

```bash
git submodule update --init --recursive
brew install uv
uv sync
uv run rbx --help
```

## Safety

Every destructive command by default:

- refuses to run while rekordbox is open (SQLite lock),
- requires a recent `master.db` backup under `./backups/`,
- accepts `--dry-run`.

Run `rbx backup` first.

`clean-mytags` also supports `--allow-rekordbox-open` as an explicit escape hatch
when cloud keeps resurrecting MyTag rows and you need to tombstone while the app
is running.

### Restore

Backups are raw `master.db` copies (not rekordbox's own `.backup` bundle format),
so restoring is just a reverse file copy:

```bash
rbx restore --list                 # see what's available
rbx restore --dry-run              # preview restoring the newest backup
rbx restore                        # restore the newest backup (with confirmation)
rbx restore --from 2026-05-17_12-39-36
```

The live `master.db` (and `-wal`/`-shm`) get moved aside as
`master.db.replaced-<timestamp>` instead of deleted, so a botched restore is
recoverable. Delete those once rekordbox reopens cleanly.

**Cloud caveat:** if you've used rekordbox Cloud Library Sync since the backup,
opening rekordbox after a local restore may let cloud push its newer state back
over the restored library. For a real disaster scenario, sign out of cloud
before launching rekordbox post-restore.

## Commands

- **`backup`** — snapshot `master.db` (+ WAL/SHM) to `./backups/<timestamp>/`.
- **`restore [--from <timestamp>]`** — copy a backup back over rekordbox's live `master.db`. Defaults to the newest backup; pass `--list` to see what's available, `--dry-run` to preview, `--from <name-or-path>` to pick one. Live files are moved aside as `master.db.replaced-<timestamp>` rather than deleted.
- **`extract-playlists`** — dump every playlist from `rekordbox.xml` to `playlists/*.m3u8`.
- **`expand-dates [--target ...]`** — give every track a unique value in the chosen field, preserving natural (StockDate, created_at) order. Default target `stockdate` is the original minute-precision DateAdded behavior. `--target year` assigns sequential `ReleaseYear` (newest=0). `--target genre`/`--target album` assign zero-padded numeric names. Idempotent.
- **`sort --from <m3u8> [--target ...]`** — rewrite a chosen field so tracks sort in the m3u8's order. Default target `stockdate` (minute precision; iOS-broken after sync). `--target year` is the iOS-friendly recommendation. Last line of m3u8 = anchor (newest).
- **`import <dir> --sort name|mtime|ctime`** — capture a folder as an m3u8. Pair with drag-drop into rekordbox + `rbx sort`.
- **`shuffle [--target ...]`** — assign every alive track a random value in one or more fields (default `genre`; pass repeated `--target` to populate `album`/`year` too with independent permutations). The original `shuffle-genres` name still works as an alias.
- **`clean-mytags`** — tombstone junk MyTag categories/tags (e.g. Lexicon leftovers). Cloud-friendly.
- **`purge-tombstones`** — hard-delete tombstones + orphan rows. Only safe after a full cloud wipe.

### iOS sort caveat

iOS rekordbox ignores the time portion of `DateAdded` after sync, so the default
`sort --target stockdate` collides on day and iOS falls back to artist-name
order. Two ways out:

- **Recommended:** `sort --target year` writes `ReleaseYear` (0..9999, newest = 0). iOS sorts numerically. Libraries beyond 10K tracks share `9999` for the older overflow.
- **Destructive but uses the date field:** `sort --target stockdate --resolution day` cascades each m3u track to its own day. Spreads a 5K-track playlist across ~14 years of `DateAdded`, so most users prefer `--target year`.

`--target genre` and `--target album` work the same way as `year` but write
zero-padded numeric Genre/Album names; pick one if you need to keep
`ReleaseYear` intact.

## Quick examples

```bash
# Sort a playlist by a hand-edited m3u8 (default StockDate, fine on desktop rekordbox)
rbx extract-playlists
rbx backup
rbx sort --from "playlists/MY SET.m3u8" --dry-run
rbx sort --from "playlists/MY SET.m3u8"

# Same sort but iOS-friendly: write ReleaseYear instead
rbx backup && rbx sort --from "playlists/MY SET.m3u8" --target year

# Or stamp a unique ReleaseYear on every alive track in StockDate order
rbx backup && rbx expand-dates --target year

# Import a folder
rbx import ~/Downloads/incoming --sort ctime
# drag-drop into rekordbox, quit, then:
rbx backup && rbx sort --from ~/Downloads/incoming/incoming.m3u8

# Clean up MyTags
rbx backup && rbx clean-mytags --dry-run && rbx clean-mytags

# Shuffle for iOS — populate genre AND year with independent permutations
rbx backup && rbx shuffle --target genre --target year
```

## Dev

```bash
uv run pytest
uv run ruff check .
uv run ruff format .
```
