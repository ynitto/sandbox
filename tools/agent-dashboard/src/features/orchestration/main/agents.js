'use strict';

// エージェント CLI ドロップイン（agent-cli 契約）の棚卸し・検証・編集。
// 正典: schemas/agent-cli.schema.json。組み込み（kiro/claude/copilot/codex）以外の CLI は
// agents/<name>.json を置くだけで agent_cli: <name> として使える。
//
// 探索順（first-wins。同名は先勝ちで後段を陰らせる）:
//   $KIRO_AGENTS_DIR → <プロジェクトルート>/agents/ → ~/.agents/agents/ → ~/.kiro/agents/
// dashboard は棚卸し（どこに何があるか・検証エラー）を見せ、その場で作成・編集・削除できる。
// 結合はデータ契約のみ（各エンジンは自前の小さなローダで解釈する）。

const fs = require('fs');
const os = require('os');
const path = require('path');
const { agentHomeSubdir, sharedHomeRoots } = require('../../../base/main/agent-home');

const BUILTINS = ['kiro', 'claude', 'copilot', 'codex'];
// schemas/agent-cli.schema.json の properties と 1:1（正典はスキーマ。ここは静的検証の複製）。
const ALLOWED_KEYS = [
  'name', 'relative_cost', 'command', 'prompt_via', 'prompt_flag', 'file_flag', 'read_flag',
  'model_flag', 'default_model', 'output', 'env', 'timeout', 'empty_output_is_error',
  'variants', 'command_suffix', 'skill_command_prefix',
  'write_args', 'readonly_args', 'readonly', 'headless_autonomy', 'slash_native',
  'no_session_args', 'spill',
  'interactive', 'errors', 'session_log',
  // 用途別の起動差（1 エージェント = 1 定義にするための入れ物）。
  'profiles',
];
const OUTPUT_ENUM = ['stdout', 'file'];
const PROMPT_VIA_ENUM = ['stdin', 'argv'];
const ERROR_CLASS_ENUM = ['quota', 'auth', 'env', 'transient'];
const READONLY_ENUM = ['enforced', 'best-effort'];
const HEADLESS_AUTONOMY_ENUM = ['tool-loop', 'single-shot'];
const SESSION_LOG_FORMAT_ENUM = ['jsonl-dir', 'kiro-sqlite', 'opencode-sqlite'];
const STRING_ARRAY_FIELDS = ['command_suffix', 'write_args', 'readonly_args', 'no_session_args'];

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
  // 実行エンジンが担当しているプロジェクト配下の agents/（設定 roots は W2-4 で廃止）
  const roots = require('../../agent-project/main/engine').projectRoots(cfg);
  for (const root of roots) {
    if (root) dirs.push(path.join(expandHome(String(root)), 'agents'));
  }
  // Windows では WSL・Windows 両ホームを並べる（正典＝エンジン側が先）。
  for (const root of sharedHomeRoots()) dirs.push(path.join(root, '.agents', 'agents'));
  for (const root of sharedHomeRoots()) dirs.push(path.join(root, '.kiro', 'agents'));
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
  return agentHomeSubdir('agents');
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
  if (spec.relative_cost !== undefined
      && (!Number.isFinite(Number(spec.relative_cost)) || Number(spec.relative_cost) < 0)) {
    errors.push('relative_cost は 0 以上の数値で指定してください');
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
        if (rule.quota_kind !== undefined && !['exhausted', 'rate_limit'].includes(rule.quota_kind)) {
          errors.push(`errors[${i}].quota_kind が不正です: ${rule.quota_kind}`);
        }
      });
    }
  }
  if (spec.env !== undefined && !isPlainObject(spec.env)) errors.push('env はオブジェクトで指定してください');
  if (spec.variants !== undefined) {
    if (!isPlainObject(spec.variants) || !Object.values(spec.variants).every((v) => typeof v === 'string')) {
      errors.push('variants は文字列→文字列のオブジェクトで指定してください');
    }
  }
  if (spec.readonly !== undefined && !READONLY_ENUM.includes(spec.readonly)) {
    errors.push(`readonly が不正です: ${spec.readonly}（enforced / best-effort）`);
  }
  if (spec.headless_autonomy !== undefined && !HEADLESS_AUTONOMY_ENUM.includes(spec.headless_autonomy)) {
    errors.push(`headless_autonomy が不正です: ${spec.headless_autonomy}（tool-loop / single-shot）`);
  }
  if (spec.slash_native !== undefined && typeof spec.slash_native !== 'boolean') {
    errors.push(`slash_native が不正です: ${spec.slash_native}（true / false）`);
  }
  for (const field of STRING_ARRAY_FIELDS) {
    const value = spec[field];
    if (value !== undefined && (!Array.isArray(value) || !value.every((v) => typeof v === 'string'))) {
      errors.push(`${field} は文字列の配列で指定してください`);
    }
  }
  if (spec.interactive !== undefined) {
    if (!isPlainObject(spec.interactive)) {
      errors.push('interactive はオブジェクトで指定してください');
    } else if (!Array.isArray(spec.interactive.command) || spec.interactive.command.length < 1
        || !spec.interactive.command.every((c) => typeof c === 'string')) {
      errors.push('interactive.command は 1 要素以上の文字列配列（実行 argv）が必要です');
    }
  }
  if (spec.spill !== undefined && !isPlainObject(spec.spill)) {
    errors.push('spill はオブジェクトで指定してください');
  }
  if (spec.session_log !== undefined) {
    if (!isPlainObject(spec.session_log)) {
      errors.push('session_log はオブジェクトで指定してください');
    } else {
      if (!SESSION_LOG_FORMAT_ENUM.includes(spec.session_log.format)) {
        errors.push(`session_log.format が不正です: ${spec.session_log.format}（${SESSION_LOG_FORMAT_ENUM.join(' / ')}）`);
      }
      if (!Array.isArray(spec.session_log.paths) || !spec.session_log.paths.length
          || !spec.session_log.paths.every((p) => typeof p === 'string')) {
        errors.push('session_log.paths は 1 要素以上の文字列配列が必要です');
      }
    }
  }
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
  // variant は「1 つのエージェントを用途で使い分ける」実体（例: ollama-json は ollama の
  // planner/judge 用の変種）。variants の値に現れる名前は他の定義の内部部品であり、
  // 一覧・候補選択では base 定義（ollama）とは別枠の独立候補として出さない
  // （UI 側が isVariantTarget で絞り込む。読み込み・検証・argv 組み立ては従来どおり行う
  // ——エンジンは base の agent_cli をそのまま渡して自分で解決するため、この定義自体は
  // 引き続き実在・ロード可能である必要がある）。
  const variantTargets = new Set();
  for (const { spec, shadowed, errors } of dropins) {
    if (shadowed || errors.length) continue;
    if (!isPlainObject(spec) || !isPlainObject(spec.variants)) continue;
    for (const target of Object.values(spec.variants)) {
      if (typeof target === 'string' && target.trim()) variantTargets.add(target.trim().toLowerCase());
    }
  }
  for (const dropin of dropins) {
    dropin.isVariantTarget = variantTargets.has(dropin.name.toLowerCase());
  }
  return { builtins: BUILTINS.slice(), dropins };
}

// variant 先は base エージェントの用途別実体であり、tier や workload の
// 汎用候補として直接選ばない。判定を renderer と profiles で複製しないよう、
// first-wins で有効な定義だけから対象名を返す。
//
// **実ファイルの有無で判定しない。** 以前はここが `isVariantTarget`（＝同名の
// ドロップインが実在するか）を見ていたため、2026-08-25 の profile 統一で
// `ollama-json.json` 等 5 ファイルを消した瞬間に**空集合を返すようになり、
// 「変種先を tier 候補にできない」保存時ガードが黙って無効化していた**
// （12b をコード worker へ流さない構成上の封じが外れていた）。
// variants の値はいまや「base 名 + profile 名」の綴りなので、宣言そのものを見る。
function variantTargetNames(cfg) {
  const targets = new Set();
  for (const dropin of list(cfg).dropins) {
    if (dropin.shadowed || (dropin.errors || []).length) continue;
    const spec = dropin.spec;
    if (!isPlainObject(spec) || !isPlainObject(spec.variants)) continue;
    for (const target of Object.values(spec.variants)) {
      const name = String(target || '').trim().toLowerCase();
      // 自分自身を指す宣言は振り替えにならない（resolve_variant も無視する）。
      if (name && name !== String(dropin.name || '').trim().toLowerCase()) targets.add(name);
    }
  }
  return targets;
}

// ドロップイン定義の作成・編集。既定の書込先は ~/.agents/agents/。検証を通ってから原子書換。
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

module.exports = { list, variantTargetNames, save, remove, validateSpec, searchDirs, BUILTINS };
