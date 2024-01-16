import { DOMImplementation } from "xmldom";
const domImplementation = new DOMImplementation();
const doctype = domImplementation.createDocumentType("html", "", "");
const document = domImplementation.createDocument(null, "html", doctype);
export function isElement(node: any): node is Element {
  return node.nodeType === document.ELEMENT_NODE;
}

/**
 * FOLDER
 */
export interface FolderAttributes {
  Type: "0";
  Name: string;
  Count: string;
}
export interface Folder extends Element {
  tagName: "NODE";
  getAttribute<K extends keyof FolderAttributes>(attr: K): FolderAttributes[K];
  childNodes: NodeListOf<Folder | Playlist>;
}
export function isFolder(node: any): node is Folder {
  return (
    isElement(node) &&
    node.tagName === "NODE" &&
    node.getAttribute("Type") === "0" &&
    Array.from(node.childNodes)
      .filter(isElement)
      .every((childNode) => isFolder(childNode) || isPlaylist(childNode))
  );
}

/**
 * PLAYLIST
 */
export interface PlaylistAttributes {
  Type: "1";
  Name: string;
  KeyType: string;
  Entries: string;
}
export interface Playlist extends Element {
  tagName: "NODE";
  getAttribute<K extends keyof PlaylistAttributes>(attr: K): PlaylistAttributes[K];
  childNodes: NodeListOf<Track>;
}
export function isPlaylist(node: any): node is Playlist {
  return (
    isElement(node) &&
    node.tagName === "NODE" &&
    node.getAttribute("Type") === "1" &&
    Array.from(node.childNodes)
      .filter(isElement)
      .every((childNode) => isTrackReference(childNode))
  );
}

/**
 * TRACK
 */
export interface TrackAttributes {
  TrackID: string;
  Name: string;
  Artist: string;
  Composer: string;
  Album: string;
  Grouping: string;
  Genre: string;
  Kind: string;
  Size: string;
  TotalTime: string;
  DiscNumber: string;
  TrackNumber: string;
  Year: string;
  AverageBpm: string;
  DateAdded: string;
  BitRate: string;
  SampleRate: string;
  Comments: string;
  PlayCount: string;
  Rating: string;
  Location: string;
  Remixer: string;
  Tonality: string;
  Label: string;
  Mix: string;
  Colour: string;
}
export interface Track extends Element {
  tagName: "TRACK";
  getAttribute<K extends keyof TrackAttributes>(attr: K): TrackAttributes[K];
  childNodes: NodeListOf<Tempo>;
}
export function isTrack(node: any): node is Track {
  return (
    isElement(node) &&
    node.tagName === "TRACK" &&
    !!node.getAttribute("TrackID") &&
    Array.from(node.childNodes)
      .filter(isElement)
      .every((childNode) => isTempo(childNode) || isPositionMark(childNode))
  );
}

/**
 * TEMPO
 */
export interface TempoAttributes {
  Inizio: string;
  Bpm: string;
  Metro: string;
  Battito: string;
}
export interface Tempo extends Element {
  tagName: "TEMPO";
  getAttribute<K extends keyof TempoAttributes>(attr: K): TempoAttributes[K];
}
export function isTempo(node: any): node is Tempo {
  return isElement(node) && node.tagName === "TEMPO";
}

/**
 * POSITION_MARK
 */
export interface PositionMarkAttributes {
  Name: string;
  Type: string;
  Start: string;
  End: string;
  Num: string;
  Red: string;
  Green: string;
  Blue: string;
}
export interface PositionMark extends Element {
  tagName: "POSITION_MARK";
  getAttribute<K extends keyof PositionMarkAttributes>(attr: K): PositionMarkAttributes[K];
}
export function isPositionMark(node: any): node is PositionMark {
  return isElement(node) && node.tagName === "POSITION_MARK";
}

export interface ColorsByCueNumber {
  [cueNumber: PositionMarkAttributes["Num"]]: {
    color: string;
    cueColor: string;
    trackColor: string;
  };
}
// cant remember which of these color values are custom
export const colorsByCueNumber: ColorsByCueNumber = {
  0: { color: "pink", cueColor: "0xF870F8", trackColor: "0xFF007F" },
  1: { color: "red", cueColor: "0xF80000", trackColor: "0xFF0000" },
  2: { color: "orange", cueColor: "0xF8A030", trackColor: "0xFFA500" },
  3: { color: "yellow", cueColor: "0xC3AF01", trackColor: "0xFFFF00" },
  4: { color: "green", cueColor: "0x04DF03", trackColor: "0x00FF00" },
  5: { color: "teal", cueColor: "0x00C0F8", trackColor: "0x25FDE9" },
  6: { color: "blue", cueColor: "0x0050F8", trackColor: "0x0000FF" },
  7: { color: "purple", cueColor: "0x9808F8", trackColor: "0x660099" },
};

/**
 * TRACK_REFERENCE
 */
export interface TrackReferenceAttributes {
  Key: TrackAttributes["TrackID"];
}
export interface TrackReference extends Element {
  tagName: "TRACK";
  getAttribute<K extends keyof TrackReferenceAttributes>(attr: K): TrackReferenceAttributes[K];
}
export function isTrackReference(node: any): node is TrackReference {
  return isElement(node) && node.tagName === "TRACK" && !!node.getAttribute("Key");
}
