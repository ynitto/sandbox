'use strict';
// glob の展開。Node の版（fs.globSync は 22 から）にも、シェル（Windows はシェルが展開しない）にも頼らない。
//
//   **  0 個以上のフォルダ     *  / を含まない 0 文字以上     ?  / 以外の 1 文字
//   {a,b}  どれか              [abc] [a-z] [!a]  1 文字の集合
// パスの区切りは / でも \ でもよい。node_modules と . で始まるフォルダはたどらない（パターンに明示すればたどる）。

const fs = require('fs');
const path = require('path');

const isGlob = (s) => /[*?[{]/.test(s);

function toRegex(glob) {
  let re = '';
  for (let i = 0; i < glob.length; i++) {
    const ch = glob[i];
    if (ch === '*') {
      if (glob[i + 1] === '*') {
        // **/ は 0 個以上のフォルダ、末尾の ** は何でも
        if (glob[i + 2] === '/') { re += '(?:.*/)?'; i += 2; } else { re += '.*'; i += 1; }
      } else re += '[^/]*';
    } else if (ch === '?') re += '[^/]';
    else if (ch === '{') {
      const end = glob.indexOf('}', i);
      if (end < 0) { re += '\\{'; continue; }
      re += `(?:${glob.slice(i + 1, end).split(',').map((p) => toRegex(p).slice(1, -1)).join('|')})`;
      i = end;
    } else if (ch === '[') {
      const end = glob.indexOf(']', i + 1);
      if (end < 0) { re += '\\['; continue; }
      let body = glob.slice(i + 1, end);
      if (body.startsWith('!')) body = `^${body.slice(1)}`;
      re += `[${body.replace(/\\/g, '\\\\')}]`;
      i = end;
    } else re += ch.replace(/[.+^$()|\\]/g, '\\$&');
  }
  return `^${re}$`;
}

// glob の頭の、glob を含まない部分（たどり始めるフォルダ）
function baseOf(glob) {
  const parts = glob.split('/');
  const fixed = [];
  for (const p of parts) { if (isGlob(p)) break; fixed.push(p); }
  if (fixed.length === parts.length) fixed.pop(); // 最後まで glob が無い（呼ばれない想定）
  const base = fixed.join('/');
  return base === '' ? (glob.startsWith('/') ? '/' : '.') : base;
}

function globSync(pattern, { cwd = process.cwd() } = {}) {
  const glob = pattern.split(path.sep).join('/').replace(/\\/g, '/');
  const base = baseOf(glob);
  const abs = path.resolve(cwd, base);
  const re = new RegExp(toRegex(glob.slice(base === '.' && !glob.startsWith('./') ? 0 : base.length).replace(/^\//, '')));
  const explicitHidden = /(^|\/)\.[^/.]/.test(glob) || glob.includes('node_modules');
  const out = [];
  const walk = (dir, rel) => {
    let entries;
    try { entries = fs.readdirSync(dir, { withFileTypes: true }); } catch { return; }
    for (const e of entries) {
      const r = rel ? `${rel}/${e.name}` : e.name;
      const p = path.join(dir, e.name);
      if (e.isDirectory()) {
        if (!explicitHidden && (e.name.startsWith('.') || e.name === 'node_modules')) continue;
        walk(p, r);
      } else if (re.test(r)) out.push(p);
    }
  };
  walk(abs, '');
  return out.sort();
}

module.exports = { globSync, isGlob, toRegex };
