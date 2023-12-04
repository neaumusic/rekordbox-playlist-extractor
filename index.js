import fs from "fs";
import { DOMParser } from "xmldom";
import xpath from "xpath";

/** USER CONFIGURABLE */
const outputDirectory = "playlists";
const folderDelimiter = " ";
const shuffleTrackOrder = false;

/** read the file, parse with xmldom */
const rbxml = fs.readFileSync("./rekordbox.xml", "utf8");
const doc = new DOMParser().parseFromString(rbxml);

/** clear the output directory */
fs.rmSync(outputDirectory, { recursive: true, force: true });
fs.mkdirSync(outputDirectory);

/** store track metadata by TrackID so its easy to access later */
const tracksCache = Array.from(
  xpath.select("/DJ_PLAYLISTS/COLLECTION/TRACK", doc)
).reduce((cache, t) => {
  cache[t.getAttribute("TrackID")] = t;
  return cache;
}, {});

/** xpath root selectors */
const root = xpath.select("/DJ_PLAYLISTS/PLAYLISTS/NODE[@Name='ROOT']", doc)[0];
const folders = Array.from(xpath.select("./NODE[@Type='0']", root));
const playlists = Array.from(xpath.select("./NODE[@Type='1']", root));

playlists.forEach((p) => parsePlaylist(p, ""));
folders.forEach((f) => parseFolder(f, ""));

function parseFolder(folder, pathPrefix) {
  /** sanitize folder name */
  const folderName = folder.getAttribute("Name").replace(/[/\\?%*:|"<>]/g, "-");
  const path = `${pathPrefix}${folderName}`;
  console.log(`-----------> parsing ${path}`);

  /** parse playlists */
  const playlists = Array.from(xpath.select("./NODE[@Type='1']", folder));
  playlists.forEach((p) => parsePlaylist(p, path));

  /** parse sub-folders (recursive) */
  const folders = Array.from(xpath.select("./NODE[@Type='0']", folder));
  folders.forEach((f) => parseFolder(f, `${path}${folderDelimiter}`));
}

function parsePlaylist(playlist, pathPrefix) {
  /** sanitize playlist name */
  const playlistName = playlist
    .getAttribute("Name")
    .replace(/[/\\?%*:|"<>]/g, "-");
  const path = `${pathPrefix}${
    pathPrefix ? folderDelimiter : ""
  }${playlistName}.m3u8`;
  console.log(`- ${outputDirectory}/${path}`);

  const sortRandomly = (_a, _b) => (Math.random() > 0.5 ? -1 : 1);
  const sortByDateAdded = (a, b) =>
    new Date(a.getAttribute("DateAdded")) >
    new Date(b.getAttribute("DateAdded"))
      ? -1
      : 1;

  /** get track ids */
  const trackIds = Array.from(xpath.select("./TRACK", playlist)).map((t) =>
    t.getAttribute("Key")
  );
  /** sort or shuffle tracks */
  const tracks = trackIds
    .map((id) => tracksCache[id])
    .sort(shuffleTrackOrder ? sortRandomly : sortByDateAdded);

  /** write playlist file */
  fs.writeFileSync(
    `${outputDirectory}/${path}`,
    ["#EXTM3U", tracks.map((t) => parseTrack(t)).join("")].join("\n")
  );
}

function parseTrack(track) {
  const totalTime = track.getAttribute("TotalTime");
  const artist = track.getAttribute("Artist");
  const trackName = track.getAttribute("Name");
  const location = decodeURIComponent(track.getAttribute("Location"));

  return `#EXTINF:${totalTime},${artist} - ${trackName}\n${location}\n`;
}
