## Rekordbox Playlist Extractor

Extracts playlists from rekordbox.xml to .m3u8 files

- Useful for transferring tags etc into Apple Music
  - Do not use iCloud Music (I remember caching / reconciliation issues on Apple's end)
- Can shuffle playlist track orders (set `shouldShufflePlaylists` boolean to `true`)
- Can map hot cues to track colors, and track colors to hot cues (at TotalTime - 0.01)

Generates a `rekordbox-modified.xml` (which can be used to re-import and shuffle track genres)

## Usage

Export `"rekordbox.xml"` file into the root directory and run `yarn`

For troubleshooting, use Chat GPT or Cursor AI 😎 ✌️

If you get any error like 'invalid track' please let me know, but you can use `as Track` etc to override the type guards

https://github.com/neaumusic/rekordbox-playlist-extractor/assets/3423750/ffdd1ebb-0160-4874-a632-3020a5bc3f75
