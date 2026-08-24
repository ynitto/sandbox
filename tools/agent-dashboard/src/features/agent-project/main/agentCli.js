'use strict';

// エージェント CLI 定義（agents/<name>.json）のローダ。契約の正典は
// schemas/agent-cli.schema.json で、Python 側の実装は agentcore/agentcli.py。
//
// **なぜ dashboard だけ自前実装なのか**: 定義の解決は CLIチャットの候補描画や Doctor の
// 起動のたびに走る。Python を起こすとそのたびにプロセス起動待ちが挟まり、UI が固まって
// 見える（S3-4 で host.yaml の読み取りを nodeRepos.js に置いたのと同じ理由）。
// 2 実装になる代償は、同じ定義から同じ argv が出ることをゴールデンテストで固定して払う
// （test/agent-cli-golden.test.js が Python 側の GOLDEN と同じ期待値を持つ）。
//
// argv の組み立て順（契約）:
//   command + (write_args | readonly_args) + no_session_args? + spill.args?
//           + model_flag model + command_suffix + argv 渡しのプロンプト

const fs = require('fs');
const os = require('os');
const path = require('path');
const { agentHomeSubdir, sharedHomeRoot } = require('../../../base/main/agent-home');

class AgentCliError extends Error {}

// 同梱定義の置き場。開発起動ではリポジトリ直下の agents/、パッケージ版では
// extraResources で同梱した resources/agents/。どちらも「最後の候補」で、上位の
// ディレクトリに置いた定義が勝つ（first-wins）。
function bundledDir() {
  const packaged = process.resourcesPath
    ? path.join(process.resourcesPath, 'agents')
    : '';
  if (packaged && fs.existsSync(path.join(packaged, 'kiro.json'))) return packaged;
  let dir = __dirname;
  for (let i = 0; i < 8; i += 1) {
    const cand = path.join(dir, 'agents');
    if (fs.existsSync(path.join(cand, 'kiro.json'))) return cand;
    const up = path.dirname(dir);
    if (up === dir) break;
    dir = up;
  }
  return '';
}

function pluginDirs(projectDir) {
  const dirs = [];
  if (process.env.KIRO_AGENTS_DIR) dirs.push(String(process.env.KIRO_AGENTS_DIR));
  dirs.push(path.join(String(projectDir || process.cwd()), 'agents'));
  dirs.push(agentHomeSubdir('agents'));
  // kiro-cli はエンジン側（Windows では WSL）で動くので、その定義もエンジン側のホームにある
  dirs.push(path.join(sharedHomeRoot(), '.kiro', 'agents'));
  const bundled = bundledDir();
  if (bundled) dirs.push(bundled);
  return dirs;
}

function strs(raw, field, file) {
  if (raw == null) return [];
  if (!Array.isArray(raw) || raw.some((x) => typeof x !== 'string')) {
    throw new AgentCliError(`エージェント定義 ${file}: ${field} は文字列配列が必要です`);
  }
  return raw.map(String);
}

// スキーマの各フィールドを正規化する。壊れた定義は黙って無視せず例外
// （設定ミスの静かな握り潰しを作らない — Python 側と同じ方針）。
function normalize(spec, name, file) {
  if (!spec || typeof spec !== 'object' || Array.isArray(spec)) {
    throw new AgentCliError(`エージェント定義 ${file}: オブジェクトが必要です`);
  }
  const command = strs(spec.command, 'command', file);
  if (!command.length) {
    throw new AgentCliError(`エージェント定義 ${file}: command は 1 要素以上の文字列配列が必須です`);
  }
  const output = spec.output === 'file' ? 'file' : 'stdout';
  if (output === 'file' && !command.some((t) => t.includes('{output_file}'))) {
    throw new AgentCliError(`エージェント定義 ${file}: output=file には command 中の {output_file} が必要です`);
  }
  const readonly = spec.readonly == null ? 'best-effort' : String(spec.readonly);
  if (readonly !== 'enforced' && readonly !== 'best-effort') {
    throw new AgentCliError(`エージェント定義 ${file}: readonly は enforced か best-effort です`);
  }
  const relativeCost = spec.relative_cost == null ? 1 : Number(spec.relative_cost);
  if (!Number.isFinite(relativeCost) || relativeCost < 0) {
    throw new AgentCliError(`エージェント定義 ${file}: relative_cost は 0 以上の数値です`);
  }
  // headless で 1 回起動したとき自分でツールを回して完遂できるか。interactive の有無や
  // file_flag からは推測しない（定義の申告が正典）。未宣言は安全側の single-shot。
  const headlessAutonomy = spec.headless_autonomy == null
    ? 'single-shot' : String(spec.headless_autonomy);
  if (headlessAutonomy !== 'tool-loop' && headlessAutonomy !== 'single-shot') {
    throw new AgentCliError(`エージェント定義 ${file}: headless_autonomy は tool-loop か single-shot です`);
  }
  const errors = [];
  for (const e of Array.isArray(spec.errors) ? spec.errors : []) {
    try {
      errors.push({ cls: String((e && e.class) || 'env'),
                    re: new RegExp(String((e && e.match) || ''), 'i'),
                    hint: String((e && e.hint) || ''),
                    quotaKind: String((e && e.quota_kind) || '') });
    } catch (err) {
      throw new AgentCliError(`エージェント定義 ${file}: errors.match が正規表現として不正です: ${err.message}`);
    }
  }
  const sp = (spec.spill && typeof spec.spill === 'object') ? spec.spill : {};
  const out = {
    name: String(spec.name || name),
    relativeCost,
    file,
    command,
    commandSuffix: strs(spec.command_suffix, 'command_suffix', file),
    skillCommandPrefix: spec.skill_command_prefix != null ? String(spec.skill_command_prefix) : '/',
    promptVia: spec.prompt_via === 'argv' ? 'argv' : 'stdin',
    promptFlag: spec.prompt_flag != null ? String(spec.prompt_flag) : null,
    fileFlag: spec.file_flag != null ? String(spec.file_flag) : null,
    readFlag: spec.read_flag != null ? String(spec.read_flag) : null,
    modelFlag: spec.model_flag != null ? String(spec.model_flag) : null,
    defaultModel: spec.default_model != null ? String(spec.default_model) : null,
    output,
    env: spec.env && typeof spec.env === 'object' ? spec.env : {},
    timeoutMs: Number(spec.timeout) > 0 ? Number(spec.timeout) * 1000 : null,
    emptyOutputIsError: spec.empty_output_is_error !== false,
    writeArgs: strs(spec.write_args, 'write_args', file),
    readonlyArgs: strs(spec.readonly_args, 'readonly_args', file),
    readonly,
    noSessionArgs: strs(spec.no_session_args, 'no_session_args', file),
    headlessAutonomy,
    spill: { args: strs(sp.args, 'spill.args', file), instruction: String(sp.instruction || '') },
    errors,
    interactive: null,
  };
  const it = (spec.interactive && typeof spec.interactive === 'object') ? spec.interactive : null;
  if (it) {
    const icmd = strs(it.command, 'interactive.command', file);
    if (!icmd.length) {
      throw new AgentCliError(`エージェント定義 ${file}: interactive.command は 1 要素以上必要です`);
    }
    const inject = it.prompt_inject == null ? 'send-keys' : String(it.prompt_inject);
    if (inject !== 'send-keys' && inject !== 'file') {
      throw new AgentCliError(`エージェント定義 ${file}: interactive.prompt_inject は send-keys か file です`);
    }
    out.interactive = {
      command: icmd,
      // トップレベルの write_args はヘッドレス専用の危険フラグを含むので継承しない
      writeArgs: strs(it.write_args, 'interactive.write_args', file),
      // readonly / no_session は省略時にトップレベルを継承する（同じ知識を 2 度書かせない）
      readonlyArgs: 'readonly_args' in it
        ? strs(it.readonly_args, 'interactive.readonly_args', file) : out.readonlyArgs,
      noSessionArgs: 'no_session_args' in it
        ? strs(it.no_session_args, 'interactive.no_session_args', file) : out.noSessionArgs,
      readyPattern: String(it.ready_pattern || ''),
      readyTimeoutSec: Number(it.ready_timeout_sec) > 0 ? Number(it.ready_timeout_sec) : 60,
      promptInject: inject,
    };
  }
  return out;
}

const cache = new Map();

// agents/<name>.json を探索順に読む。見つからない・壊れているは AgentCliError
// （黙って別 CLI へ倒さない。組み込み名も定義ファイル化した今、失敗はほぼインストール破損）。
function loadCli(name, projectDir, { useCache = true } = {}) {
  const key = String(name || '').trim().toLowerCase();
  if (!key || !/^[\w.-]+$/.test(key)) {
    throw new AgentCliError(`agent_cli の名前が不正です: ${name}`);
  }
  // 区切りは `\u0000`（**エスケープで書く**。生の NUL バイトをソースへ直接埋めると git が
  // ファイルごとバイナリ扱いし、diff もレビューもマージもできなくなる）。CLI 名は
  // `[\w.-]+` に制限済みなので、この 1 文字が名前とパスの境界を一意に決める。
  const cacheKey = `${key}\u0000${projectDir || ''}`;
  if (useCache && cache.has(cacheKey)) return cache.get(cacheKey);
  const dirs = pluginDirs(projectDir);
  for (const dir of dirs) {
    const file = path.join(dir, `${key}.json`);
    let raw;
    try {
      raw = fs.readFileSync(file, 'utf8');
    } catch {
      continue;                      // 無いディレクトリ・無いファイルは次の候補へ
    }
    let parsed;
    try {
      parsed = JSON.parse(raw);
    } catch (e) {
      throw new AgentCliError(`エージェント定義 ${file}: JSON として読めません: ${e.message}`);
    }
    const spec = normalize(parsed, key, file);
    if (useCache) cache.set(cacheKey, spec);
    return spec;
  }
  throw new AgentCliError(
    `未知の agent_cli です: ${key}（agents/${key}.json が見つかりません）\n`
    + `  探索順: ${dirs.join(' → ')}\n`
    + '  組み込み CLI もこの定義ファイルで動きます。インストールが壊れている可能性があります。'
  );
}

function clearCache() {
  cache.clear();
}

function modeArgs(spec, { interactive, readonly, noSession }) {
  const src = (interactive && spec.interactive) ? spec.interactive : spec;
  const args = readonly ? [...src.readonlyArgs] : [...src.writeArgs];
  if (noSession) {
    for (const tok of src.noSessionArgs) {
      if (!args.includes(tok)) args.push(tok);   // readonly と重複するフラグを 2 度足さない
    }
  }
  return args;
}

function expand(tokens, model, holder) {
  const out = [];
  for (const tok of tokens) {
    if (tok.includes('{model}')) {
      if (!model) continue;                      // モデル未指定は {model} を含むトークンごと落とす
      out.push(tok.split('{model}').join(model));
      continue;
    }
    if (tok.includes('{output_file}')) {
      if (!holder.path) {
        holder.path = path.join(os.tmpdir(),
          `agent-dashboard-cli-${process.pid}-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}.txt`);
      }
      out.push(tok.split('{output_file}').join(holder.path));
      continue;
    }
    out.push(tok);
  }
  return out;
}

// ヘッドレス 1 回分を組み立てる（実行はしない・決定的）。
// spillPath を渡すと、渡された prompt の末尾へ spill.instruction（{file} 置換済み）を足し、
// そのモードの権限フラグを spill.args で **置き換える**（kiro の --trust-tools= と
// --trust-tools=fs_read が並んで後勝ちに賭けるのを避ける）。
function headlessCmd(spec, model, prompt, {
  readonly = false, noSession = false, spillPath = '', files = [], readFiles = [],
} = {}) {
  const m = String(model || spec.defaultModel || '');
  const holder = {};
  let argv = expand(spec.command, m, holder);
  const useSpill = !!(spillPath && spec.spill.instruction);
  let mode = modeArgs(spec, { interactive: false, readonly, noSession });
  if (useSpill && spec.spill.args.length) {
    mode = mode.filter((t) => !spec.readonlyArgs.includes(t) && !spec.writeArgs.includes(t));
    mode = [...spec.spill.args, ...mode];
  }
  argv = argv.concat(expand(mode, m, holder));
  if (m && spec.modelFlag && !spec.command.some((t) => t.includes('{model}'))) {
    argv.push(spec.modelFlag, m);
  }
  if (spec.fileFlag) {
    for (const file of files) argv.push(spec.fileFlag, String(file));
  }
  if (spec.readFlag) {
    for (const file of readFiles) argv.push(spec.readFlag, String(file));
  }
  argv = argv.concat(expand(spec.commandSuffix, m, holder));

  // 退避時、指示は **置き換えず付け足す**。呼び出し側の指示（役割・出力書式）は本文とは別に
  // argv へ載っていることがあり（Doctor がまさにそれ）、置き換えると役割ごと消える。
  let text = String(prompt == null ? '' : prompt);
  if (useSpill) {
    text = (text ? `${text} ` : '') + spec.spill.instruction.split('{file}').join(String(spillPath));
  }
  let stdin = null;
  if (spec.promptVia === 'argv') {
    if (spec.promptFlag) argv.push(spec.promptFlag, text);
    else argv.push(text);
  } else {
    stdin = text;
  }
  return {
    command: argv[0],
    args: argv.slice(1),
    argv,
    stdin,
    outputFile: holder.path || null,
    env: spec.env,
    emptyOutputIsError: spec.emptyOutputIsError,
    timeoutMs: spec.timeoutMs,
    readonlyWarning: readonlyWarning(spec, readonly),
  };
}

function interactiveCmd(spec, model, { readonly = false, noSession = false } = {}) {
  const it = spec.interactive;
  if (!it) {
    throw new AgentCliError(
      `${spec.name} は対話起動に対応していません（${spec.file} に interactive.command がありません）`);
  }
  const m = String(model || spec.defaultModel || '');
  if (it.command.some((t) => t.includes('{model}')) && !m) {
    throw new AgentCliError(`${spec.name} の対話起動にはモデルの指定が必要です`);
  }
  const holder = {};
  let argv = expand(it.command, m, holder);
  argv = argv.concat(expand(modeArgs(spec, { interactive: true, readonly, noSession }), m, holder));
  if (m && spec.modelFlag && !it.command.some((t) => t.includes('{model}'))) {
    argv.push(spec.modelFlag, m);
  }
  if (holder.path) {
    throw new AgentCliError(`${spec.name} の interactive.command に {output_file} は使えません`);
  }
  return argv;
}

// 読み取り専用を要求したが CLI が保証しないときの警告（S9 未決 7 の決着）。
// このレイヤは宣言どおりの argv を組み立てるだけで、フラグを無視する CLI への防御は持たない。
// できるのは「保証できない」ことを人に伝えることだけ。
function readonlyWarning(spec, readonly) {
  if (!readonly || spec.readonly === 'enforced') return '';
  return `${spec.name} は読み取り専用を保証しません`
    + '（助言のみのつもりでもファイル変更やコマンド実行が起こりえます）';
}

function readyPattern(spec, fallback = '') {
  return (spec && spec.interactive && spec.interactive.readyPattern) || fallback;
}

function readyTimeoutSec(spec, fallback = 60) {
  return (spec && spec.interactive && spec.interactive.readyTimeoutSec) || fallback;
}

function promptInject(spec) {
  return (spec && spec.interactive && spec.interactive.promptInject) || 'send-keys';
}

function skillCommandPrefix(spec) {
  return (spec && spec.skillCommandPrefix) || '/';
}

// セッションへ送るテキストの行頭 `/` を、その CLI のスキル起動記号へ差し替える
// （codex は `/skill-name` ではなく `$skill-name`）。既定 `/` の CLI では何も変えない。
// 対象は「行頭が `/` + 英数字」だけ——パス（`/home/...`）は 2 つ目の `/` を含むので、
// 最初のトークンに `/` が現れたら書き換えない。
function rewriteSkillCommands(text, prefix) {
  const p = String(prefix || '/');
  if (p === '/') return String(text == null ? '' : text);
  return String(text == null ? '' : text).replace(
    /^\/(?=[A-Za-z0-9_-]+(?:[\s:]|$))/gm, p
  );
}

function classifyError(spec, blob) {
  const text = String(blob || '');
  for (const rule of (spec && spec.errors) || []) {
    if (rule.re.test(text)) return { cls: rule.cls, hint: rule.hint };
  }
  return null;
}

module.exports = {
  AgentCliError,
  pluginDirs,
  bundledDir,
  normalize,
  loadCli,
  clearCache,
  headlessCmd,
  interactiveCmd,
  readonlyWarning,
  readyPattern,
  readyTimeoutSec,
  promptInject,
  skillCommandPrefix,
  rewriteSkillCommands,
  classifyError,
};
