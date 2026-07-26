'use strict';

// agent-project / agent-flow の設定ファイル（.agent/agent-project.{yaml,yml,json} 等）から
// ビュアーが必要とする少数のトップレベルキー（bus / lock_dir）だけを読む簡易リーダー。
// 両ツールの CONFIG_DEFAULTS はトップレベルの「key: value」スカラなので、**読むのは
// トップレベルのスカラだけ**（ネストした値は daemon 側の関心なので無視する）。
// 探索順は各ツールの _find_config と同じ .agent ディレクトリ（呼び出し側が
// <workdir>/.kiro 相当を渡し、無ければ ~/.kiro）を使う。

const fs = require('fs');
const path = require('path');
const { agentHomeDir } = require('../../../base/main/agent-home');
const { parseYaml, isPlainObject, scalarString } = require('../../../base/main/yaml');

function readText(file) {
  try {
    // CRLF は読み時に正規化する（行末 \r で `key: value` の `$` アンカー照合が外れるのを防ぐ）
    return fs.readFileSync(file, 'utf8').replace(/\r\n/g, '\n');
  } catch {
    return null;
  }
}

// トップレベルのスカラだけを文字列で返す（ネスト・配列は無視、空値は落とす）。
// 値を常に文字列で返すのは呼び出し側の既存契約（`String(values.bus)` 等）を保つため。
function parseFlatYaml(text) {
  const doc = parseYaml(text);
  const out = {};
  if (!isPlainObject(doc)) return out;
  for (const [key, raw] of Object.entries(doc)) {
    const val = scalarString(raw);
    if (val !== null && val !== '') out[key] = val;
  }
  return out;
}

// baseDirs（.agent ディレクトリ候補）を順に探索し、最初に見つかった設定を返す
function readToolConfig(baseName, baseDirs) {
  const dirs = [...(baseDirs || []), agentHomeDir()];
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

// agent-project → agent-flow の順でキーを探す（daemon ロック等、両方が持ち得る値用）
function lookupScalar(key, baseDirs) {
  for (const name of ['agent-project', 'agent-flow']) {
    const cfg = readToolConfig(name, baseDirs);
    if (cfg && cfg.values[key] !== undefined && cfg.values[key] !== null) {
      const v = String(cfg.values[key]).trim();
      if (v) return { value: v, file: cfg.file };
    }
  }
  return null;
}

module.exports = { readToolConfig, lookupScalar, parseFlatYaml };
