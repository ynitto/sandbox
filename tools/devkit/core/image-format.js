"use strict";

const { PNG_SIGNATURE } = require("./png");
const { isJpeg } = require("./jpeg");

function svgTag(buffer) {
  let xml = buffer.toString("utf8").trimStart();
  const preamble = /^(?:<\?[\s\S]*?\?>|<!--[\s\S]*?-->|<!DOCTYPE\s+svg\b(?:[^"'[>]|"[^"]*"|'[^']*'|\[[\s\S]*?\])*>)\s*/i;
  for (let match; (match = preamble.exec(xml));) xml = xml.slice(match[0].length);
  return xml.match(/^<svg(?=[\s/>]|$)((?:[^>"']|"[^"]*"|'[^']*')*)(?:>|$)/i);
}

function imageFormat(buffer) {
  const ascii = (start, end) => buffer.toString("latin1", start, end);
  if (buffer.subarray(0, 8).equals(PNG_SIGNATURE)) return "PNG";
  if (isJpeg(buffer)) return "JPEG";
  if (["GIF87a", "GIF89a"].includes(ascii(0, 6))) return "GIF";
  if (ascii(0, 4) === "RIFF" && ascii(8, 12) === "WEBP") return "WebP";
  if (ascii(0, 2) === "BM") return "BMP";
  if (svgTag(buffer)) return "SVG";
  return undefined;
}

module.exports = { imageFormat, svgTag };
