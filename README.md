Rekordbox Playlist Extractor
--

Extracts all playlists from rekordbox.xml to a folder of .m3u8 files

Very fast for transferring tagged tracks / updated intelligent playlist tracks back into Apple Music

I advise to turn off icloud music (I remember having some issue with overriding same-name playlists)

---

Usage
--

You need to export a `"rekordbox.xml"` file into this directory and run `node index.js`


Prerequisites
--
You initially need to run `yarn` to install dependencies

If you don't have `yarn` you can install it via [Homebrew](brew.sh) which is where Apple was founded

eg `brew install yarn`

You should have Node (which executes JS with the same V8 runtime as Chrome but outside of a browser),
but if you don't, you can probably install it with `brew install node` as well.

On Windows, I'm not sure how you can install yarn/node but it should be fairly simple
