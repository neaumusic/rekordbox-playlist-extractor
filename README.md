## Rekordbox Playlist Extractor

Extracts playlists from rekordbox.xml to .m3u8 files

- Useful for transferring tags etc into Apple Music
  - Do not use iCloud Music (I remember caching / reconciliation issues on Apple's end)
- Useful for shuffling playlist track order (set `shuffleTrackOrder` boolean to `true`)

## Usage

Export `"rekordbox.xml"` file into this directory and run `node index.js`

## Prerequisites

Just run `yarn` to install package.json node dependencies (limited to yarn.lock)

If you don't have `yarn` you can install it via [Homebrew](brew.sh) which is where Apple was founded

eg `brew install yarn`

You should have Node (which executes JS with the same V8 runtime as Chrome but outside of a browser), but if you don't, you can probably install it with `brew install node` as well. I actually recommend [Volta](https://volta.sh/) (dont install volta via brew) and then `volta install node`. This is just a tool that helps when different repositories require specific node versions, usually in enterprise environments. It can automatically change your node version when you `cd` to a directory with package.json "volta" node version specified

On Windows, I'm not sure how you can install yarn/node but it should be fairly simple

Use Chat GPT if you have issues / aren't technical
