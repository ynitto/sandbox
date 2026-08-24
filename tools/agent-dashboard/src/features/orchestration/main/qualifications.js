'use strict';

// Read-only management-plane view. agent-audit is the sole writer.
const fs = require('fs');
const path = require('path');
const control = require('./control');
const { sharedStateReadPath } = require('../../../base/main/agent-home');

const QUALIFICATIONS_FILE = 'qualifications.json';

function isObject(value) {
  return value !== null && typeof value === 'object' && !Array.isArray(value);
}

function resolveFile(cfg) {
  const orchestration = (cfg && cfg.orchestration) || {};
  return String(orchestration.qualificationsFile || '').trim()
    || path.join(control.resolveControlDir(cfg), QUALIFICATIONS_FILE);
}

function load(cfg) {
  const resolved = resolveFile(cfg);
  const file = sharedStateReadPath(path.dirname(resolved), path.basename(resolved));
  try {
    const document = JSON.parse(fs.readFileSync(file, 'utf8'));
    if (!isObject(document) || document.version !== 1
        || !Number.isInteger(document.revision) || document.revision < 0
        || !Array.isArray(document.candidates)) throw new Error('契約に適合しません');
    return { ...document, exists: true, file };
  } catch (error) {
    return { version: 1, revision: 0, candidates: [], exists: false, file,
      error: error && error.code === 'ENOENT' ? '' : String(error.message || error) };
  }
}

module.exports = { QUALIFICATIONS_FILE, resolveFile, load };
