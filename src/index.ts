import { extractPlaylists } from "./extractPlaylists";
import { createModifiedXml } from "./createModifiedXml";

// create "rekordbox-modified.xml" file
const shouldRandomizeGenres = false;
const shouldMapCuesToColor = false;
const shouldMapColorToCues = false;

// create "playlists" directory of m3u8 files
const shouldExtractPlaylists = true;
const shouldShufflePlaylists = false;

if (shouldRandomizeGenres || shouldMapCuesToColor || shouldMapColorToCues) {
  createModifiedXml({
    shouldRandomizeGenres,
    shouldMapCuesToColor,
    shouldMapColorToCues,
  });
}

if (shouldExtractPlaylists) {
  extractPlaylists(shouldShufflePlaylists);
}
