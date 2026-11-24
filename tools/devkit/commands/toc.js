"use strict";

const fs = require("node:fs");
const path = require("node:path");
const { ArgParser, ArgTypeError, UsageError } = require("../core/args");

const OPEN = "<!-- toc -->";
const CLOSE = "<!-- /toc -->";
const HEADING = /^ {0,3}(#{1,6})(?:[ \t]+(.*)|$)/s;
const FENCE = /^ {0,3}(`{3,}|~{3,})(.*)$/s;
const BOM = Buffer.from([0xef, 0xbb, 0xbf]);
const SPACE = /[\p{White_Space}\x1c-\x1f]/u;
const trim = (text) => text.replace(/^[\p{White_Space}\x1c-\x1f]+|[\p{White_Space}\x1c-\x1f]+$/gu, "");

function scan(lines) {
  const headings = [];
  let start = null;
  let end = null;
  let closingWithoutOpening = false;
  let fence = null;
  let tocFence = null;
  let inComment = false;
  const opensFence = (match) => match && (match[1][0] === "~" || !match[2].includes("`"));
  const closesFence = (match, run) =>
    match && match[1][0] === run[0] && match[1].length >= run.length && !trim(match[2]);

  for (const [index, raw] of lines.entries()) {
    const line = raw.replace(/[\r\n]+$/, "");
    const match = FENCE.exec(line);
    if (start !== null && end === null) {
      if (tocFence) {
        if (closesFence(match, tocFence)) tocFence = null;
      } else if (opensFence(match)) {
        tocFence = match[1];
      } else if (trim(line) === CLOSE) {
        end = index;
      }
      continue;
    }
    if (fence) {
      if (closesFence(match, fence)) fence = null;
      continue;
    }
    if (!inComment) {
      if (start === null && trim(line) === OPEN) {
        start = index;
        continue;
      }
      if (start === null && trim(line) === CLOSE) {
        closingWithoutOpening = true;
        continue;
      }
      if (opensFence(match)) {
        fence = match[1];
        continue;
      }
      const heading = HEADING.exec(line);
      if (heading) headings.push([index, heading[1].length, heading[2] || ""]);
    }

    let position = 0;
    while (true) {
      const delimiter = inComment ? "-->" : "<!--";
      const found = line.indexOf(delimiter, position);
      if (found === -1) break;
      inComment = !inComment;
      position = found + delimiter.length;
    }
  }
  return { headings, start, end, closingWithoutOpening };
}

function renderToc(headings, minLevel, maxLevel, newline) {
  const used = new Set();
  const suffixes = new Map();
  const items = [];
  for (const [, level, rawTitle] of headings) {
    const title = trim(rawTitle.replace(/<!--.*?(?:-->|$)/gs, "").replace(/(?:^|[ \t]+)#+[ \t]*$/, ""));
    const slug = Array.from(title.toLowerCase())
      .filter((char) => SPACE.test(char) || /[-_\p{L}\p{M}\p{N}]/u.test(char))
      .map((char) => SPACE.test(char) ? "-" : char).join("");
    let anchor = slug;
    let suffix = suffixes.get(slug) ?? 0;
    while (used.has(anchor)) anchor = `${slug}-${++suffix}`;
    used.add(anchor);
    suffixes.set(slug, suffix);
    if (level >= minLevel && level <= maxLevel) {
      const label = title.replace(/[\\[\]]/g, "\\$&");
      items.push(`${"  ".repeat(level - minLevel)}- [${label}](#${anchor})${newline}`);
    }
  }
  return items;
}

const usage = "devkit toc [--min-level {1,2,3,4,5,6}] [--max-level {1,2,3,4,5,6}] [--create] [--check | --stdout] file";

function run(argv, io) {
  const parser = new ArgParser({
    prog: "devkit toc", usage, description: "Update a Markdown table of contents.",
    negativePattern: /^-\d+$/,
  });
  const level = (value) => {
    if (!/^[+-]?\d(?:_?\d)*$/.test(trim(value))) {
      throw new ArgTypeError(`invalid int value: '${value}'`);
    }
    return Number(value.replaceAll("_", ""));
  };
  const choices = [1, 2, 3, 4, 5, 6];
  parser.addPositional("file", { help: "Markdown file, or - for stdin" });
  parser.addOption(["--min-level"], { key: "minLevel", type: level, choices, def: 2 });
  parser.addOption(["--max-level"], { key: "maxLevel", type: level, choices, def: 6 });
  parser.addFlag(["--create"], { help: "insert missing TOC markers" });
  parser.addFlag(["--check"], { help: "check whether the TOC is current" });
  parser.addFlag(["--stdout"], { help: "print the updated document" });
  const args = parser.parse(argv);
  if (args.check && args.stdout) throw new UsageError("argument --stdout: not allowed with argument --check");
  if (args.minLevel > args.maxLevel) throw new UsageError("--min-level must not exceed --max-level");

  try {
    const file = args.file === "-" ? null : path.resolve(io.cwd, args.file);
    const original = file === null ? io.readStdin() : fs.readFileSync(file);
    const hasBom = original.subarray(0, BOM.length).equals(BOM);
    const document = new TextDecoder("utf-8", { fatal: true }).decode(original);
    const lines = document.match(/[^\r\n]*(?:\r\n|\r|\n)|[^\r\n]+$/g) || [];
    const newline = document.match(/\r\n|\r|\n/)?.[0] ?? "\n";
    const { headings, start, end, closingWithoutOpening } = scan(lines);

    if (start === null) {
      if (closingWithoutOpening) throw new Error("TOC opening marker is missing");
      if (args.check) {
        io.err("TOC has not been inserted\n");
        return 1;
      }
      if (!args.create) throw new Error("TOC markers are missing (use --create to insert them)");
      if (!headings.length) throw new Error("no ATX heading found for TOC insertion");
    } else if (end === null) {
      throw new Error("TOC closing marker is missing");
    }

    const items = renderToc(headings, args.minLevel, args.maxLevel, newline);
    if (start === null) {
      const index = headings[0][0];
      if (!/[\r\n]$/.test(lines[index])) lines[index] += newline;
      const separator = trim(lines[index + 1] ?? "") ? newline : "";
      lines.splice(index + 1, 0, [newline, OPEN + newline, ...items, CLOSE + newline, separator].join(""));
    } else {
      lines.splice(start + 1, end - start - 1, items.join(""));
    }

    const updated = Buffer.from((hasBom ? "\uFEFF" : "") + lines.join(""));
    const changed = !updated.equals(original);
    if (args.check) {
      if (changed) {
        io.err("TOC is out of date\n");
        return 1;
      }
    } else if (file === null || args.stdout) {
      io.outBuffer(updated);
    } else if (changed) {
      fs.writeFileSync(file, updated);
    }
    return 0;
  } catch (error) {
    io.err(`devkit toc: ${error.message}\n`);
    return 2;
  }
}

module.exports = { name: "toc", help: "Markdown の目次を更新する", usage, example: "devkit toc README.md", run };
