"use strict";

const { existsSync } = require("node:fs");
const path = require("node:path");

function resolveDevkitDir({ env = process.env, resourcesPath, appDir, exists = existsSync }) {
  const candidates = [
    env.CLIPIT_DEVKIT_PATH,
    resourcesPath && path.join(resourcesPath, "devkit"),
    appDir && path.resolve(appDir, "../../devkit"),
    // In the repository, clipit-app and devkit are siblings under tools/.
    appDir && path.resolve(appDir, "../devkit"),
  ];
  return candidates.find((dir) => dir && exists(path.join(dir, "index.js"))) ?? null;
}

function loadDevkit(options) {
  const dir = resolveDevkitDir(options);
  if (!dir) throw new Error("devkit の index.js が見つかりません。");
  const entry = path.resolve(dir, "index.js");
  const { listCommands, runCommand } = require(entry);
  if (typeof listCommands !== "function" || typeof runCommand !== "function") {
    throw new Error(`devkit API が見つかりません。 ${entry}`);
  }
  return { listCommands, runCommand };
}

module.exports = { resolveDevkitDir, loadDevkit };
