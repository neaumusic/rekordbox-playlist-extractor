## Rekordbox Playlist Extractor

Extracts playlists from rekordbox.xml to .m3u8 files

- Useful for transferring tags etc into Apple Music
  - Make sure "Keep Music Media folder organized" is unchecked since you don't want it to move the files, and "iCloud Music Library" is unchecked since there are caching issues where the cloud version will override the latest local tracks/playlists.
  - Do not use iCloud Music (I remember caching / reconciliation issues on Apple's end)
- Can shuffle playlist track orders (set `shouldShufflePlaylists` boolean to `true`)

Generates a `rekordbox-modified.xml` (which can be used to re-import and shuffle track genres)

## Usage

Export `"rekordbox.xml"` file into the root directory and run `yarn` (version 1.. this is an old repo)

For troubleshooting, I highly recommend Cursor AI or lovable.dev

If you get any error like 'invalid track' please let me know, but you can use `as Track` etc to override the type guards

https://github.com/neaumusic/rekordbox-playlist-extractor/assets/3423750/ffdd1ebb-0160-4874-a632-3020a5bc3f75
