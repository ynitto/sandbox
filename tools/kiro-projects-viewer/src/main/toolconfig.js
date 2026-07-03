'use strict';

// kiro-projects / kiro-flow の設定ファイル（.kiro/kiro-projects.{yaml,yml,json} 等）から
// ビュアーが必要とする少数のトップレベルキー（bus / lock_dir）だけを読む簡易リーダー。
// 両ツールの CONFIG_DEFAULTS はトップレベルの「key: value」スカラなので、YAML の
// フルパースはせず平坦な行だけを拾う（ネストした値は無視する）。
// 探索順は各ツールの _find_config と同じ .kiro ディレクトリ（呼び出し側が
// <workdir>/.kiro 相当を渡し、無ければ ~/.kiro）を使う。

const fs = require('fs');
const os = require('os');
const path = require('path');

function readText(file) {
  try {
    return fs.readFileSync(file, 'utf8');
  } catch {
    return null;
  }
}

function stripQuotes(s) {
  const t = String(s || '').trim();
  if (t.length >= 2 && ((t[0] === '"' && t.endsWith('"')) || (t[0] === "'" && t.endsWith("'")))) {
    return t.slice(1, -1);
  }
  return t;
}

// トップレベルの `key: value` 行だけを拾う（インデント行＝ネストは無視）
function parseFlatYaml(text) {
  const out = {};
  for (const line of String(text || '').split('\n')) {
    if (!line || line[0] === ' ' || line[0] === '\t' || line[0] === '#') continue;
    const m = line.match(/^([A-Za-z_][\w-]*):\s*(.*)$/);
    if (!m) continue;
    const val = stripQuotes(m[2].replace(/\s+#.*$/, ''));
    if (val !== '') out[m[1]] = val;
  }
  return out;
}

// baseDirs（.kiro ディレクトリ候補）を順に探索し、最初に見つかった設定を返す
function readToolConfig(baseName, baseDirs) {
  const dirs = [...(baseDirs || []), path.join(os.homedir(), '.kiro')];
  for (const dir of dirs) {
    if (!dir) continue;
    for (const ext of ['yaml', 'yml', 'json']) {
      const file = path.join(dir, `${baseName}.${ext}`);
      const text = readText(file);
      if (text === null) continue;
      if (ext === 'json') {
        try {
          const obj = JSON.parse(text);
          if (obj && typeof obj === 'object') return { file, values: obj };
        } catch {
          continue;
        }
      } else {
        return { file, values: parseFlatYaml(text) };
      }
    }
  }
  return null;
}

// kiro-projects → kiro-flow の順でキーを探す（daemon ロック等、両方が持ち得る値用）
function lookupScalar(key, baseDirs) {
  for (const name of ['kiro-projects', 'kiro-flow']) {
    const cfg = readToolConfig(name, baseDirs);
    if (cfg && cfg.values[key] !== undefined && cfg.values[key] !== null) {
      const v = String(cfg.values[key]).trim();
      if (v) return { value: v, file: cfg.file };
    }
  }
  return null;
}

module.exports = { readToolConfig, lookupScalar, parseFlatYaml };
