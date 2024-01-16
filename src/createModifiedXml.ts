import xpath from "xpath";
import fs from "fs";
import { DOMParser, XMLSerializer } from "xmldom";
import { colorsByCueNumber, isPositionMark, isTrack } from "./types";

type ModifyXML = {
  shouldRandomizeGenres: boolean;
  shouldMapCuesToColor: boolean;
  shouldMapColorToCues: boolean;
};
export function createModifiedXml({ shouldRandomizeGenres, shouldMapCuesToColor, shouldMapColorToCues }: ModifyXML) {
  fs.copyFileSync("rekordbox.xml", "rekordbox-modified.xml");
  const domParser = new DOMParser();
  const xmlSerializer = new XMLSerializer();
  const rbxml = fs.readFileSync("rekordbox-modified.xml", "utf8");
  const doc = domParser.parseFromString(rbxml);

  if (shouldRandomizeGenres) {
    const tracks = Array.from(xpath.select(`/DJ_PLAYLISTS/COLLECTION/TRACK`, doc));
    tracks.forEach((track) => {
      if (!isTrack(track)) throw new Error(`invalid track`);
      track.setAttribute("Genre", String(Math.random()));
    });
  }

  if (shouldMapCuesToColor || shouldMapColorToCues) {
    Object.entries(colorsByCueNumber).forEach(([cueNumber, colors]) => {
      const { color, cueColor, trackColor } = colors;
      if (shouldMapCuesToColor) {
        console.log(`-----------> ${color} hot cue Num="${cueNumber}" to ${color} track Colour="${trackColor}"`);
        mapCueToTrackColor(cueNumber, trackColor, color, doc);
      }
      if (shouldMapColorToCues) {
        console.log(`-----------> ${color} track Colour="${trackColor}" to ${color} hot cue Num="${cueNumber}" (${cueColor})`);
        mapTrackColorToCue(cueNumber, trackColor, cueColor, color, doc);
      }
    });
  }

  const xmlString = xmlSerializer.serializeToString(doc);
  fs.writeFileSync("rekordbox-modified.xml", xmlString);
}

function mapCueToTrackColor(cueNumber: string, trackColor: string, color: string, doc: Document) {
  const cueXPath = `POSITION_MARK[@Num="${cueNumber}"]`;
  const tracksXPath = `/DJ_PLAYLISTS/COLLECTION/TRACK[${cueXPath}]`;
  const tracksWithCues = Array.from(xpath.select(tracksXPath, doc));

  tracksWithCues.forEach((trackWithCue) => {
    if (!isTrack(trackWithCue)) throw new Error("invalid track");
    const firstHotCue = xpath.select(cueXPath, trackWithCue)[0];
    if (!isPositionMark(firstHotCue)) throw new Error("invalid firstHotCue");
    trackWithCue.setAttribute("Colour", trackColor);
    trackWithCue.removeChild(firstHotCue);
    console.log(`- (${color}) ${trackWithCue.getAttribute("Artist")} - ${trackWithCue.getAttribute("Name")}`);
  });
}

function mapTrackColorToCue(cueNumber: string, trackColor: string, cueColor: string, color: string, doc: Document) {
  const tracksXPath = `/DJ_PLAYLISTS/COLLECTION/TRACK[@Colour="${trackColor}"]`;
  const coloredTrack = Array.from(xpath.select(tracksXPath, doc));

  coloredTrack.forEach((coloredTrack) => {
    if (!isTrack(coloredTrack)) throw new Error("invalid track");
    const oldCueTime = Number(coloredTrack.getAttribute("TotalTime")) - 0.01;
    const oldCueXPath = `POSITION_MARK[starts-with(@Start, ${oldCueTime})]`;
    const oldCues = Array.from(xpath.select(oldCueXPath, coloredTrack));
    oldCues.forEach((oldCue) => {
      if (!isPositionMark(oldCue)) throw new Error("invalid oldCue");
      coloredTrack.removeChild(oldCue);
    });

    const [_match, red, green, blue] = cueColor.match(/0x(.{2})(.{2})(.{2})/)!;
    const newCue = doc.createElement("POSITION_MARK");
    newCue.setAttribute("Name", "Track Color");
    newCue.setAttribute("Type", "0");
    newCue.setAttribute("Start", String(oldCueTime));
    newCue.setAttribute("Num", String(cueNumber));
    newCue.setAttribute("Red", String(parseInt(red, 16)));
    newCue.setAttribute("Green", String(parseInt(green, 16)));
    newCue.setAttribute("Blue", String(parseInt(blue, 16)));

    coloredTrack.appendChild(newCue);
    console.log(`- (${color}) ${coloredTrack.getAttribute("Artist")} - ${coloredTrack.getAttribute("Name")}`);
  });
}
