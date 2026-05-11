import { extractPlaylists } from "./extractPlaylists";
import { createModifiedXml } from "./createModifiedXml";

// create "rekordbox-modified.xml" file
const shouldRandomizeGenres = true;

// create "playlists" directory of m3u8 files
const shouldExtractPlaylists = true;
const shouldShufflePlaylists = true;

if (shouldRandomizeGenres) {
  createModifiedXml({ shouldRandomizeGenres });
}

if (shouldExtractPlaylists) {
  extractPlaylists(shouldShufflePlaylists);
}
