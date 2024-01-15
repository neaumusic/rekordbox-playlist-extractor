import fs from "fs";
import { DOMParser, XMLSerializer } from "xmldom";
import xpath from "xpath";
import { Folder, Playlist, Track, isFolder, isPlaylist, isTrack, isTrackReference } from "./types";

// USER CONFIGURABLE
const shouldExtractPlaylists = true;
const shouldShufflePlaylists = false;
const outputDirectory = "playlists";
const folderDelimiter = " ";
if (shouldExtractPlaylists) {
  // write m3u8s
  extractPlaylists();
}

// USER CONFIGURABLE
const shouldRandomizeGenres = true;
if (shouldRandomizeGenres) {
  // write random genres to new xml
  randomizeGenres();
}

function randomizeGenres() {
  fs.copyFileSync("rekordbox.xml", "rekordbox-shuffled-genres.xml");
  const domParser = new DOMParser();
  const xmlSerializer = new XMLSerializer();
  const rbxml = fs.readFileSync("rekordbox-shuffled-genres.xml", "utf8");
  const doc = domParser.parseFromString(rbxml);

  const tracks = Array.from(xpath.select(`/DJ_PLAYLISTS/COLLECTION/TRACK`, doc));
  tracks.forEach((track) => {
    if (!isTrack(track)) throw new Error(`invalid track`);
    track.setAttribute("Genre", String(Math.random()));
  });

  const xmlString = xmlSerializer.serializeToString(doc);
  fs.writeFileSync("rekordbox-shuffled-genres.xml", xmlString);
}

function extractPlaylists() {
  // clear the output directory
  fs.rmSync(outputDirectory, { recursive: true, force: true });
  fs.mkdirSync(outputDirectory);

  // read the file, parse with xmldom
  const rbxml = fs.readFileSync("./rekordbox.xml", "utf8");
  const doc = new DOMParser().parseFromString(rbxml);

  // store track metadata by TrackID so its easy to access later
  const tracks = Array.from(xpath.select("/DJ_PLAYLISTS/COLLECTION/TRACK", doc));
  const tracksCache = tracks.reduce<{ [id: string]: Track }>((tracksCache, track) => {
    if (!isTrack(track)) throw new Error(`invalid track`);
    const trackId = track.getAttribute("TrackID");
    tracksCache[trackId] = track;
    return tracksCache;
  }, {});

  // xpath root selectors
  const root = xpath.select("/DJ_PLAYLISTS/PLAYLISTS/NODE[@Name='ROOT']", doc)[0];
  if (!isFolder(root)) throw new Error(`invalid root`);

  const path = "";
  const playlists = Array.from(xpath.select("./NODE[@Type='1']", root));
  const folders = Array.from(xpath.select("./NODE[@Type='0']", root));
  playlists.forEach((playlist) => {
    if (!isPlaylist(playlist)) throw new Error(`invalid playlist`);
    parsePlaylist(playlist, path);
  });
  folders.forEach((folder) => {
    if (!isFolder(folder)) throw new Error(`invalid folder`);
    parseFolder(folder, path);
  });

  function parseFolder(folder: Folder, pathPrefix: string) {
    // sanitize folder name
    const folderName = folder.getAttribute("Name").replace(/[/\\?%*:|"<>]/g, "-");
    const path = `${pathPrefix}${folderName}`;
    console.log(`-----------> parsing ${path}`);

    // parse playlists
    const playlists = Array.from(xpath.select("./NODE[@Type='1']", folder));
    playlists.forEach((playlist) => {
      if (!isPlaylist(playlist)) throw new Error(`invalid playlist`);
      parsePlaylist(playlist, path);
    });

    // parse sub-folders (recursive)
    const folders = Array.from(xpath.select("./NODE[@Type='0']", folder));
    folders.forEach((folder) => {
      if (!isFolder(folder)) throw new Error(`invalid folder`);
      parseFolder(folder, `${path}${folderDelimiter}`);
    });
  }

  function parsePlaylist(playlist: Playlist, pathPrefix: string) {
    // sanitize playlist name
    const playlistName = playlist.getAttribute("Name").replace(/[/\\?%*:|"<>]/g, "-");
    const path = `${pathPrefix}${pathPrefix ? folderDelimiter : ""}${playlistName}.m3u8`;
    console.log(`- ${outputDirectory}/${path}`);

    const sortRandomly = () => (Math.random() > 0.5 ? -1 : 1);
    const sortByDateAdded = (a: Track, b: Track) => (new Date(a.getAttribute("DateAdded")) > new Date(b.getAttribute("DateAdded")) ? -1 : 1);

    // get track ids
    const trackIds = Array.from(xpath.select("./TRACK", playlist)).map((trackReference) => {
      if (!isTrackReference(trackReference)) throw new Error(`invalid trackReference`);
      return trackReference.getAttribute("Key");
    });
    // sort or shuffle tracks
    const tracks = trackIds.map((id) => tracksCache[id]).sort(shouldShufflePlaylists ? sortRandomly : sortByDateAdded);

    // write playlist file
    fs.writeFileSync(`${outputDirectory}/${path}`, ["#EXTM3U", tracks.map((t) => parseTrack(t)).join("")].join("\n"));
  }

  function parseTrack(track: Track) {
    const totalTime = track.getAttribute("TotalTime");
    const artist = track.getAttribute("Artist");
    const trackName = track.getAttribute("Name");
    const location = decodeURIComponent(track.getAttribute("Location"));

    return `#EXTINF:${totalTime},${artist} - ${trackName}\n${location}\n`;
  }
}
