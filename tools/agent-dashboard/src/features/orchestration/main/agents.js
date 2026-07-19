'use strict';

// エージェント CLI ドロップイン（agent-cli 契約）の棚卸し・検証・編集。
// 正典: schemas/agent-cli.schema.json。組み込み（kiro/claude/copilot/codex）以外の CLI は
// agents/<name>.json を置くだけで agent_cli: <name> として使える。
//
// 探索順（first-wins。同名は先勝ちで後段を陰らせる）:
//   $KIRO_AGENTS_DIR → <プロジェクトルート>/agents/ → ~/.agent/agents/ → ~/.kiro/agents/
// dashboard は棚卸し（どこに何があるか・検証エラー）を見せ、その場で作成・編集・削除できる。
// 結合はデータ契約のみ（各エンジンは自前の小さなローダで解釈する）。

const fs = require('fs');
const os = require('os');
const path = require('path');

const BUILTINS = ['kiro', 'claude', 'copilot', 'codex'];
const ALLOWED_KEYS = [
  'name', 'command', 'prompt_via', 'prompt_flag', 'model_flag', 'default_model',
  'output', 'env', 'timeout', 'empty_output_is_error', 'errors',
];
const OUTPUT_ENUM = ['stdout', 'file'];
const PROMPT_VIA_ENUM = ['stdin', 'argv'];
const ERROR_CLASS_ENUM = ['quota', 'auth', 'env', 'transient'];

function expandHome(p) {
  if (!p) return p;
  return p === '~' || p.startsWith('~/') ? path.join(os.homedir(), p.slice(1)) : p;
}

function isPlainObject(v) {
  return v !== null && typeof v === 'object' && !Array.isArray(v);
}

// 探索ディレクトリ（存在有無に関わらず、優先順で並べる）。
function searchDirs(cfg) {
  const dirs = [];
  if (process.env.KIRO_AGENTS_DIR) dirs.push(expandHome(process.env.KIRO_AGENTS_DIR));
  const roots = (cfg && cfg.projects && Array.isArray(cfg.projects.roots)) ? cfg.projects.roots : [];
  for (const root of roots) {
    if (root) dirs.push(path.join(expandHome(String(root)), 'agents'));
  }
  dirs.push(path.join(os.homedir(), '.agent', 'agents'));
  dirs.push(path.join(os.homedir(), '.kiro', 'agents'));
  // 重複は先勝ちで畳む
  const seen = new Set();
  const out = [];
  for (const d of dirs) {
    const r = path.resolve(d);
    if (seen.has(r)) continue;
    seen.add(r);
    out.push(d);
  }
  return out;
}

function defaultSaveDir() {
  return path.join(os.homedir(), '.agent', 'agents');
}

// 契約（agent-cli.schema.json）の必須・許可・enum を静的検証する。エラーは文字列配列で返す（throw しない）。
function validateSpec(spec) {
  const errors = [];
  if (!isPlainObject(spec)) {
    return ['定義がオブジェクトではありません'];
  }
  for (const key of Object.keys(spec)) {
    if (!ALLOWED_KEYS.includes(key)) errors.push(`未知のフィールド: ${key}`);
  }
  if (!Array.isArray(spec.command) || spec.command.length < 1) {
    errors.push('command は 1 要素以上の配列（実行 argv）が必要です');
  } else if (!spec.command.every((c) => typeof c === 'string')) {
    errors.push('command の要素は文字列である必要があります');
  }
  if (spec.output !== undefined && !OUTPUT_ENUM.includes(spec.output)) {
    errors.push(`output が不正です: ${spec.output}（stdout / file）`);
  }
  if (spec.prompt_via !== undefined && !PROMPT_VIA_ENUM.includes(spec.prompt_via)) {
    errors.push(`prompt_via が不正です: ${spec.prompt_via}（stdin / argv）`);
  }
  if (spec.errors !== undefined) {
    if (!Array.isArray(spec.errors)) {
      errors.push('errors は配列で指定してください');
    } else {
      spec.errors.forEach((rule, i) => {
        if (!isPlainObject(rule)) {
          errors.push(`errors[${i}] はオブジェクトで指定してください`);
          return;
        }
        if (typeof rule.match !== 'string' || !rule.match) errors.push(`errors[${i}].match が必要です`);
        if (!ERROR_CLASS_ENUM.includes(rule.class)) {
          errors.push(`errors[${i}].class が不正です: ${rule.class}（quota / auth / env / transient）`);
        }
      });
    }
  }
  if (spec.env !== undefined && !isPlainObject(spec.env)) errors.push('env はオブジェクトで指定してください');
  return errors;
}

// 棚卸し: 組み込み一覧と、探索 4 ディレクトリのドロップイン（first-wins の陰り表示つき）。
function list(cfg) {
  const dirs = searchDirs(cfg);
  const dropins = [];
  const seen = new Set(); // 先に現れた <name> が勝つ
  for (const dir of dirs) {
    let names;
    try {
      names = fs.readdirSync(dir).filter((n) => n.endsWith('.json')).sort();
    } catch {
      continue;
    }
    for (const file of names) {
      const name = file.slice(0, -'.json'.length);
      const full = path.join(dir, file);
      let spec = null;
      const errors = [];
      try {
        spec = JSON.parse(fs.readFileSync(full, 'utf8'));
      } catch (err) {
        errors.push(`JSON として読めません: ${err.message}`);
      }
      if (spec !== null) errors.push(...validateSpec(spec));
      if (BUILTINS.includes(name)) {
        errors.push(`組み込み名 ${name} は上書きできません（このドロップインは無視されます）`);
      }
      const shadowed = seen.has(name);
      if (!shadowed) seen.add(name);
      dropins.push({ name, dir, path: full, spec, shadowed, errors });
    }
  }
  return { builtins: BUILTINS.slice(), dropins };
}

// ドロップイン定義の作成・編集。既定の書込先は ~/.agent/agents/。検証を通ってから原子書換。
function save(cfg, payload) {
  const p = payload || {};
  const name = String(p.name || '').trim();
  if (!name) throw new Error('name が必要です');
  if (!/^[A-Za-z0-9._-]+$/.test(name)) throw new Error(`name に使えない文字が含まれています: ${name}`);
  if (BUILTINS.includes(name)) throw new Error(`組み込み名 ${name} は上書きできません`);
  const spec = p.spec;
  const errors = validateSpec(spec);
  if (errors.length) throw new Error(`定義が契約に適合しません: ${errors.join(' / ')}`);
  const dir = expandHome(String(p.dir || '') || defaultSaveDir());
  fs.mkdirSync(dir, { recursive: true });
  const target = path.join(dir, `${name}.json`);
  const tmp = `${target}.tmp.${process.pid}`;
  fs.writeFileSync(tmp, `${JSON.stringify(spec, null, 2)}\n`);
  fs.renameSync(tmp, target);
  return { name, dir, path: target };
}

// ドロップイン定義の削除（既知の agents ディレクトリ配下だけ）。
function remove(cfg, payload) {
  const p = payload || {};
  const name = String(p.name || '').trim();
  if (!name) throw new Error('name が必要です');
  if (BUILTINS.includes(name)) throw new Error(`組み込み名 ${name} は削除できません`);
  const dir = expandHome(String(p.dir || '') || defaultSaveDir());
  const known = new Set(searchDirs(cfg).map((d) => path.resolve(d)));
  known.add(path.resolve(defaultSaveDir()));
  if (!known.has(path.resolve(dir))) {
    throw new Error('既知の agents ディレクトリではないため削除できません');
  }
  const target = path.join(dir, `${name}.json`);
  try {
    fs.unlinkSync(target);
  } catch (err) {
    if (err.code !== 'ENOENT') throw err;
  }
  return { name, dir, path: target, removed: true };
}

module.exports = { list, save, remove, validateSpec, searchDirs, BUILTINS };
