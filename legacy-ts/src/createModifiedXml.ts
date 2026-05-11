import xpath from "xpath";
import fs from "fs";
import { DOMParser, XMLSerializer } from "xmldom";
import { isTrack } from "./types";

type ModifyXML = {
  shouldRandomizeGenres: boolean;
};
export function createModifiedXml({ shouldRandomizeGenres }: ModifyXML) {
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

  const xmlString = xmlSerializer.serializeToString(doc);
  fs.writeFileSync("rekordbox-modified.xml", xmlString);
}
