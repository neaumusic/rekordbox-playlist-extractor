import fs from "fs";
import moment from "moment";
import { DOMParser } from "xmldom";
import xpath from "xpath";

const rbxml = fs.readFileSync("./rekordbox.xml", "utf8");
const doc = new DOMParser().parseFromString(rbxml);

const tracksCache = Array.from(xpath.select("/DJ_PLAYLISTS/COLLECTION/TRACK", doc)).reduce((cache, t) => {
  cache[t.getAttribute("TrackID")] = t;
  return cache;
}, {});

const root = xpath.select("/DJ_PLAYLISTS/PLAYLISTS/NODE[@Name='ROOT']", doc)[0];
const folders = Array.from(xpath.select("./NODE[@Type='0']", root));
const playlists = Array.from(xpath.select("./NODE[@Type='1']", root));
const outputDirectory = "playlists";
const folderDelimiter = " ";
const runTime = moment().format("YYMMDDHHmmss"); // can be added to playlist path

fs.rmSync(outputDirectory, { recursive: true, force: true });
fs.mkdirSync(outputDirectory);

playlists.forEach(p => {
  parsePlaylist(p, "");
});

folders.forEach(f => {
  parseFolder(f, "");
});

function parseFolder (folder, pathPrefix) {
  const folderName = folder.getAttribute("Name").replace(/[/\\?%*:|"<>]/g, '-');
  const path = `${pathPrefix}${folderName}`;

  const folders = Array.from(xpath.select("./NODE[@Type='0']", folder));
  const playlists = Array.from(xpath.select("./NODE[@Type='1']", folder));

  console.log(`-----------> parsing ${path}`);
  playlists.forEach(p => {
    parsePlaylist(p, path);
  });

  folders.forEach(f => {
    parseFolder(f, `${path}${folderDelimiter}`);
  });
}

function parsePlaylist (playlist, pathPrefix) {
  const playlistName = playlist.getAttribute("Name").replace(/[/\\?%*:|"<>]/g, '-');
  const path = `${pathPrefix}${folderDelimiter}${playlistName}.m3u8`;

  const trackIds = Array.from(xpath.select("./TRACK", playlist)).map(t => t.getAttribute("Key"));
  const tracks = trackIds.map(id => tracksCache[id])
    .sort((a, b) => Math.random() > 0.5 ? -1 : 1);

  console.log(`- ${outputDirectory}/${path}`);
  fs.writeFileSync(`${outputDirectory}/${path}`, [
    "#EXTM3U",
    tracks.map(t => parseTrack(t))
  ].join('\n'));
}

function parseTrack (track) {
  const totalTime = track.getAttribute("TotalTime");
  const artist = track.getAttribute("Artist");
  const trackName = track.getAttribute("Name");
  const location = decodeURIComponent(track.getAttribute("Location"));

  return `#EXTINF:${totalTime},${artist} - ${trackName}\n${location}\n`;
}
