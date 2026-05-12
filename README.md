# rbx — Rekordbox Library CLI

Python CLI for working with rekordbox 7's `master.db`. Most commands write the DB directly; `extract-playlists` still reads `rekordbox.xml` because that's the only way to get smart-playlist contents.

Replaces the original TypeScript scripts (kept in `legacy-ts/` for reference).

## What it does

- **`extract-playlists`** — walk the playlist tree in `rekordbox.xml` and write every playlist (including smart playlists) to `playlists/*.m3u8`.
- **`randomize-genres`** — assign every alive track a unique permuted Genre name (`"0000"`..`"N-1"` for N tracks, shuffled) directly in `master.db`. Bypasses iOS rekordbox's lack of native shuffle (sort by Genre = random playback). Re-runs reuse the same `djmdGenre` rows, so no tombstone churn from re-shuffling.
- **`expand-dates`** — give every track a unique `DateAdded` timestamp at minute precision, preserving the natural (StockDate, created_at) order — the order rekordbox's UI itself uses when sorting by Date Added. Anchors the latest tracks at their existing timestamps and pushes earlier colliders backward in time (across day boundaries if needed). Idempotent and safe to re-run. Recommended before `sort` so non-m3u tracks don't get rearranged by sub-resolution duplicates.
- **`sort`** — rewrite `DateAdded` on the tracks listed in an m3u8 so they sort in that order. Anchors on the m3u's last entry (it keeps its current date), packs the rest tightly behind it, and cascades any colliding non-m3u library tracks earlier so all dates remain globally unique. Non-m3u tracks outside the m3u-affected window are not touched.
- **`import`** — capture a folder of audio files as an m3u8 (written to `<folder>/<folder>.m3u8`), sorted by `name|mtime|ctime`. Pairs with a manual rekordbox drag-drop and `sort` to date-stamp the folder in a chosen order.
- **`purge-tombstones`** — hard-delete soft-deleted rows from `djmdContent` (+ their orphan child rows). Pass `--include-genres` for a one-time cleanup of unreferenced `djmdGenre` tombstones. Rekordbox keeps every deleted track and orphaned genre as a tombstone for cloud-sync reconciliation; on a heavily-churned library this can be 5–10× your real row count and is implicated in flaky cloud sync. **Only run after a full cloud wipe.**
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

This gives every track a unique timestamp, which is the foundation for predictable sort-by-DateAdded behavior. The algorithm is a **backward sweep**: tracks are sorted ascending by `(snapped StockDate, created_at, ContentID)`, then walked from the latest item back to the earliest. Each track keeps its snapped timestamp if it's strictly earlier than the previously-assigned one; otherwise it gets pushed back by one minute. Cascades may spill into the previous day — that's by design, because keeping the most recent tracks anchored at their real timestamps is more important than respecting day boundaries. Run `--dry-run` first; it'll tell you how many tracks (if any) will be pushed into a different day.

**Why three sort keys?** `StockDate` is what rekordbox displays as "Date Added" — but in practice it's stored at date-only precision, so same-day cohorts need a tiebreaker. The SQLite row's `created_at` column is millisecond-precise and matches the order rekordbox's UI itself uses. `ContentID` is essentially random (not import order) and is kept only as a deterministic last-resort tiebreaker for the rare case of identical `created_at` values.

### Sort a set of tracks via m3u8

```bash
rbx extract-playlists                            # write rekordbox.xml's playlists to ./playlists/
# edit "playlists/MY SET.m3u8" by reordering its lines in any text editor
rbx backup
rbx sort --from "playlists/MY SET.m3u8" --dry-run
rbx sort --from "playlists/MY SET.m3u8"
```

After committing, reopen rekordbox — sort by Date Added — the tracks will be in your m3u order. Cloud sync to iOS preserves the new order because it's the underlying `DateAdded`, not a per-playlist position.

**How it works:** `sort` anchors on the m3u's LAST entry (it keeps its current StockDate); the rest of the m3u is packed tightly behind it at one-minute intervals (oldest first). If any non-m3u library track happens to occupy one of those new slots, it's cascaded earlier (one minute at a time) until it finds a free slot. Non-m3u tracks outside the m3u-affected window are not touched at all.

**Order convention** (important to internalise — getting this backwards on a large playlist inverts your library): the FIRST entry in the m3u8 receives the OLDEST StockDate, the LAST entry the NEWEST (the anchor). Sorting Date Added ascending in rekordbox plays the playlist top-to-bottom; descending plays it bottom-to-top. `rbx sort` prints a first/last preview before committing — eyeball it.

**Picking the right anchor:** because the last m3u entry keeps its current date, the entire playlist relocates to that track's era. If you put a 2020-dated track at the end of the m3u, the whole playlist will end up dated 2020 (and may cascade hundreds of unrelated tracks earlier). Usually you want the anchor to be a recently-added track. Run with `--dry-run` first; the cascade count tells you how invasive the operation will be.

### Import a folder of new tracks in a chosen order

Three steps. The CLI deliberately doesn't add tracks to `master.db` itself — pyrekordbox's `add_content` skips rekordbox's normal import pipeline (analysis queue, cloud-storage detection, tag reading) and produces second-class rows. Letting rekordbox do the import is the only way to get fully-loaded tracks.

```bash
# 1. Capture the desired order as an m3u8 (written to <folder>/<folder>.m3u8).
rbx import ~/Downloads/incoming --sort ctime

# 2. In rekordbox: drag-drop the folder into Collection. Wait for analysis to
#    finish (BPM, waveform, key all populate). Then quit rekordbox.

# 3. Restamp DateAdded so the tracks sort in the m3u8 order.
rbx backup
rbx sort --from ~/Downloads/incoming/incoming.m3u8 --dry-run
rbx sort --from ~/Downloads/incoming/incoming.m3u8
```

`import` and `sort` share the same m3u convention (top = oldest, bottom = newest), so the import-then-sort workflow round-trips with no manual editing. The same `sort` step works for *any* m3u8 — including playlists you've manually reordered in a text editor.

### Purge tombstoned rows

Rekordbox never hard-deletes from `djmdContent` or `djmdGenre` — removed tracks and orphaned genres stick around as tombstones (`rb_local_deleted = 1`). Cloud sync uses these to reconcile deletions across devices. On a long-lived library they can outnumber your real rows ~10:1 and may contribute to flaky cloud sync.

> **CRITICAL: only run after a full rekordbox cloud-account wipe.** This command hard-deletes rows that cloud sync is still tracking. If any other device on your account still has the original cloud state, sync will push the "deleted" rows back and you'll be in an inconsistent state. The intended workflow is: wipe cloud → run purge → use the now-pristine local DB as the seed for re-uploading. The `assert_recent_backup` guard means you can always roll back if something goes sideways.

```bash
rbx backup
rbx purge-tombstones --dry-run                   # content tombstones + orphan rows
rbx purge-tombstones                             # commit
rbx purge-tombstones --include-genres --dry-run  # also count unreferenced genre tombstones
rbx purge-tombstones --include-genres            # one-time legacy-genre cleanup
```

What gets deleted:

1. Orphan child rows in tables that FK-reference `djmdContent.ID` (cues, history, playlist memberships, etc.).
2. Tombstoned `djmdContent` rows.
3. **With `--include-genres`**: tombstoned `djmdGenre` rows not referenced by any alive `djmdContent`. Gated behind a flag because `randomize-genres` reuses genre rows, so normal operation doesn't accumulate genre tombstones — the flag exists mainly to clean up legacy residue from the old XML round-trip flow.

### Genre randomization for iOS shuffle

iOS rekordbox has no shuffle button. Workaround: give every track a unique random Genre, sort by Genre on iOS.

```bash
rbx backup
rbx randomize-genres --dry-run        # preview a few of the assignments
rbx randomize-genres                  # commit
```

Open rekordbox, let cloud sync push the change to iOS, sort by Genre on the iOS app.

**How it works**: for N alive tracks, generate a random permutation of `0..N-1`, format each as a zero-padded string (e.g. `"0000".."3868"` for 3,869 tracks), and assign one to each track. Re-runs use the same N strings — they look up the existing `djmdGenre` rows by name and rebind tracks to them. No new genre rows on re-shuffles, no tombstone churn.

The namespace only changes width when track count crosses a power of ten (e.g. 9,999 → 10,000 forces 5-digit names). When that happens, every old 4-digit name becomes orphaned in one go and rekordbox tombstones them on next cloud sync. Run `purge-tombstones --include-genres` after a cloud wipe if you want to clean those up.

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
    expand_dates.py
    sort.py
    import_cmd.py
    purge_tombstones.py
tests/
  test_dates.py
legacy-ts/              # original TypeScript scripts, kept for reference
```

## Roadmap

A TypeScript Electron / React Native GUI is planned. The CLI (`rbx`) is the foundational layer; the GUI will shell out to it.
