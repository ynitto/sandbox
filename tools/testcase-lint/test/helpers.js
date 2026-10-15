'use strict';
const fs = require('fs');
const os = require('os');
const path = require('path');
const { audit } = require('../src/audit');
const { defaults } = require('../src/config');
const { main } = require('../src/cli');

const ROOT = fs.mkdtempSync(path.join(os.tmpdir(), 'testcase-lint-'));
process.on('exit', () => fs.rmSync(ROOT, { recursive: true, force: true }));

let n = 0;
// files: { 'name': '内容' } を一時フォルダに書き、そのフォルダを返す
function tmp(files) {
  const dir = path.join(ROOT, String(n++));
  for (const [name, content] of Object.entries(files)) {
    const p = path.join(dir, name);
    fs.mkdirSync(path.dirname(p), { recursive: true });
    fs.writeFileSync(p, content.replace(/^\n/, ''));
  }
  return dir;
}

// ケース（と仕様・実装）を点検して指摘を返す
async function run(cases, { spec, source, config = {}, name = 'cases.yaml' } = {}) {
  const dir = tmp({ [name]: cases, ...(spec ? Object.fromEntries(Object.entries(spec).map(([k, v]) => [`spec/${k}`, v])) : {}), ...(source ? Object.fromEntries(Object.entries(source).map(([k, v]) => [`src/${k}`, v])) : {}) });
  const cfg = { ...defaults(), ...config };
  const result = await audit({ cases: [path.join(dir, name)], spec: spec ? [path.join(dir, 'spec')] : [], source: source ? [path.join(dir, 'src')] : [], config: cfg });
  return result.findings;
}

const codes = (fs_) => [...new Set(fs_.map((f) => f.code))].sort();
const byCode = (fs_, code) => fs_.filter((f) => f.code === code);

async function cli(args) {
  let out = ''; let err = '';
  const io = { out: { write: (s) => { out += s; } }, err: { write: (s) => { err += s; } } };
  const code = await main(args, io);
  return { code, out, err };
}

module.exports = { tmp, run, codes, byCode, cli, ROOT };
